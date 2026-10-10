"""Small disposable input writers; no signature or policy verification is mocked here."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import warnings
import zipfile

from diamaneos_tools import signing_archive as za
from diamaneos_tools.signing_inputs import canonical_sha256, sha256_file


class TemporaryInputs:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='signing-input-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()


def write_zip(path, members):
    """Keep duplicate/unsafe entries available for the archive rejection fixtures."""
    if isinstance(members, dict):
        members = members.items()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w') as handle:
            for name, data in members:
                if isinstance(data, Path):
                    handle.write(data, name)
                else:
                    handle.writestr(name, data)


def rewrite_zip(path, change):
    with zipfile.ZipFile(path) as handle:
        entries = [(info, change(info.filename, handle.read(info))) for info in handle.infolist()]
    write_zip(path, entries)


def write_tar(path, entries, *, linkname=''):
    with tarfile.open(path, 'w') as handle:
        for name, data, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = linkname
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            handle.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def apk_plan(role, certificate, package_id):
    return {'kind': 'apk', 'container': role, 'payload': None,
            'certificate_sha256': certificate, 'package_id': package_id}


def apex_plan(certificate, public_key):
    return {'kind': 'apex', 'container': 'releasekey', 'payload': 'avb',
            'certificate_sha256': certificate, 'payload_key_sha256': sha256_file(public_key)}


def publication_description():
    return {'schema_version': 1, 'signed_target_files': 'target-files.zip', 'images_archive': 'images.zip',
            'ota': [{'file': 'ota.zip', 'source_target_files': None}], 'factory_archive': 'factory.tar',
            'factory_signature': 'factory.tar.sig', 'release_record': 'release.json', 'release_signature': 'release.json.sig'}


def publication_plan(target, packages, keys, provenance, policy, **layout):
    """Fixture envelope only; trust identities and layout are explicit test inputs."""
    plan = {'schema_version': 2, 'mode': 'qualification', 'policy_sha256': canonical_sha256(policy),
            'key_inventory_sha256': canonical_sha256(keys.inventory),
            'public_material_sha256': canonical_sha256(keys.files), 'public_identities': keys.public_ids,
            'provenance': provenance, 'changed_projects': [], 'metadata': za.package_metadata_shape(za.metadata(target)),
            'packages': packages, 'roles': {}, 'mac_permissions': {}, 'chains': {},
            'passthrough_images': {}, 'previous_artifacts': {}, **layout}
    plan['plan_sha256'] = canonical_sha256(plan)
    return plan


def release_record(root, plan, description):
    names = [description[key] for key in ('signed_target_files', 'images_archive', 'factory_archive', 'factory_signature')]
    names += [ota['file'] for ota in description['ota']]
    path = root / description['release_record']
    path.write_text(json.dumps({'schema_version': 1, 'plan_sha256': plan['plan_sha256'],
                                'artifacts': {name: sha256_file(root / name) for name in names}}))
    return path
