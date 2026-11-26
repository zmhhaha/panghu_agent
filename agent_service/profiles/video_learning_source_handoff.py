"""File-based author handoff shared by the picture and reference-video profiles."""
import json
import math

from .video import _read, decode_video_json
from .video_animation_create import source_sha256
from .video_animation_experiment import FILE_NAMES, SOURCE_MAX_BYTES, SOURCE_TOTAL_BYTES
from ..contracts import ServiceError


SOURCE_HANDOFF = 'workspace-files-v1'
_SOURCE_DIRECTORY = 'output-source'
_INLINE_EXAMPLE = '"files":{"scene.html":"COMPLETE SOURCE","scene.js":"COMPLETE SOURCE"}'
_FILE_EXAMPLE = '"file_names":["scene.html","scene.js"]'


def validate_marker(document, *, author, require):
    if 'source_handoff' in document:
        require(author and document['source_handoff'] == SOURCE_HANDOFF)


def task_for_input(document, input_sha256):
    task = {'input_sha256': input_sha256}
    if 'source_handoff' in document:
        task['source_handoff'] = document['source_handoff']
    return task


def author_contract(contract):
    """Project the pinned v1 contract to the selected author handoff."""
    if contract.count(_INLINE_EXAMPLE) != 1:
        raise ServiceError('learning_source_contract_integrity', 500)
    return contract.replace(_INLINE_EXAMPLE, _FILE_EXAMPLE).replace(
        '`source.files` is a map requiring scene.html and optionally scene.js/scene.css, at most 256 KiB each and 512 KiB total.',
        '`source.file_names` lists scene.html and optionally scene.js/scene.css. Source files are at most 256 KiB each and 512 KiB total.'
    ).replace(
        'files require scene.html and may include scene.js/scene.css, at most 256KiB each/512KiB total.',
        '`source.file_names` lists scene.html and optionally scene.js/scene.css. Source files are at most 256 KiB each and 512 KiB total.'
    )


AUTHOR_PROMPT = (
    f'For this create task, use write to save complete, unescaped UTF-8 source as '
    f'{_SOURCE_DIRECTORY}/scene.html, optionally {_SOURCE_DIRECTORY}/scene.js and '
    f'{_SOURCE_DIRECTORY}/scene.css. Write those source files before output.json. '
    'Set source.file_names to the unique basenames you wrote, include scene.html, and '
    'omit source.files and source.source_sha256. Serialize the remaining result fields '
    'as one strict UTF-8 JSON object, with escaped string contents, unique keys and finite '
    'numbers. The host reads the listed files after the writers stop and serializes the '
    'canonical result. For an observation request, follow the source=null handoff below.\n'
)


class _JsonIssue(ValueError):
    pass


def _unique_keys(items):
    result = {}
    for key, value in items:
        if key in result:
            raise _JsonIssue('duplicate_key')
        result[key] = value
    return result


def _non_finite(_):
    raise _JsonIssue('non_finite_number')


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        _non_finite(text)
    return value


def decode_author_output(raw, *, file_handoff=False):
    """Keep strict legacy decoding and report bounded metadata locations."""
    code = 'learning_output_json_invalid' if file_handoff else 'invalid_video_json'
    if not file_handoff:
        try:
            return decode_video_json(raw)
        except ServiceError as error:
            if error.code != code:
                raise
    details = dict(phase='collect_output', path='/output.json', reason='strict_json')
    try:
        text = raw.decode('utf-8') if isinstance(raw, bytes) else raw
        value = json.loads(text, object_pairs_hook=_unique_keys, parse_constant=_non_finite,
            parse_float=_finite_float)
        if file_handoff:
            return value
    except json.JSONDecodeError as issue:
        details.update(reason='json_syntax', line=issue.lineno, column=issue.colno, offset=issue.pos)
    except UnicodeDecodeError as issue:
        details.update(reason='invalid_utf8', offset=issue.start)
    except _JsonIssue as issue:
        details['reason'] = str(issue)
    except (TypeError, ValueError, RecursionError):
        pass
    error = ServiceError(code, 422)
    error.details = details
    raise error from None


def read_manifest(workspace, maximum):
    raw = _read_output_file(workspace, 'output.json', maximum, 'learning_output_manifest_file_invalid')
    return decode_author_output(raw, file_handoff=True)


def _read_output_file(workspace, name, maximum, code):
    try:
        return _read(workspace, name, maximum)
    except ServiceError as error:
        if error.code != 'invalid_video_file':
            raise
        raise ServiceError(code, 422) from None


def collect_source(workspace, output, *, file_handoff):
    if type(output) is not dict or output.get('source') is None:
        return
    source = output['source']
    if file_handoff:
        _collect_files(workspace, source)
    if type(source) is dict and set(source) == {'engine', 'strategy', 'files'}:
        source['source_sha256'] = source_sha256(source)


def _collect_files(workspace, source):
    if type(source) is not dict or set(source) != {'engine', 'strategy', 'file_names'}:
        raise ServiceError('learning_source_manifest_invalid', 422)
    names = source['file_names']
    if (type(names) is not list or not 1 <= len(names) <= len(FILE_NAMES)
            or any(type(name) is not str or name not in FILE_NAMES for name in names)
            or len(set(names)) != len(names) or 'scene.html' not in names):
        raise ServiceError('learning_source_manifest_invalid', 422)
    files = {}
    total = 0
    for name in names:
        raw = _read_output_file(workspace, f'{_SOURCE_DIRECTORY}/{name}', SOURCE_MAX_BYTES,
            'learning_source_file_invalid')
        total += len(raw)
        if total > SOURCE_TOTAL_BYTES:
            raise ServiceError('animation_experiment_source_too_large', 422)
        try:
            files[name] = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ServiceError('learning_source_utf8_invalid', 422) from None
    del source['file_names']
    source['files'] = files
