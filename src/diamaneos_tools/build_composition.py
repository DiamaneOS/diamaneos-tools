"""Install an authenticated overlay and detach its moving projects at declared commits.

The source-sync adapter owns the workspace lock. This helper neither resolves
network refs nor relaxes the final clean-source preflight.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from . import build


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, timeout=120)
    if result.returncode:
        raise build.BuildError('composition Git operation failed: ' + args[0])
    return result.stdout


def derive(signed_xml, overlay, overlay_revision, resolved, overlay_url=None):
    """Compute the composition record for an overlay and exact resolutions.

    Maintainers use it to move pins; it performs the same checks as preflight.
    """
    if not build.SHA1_RE.fullmatch(overlay_revision or ''):
        raise build.BuildError('overlay revision must be an exact commit')
    composed = build.compose_overlay(signed_xml, overlay, resolved)
    rows, digest = build.parse_project_map(composed)
    result = {'overlay_revision': overlay_revision, 'overlay_sha256': build.sha256_bytes(overlay),
              'project_count': len(rows), 'project_map_sha256': digest}
    if resolved:
        result['resolved_revisions'] = dict(sorted(resolved.items()))
    if overlay_url:
        result['overlay_url'] = overlay_url
    return result


def prepare(config, source, overlay_root, signed_xml, replace=False):
    composition = config.get('composition')
    if not composition:
        raise build.BuildError('environment has no source composition')
    if git(overlay_root, 'rev-parse', 'HEAD').decode().strip() != composition['overlay_revision']:
        raise build.BuildError('overlay repository revision mismatch')
    if git(overlay_root, 'status', '--porcelain=v1', '--untracked-files=all'):
        raise build.BuildError('overlay repository is dirty')
    data = git(overlay_root, 'show', 'HEAD:diamaneos.xml')
    # Validate the complete composition before changing the target workspace.
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / '.repo/local_manifests'
        directory.mkdir(parents=True)
        (directory / 'diamaneos.xml').write_bytes(data)
        build.compose_source_manifest(config, Path(temp), signed_xml)
    directory = source / '.repo/local_manifests'
    if directory.is_symlink():
        raise build.BuildError('local manifest directory is a symlink')
    directory.mkdir(exist_ok=True)
    path = directory / 'diamaneos.xml'
    entries = list(directory.iterdir())
    if entries:
        if entries != [path] or path.is_symlink() or not path.is_file():
            raise build.BuildError('existing local manifests differ from the declared overlay')
        if path.read_bytes() == data:
            return
        if not replace:
            raise build.BuildError('existing local manifests differ from the declared overlay')
    with tempfile.NamedTemporaryFile(dir=directory, prefix='.overlay-', delete=False) as f:
        temporary = Path(f.name)
        try:
            f.write(data)
            f.flush()
            os.fchmod(f.fileno(), 0o640)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def checkout(config, source, signed_xml):
    build.compose_source_manifest(config, source, signed_xml)
    for relative, revision in config['composition'].get('resolved_revisions', {}).items():
        project = source / relative
        if project.resolve() != project.absolute() or not project.is_dir():
            raise build.BuildError('resolved project root is missing or redirected')
        if Path(git(project, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != project.resolve():
            raise build.BuildError('resolved project is not an independent checkout')
        if git(project, 'status', '--porcelain=v1', '--untracked-files=all'):
            raise build.BuildError('resolved project has local changes')
        if git(project, 'rev-parse', revision + '^{commit}').decode().strip() != revision:
            raise build.BuildError('resolved project commit is unavailable')
        git(project, '-c', 'core.hooksPath=/dev/null', 'checkout', '--detach', revision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare', 'checkout'))
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--overlay-root', type=Path)
    args = parser.parse_args()
    try:
        config, _ = build.load_config(args.config)
        source = args.source_root.resolve(strict=True)
        signed_xml = git(source / '.repo/manifests', 'show', config['upstream']['peeled_commit'] + ':default.xml')
        if build.sha256_bytes(signed_xml) != config['upstream']['default_manifest_sha256']:
            raise build.BuildError('upstream manifest content mismatch')
        if args.operation == 'prepare':
            if args.overlay_root is None:
                raise build.BuildError('prepare requires an overlay repository')
            prepare(config, source, args.overlay_root, signed_xml)
        else:
            checkout(config, source, signed_xml)
    except (build.BuildError, OSError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, 'ERROR: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
