"""Publication layout and byte consistency fixtures; no native signature proof."""
import hashlib
import io
from pathlib import Path
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from diamaneos_tools import signing_inputs as inputs, signing_layout as layout, signing_verify as api


def fixture_images():
    partitions = {name: ('fixture partition ' + name).encode()
                  for name in ('boot', 'vbmeta', 'system', 'vendor')}
    passthrough = {name: ('fixture passthrough ' + name).encode() for name in (
        'abl.elf', 'NON-HLOS.bin', 'modemst1.img', 'modemst2.img', 'misc.img',
        'frp.img', 'metadata.img', 'super_empty.img')}
    plan = {'partitions': list(partitions), 'logical_partitions': ['system', 'vendor'],
            'passthrough_images': {name: hashlib.sha256(data).hexdigest() for name, data in passthrough.items()},
            'packages': {}, 'chains': {}}
    images = {name + '.img': data for name, data in partitions.items()}
    images.update(passthrough)
    return plan, partitions, images


def write_zip(path, members):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(path, 'w') as archive:
            for name, data in members:
                archive.writestr(name, data)


class ImageMembersTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.partitions, self.images = fixture_images()
        self.members = set(self.images)

    def test_complete_direct_layout_is_accepted(self):
        self.assertEqual({'system.img', 'vendor.img'}, layout.image_members(self.plan, self.members))

    def test_every_declared_passthrough_is_required(self):
        for name in self.plan['passthrough_images']:
            with self.subTest(member=name), self.assertRaises(inputs.SigningError):
                layout.image_members(self.plan, self.members - {name})

    def test_every_physical_partition_is_required_with_and_without_super(self):
        for name in ('boot.img', 'vbmeta.img'):
            for super_member in (set(), {'super.img'}):
                with self.subTest(member=name, super=bool(super_member)), self.assertRaises(inputs.SigningError):
                    layout.image_members(self.plan, (self.members | super_member) - {name})

    def test_every_logical_partition_is_required_without_super(self):
        for missing in ({'system.img'}, {'vendor.img'}, {'system.img', 'vendor.img'}):
            with self.subTest(missing=missing), self.assertRaises(inputs.SigningError):
                layout.image_members(self.plan, self.members - missing)

    def test_super_allows_all_logical_omitted_or_all_included(self):
        for omitted in (set(), {'system.img', 'vendor.img'}):
            with self.subTest(omitted=omitted):
                observed = (self.members - omitted) | {'super.img'}
                self.assertEqual({'system.img', 'vendor.img'}, layout.image_members(self.plan, observed))

    def test_super_never_allows_a_partial_direct_logical_set(self):
        for name in ('system.img', 'vendor.img'):
            with self.subTest(missing=name), self.assertRaises(inputs.SigningError):
                layout.image_members(self.plan, (self.members | {'super.img'}) - {name})

    def test_unlisted_images_nonimages_and_directory_names_refuse(self):
        for name in ('extra.img', 'flash-all.sh', 'notes.txt', 'images/', 'boot.img/', 'nested/boot.img'):
            with self.subTest(member=name), self.assertRaises(inputs.SigningError):
                layout.image_members(self.plan, self.members | {name})

    def test_super_is_not_permitted_without_logical_partitions(self):
        plan = dict(self.plan, partitions=['boot', 'vbmeta'], logical_partitions=[])
        observed = self.members - {'system.img', 'vendor.img'}
        self.assertEqual(set(), layout.image_members(plan, observed))
        with self.assertRaises(inputs.SigningError):
            layout.image_members(plan, observed | {'super.img'})


class ImageArchiveBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan, self.partitions, self.images = fixture_images()
        self.target = self.root / 'target-files.zip'
        self.published = self.root / 'images.zip'
        write_zip(self.target, [('IMAGES/' + name + '.img', data) for name, data in self.partitions.items()])
        # Rejected layout must stop before any native method or key is needed.
        self.tools = SimpleNamespace(scratch=self.root, run=self.native_forbidden)
        self.keys = SimpleNamespace(avb=self.native_forbidden)

    def native_forbidden(self, *_args, **_kwargs):
        self.fail('Archive layout reached native verification instead of refusing')

    def refuse(self, members):
        write_zip(self.published, members)
        with self.assertRaises(inputs.SigningError):
            api.publication_images(self.published, self.target, self.plan, self.tools, self.keys)

    def test_image_archive_rejects_missing_passthrough_and_opaque_files(self):
        for name in self.plan['passthrough_images']:
            with self.subTest(missing=name):
                self.refuse([(n, data) for n, data in self.images.items() if n != name])
        for name in ('flash-all.sh', 'README', 'extra.img'):
            with self.subTest(extra=name):
                self.refuse([*self.images.items(), (name, b'opaque fixture')])

    def test_image_archive_rejects_even_empty_unlisted_directories(self):
        for name in ('empty/', 'images/', 'boot.img/'):
            with self.subTest(directory=name):
                self.refuse([*self.images.items(), (name, b'')])

    def test_image_archive_rejects_duplicates(self):
        self.refuse([*self.images.items(), ('boot.img', self.images['boot.img'])])

    def test_image_archive_rejects_links_and_traversal(self):
        link = zipfile.ZipInfo('boot.img')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.refuse([*((name, data) for name, data in self.images.items() if name != 'boot.img'),
                     (link, b'../outside.img')])
        for name in ('../escape.img', '/escape.img', './escape.img', 'nested/escape.img', 'nested\\escape.img'):
            with self.subTest(member=name):
                self.refuse([*self.images.items(), (name, b'fixture')])


