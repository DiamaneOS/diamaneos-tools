"""Build the apps DiamaneOS ships as presigned prebuilts: the App Store and Auditor.

GrapheneOS builds both with Gradle and commits the signed release APKs to
platform_external_AppStore and platform_external_Auditor. DiamaneOS builds its
forks the same way, at the commits config/apps.json pins, with a pinned JDK and
Android SDK kept apart from the OS tree:

  apps toolchain --root ROOT        install the JDK and SDK packages (network)
  apps build --root ROOT --app ID   build the unsigned release APK (network: Gradle
                                    fetches the dependencies the app's verification
                                    metadata pins); with --test-key-dir, also sign it
                                    with the DiamaneOS test app key
  apps publish --run RUN --to DIR   copy the signed APK and its v4 signature into a
                                    checkout of the app's prebuilt project (no commit):
                                    --test for a test-signed build, or --signed APK
                                    --idsig FILE for one the owner signed offline

publish checks that the signed APK is exactly the run's unsigned APK plus an APK
Signing Block, with one signer that is not GrapheneOS's: the test key, or the
app's release_signer.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import zipfile

from . import process
from .vendor_extract import sha

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config/apps.json'
TOOLS_URL = 'https://github.com/DiamaneOS/diamaneos-tools'
SHA1 = re.compile(r'[0-9a-f]{40}')
SHA256 = re.compile(r'[0-9a-f]{64}')
PACKAGE = re.compile(r'[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+')
RUN_NAME = re.compile(r'([a-z0-9-]+)-([0-9]{8})T[0-9]{6}Z')
SDK_PACKAGE = re.compile(r'[a-z-]+;[A-Za-z0-9.-]+')
# Files of a test key directory (BUILD.md "Building the apps").
TEST_KEYSTORE, TEST_KEY_ALIAS, TEST_KEYSTORE_PASS, TEST_CATALOG_KEY = (
    'apps-test.p12', 'test', 'keystore-password', 'apps-test.0.pub')
SIGNING_FLAGS = ('--alignment-preserved', '--v1-signing-enabled', 'false', '--v2-signing-enabled', 'true',
                 '--v3-signing-enabled', 'false', '--v4-signing-enabled', 'true')
GRADLE_TASK = 'assembleRelease'
UNSIGNED_OUTPUT = 'app/build/outputs/apk/release/app-release-unsigned.apk'


class AppsError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise AppsError(message)


def load(path=CONFIG):
    """config/apps.json, checked."""
    from . import safe_json
    document = safe_json.load_json(path)
    require(isinstance(document, dict) and document.get('schema_version') == 1, 'invalid apps config')
    tc = document.get('toolchain')
    require(isinstance(tc, dict), 'apps config has no toolchain')
    for name in ('jdk', 'cmdline_tools'):
        part = tc.get(name)
        require(isinstance(part, dict) and isinstance(part.get('version'), str) and part['version']
                and str(part.get('url', '')).startswith('https://') and SHA256.fullmatch(str(part.get('sha256'))),
                'invalid toolchain entry: ' + name)
    require(isinstance(tc.get('sdk_packages'), list) and tc['sdk_packages']
            and all(isinstance(p, str) and SDK_PACKAGE.fullmatch(p) for p in tc['sdk_packages']),
            'invalid SDK package list')
    require('build-tools;' + str(tc.get('build_tools')) in tc['sdk_packages'], 'build_tools is not an installed package')
    theirs = document.get('grapheneos_signers')
    require(isinstance(theirs, list) and theirs and all(isinstance(d, str) and SHA256.fullmatch(d) for d in theirs),
            'invalid grapheneos_signers')
    apps = document.get('apps')
    require(isinstance(apps, list) and apps, 'apps config lists no apps')
    seen = set()
    for app in apps:
        require(isinstance(app, dict) and re.fullmatch(r'[a-z][a-z0-9]*', str(app.get('id'))), 'invalid app id')
        require(app['id'] not in seen, 'duplicate app: ' + app['id'])
        seen.add(app['id'])
        require(PACKAGE.fullmatch(str(app.get('package'))), 'invalid package of ' + app['id'])
        require(isinstance(app.get('version_code'), int) and app['version_code'] > 0, 'invalid version_code of ' + app['id'])
        require(str(app.get('repository', '')).startswith('https://github.com/DiamaneOS/'),
                app['id'] + ' must build from a DiamaneOS repository')
        require(SHA1.fullmatch(str(app.get('commit'))), app['id'] + ' must pin a full commit')
        env = app.get('environment')
        require(isinstance(env, dict) and all(re.fullmatch(r'[A-Z][A-Z0-9_]*', k) and isinstance(v, str)
                                              for k, v in env.items()), 'invalid environment of ' + app['id'])
        require(isinstance(app.get('test_catalog_key'), bool), 'invalid test_catalog_key of ' + app['id'])
        require(re.fullmatch(r'external/[A-Za-z0-9_]+', str(app.get('prebuilt'))), 'invalid prebuilt of ' + app['id'])
        signer = app.get('release_signer')
        require(signer is None or (isinstance(signer, str) and SHA256.fullmatch(signer) and signer not in theirs),
                'release_signer of ' + app['id'] + ' must be our own certificate digest or null')
    return document


def app_config(config, app_id):
    found = [a for a in config['apps'] if a['id'] == app_id]
    require(found, 'unknown app: ' + str(app_id) + ' (known: ' + ', '.join(a['id'] for a in config['apps']) + ')')
    return found[0]


def call(argv, *, cwd=None, env=None, timeout=600, log=None):
    env = dict(os.environ if env is None else env, GIT_TERMINAL_PROMPT='0')
    result = process.run(list(map(str, argv)), timeout, max_output_bytes=256 * 1024 * 1024, cwd=cwd, env=env,
                         log_path=log, capture_bytes=16384 if log else None)
    if result['transport'] != 'ok':
        tail = b''.join(result.get(k) or b'' for k in ('stdout', 'stderr')).decode('utf-8', 'replace').strip()[-2000:]
        raise AppsError('command failed: ' + ' '.join(map(str, argv[:3])) + ' (' + result['transport'] + ')'
                        + (': ' + tail if tail else '; see ' + str(log)))
    return result['stdout'].decode('utf-8', 'replace')


# ------------------------------------------------------------------ toolchain

class Toolchain:
    """The installed toolchain under ROOT/toolchain."""

    def __init__(self, root, config):
        tc = config['toolchain']
        self.root = Path(root)
        self.dir = self.root / 'toolchain'
        self.jdk = self.dir / ('jdk-' + tc['jdk']['version'])
        self.sdk = self.dir / 'android-sdk'
        self.sdkmanager = self.sdk / 'cmdline-tools' / tc['cmdline_tools']['version'] / 'bin/sdkmanager'
        self.build_tools = self.sdk / 'build-tools' / tc['build_tools']
        self.record = self.dir / 'toolchain.json'
        self.expected = {'jdk': tc['jdk'], 'cmdline_tools': tc['cmdline_tools'], 'sdk_packages': tc['sdk_packages']}

    def environment(self):
        """A clean environment: the toolchain, private user state under ROOT, nothing from the caller."""
        state = {name: self.root / name for name in ('home', 'android-user-home', 'gradle-home', 'java-prefs')}
        for path in state.values():
            path.mkdir(parents=True, exist_ok=True)
        return {'PATH': f'{self.jdk}/bin:/usr/bin:/bin', 'HOME': str(state['home']), 'LANG': 'C.UTF-8', 'TZ': 'UTC',
                'JAVA_HOME': str(self.jdk), 'ANDROID_HOME': str(self.sdk),
                'ANDROID_USER_HOME': str(state['android-user-home']), 'GRADLE_USER_HOME': str(state['gradle-home']),
                'GRADLE_OPTS': '-Djava.util.prefs.userRoot=' + str(state['java-prefs']),
                'SDKMANAGER_OPTS': '-Djava.util.prefs.userRoot=' + str(state['java-prefs'])}

    def check(self):
        require(self.record.is_file() and json.loads(self.record.read_text()) == self.expected,
                'the toolchain under ' + str(self.dir) + ' is missing or not the pinned one; run apps toolchain')
        for tool in ('aapt2', 'apksigner', 'zipalign'):
            require((self.build_tools / tool).is_file(), 'build tools lack ' + tool + '; run apps toolchain')


def fetch(url, directory, digest):
    """A download checked against its pinned SHA-256; a matching earlier download is reused."""
    target = directory / url.rsplit('/', 1)[-1]
    if not (target.is_file() and sha(target) == digest):
        part = target.with_name(target.name + '.part')
        part.unlink(missing_ok=True)
        call(['curl', '-fsSL', '--proto', '=https', '--retry', '3', '-o', part, url], timeout=3600)
        require(sha(part) == digest, 'download does not match its pinned SHA-256: ' + url)
        os.replace(part, target)
    return target


def unpack(archive, target, command):
    if target.exists():
        shutil.rmtree(target)
    temporary = target.with_name(target.name + '.tmp')
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    call(command(archive, temporary), timeout=1800)
    entries = list(temporary.iterdir())
    require(len(entries) == 1 and entries[0].is_dir(), 'unexpected archive layout: ' + archive.name)
    os.replace(entries[0], target)
    temporary.rmdir()


def install_toolchain(root, config):
    root = Path(root).absolute()
    t = Toolchain(root, config)
    tc = config['toolchain']
    downloads = root / 'downloads'
    downloads.mkdir(parents=True, exist_ok=True)
    t.record.unlink(missing_ok=True)
    jdk = fetch(tc['jdk']['url'], downloads, tc['jdk']['sha256'])
    unpack(jdk, t.jdk, lambda a, d: ['tar', '-xzf', a, '-C', d])
    tools = fetch(tc['cmdline_tools']['url'], downloads, tc['cmdline_tools']['sha256'])
    unpack(tools, t.sdkmanager.parents[1], lambda a, d: ['unzip', '-q', a, '-d', d])
    licence = t.sdk / 'licenses/android-sdk-license'
    require(licence.is_file(), 'accept the Android SDK licence yourself first (the tools never accept it): '
            f'JAVA_HOME={t.jdk} {t.sdkmanager} --sdk_root={t.sdk} --licenses')
    # stdin is empty, so a package needing another licence fails instead of being accepted.
    call([t.sdkmanager, '--sdk_root=' + str(t.sdk), '--install', *tc['sdk_packages']],
         env=t.environment(), timeout=3600, log=root / ('sdkmanager-' + stamp() + '.log'))
    t.record.write_text(json.dumps(t.expected, indent=1) + '\n')
    t.check()
    return {'status': 'PASS', 'operation': 'apps-toolchain', 'jdk': tc['jdk']['version'],
            'cmdline_tools': tc['cmdline_tools']['version'], 'sdk_packages': tc['sdk_packages']}


# ---------------------------------------------------------------- APK checks

def eocd(data):
    index = data.rfind(b'PK\x05\x06', max(0, len(data) - 65557))
    require(index >= 0 and len(data) >= index + 22, 'not a ZIP archive')
    size, offset = struct.unpack('<II', data[index + 12:index + 20])
    require(offset + size <= index, 'invalid ZIP central directory')
    return index, offset, size


def signed_matches_unsigned(signed, unsigned):
    """Whether signed is unsigned plus an APK Signing Block (and zero padding before it): same
    entries byte for byte, same central directory, only the directory offset moved."""
    u_end, u_cd, u_size = eocd(unsigned)
    s_end, s_cd, s_size = eocd(signed)
    if u_size != s_size or s_cd < u_cd + 32 or signed[:u_cd] != unsigned[:u_cd]:
        return False
    if signed[s_cd - 16:s_cd] != b'APK Sig Block 42':
        return False
    size = struct.unpack('<Q', signed[s_cd - 24:s_cd - 16])[0]
    start = s_cd - size - 8
    if start < u_cd or struct.unpack('<Q', signed[start:start + 8])[0] != size or any(signed[u_cd:start]):
        return False
    tail_u, tail_s, at = unsigned[u_cd:], signed[s_cd:], u_end - u_cd
    return (len(tail_u) == len(tail_s) and tail_s[:at + 16] == tail_u[:at + 16]
            and struct.unpack('<I', tail_s[at + 16:at + 20])[0] == s_cd and tail_s[at + 20:] == tail_u[at + 20:])


def signer_digest(output):
    """The one signer's certificate SHA-256 from apksigner verify --print-certs --verbose with a v4
    signature file; the APK must verify with schemes v2 and v4, as GrapheneOS signs these apps."""
    schemes = dict(re.findall(r'^Verified using (v[0-9.]+) scheme \([^)]*\): (true|false)$', output, re.M))
    require(schemes.get('v2') == 'true' and schemes.get('v4') == 'true', 'not signed with schemes v2 and v4')
    digests = re.findall(r'^Signer #(\d+) certificate SHA-256 digest: ([0-9a-f]{64})$', output, re.M)
    require(len(digests) == 1 and digests[0][0] == '1', 'expected exactly one signer')
    return digests[0][1]


def badging(output):
    match = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)'", output, re.M)
    require(match, 'aapt2 badging has no package line')
    return match.group(1), int(match.group(2))


# ---------------------------------------------------------------------- build

def stamp(now=None):
    return (now or datetime.datetime.now(datetime.timezone.utc)).strftime('%Y%m%dT%H%M%SZ')


def tools_state():
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=60)
    status = subprocess.run(['git', '-C', str(ROOT), 'status', '--porcelain'], capture_output=True, text=True, timeout=60)
    commit = head.stdout.strip() if head.returncode == 0 else None
    return {'commit': commit if commit and SHA1.fullmatch(commit) else None,
            'clean': head.returncode == 0 and status.returncode == 0 and not status.stdout.strip()}


def test_catalog_key(directory):
    lines = [l for l in (Path(directory) / TEST_CATALOG_KEY).read_text().splitlines()
             if l.strip() and not l.startswith('untrusted comment:')]
    require(len(lines) == 1 and re.fullmatch(r'RW[A-Za-z0-9+/]{54}', lines[0]),
            TEST_CATALOG_KEY + ' is not a signify public key')
    return lines[0]


def sign_test(toolchain, unsigned, run, key_dir, env):
    keystore, password = Path(key_dir) / TEST_KEYSTORE, Path(key_dir) / TEST_KEYSTORE_PASS
    require(keystore.is_file() and password.is_file(), 'the test key directory lacks ' + TEST_KEYSTORE
            + ' or ' + TEST_KEYSTORE_PASS)
    apk, idsig = run / 'app-release.apk', run / 'app-release.apk.idsig'
    apksigner = toolchain.build_tools / 'apksigner'
    call([apksigner, 'sign', '--ks', keystore, '--ks-key-alias', TEST_KEY_ALIAS, '--ks-pass', 'file:' + str(password),
          *SIGNING_FLAGS, '--out', apk, unsigned], env=env)
    return apk, idsig


def verify_signed(toolchain, apk, idsig, unsigned, config, env):
    digest = signer_digest(call([toolchain.build_tools / 'apksigner', 'verify', '--print-certs', '--verbose',
                                 '--v4-signature-file', idsig, apk], env=env))
    require(digest not in config['grapheneos_signers'], 'the APK is signed with a GrapheneOS key')
    require(signed_matches_unsigned(apk.read_bytes(), unsigned.read_bytes()),
            'the signed APK is not the unsigned build plus a signature')
    call([toolchain.build_tools / 'zipalign', '-c', '-P', '16', '4', apk], env=env)
    return digest


def build(root, app_id, test_key_dir=None, config=None, now=None):
    root = Path(root).absolute()
    config = config or load()
    app = app_config(config, app_id)
    toolchain = Toolchain(root, config)
    toolchain.check()
    env = toolchain.environment()
    run = root / 'runs' / f'{app_id}-{stamp(now)}'
    run.mkdir(parents=True)
    source = run / 'source'
    source.mkdir()
    git = lambda *args: call(['git', '-c', 'core.hooksPath=/dev/null', '-C', source, *args], env=env, timeout=1800)
    git('init', '-q')
    git('fetch', '-q', '--depth=1', app['repository'], app['commit'])
    git('-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', app['commit'])
    require(git('rev-parse', 'HEAD').strip() == app['commit'] and not git('status', '--porcelain').strip(),
            'the source checkout is not the pinned commit')
    inputs = dict(app['environment'])
    if test_key_dir and app['test_catalog_key']:
        inputs['REPO_PUBLIC_KEY'] = test_catalog_key(test_key_dir)
    jdk = str(toolchain.jdk)
    call([source / 'gradlew', '--no-daemon', '--console=plain', '--dependency-verification', 'strict',
          '-Dorg.gradle.java.installations.auto-detect=false', '-Dorg.gradle.java.installations.auto-download=false',
          '-Dorg.gradle.java.installations.paths=' + jdk, '-Pkotlin.compiler.execution.strategy=in-process',
          GRADLE_TASK], cwd=source, env={**env, **inputs}, timeout=3600, log=run / 'gradle.log')
    unsigned = run / 'app-release-unsigned.apk'
    shutil.copyfile(source / UNSIGNED_OUTPUT, unsigned)
    package, version = badging(call([toolchain.build_tools / 'aapt2', 'dump', 'badging', unsigned], env=env))
    require((package, version) == (app['package'], app['version_code']),
            f'built {package} {version}, config/apps.json pins {app["package"]} {app["version_code"]}')
    result = {'status': 'PASS', 'operation': 'apps-build', 'app': app_id, 'package': package, 'version_code': version,
              'repository': app['repository'], 'commit': app['commit'], 'inputs': inputs, 'tools': tools_state(),
              'toolchain': toolchain.expected, 'unsigned_sha256': sha(unsigned), 'signed': None}
    if test_key_dir:
        apk, idsig = sign_test(toolchain, unsigned, run, test_key_dir, env)
        digest = verify_signed(toolchain, apk, idsig, unsigned, config, env)
        result['signed'] = {'key': 'test', 'signer_sha256': digest, 'sha256': sha(apk), 'idsig_sha256': sha(idsig)}
    shutil.rmtree(source)
    (run / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    return dict(result, run=str(run))


# -------------------------------------------------------------------- publish

def readme_lines(result, signed, run):
    match = RUN_NAME.fullmatch(run.name)
    built = datetime.datetime.strptime(match.group(2), '%Y%m%d').date() if match else datetime.date.today()
    name = result['repository'].removesuffix('.git').rsplit('/', 1)[-1]
    tools = result['tools']
    key = ('the DiamaneOS test app key (test builds only, never a release)' if signed['key'] == 'test'
           else 'the DiamaneOS release key of ' + name)
    return [
        f'- Source: [{name}]({result["repository"].removesuffix(".git")}) at `{result["commit"][:7]}`, '
        f'version code {result["version_code"]}.',
        f'- Tools: [diamaneos-tools]({TOOLS_URL}) at `{(tools.get("commit") or "unknown")[:7]}`'
        + ('' if tools.get('clean') else ' (modified checkout)') + '.',
        f'- Unsigned APK SHA-256 `{result["unsigned_sha256"]}`.',
        f'- Signed with {key}: certificate SHA-256 `{signed["signer_sha256"]}`, APK SHA-256 `{signed["sha256"]}`.',
        f'- Built {built.day} {built.strftime("%B")} {built.year}.',
    ]


def update_readme(text, lines, title):
    head = '## This build'
    if not text.strip():
        text = (f'# {title}\n\nDiamaneOS\'s build of {title}, shipped presigned. `diamaneos apps` in '
                f'[diamaneos-tools]({TOOLS_URL}) builds it and copies it here.\n\n{head}\n')
    rows = text.split('\n')
    require(rows.count(head) == 1, 'the README has no single "## This build" section')
    start = rows.index(head) + 1
    end = next((i for i in range(start, len(rows)) if rows[i].startswith('## ')), len(rows))
    return '\n'.join(rows[:start] + [''] + lines + [''] + rows[end:]).rstrip('\n') + '\n'


def publish(run, checkout, test=False, signed=None, idsig=None, config=None, apksigner=None):
    run, checkout = Path(run).absolute(), Path(checkout).absolute()
    config = config or load()
    result = json.loads((run / 'result.json').read_text())
    require(result.get('status') == 'PASS' and result.get('operation') == 'apps-build', 'the run did not pass')
    app = app_config(config, result['app'])
    unsigned = run / 'app-release-unsigned.apk'
    require(unsigned.is_file() and sha(unsigned) == result['unsigned_sha256'], 'the unsigned APK differs from the run')
    if signed is None:
        require(test, 'name the owner-signed APK with --signed, or pass --test to publish the test-signed build')
        record = result.get('signed')
        require(record and record.get('key') == 'test', 'the run has no test-signed build')
        apk, sig = run / 'app-release.apk', run / 'app-release.apk.idsig'
        require(sha(apk) == record['sha256'] and sha(sig) == record['idsig_sha256'], 'the signed APK differs from the run')
    else:
        require(not test and idsig, 'an owner-signed APK needs its --idsig and no --test')
        require(app['release_signer'], 'config/apps.json records no release key for ' + app['id'])
        apk, sig = Path(signed).absolute(), Path(idsig).absolute()
    if apksigner is None:
        toolchain = Toolchain(run.parents[1], config)
        apksigner = lambda a, s: call([toolchain.build_tools / 'apksigner', 'verify', '--print-certs', '--verbose',
                                       '--v4-signature-file', s, a], env=toolchain.environment())
    digest = signer_digest(apksigner(apk, sig))
    require(digest not in config['grapheneos_signers'], 'refusing a GrapheneOS-signed APK')
    if signed is None:
        require(digest == result['signed']['signer_sha256'], 'the test-signed APK has another signer than its run')
    else:
        require(digest == app['release_signer'], 'the APK is not signed with the release key of ' + app['id'])
    require(signed_matches_unsigned(apk.read_bytes(), unsigned.read_bytes()),
            'the signed APK is not the run\'s unsigned build plus a signature')
    require(checkout.is_dir() and (checkout / '.git').exists() and (checkout / 'Android.bp').is_file()
            and (checkout / 'prebuilt').is_dir(), 'the target must be a checkout of ' + app['prebuilt'])
    status = subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain', '--untracked-files=all'],
                            capture_output=True, text=True, timeout=300)
    require(status.returncode == 0 and not status.stdout, 'the checkout has uncommitted changes')
    readme = checkout / 'README.md'
    record = {'key': 'test' if signed is None else 'release', 'signer_sha256': digest, 'sha256': sha(apk)}
    lines = readme_lines(result, record, run)
    text = update_readme(readme.read_text() if readme.is_file() else '', lines,
                         {'appstore': 'App Store', 'auditor': 'Auditor'}.get(app['id'], app['id']))
    for source, name in ((apk, 'app-release.apk'), (sig, 'app-release.apk.idsig')):
        target = checkout / 'prebuilt' / name
        shutil.copyfile(source, target)
        target.chmod(0o644)
        require(sha(target) == sha(source), 'copy differs: ' + name)
    readme.write_text(text)
    return {'status': 'PASS', 'operation': 'apps-publish', 'app': app['id'], 'key': record['key'],
            'signer_sha256': digest, 'apk_sha256': record['sha256'], 'readme': lines, 'committed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(prog='diamaneos apps', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    toolchain = commands.add_parser('toolchain', help='install the pinned JDK and Android SDK packages')
    toolchain.add_argument('--root', type=Path, required=True, help='app build directory, outside the OS tree')
    build_cmd = commands.add_parser('build', help='build one app at its pinned commit')
    build_cmd.add_argument('--root', type=Path, required=True)
    build_cmd.add_argument('--app', required=True)
    build_cmd.add_argument('--test-key-dir', type=Path, help='sign with the DiamaneOS test app key in this directory')
    publish_cmd = commands.add_parser('publish', help='copy a signed build into a prebuilt project checkout')
    publish_cmd.add_argument('--run', type=Path, required=True)
    publish_cmd.add_argument('--to', type=Path, required=True)
    publish_cmd.add_argument('--test', action='store_true', help='publish the run\'s test-signed build')
    publish_cmd.add_argument('--signed', type=Path, help='the owner-signed APK of the run')
    publish_cmd.add_argument('--idsig', type=Path, help='its APK signature scheme v4 file')
    args = parser.parse_args(argv)
    try:
        if args.command == 'toolchain':
            output = install_toolchain(args.root, load())
        elif args.command == 'build':
            output = build(args.root, args.app, args.test_key_dir)
        else:
            output = publish(args.run, args.to, args.test, args.signed, args.idsig)
        print(json.dumps(output, indent=2))
        return 0
    except (AppsError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print('ERROR: apps ' + args.command + ' failed: ' + str(error), file=sys.stderr)
        return 2
