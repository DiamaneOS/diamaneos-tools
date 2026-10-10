"""One publication layout for partition sets and their factory container."""
from pathlib import Path

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