class SuperRepresentationTests(unittest.TestCase):
    """Only unpacking and downstream signature checks are controlled test seams."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.plan, self.partitions, self.images = fixture_images()
        self.target = self.root / 'target-files.zip'
        self.published = self.root / 'images.zip'
        write_zip(self.target, [('IMAGES/' + name + '.img', data) for name, data in self.partitions.items()])
        self.unpacked = {name + '_a.img': self.partitions[name] for name in ('system', 'vendor')}
        self.unpacked.update({'system_b.img': b'', 'vendor_b.img': b''})
        self.tools = SimpleNamespace(scratch=self.root, run=Mock(side_effect=self.unpack))

    def unpack(self, command, arguments):
        self.assertEqual('lpunpack', command)
        source, destination = map(Path, arguments)
        self.assertEqual(b'fixture super container', source.read_bytes())
        for name, data in self.unpacked.items():
            (destination / name).write_bytes(data)

    def compare(self, images, failure=None):
        write_zip(self.published, [*images.items(), ('super.img', b'fixture super container')])
        keys = object()

        def inspect_final(tools, public, final, partitions, chains):
            self.assertIs(self.tools, tools)
            self.assertIs(keys, public)
            self.assertEqual(self.plan['partitions'], partitions)
            self.assertEqual(self.plan['chains'], chains)
            for name, data in self.partitions.items():
                self.assertEqual(data, (final / (name + '.img')).read_bytes())
            return sorted(partitions)

        with patch.object(api, 'verify_images', side_effect=inspect_final) as image_check, \
                patch.object(api, 'verify_installed_packages') as packages:
            if failure is None:
                result = api.publication_images(self.published, self.target, self.plan, self.tools, keys)
                self.assertEqual(sorted(self.partitions), result)
                image_check.assert_called_once()
                packages.assert_called_once()
            else:
                with self.assertRaisesRegex(inputs.SigningError, failure):
                    api.publication_images(self.published, self.target, self.plan, self.tools, keys)
                image_check.assert_not_called()
                packages.assert_not_called()

    def test_matching_complete_direct_and_super_bytes_reach_later_checks(self):
        self.compare(self.images)
        self.tools.run.assert_called_once()

    def test_all_logical_images_can_be_supplied_only_by_super(self):
        images = {name: data for name, data in self.images.items() if name not in ('system.img', 'vendor.img')}
        self.compare(images)

    def test_each_direct_logical_mismatch_refuses_before_signature_checks(self):
        for name in ('system', 'vendor'):
            with self.subTest(partition=name):
                images = dict(self.images, **{name + '.img': b'changed direct fixture'})
                self.compare(images, 'Direct partition differs from its super contents')

    def test_matching_direct_and_super_cannot_replace_signed_target_bytes(self):
        self.unpacked['system_a.img'] = b'same changed logical fixture'
        images = dict(self.images, **{'system.img': self.unpacked['system_a.img']})
        self.compare(images, 'Published partition differs from signed target-files')

    def test_incomplete_super_contents_refuse(self):
        self.unpacked.pop('vendor_a.img')
        images = {name: data for name, data in self.images.items() if name not in ('system.img', 'vendor.img')}
        self.compare(images, 'missing|symlinked')

    def test_partial_direct_logical_set_refuses_before_unpacking(self):
        images = {name: data for name, data in self.images.items() if name != 'vendor.img'}
        self.compare(images, 'only some logical partition images')
        self.tools.run.assert_not_called()

    def test_populated_second_slot_and_undeclared_super_partition_refuse(self):
        for extra in ({'system_b.img': b'populated'}, {'unlisted_a.img': b'unknown partition'}):
            with self.subTest(extra=extra):
                original = dict(self.unpacked)
                self.unpacked.update(extra)
                self.compare(self.images, 'Published super has a populated second slot|Super contains an undeclared partition')
                self.unpacked = original


class FactoryContentsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.images = self.root / 'fixture-publication-images.zip'
        write_zip(self.images, [('boot.img', b'fixture partition')])
        self.data = self.images.read_bytes()
        self.factory = self.root / 'factory.tar'

    def factory_members(self, entries):
        with tarfile.open(self.factory, 'w') as archive:
            for name, data, kind in entries:
                member = tarfile.TarInfo(name)
                member.type = kind
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = self.images.name
                if kind == tarfile.REGTYPE:
                    member.size = len(data)
                archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)

    def image_entry(self):
        return self.images.name, self.data, tarfile.REGTYPE

    def test_factory_accepts_exact_single_flat_images_archive(self):
        self.factory_members([self.image_entry()])
        self.assertIsNone(api.verify_factory_contents(self.factory, self.images))

    def test_missing_or_wrongly_named_factory_member_refuses(self):
        for entries in ([], [('other-images.zip', self.data, tarfile.REGTYPE)]):
            with self.subTest(entries=entries):
                self.factory_members(entries)
                with self.assertRaises(inputs.SigningError):
                    api.verify_factory_contents(self.factory, self.images)

    def test_opaque_scripts_files_and_empty_directories_are_not_factory_extras(self):
        for name, kind in (('flash-all.sh', tarfile.REGTYPE), ('notes.txt', tarfile.REGTYPE),
                           ('empty', tarfile.DIRTYPE), ('images', tarfile.DIRTYPE)):
            with self.subTest(member=name):
                self.factory_members([self.image_entry(), (name, b'fixture extra', kind)])
                with self.assertRaises(inputs.SigningError):
                    api.verify_factory_contents(self.factory, self.images)

    def test_factory_duplicate_members_refuse_even_when_bytes_match(self):
        self.factory_members([self.image_entry(), self.image_entry()])
        with self.assertRaises(inputs.SigningError):
            api.verify_factory_contents(self.factory, self.images)

    def test_factory_links_and_nonregular_members_refuse(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind):
                self.factory_members([(self.images.name, b'', kind)])
                with self.assertRaises(inputs.SigningError):
                    api.verify_factory_contents(self.factory, self.images)

    def test_factory_nested_absolute_and_traversing_names_refuse(self):
        for prefix in ('nested/', './', '../', '/', 'nested\\'):
            with self.subTest(prefix=prefix):
                self.factory_members([(prefix + self.images.name, self.data, tarfile.REGTYPE)])
                with self.assertRaises(inputs.SigningError):
                    api.verify_factory_contents(self.factory, self.images)

    def test_factory_must_embed_the_exact_verified_bytes(self):
        for data in (b'', self.data[:-1], self.data + b'changed'):
            with self.subTest(size=len(data)):
                self.factory_members([(self.images.name, data, tarfile.REGTYPE)])
                with self.assertRaisesRegex(inputs.SigningError, 'Factory archive contains different images'):
                    api.verify_factory_contents(self.factory, self.images)


if __name__ == '__main__':
    unittest.main()
