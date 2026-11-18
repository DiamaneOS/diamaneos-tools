import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from diamaneos_tools import signing_archive as za
from diamaneos_tools.signing_inputs import SigningError


class DamagedArchiveTests(unittest.TestCase):
    """A damaged compressed member is the archive's refusal, not a decompressor error."""

    def damaged(self, offset, method=zipfile.ZIP_DEFLATED):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w', method) as handle:
            info = zipfile.ZipInfo('bin/avbtool')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            info.compress_type = method
            handle.writestr(info, bytes(range(256)) * 64)
        data = bytearray(out.getvalue())
        # Inside the compressed stream, after the 30-byte local header and the name.
        data[30 + len('bin/avbtool') + offset] ^= 0xff
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        path = directory / 'otatools.zip'
        path.write_bytes(bytes(data))
        return path

    def test_damaged_deflate_stream_is_refused(self):
        for offset in (0, 1, 2, 5):
            with self.subTest(offset=offset):
                path = self.damaged(offset)
                with self.assertRaises(SigningError):
                    with za.archive(path) as handle:
                        za.digest_member(handle, 'bin/avbtool')
                with self.assertRaises(SigningError):
                    za.unpack_tools(path, path.parent / ('tools-' + str(offset)))

    def test_damaged_lzma_stream_is_refused(self):
        # Past the 4-byte LZMA header of the ZIP member and its 5 property bytes.
        for offset in (9, 10, 12, 20):
            with self.subTest(offset=offset):
                path = self.damaged(offset, zipfile.ZIP_LZMA)
                with self.assertRaises(SigningError):
                    with za.archive(path) as handle:
                        za.digest_member(handle, 'bin/avbtool')


class UndecodableNameTests(unittest.TestCase):
    """A member name that is not UTF-8 under the UTF-8 name flag is the archive's refusal."""

    def test_name_that_is_not_utf8_is_refused(self):
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as handle:
            info = zipfile.ZipInfo('bin/é')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            handle.writestr(info, b'#!/bin/sh\n')
        # The name bytes stop being UTF-8 in the local header only, then in the central directory too.
        for count in (1, 2):
            with self.subTest(count=count):
                directory = Path(tempfile.mkdtemp())
                self.addCleanup(shutil.rmtree, directory)
                path = directory / 'otatools.zip'
                path.write_bytes(out.getvalue().replace('é'.encode(), b'\xc3\x28', count))
                with self.assertRaises(SigningError):
                    with za.archive(path) as handle:
                        za.digest_member(handle, handle.namelist()[0])
                with self.assertRaises(SigningError):
                    za.unpack_tools(path, directory / 'tools')


class UnpackTests(unittest.TestCase):
    """unpack_tools writes private files and leaves nothing behind when it refuses."""

    def test_files_are_private_while_written_and_a_refusal_leaves_nothing(self):
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, directory)
        path = directory / 'otatools.zip'
        with zipfile.ZipFile(path, 'w') as handle:
            handle.writestr('bin/avbtool', b'fixture tool')
            # Refused only after the first file is written: its name is a file, not a directory.
            handle.writestr('bin/avbtool/other', b'fixture member under a file')
        modes, copy = [], shutil.copyfileobj

        def recording(source, target, *arguments):
            modes.append(os.fstat(target.fileno()).st_mode & 0o777)
            return copy(source, target, *arguments)
        with mock.patch.object(za.shutil, 'copyfileobj', side_effect=recording):
            with self.assertRaises(SigningError):
                za.unpack_tools(path, directory / 'tools')
        self.assertEqual([0o600], modes)
        self.assertFalse((directory / 'tools').exists())


if __name__ == '__main__':
    unittest.main()
