"""Preserve explicitly supplied visual references; never fetch or certify them."""
from __future__ import annotations

from copy import deepcopy
import re
from urllib.parse import unquote, urlsplit

from ..contracts import ServiceError


_URL = re.compile(r'https?://[^\s<>"\x27`，。；、！？“”‘’]*', re.I)
_PAIRS = (('(', ')'), ('[', ']'), ('{', '}'), ('（', '）'), ('【', '】'))
_REFERENCE_NOTE = '\n\n画面保留输入提供的完整参考链接（未经自动核验）：\n'
_MARKER = re.compile(r'(?:固定版本|版本|version|commit|核对(?:日期)?|日期)\s*[:：]?\s*'
                     r'(?:[0-9a-f]{7,40}|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})', re.I)
_SENSITIVE_KEYS = frozenset({'token', 'accesstoken', 'refreshtoken', 'idtoken', 'authtoken',
    'jwt', 'code', 'clientsecret', 'secret', 'password', 'passwd', 'pwd', 'apikey',
    'authorization', 'auth', 'bearer', 'credential', 'credentials', 'signature', 'sig',
    'session', 'sessionid', 'authkey', 'oauthverifier'})


def _url_text(value):
    # Strip prose/Markdown delimiters only when they are unbalanced. Parentheses
    # within a URL (for example Wikipedia article names) remain byte-for-byte.
    depth = {opening: 0 for opening, _ in _PAIRS}
    closing = {end: opening for opening, end in _PAIRS}
    for index, char in enumerate(value):
        if char in depth:
            depth[char] += 1
        elif char in closing:
            opening = closing[char]
            if not depth[opening]:
                value = value[:index]
                break
            depth[opening] -= 1
    return value.rstrip('.,;!，。；、！？')


def _sensitive_component(value):
    # OAuth fragments and signed asset queries are display leaks even though
    # the renderer never fetches them. Decode keys, not the URL we preserve.
    decoded = unquote(value)
    for key in re.findall(r'(?:^|[?&#;/])([^?&#;/=]+)=', decoded):
        normalized = re.sub(r'[^a-z0-9]', '', key.lower())
        if (normalized in _SENSITIVE_KEYS or normalized.startswith(('xamz', 'xgoog'))
                or normalized.endswith(('accesstoken', 'refreshtoken', 'clientsecret', 'apikey'))):
            return True
    return bool(re.search(r'(?:^|[?&#;/])(?:bearer|basic)\s+\S+', decoded, re.I))


def _validate_reference(url):
    # Error codes never include URLs, query values or account identifiers.
    if len(url) > 512:
        raise ServiceError('video_reference_too_long', 422)
    try:
        parts = urlsplit(url)
        valid = parts.scheme in {'http', 'https'} and bool(parts.hostname)
        _ = parts.port
    except ValueError:
        raise ServiceError('video_reference_invalid', 422) from None
    if not valid:
        raise ServiceError('video_reference_invalid', 422)
    if (parts.username is not None or parts.password is not None
            or _sensitive_component(parts.query) or _sensitive_component(parts.fragment)):
        raise ServiceError('video_reference_sensitive_url', 422)


def reference_urls(*texts):
    result = []
    for text in texts:
        for match in _URL.finditer(text or ''):
            url = _url_text(match.group())
            _validate_reference(url)
            if url not in result:
                result.append(url)
    return result


def _preserve_markers(description, inputs, maximum):
    markers = list(dict.fromkeys(match.group() for text in inputs
        if reference_urls(text) for match in _MARKER.finditer(text)))
    missing = [marker for marker in markers if marker not in description]
    if missing:
        description += '\n输入提供的来源标记（未经自动核验）：' + '；'.join(missing)
    if len(description) > maximum:
        raise ServiceError('video_references_require_split', 422)
    return description


def preserve_visual_references(description, *inputs, maximum=8000):
    """Append missing literal references without rewriting the model's prose.

    This is visual provenance only, not narration or a claim of fact checking.
    Too much input must be resolved explicitly instead of silently truncating it.
    """
    existing = set(reference_urls(description))
    missing = [url for url in reference_urls(*inputs) if url not in existing]
    if len(existing) + len(missing) > 2:
        raise ServiceError('video_references_require_split', 422)
    result = description + _REFERENCE_NOTE + '\n'.join(missing) if missing else description
    if len(result) > maximum:
        raise ServiceError('video_references_require_split', 422)
    return _preserve_markers(result, inputs, maximum)


def preserve_project_references(scenes, *texts, maximum=8000):
    """Return copied scenes, preserving links in place and filling from the end.

    Each scene may display two links. Existing model allocations, narration,
    order and unrelated fields are unchanged; only missing references append
    to a visual description with enough remaining space. Failure is atomic.
    """
    result = deepcopy(scenes)
    existing = [reference_urls(scene['visual_description']) for scene in result]
    if any(len(urls) > 2 or len(scene['visual_description']) > maximum
           for scene, urls in zip(result, existing)):
        raise ServiceError('video_references_require_split', 422)
    assigned = [[] for _ in result]
    seen = {url for urls in existing for url in urls}
    missing = [url for url in reference_urls(*texts) if url not in seen]
    for url in missing:
        for index in range(len(result) - 1, -1, -1):
            if len(existing[index]) + len(assigned[index]) >= 2:
                continue
            proposed = result[index]['visual_description'] + _REFERENCE_NOTE + '\n'.join([*assigned[index], url])
            if len(proposed) <= maximum:
                assigned[index].append(url)
                break
        else:
            raise ServiceError('video_references_require_split', 422)
    for scene, urls in zip(result, assigned):
        if urls:
            scene['visual_description'] += _REFERENCE_NOTE + '\n'.join(urls)
        references = set(reference_urls(scene['visual_description']))
        related_inputs = [text for text in texts if references.intersection(reference_urls(text))]
        scene['visual_description'] = _preserve_markers(scene['visual_description'], related_inputs, maximum)
    return result


def validate_reference_subset(scenes, *texts):
    """Refuse model-invented visual provenance, without echoing its URL."""
    supplied = set(reference_urls(*texts))
    for scene in scenes:
        if not set(reference_urls(scene['visual_description'])) <= supplied:
            raise ServiceError('video_reference_not_in_input', 422)


def preserve_plan_references(plan, document):
    """Sources are inert display text and must originate in the frozen input."""
    supplied = reference_urls(document['visual_description'], document.get('instruction', ''))
    if len(supplied) > 2:
        raise ServiceError('video_references_require_split', 422)
    result = deepcopy(plan)
    sources = result.get('sources', [])
    if any(row['url'] not in supplied for row in sources):
        raise ServiceError('motion_reference_not_in_input', 422)
    if supplied:
        labels = {row['url']: row['label'] for row in sources}
        markers = list(dict.fromkeys(match.group() for text in (
            document['visual_description'], document.get('instruction', '')) for match in _MARKER.finditer(text)))
        result['sources'] = []
        for url in supplied:
            label = labels.get(url, '输入提供的参考链接')
            for marker in markers:
                if marker not in label:
                    label += '；' + marker
            if len(label) > 100:
                raise ServiceError('video_references_require_split', 422)
            result['sources'].append(dict(url=url, label=label))
    return result
