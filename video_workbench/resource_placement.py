"""Immutable resource content and its separately frozen film placement.

Manim's v1 manifest retains the producer's frame coordinates.  A placement
never rewrites that manifest or changes the resource/source hashes: consumers
map the whole-film frame into the original sequence at the agreed start.
"""
from copy import deepcopy

from agent_service.contracts import digest
from .manim_resource import require

SCHEMA = 'video.resource-placement.v1'
FIELDS = {'schema', 'resource_sha256', 'resource_start_frame',
    'global_start_frame', 'frame_count', 'fps', 'sha256'}


def freeze_manim_placement(descriptor, *, start_frame, project_frame_count):
    """Bind one unchanged resource version to a legal whole-film interval."""
    require(type(descriptor) is dict and descriptor.get('schema') == 'video.manim-method-resource.v1'
        and type(descriptor.get('sha256')) is str
        and descriptor['sha256'] == digest({key: value for key, value in descriptor.items() if key != 'sha256'}),
        'resource_placement_descriptor_invalid', 409)
    manifest = descriptor.get('manifest')
    require(type(manifest) is dict and manifest.get('schema') == 'video.manim-frame-resource.v1'
        and manifest.get('fps') == 24
        and type(manifest.get('global_start_frame')) is int
        and 0 <= manifest['global_start_frame'] <= 14400
        and type(manifest.get('frame_count')) is int and 1 <= manifest['frame_count'] <= 240,
        'resource_placement_manifest_invalid', 409)
    require(type(project_frame_count) is int and 1 <= project_frame_count <= 14400
        and type(start_frame) is int and 0 <= start_frame
        and start_frame + manifest['frame_count'] <= project_frame_count,
        'resource_placement_outside_project', 409)
    value = dict(schema=SCHEMA, resource_sha256=descriptor['sha256'],
        resource_start_frame=manifest['global_start_frame'], global_start_frame=start_frame,
        frame_count=manifest['frame_count'], fps=manifest['fps'])
    value['sha256'] = digest(value)
    return value


def validate_manim_placement(value, descriptor, *, project_frame_count):
    """Validate content custody and placement without altering either input."""
    require(type(value) is dict and set(value) == FIELDS and value.get('schema') == SCHEMA
        and value.get('sha256') == digest({key: item for key, item in value.items() if key != 'sha256'}),
        'resource_placement_binding_invalid', 409)
    expected = freeze_manim_placement(descriptor, start_frame=value['global_start_frame'],
        project_frame_count=project_frame_count)
    require(value == expected, 'resource_placement_resource_changed', 409)
    return deepcopy(value)
