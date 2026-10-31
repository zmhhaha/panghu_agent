"""Bounded, plain-text executor questions; never expose an internal result."""
from __future__ import annotations

import re


MAX_QUESTIONS = 20
MAX_QUESTION_CHARS = 1000
MAX_TOTAL_CHARS = 10000
_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u202a-\u202e\u2066-\u2069\ud800-\udfff]')


def normalize_questions(value):
    """Select only the reviewed text shapes, with bounded work and output.

    The text is an executor's report, not proof of a missing tool, permission or
    successful gate. Unknown fields/objects are ignored, never stringified.
    """
    if type(value) is not list:
        return []
    result, total = [], 0
    for item in value[:100]:
        raw = item.get('question') if type(item) is dict else item
        if type(raw) is not str:
            continue
        text = _CONTROLS.sub('', raw[:4096]).strip()
        if not text:
            continue
        available = min(MAX_QUESTION_CHARS, MAX_TOTAL_CHARS - total)
        if available <= 0:
            break
        if len(text) > available:
            text = text[:available - 1] + '…'
        result.append(text)
        total += len(text)
        if len(result) == MAX_QUESTIONS:
            break
    return result
