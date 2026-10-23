"""One publication layout for partition sets and their factory container."""
from pathlib import Path, PurePosixPath
import stat

from .signing_inputs import require


def image_members(plan, observed):
    partitions = {name + '.img' for name in plan['partitions']}
    passthrough = set(plan['passthrough_images'])
    logical = {name + '.img' for name in plan.get('logical_partitions', [])}
    permitted = partitions | passthrough | ({'super.img'} if logical else set())
    require(observed <= permitted, 'Image archive contains an unlisted member')
    require(passthrough <= observed, 'Image archive lacks a declared passthrough file')
    required = partitions - logical if 'super.img' in observed else partitions
    require(required <= observed, 'Image archive lacks a declared partition')
    supplied_logical = observed & logical
    require(not supplied_logical or supplied_logical == logical,
            'Image archive has only some logical partition images')
    return logical


def factory_members(image_archive):
    # Factory containers carry the verified image archive as a single flat member.
    # Flashing programs are separate publication artifacts rather than opaque extras.
    return {Path(image_archive).name}


FACTORY_METADATA = {'build.json', 'SHA256SUMS', 'resolved-manifest.xml',
                    'flash-all.sh', 'flash-all.bat', 'avb_pkmd.bin'}
PUBLICATION_FIELDS = {'schema_version', 'signed_target_files', 'images_archive', 'ota',
                      'factory_archive', 'factory_signature', 'release_record', 'release_signature'}


def full_image_members(plan):
    """Factory installs write each logical image in fastbootd, on super_empty.img, so they carry no super.img."""
    members = {name + '.img' for name in plan['partitions']} | set(plan['passthrough_images'])
    require('super.img' not in members, 'Factory images never carry super.img')
    require(not members & FACTORY_METADATA, 'Image names overlap factory metadata')
    return members


def super_layout(record, misc, mode):
    """Bind platform bootstrap suffixes and the admitted install slot before signing."""
    ab = misc.get('ab_update')
    # Legacy non-A/B qualification follows build_super_image's absent-flag
    # default. A factory record never uses that fallback for an admitted slot.
    if ab is None and mode == 'qualification' and not record.get('flash'):
        ab = 'false'
    require(ab in {'true', 'false'}, 'Super layout requires an explicit A/B source contract')
    if ab == 'true':
        # The platform build_super_image bootstrap contract fills A. Factory builds
        # instead require the slot recorded by the admitted install metadata.
        slot = record.get('flash', {}).get('slot')
        require(slot in {'a', 'b'} or slot is None and mode == 'qualification',
                'Super layout lacks an admitted slot')
        return {'kind': 'slot-suffixed', 'active_slot': slot if slot in {'a', 'b'} else 'a'}
    require(mode == 'qualification', 'Production factory requires a slot-suffixed super layout')
    return {'kind': 'unsuffixed', 'active_slot': None}


def super_members(plan):
    """Expected active members, plus the only permitted empty inactive members."""
    from .signing_inputs import fields
    declared = plan.get('super_layout')
    require(isinstance(declared, dict), 'Super verification requires a bound layout; prepare a new plan')
    fields(declared, {'kind', 'active_slot'})
    logical = plan['logical_partitions']
    if declared['kind'] == 'unsuffixed':
        require(plan.get('mode') == 'qualification' and 'factory_input' not in plan
                and declared['active_slot'] is None, 'Unsuffixed super is only a legacy qualification layout')
        return {name + '.img': name for name in logical}, set()
    slot = declared['active_slot']
    require(declared['kind'] == 'slot-suffixed' and slot in {'a', 'b'}, 'Unknown super slot layout')
    if 'factory_input' in plan:
        require(slot == plan['factory_input']['flash']['slot'], 'Super slot differs from the admitted install slot')
    other = 'b' if slot == 'a' else 'a'
    return ({f'{name}_{slot}.img': name for name in logical}, {f'{name}_{other}.img' for name in logical})


def publication_schema(publication, mode):
    from .signing_inputs import fields
    from .safe_json import schema_errors
    fields(publication, PUBLICATION_FIELDS)
    require(not schema_errors(publication, 'signing-publication.schema.json'), 'Invalid publication description')
    version = publication['schema_version']
    require(type(version) is int and version in {1, 2}, 'Unknown publication schema')
    require(version == 2 or mode == 'qualification', 'Production requires the full factory ZIP schema')
    return version


def check_publication_files(root, expected):
    from .signing_inputs import safe_file
    for name in expected:
        safe_file(root, name)
    directories = {str(p) for name in expected for p in PurePosixPath(name).parents if str(p) != '.'}
    present = {str(p.relative_to(root)): p.lstat().st_mode for p in root.rglob('*')}
    require(set(present) == expected | directories
            and all(stat.S_ISREG(mode) if name in expected else stat.S_ISDIR(mode)
                    for name, mode in present.items()), 'Publication contains unlisted files, directories or links')
