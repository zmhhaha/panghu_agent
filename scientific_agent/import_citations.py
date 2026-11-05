"""Explicit imported-reference matching, never a fabricated machine claim trace."""
from __future__ import annotations

import re
from collections import Counter


def citation_inventory(report, mappings=None):
    from .report_exports import prose_mask
    original_report = report
    report = prose_mask(report, comments=True)
    rows, body, fence, in_references = {}, [], None, False
    for line, original_line in zip(report.splitlines(), original_report.splitlines()):
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})', line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence or line.startswith(('    ', '\t')):
            continue
        if re.match(r'^\s{0,3}#{1,6}\s+(?:参考文献|参考资料|引用文献|references|bibliography)\b', line, re.I):
            in_references = True
            continue
        reference = re.match(r'^\s*\[(\d{1,4})\]\s+(.+)', line) if in_references else None
        if reference:
            original_reference = re.match(r'^\s*\[(\d{1,4})\]\s+(.+)', original_line)
            rows.setdefault(int(reference[1]), original_reference[2].strip())
        elif not in_references:
            body.append(re.sub(r'<!--.*?-->', '', line))
    text = '\n'.join(body)
    numbers = [int(number) for number in re.findall(r'(?<!!)\[(\d{1,4})\](?!\()', text)]
    occurrences = Counter(numbers)
    for number in numbers:
        rows.setdefault(number, '')
    mappings = mappings or {}
    return dict(references=[dict(n=number, text=description, body_occurrences=occurrences[number],
        source_id=mappings.get(number), match_status='manually_matched' if number in mappings else 'unmatched')
        for number, description in sorted(rows.items())],
        unmatched_numbers=sorted(set(rows) - set(mappings)),
        validation='user_matched_not_machine_claim_validated')


def map_references(project, version, mappings):
    from .workspace import require, _event
    from .workspace_recovery import _idle
    from .pi_contracts import utc_now
    _idle(project)
    require(version.get('model_trace_available') is False, 'imported_version_required', 409)
    require(not version.get('stale') and version['input_revision'] == project['input_revision'],
            'stale_version_requires_reconfirmation', 409)
    require(not any(item['version_id'] == version['id'] for item in project['finalizations']),
            'finalized_version_is_immutable', 409)
    require(type(mappings) is list and len(mappings) <= 1000, 'invalid_reference_mappings')
    inventory = citation_inventory(version['report_markdown'])
    numbers = {item['n'] for item in inventory['references']}
    sources = {item['paper_id'] for item in version['sources']}
    matched = {}
    for row in mappings:
        require(type(row) is dict and set(row) == {'n', 'source_id'} and type(row['n']) is int
            and row['n'] in numbers and row['n'] not in matched
            and type(row['source_id']) is str and row['source_id'] in sources,
            'invalid_reference_mappings')
        matched[row['n']] = row['source_id']
    version['import_citations'] = dict(citation_inventory(version['report_markdown'], matched),
        confirmed_at=utc_now())
    # A citation decision requires a fresh independent review before finalizing.
    version['import_review'] = None
    version['confirmation_id'] = None
    _event(project, 'import_references_matched', dict(version_id=version['id'],
        matched_count=len(matched), unmatched_count=len(numbers - set(matched))))
