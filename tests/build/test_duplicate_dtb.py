"""Binary DTB controls for ambiguous command-line representations."""
import struct
import unittest

from tests.build import test_boot_input_boundary as boundary
from tests.build.test_image_verify import subject


def duplicate_tree(kind):
    """A valid DTB layout with a long first bootargs and a short duplicate."""
    def node(name):
        data = name.encode() + b'\0'
        return struct.pack('>I', 1) + data + bytes(-len(data) % 4)

    def prop(value):
        return struct.pack('>III', 3, len(value), 0) + value + bytes(-len(value) % 4)

    end = struct.pack('>I', 2)
    structure = node('') + node('chosen') + prop(b'p=' + b'a' * 2010 + b'\0')
    if kind == 'node':
        structure += end + node('chosen')
    structure += prop(b'p=x\0') + end + end + struct.pack('>I', 9)
    strings = b'bootargs\0'
    header = struct.pack('>10I', 0xd00dfeed, 56 + len(structure) + len(strings),
                         56, 56 + len(structure), 40, 17, 16, 0, len(strings), len(structure))
    return header + bytes(16) + structure + strings


class DuplicateDtbTest(unittest.TestCase):
    setUp = boundary.BootBoundaryTest.setUp
    check = boundary.BootBoundaryTest.check

    def test_duplicate_properties_cannot_hide_a_long_first_value(self):
        for source in ['VENDOR_BOOT/dtb', 'IMAGES/dtbo.img']:
            with self.subTest(source=source):
                ok, detail = self.check({source: duplicate_tree('property')}, reserve=True)
                self.assertFalse(ok, detail)
                self.assertIn('duplicate device tree property /chosen:bootargs', detail)

    def test_duplicate_node_paths_cannot_hide_a_long_first_value(self):
        for source in ['VENDOR_BOOT/dtb', 'IMAGES/dtbo.img']:
            with self.subTest(source=source):
                ok, detail = self.check({source: duplicate_tree('node')}, reserve=True)
                self.assertFalse(ok, detail)
                self.assertIn('duplicate device tree node /chosen', detail)

    def test_the_parser_rejects_both_ambiguous_representations(self):
        for kind in ['property', 'node']:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'duplicate device tree'):
                subject.parse_dtb(duplicate_tree(kind))
