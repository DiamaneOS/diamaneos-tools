"""The devices the vendor commands support, one entry each in config/devices/<codename>.json.

An entry names the device's recipes and settings (files in config/), where the
vendor tree goes in the source and its lunch product. Another device is
another entry with its own recipes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re

ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = ROOT / 'config' / 'devices'
CODENAME = re.compile(r'[A-Za-z0-9_]{1,32}')
FILES = ('build_environment', 'build', 'stock_image_recipe', 'stock_inputs', 'vendor_files', 'vendor_elf',
         'image_tools', 'firmware_inventory')
# The files whose hashes the vendor step records as its recipes.
VENDOR_RECIPES = ('stock_image_recipe', 'stock_inputs', 'vendor_files', 'vendor_elf', 'image_tools',
                  'firmware_inventory')


class DeviceError(ValueError):
    pass


@dataclass(frozen=True)
class Device:
    codename: str
    name: str
    lunch: str
    vendor_tree: str
    files: dict

    def path(self, key: str) -> Path:
        return ROOT / 'config' / self.files[key]

    def load(self, key: str) -> tuple[dict, bytes]:
        raw = self.path(key).read_bytes()
        return json.loads(raw), raw

    @property
    def vendor_recipes(self) -> tuple:
        """The vendor step's recipe files, relative to config/."""
        return tuple(self.files[key] for key in VENDOR_RECIPES)


def relative(value, what: str) -> str:
    path = PurePosixPath(value) if isinstance(value, str) else None
    if path is None or path.is_absolute() or not path.parts or any(p in ('', '.', '..') for p in path.parts) \
            or str(path) != value:
        raise DeviceError(f'{what} must be a plain relative path')
    return value


def read(path: Path) -> Device:
    entry = json.loads(path.read_bytes())
    keys = {'schema_version', 'codename', 'name', 'lunch', 'vendor_tree', 'files'}
    if not isinstance(entry, dict) or set(entry) != keys or entry['schema_version'] != 1:
        raise DeviceError(f'{path.name}: needs exactly ' + ', '.join(sorted(keys)))
    codename = entry['codename']
    if not isinstance(codename, str) or not CODENAME.fullmatch(codename) or path.stem != codename:
        raise DeviceError(f'{path.name}: the codename must be the file name')
    if not isinstance(entry['lunch'], str) or not CODENAME.fullmatch(entry['lunch']) \
            or not isinstance(entry['name'], str) or not entry['name']:
        raise DeviceError(f'{path.name}: invalid name or lunch product')
    files = entry['files']
    if not isinstance(files, dict) or set(files) != set(FILES):
        raise DeviceError(f'{path.name}: files needs exactly ' + ', '.join(FILES))
    for key, value in files.items():
        relative(value, f'{path.name}: files.{key}')
        if not (ROOT / 'config' / value).is_file():
            raise DeviceError(f'{path.name}: config/{value} is missing')
    return Device(codename, entry['name'], entry['lunch'], relative(entry['vendor_tree'], f'{path.name}: vendor_tree'),
                  dict(files))


def codenames() -> list:
    return sorted(path.stem for path in DIRECTORY.glob('*.json'))


def supported() -> str:
    return 'Supported devices: ' + ', '.join(codenames())


def get(codename: str | None) -> Device:
    """The device with this codename (any case). Refuses a missing or unknown one, naming the supported ones."""
    if not codename:
        raise DeviceError('name the device by its codename. ' + supported())
    matches = [name for name in codenames() if name.lower() == codename.lower()]
    if len(matches) != 1:
        raise DeviceError(f'unknown device {codename!r}. ' + supported())
    return read(DIRECTORY / (matches[0] + '.json'))


def sole() -> Device:
    """The device when only one is supported, for commands that do not need its name."""
    names = codenames()
    if len(names) != 1:
        raise DeviceError('name the device by its codename. ' + supported())
    return get(names[0])


def all_devices() -> list:
    return [get(name) for name in codenames()]


def by_lunch(product: str) -> Device | None:
    found = [device for device in all_devices() if device.lunch == product]
    return found[0] if len(found) == 1 else None


def argument(parser) -> None:
    parser.add_argument('device', nargs='?', metavar='CODENAME',
                        help='the device codename (' + ', '.join(codenames()) + ')')


def from_args(parser, value: str | None) -> Device:
    """The device a command names, or an argparse error that lists the supported ones."""
    try:
        return get(value)
    except (DeviceError, OSError) as error:
        parser.error(str(error))
