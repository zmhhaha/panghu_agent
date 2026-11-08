"""Repository-reviewed animation strategies shared by Lab and production.

A release is guidance, not executable code or an aesthetic approval. Publishing
a new strategy means adding a new immutable ID here in a reviewed source change;
an arbitrary self-signed document cannot become production policy. Experiments
freeze the same complete release that production places in its Agent input.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from agent_service.contracts import ServiceError


SCHEMA = 'video.animation-capability-release.v1'
DEFAULT_RELEASE_ID = 'explanatory-motion-v1'
_ID = re.compile(r'[a-z][a-z0-9_.-]{0,99}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_FIELDS = frozenset({'schema', 'id', 'revision', 'engines', 'targets', 'rules',
                     'evidence', 'limitations', 'sha256'})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value if type(value) is bytes else canonical(value)).hexdigest()


def _require(condition, code='animation_capability_release_invalid'):
    if not condition:
        raise ServiceError(code, 422)


def _text(value, limit):
    return (type(value) is str and 0 < len(value) <= limit and bool(value.strip())
            and not any((ord(char) < 32 and char not in '\r\n\t') or ord(char) == 127
                        or 0xD800 <= ord(char) <= 0xDFFF for char in value))


def _release(document):
    return dict(document, sha256=digest(document))


# This first release transports explicit engineering rules into both execution
# paths. The four Lab works did not establish an aesthetic winner; neither their
# v2 text nor a copied scene is silently promoted as a universally better model.
_RELEASES = {
    DEFAULT_RELEASE_ID: _release(dict(
        schema=SCHEMA, id=DEFAULT_RELEASE_ID, revision=1, engines=['html-svg'],
        targets=['explanatory-motion', 'identity-tracking', 'process-explanation',
                 'spatial-relationship', 'comparison'],
        rules=[
            'Use the narration and supplied beat times as the explanation clock. '
            'Make important visual changes when the corresponding idea is spoken; '
            'do not invent text or stretch or shorten the recording.',
            'Choose spatial arrangement, motion paths, ordering and emphasis for '
            'this visual problem. A fixed row of cards or the same entrance effect '
            'is not a required template. Meaningful object movement must actually happen.',
            'Keep each object and its visible label together through motion. '
            'Preserve identity, readable text and understandable relationships; '
            'do not fake movement by swapping labels or replacing the object.',
            'Give long movements enough visible time. Avoid solving one object\'s '
            'smooth path by rushing the other objects into the final moments. '
            'Use deliberate focus changes, pauses and path separation where useful.',
            'When following an object through a turn, prefer continuous motion and '
            'readable direction changes instead of unnecessary stop-and-turn corners. '
            'A longer curved path is not automatically better: compare tracking clarity '
            'and the time left for every other object.',
            'Leave a readable stable end state after the final important movement. '
            'Do not push unfinished long moves or explanations into the closing frames; '
            'plan this hold within the existing audio duration, without adding silence.',
            'Avoid unintended overlap, clipped text, early connector lines and '
            'irrelevant decoration. Put concise key information into the diagram '
            'rather than covering the picture with a narration subtitle block.',
            'Set the complete visible state from absolute time. Seeking backward '
            'and returning to zero must restore the same objects, paths and geometry '
            'without accumulated simulation or a self-running animation clock.',
        ],
        evidence=[dict(
            kind='engineering-baseline',
            reference='video_workbench/docs/animation-capability-lab-redesign.md#四任务比较与有限结论',
            scope='Rules informed by the identity-tracking engineering review; '
                  'not a selected aesthetic winner or evidence for Canvas/Three generation.',
            source_sha256='a80c4a9677929d6b11afff05f2107dd7d91f398c46b8a4e0d618a27c3c57a493')],
        limitations=[
            'Only HTML/SVG generated scenes are admitted by this release.',
            'Technical rendering checks and human aesthetic review remain separate.',
            'The strategy guides new creation; it does not transplant a Lab scene '
            'or certify general improvement from one experiment.',
        ])),
}


def capability_releases():
    """Return detached records; opening the catalogue never launches an Agent."""
    return [deepcopy(_RELEASES[key]) for key in sorted(_RELEASES)]


def capability_release(release_id=None):
    release_id = DEFAULT_RELEASE_ID if release_id is None else release_id
    _require(type(release_id) is str and _ID.fullmatch(release_id),
             'animation_capability_release_unknown')
    _require(release_id in _RELEASES, 'animation_capability_release_unknown')
    return deepcopy(_RELEASES[release_id])


def validate_release(value):
    """Require the exact reviewed release, not merely a matching client hash."""
    _require(type(value) is dict and set(value) == _FIELDS)
    _require(value.get('schema') == SCHEMA and type(value.get('id')) is str
             and _ID.fullmatch(value['id']) and type(value.get('revision')) is int
             and value['revision'] >= 1 and type(value.get('sha256')) is str
             and _SHA.fullmatch(value['sha256']))
    _require(value['sha256'] == digest({key: item for key, item in value.items() if key != 'sha256'}),
             'animation_capability_release_changed')
    for name, limit in (('engines', 3), ('targets', 24), ('rules', 32), ('limitations', 24)):
        rows = value.get(name)
        _require(type(rows) is list and 1 <= len(rows) <= limit
                 and all(_text(row, 2000) for row in rows) and len(set(rows)) == len(rows))
    _require(set(value['engines']) <= {'html-svg', 'canvas', 'three'})
    _require(all(_ID.fullmatch(target) for target in value['targets']))
    _require(type(value.get('evidence')) is list and 1 <= len(value['evidence']) <= 32)
    for row in value['evidence']:
        _require(type(row) is dict and set(row) == {'kind', 'reference', 'scope', 'source_sha256'}
                 and all(_text(row.get(name), 2000) for name in ('kind', 'reference', 'scope'))
                 and type(row.get('source_sha256')) is str and _SHA.fullmatch(row['source_sha256']))
    registered = capability_release(value['id'])
    _require(value == registered, 'animation_capability_release_changed')
    return registered


def release_guidance(value):
    """The exact rule text consumed by both experimental and production tasks."""
    release = validate_release(value)
    return ('Capability strategy release ' + release['id'] + ', SHA-256 ' + release['sha256']
            + '. The following are reusable expression rules, not a fixed scene recipe.\n'
            + '\n'.join(str(index + 1) + '. ' + rule for index, rule in enumerate(release['rules'])))


def bind_experiment_release(document, release_id=None):
    """Apply a shared release only to a new input; existing work is never edited."""
    from agent_service.profiles.video_animation_experiment import validate_experiment_input
    value = validate_experiment_input(document)
    release = capability_release(release_id)
    _require(value['capability'] in release['targets'], 'animation_capability_target_not_supported')
    _require('capability_release' not in value['constraints'], 'animation_capability_already_bound')
    allowed = value['constraints'].get('allowed_engines', release['engines'])
    allowed = [engine for engine in allowed if engine in release['engines']]
    _require(bool(allowed), 'animation_capability_engine_not_supported')
    value['constraints']['allowed_engines'] = allowed
    value['constraints']['capability_release'] = release
    local = value['guidance']
    value['constraints']['capability_local_guidance'] = local
    value['guidance'] = release_guidance(release) + ('\n\nExperiment-local direction:\n' + local if local else '')
    return validate_experiment_input(value)

