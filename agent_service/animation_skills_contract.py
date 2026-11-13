"""Immutable animation knowledge and task-scoped, progressively read catalogs.

This contract does not publish, select, execute, or visually approve a skill.
The caller supplies authenticated publication records. Read receipts describe
returned bytes; only a host-captured successful tool event proves delivery to
the Agent. A receipt copied into an Agent result is not such evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .contracts import InlineFile, ServiceError, canonical_bytes, digest, safe_relative_path


PACKAGE_SCHEMA = 'video.animation-skill-package.v1'
CATALOG_SCHEMA = 'video.animation-skill-catalog.v1'
MANIFEST_SCHEMA = 'video.animation-skill-files.v1'
SEARCH_SCHEMA = 'video.animation-skill-search.v1'
READ_SCHEMA = 'video.animation-skill-read.v1'
MAX_FILE_BYTES = 128 * 1024
MAX_PACKAGE_BYTES = 1024 * 1024
MAX_PACKAGE_FILES = 32
MAX_CATALOG_SKILLS = 256
MAX_CATALOG_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_BUNDLE_FILES = 1024
KINDS = frozenset({'foundation', 'expression'})
ENGINES = frozenset({'html-svg', 'canvas', 'three'})
PREFIX = 'animation-skills'
POLICY = ('Search and read only the skills useful for this exact task. Choose, combine, adapt '
          'or reject them yourself. A search hit is not a read, and a read does not prove visual '
          'application or quality. Create freely when nothing fits. Skills are knowledge, not '
          'permission to execute files, fetch reference URLs or publish new methods.')
_PACKAGE_FIELDS = frozenset({'schema', 'skill_id', 'version', 'kind', 'name', 'description',
    'files', 'files_sha256', 'runtime_requirements', 'sha256'})
_ENTRY_FIELDS = frozenset({'skill_id', 'version', 'kind', 'name', 'description',
    'package_sha256', 'method_version_id', 'method_content_sha256', 'publication_sha256',
    'runtime_requirements'})
_SHA = re.compile(r'^[0-9a-f]{64}$')
_NAME = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
_REMOTE = re.compile(r'''(?:https?|ftp|file)://[^\s<>"'`)\]}]+|\b(?:www\.|space\.bilibili\.com)[^\s<>"'`)\]}]*''', re.I)
# Namespace identifiers are inert strings required by local SVG/XML/HTML code.
# Match entire URL tokens: a matching prefix must not admit a remote dependency.
_NAMESPACE_URIS = frozenset({
    'http://www.w3.org/2000/svg',
    'http://www.w3.org/1999/xlink',
    'http://www.w3.org/XML/1998/namespace',
    'http://www.w3.org/2000/xmlns/',
    'http://www.w3.org/1999/xhtml',
    'http://www.w3.org/1998/Math/MathML',
})


def require(condition, code='animation_skill_invalid', status=422):
    if not condition:
        raise ServiceError(code, status)


def _text(value, maximum=400, *, empty=False):
    require(type(value) is str and len(value) <= maximum and (empty or bool(value.strip()))
        and not any((ord(c) < 32 and c not in '\n\r\t') or ord(c) == 127
                    or 0xD800 <= ord(c) <= 0xDFFF for c in value))
    return value


def _sha(value):
    require(type(value) is str and _SHA.fullmatch(value))
    return value


def _identity(value):
    _text(value, 400)
    require(not any(ord(c) < 32 for c in value))
    return value


def _json(value):
    try:
        return canonical_bytes(value)
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ServiceError('animation_skill_invalid', 422) from None


def _has_external_dependency(text):
    return any(match[0] not in _NAMESPACE_URIS for match in _REMOTE.finditer(text))


def _engines(runtime):
    require(type(runtime) is dict and type(runtime.get('engines')) is list
        and bool(runtime['engines']) and all(type(v) is str and v in ENGINES for v in runtime['engines'])
        and len(set(runtime['engines'])) == len(runtime['engines']))
    require(len(_json(runtime)) <= 8192 and not _has_external_dependency(_json(runtime).decode()),
            'animation_skill_external_dependency')
    def portable(value):
        if type(value) is dict:
            return all(type(k) is str and portable(v) for k, v in value.items())
        if type(value) is list:
            return all(portable(v) for v in value)
        return value is None or type(value) in (str, bool) or type(value) is int and abs(value) <= 9007199254740991
    require(portable(runtime), 'animation_skill_runtime_invalid')
    return runtime['engines']


def _frontmatter(text):
    # A deliberately small, documented scalar subset avoids a YAML dependency
    # and ambiguous duplicate/multiline metadata in the runtime wire contract.
    require(text.startswith('---\n') or text.startswith('---\r\n'),
            'animation_skill_frontmatter_invalid')
    lines = text.splitlines()
    try:
        end = lines.index('---', 1)
    except ValueError:
        raise ServiceError('animation_skill_frontmatter_invalid', 422) from None
    fields = {}
    for line in lines[1:end]:
        match = re.fullmatch(r'(name|description):[ \t]*(.+)', line)
        require(match is not None and match[1] not in fields,
                'animation_skill_frontmatter_invalid')
        value = match[2].strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                raise ServiceError('animation_skill_frontmatter_invalid', 422) from None
        elif value.startswith("'"):
            require(len(value) >= 2 and value.endswith("'"), 'animation_skill_frontmatter_invalid')
            value = value[1:-1].replace("''", "'")
        require(type(value) is str and '\n' not in value and '\r' not in value,
                'animation_skill_frontmatter_invalid')
        fields[match[1]] = value
    require(set(fields) == {'name', 'description'} and _NAME.fullmatch(fields['name'])
        and len(fields['name']) < 64 and bool('\n'.join(lines[end + 1:]).strip()),
        'animation_skill_frontmatter_invalid')
    _text(fields['description'], 1200)
    return fields


def _files(files):
    require(type(files) is dict and 'SKILL.md' in files and 1 <= len(files) <= MAX_PACKAGE_FILES)
    total, folded = 0, set()
    for name, text in files.items():
        try:
            safe_relative_path(name)
        except (TypeError, ValueError):
            raise ServiceError('animation_skill_path_invalid', 422) from None
        require(name == 'SKILL.md' or name.startswith(('references/', 'assets/', 'scripts/')),
                'animation_skill_path_invalid')
        require(name.casefold() not in folded, 'animation_skill_path_collision')
        folded.add(name.casefold())
        _text(text, MAX_FILE_BYTES)
        raw = text.encode('utf-8')
        require(0 < len(raw) <= MAX_FILE_BYTES, 'animation_skill_file_too_large', 413)
        total += len(raw)
        # Runtime packages are self-contained. Original URLs/observations live
        # in the separate research store, never an online runtime dependency.
        require(not _has_external_dependency(text), 'animation_skill_external_dependency')
        for linked in re.findall(r'\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)', text):
            target = linked.split('#', 1)[0]
            if not target:
                continue
            require(not target.startswith('/') and '\\' not in target and '?' not in target,
                    'animation_skill_reference_invalid')
            parts = name.split('/')[:-1]
            for part in target.split('/'):
                if part == '..':
                    require(bool(parts), 'animation_skill_reference_invalid')
                    parts.pop()
                elif part != '.':
                    parts.append(part)
            require('/'.join(parts) in files, 'animation_skill_reference_missing')
    require(total <= MAX_PACKAGE_BYTES, 'animation_skill_package_too_large', 413)
    # No file can shadow a directory (also portable across case-insensitive hosts).
    require(not any('/'.join(name.split('/')[:i]).casefold() in folded
        for name in files for i in range(1, len(name.split('/')))), 'animation_skill_path_collision')
    return _frontmatter(files['SKILL.md'])


def build_skill_package(*, skill_id, version, kind, files, runtime_requirements):
    _identity(skill_id)
    require(type(version) is int and 1 <= version <= 1000000 and type(kind) is str and kind in KINDS)
    metadata = _files(files)
    _engines(runtime_requirements)
    value = dict(schema=PACKAGE_SCHEMA, skill_id=skill_id, version=version, kind=kind,
        **metadata, files=deepcopy(files), runtime_requirements=deepcopy(runtime_requirements),
        files_sha256={name: hashlib.sha256(text.encode('utf-8')).hexdigest()
                      for name, text in files.items()})
    value['sha256'] = digest(value)
    return value


def validate_skill_package(package):
    require(type(package) is dict and set(package) == _PACKAGE_FIELDS
            and package.get('schema') == PACKAGE_SCHEMA)
    expected = build_skill_package(**{key: package[key] for key in (
        'skill_id', 'version', 'kind', 'files', 'runtime_requirements')})
    require(package == expected, 'animation_skill_package_changed', 409)
    return expected


def _entry(method):
    require(type(method) is dict and method.get('schema') == 'video.animation-method.v2'
        and method.get('status') == 'published', 'animation_skill_not_published', 409)
    package = validate_skill_package(method.get('skill_package'))
    _identity(method.get('id'))
    _sha(method.get('content_sha256'))
    publication = method.get('publication')
    require(type(publication) is dict
        and publication.get('schema') == 'video.animation-method-publication.v2'
        and publication.get('method_version_id') == method['id']
        and publication.get('method_content_sha256') == method['content_sha256']
        and publication.get('skill_package_sha256') == package['sha256']
        and type(publication.get('reference_validation_ids')) is list
        and bool(publication['reference_validation_ids'])
        and type(publication.get('transfer_validation_ids')) is list
        and bool(publication['transfer_validation_ids']), 'animation_skill_not_published', 409)
    return dict(skill_id=package['skill_id'], version=package['version'], kind=package['kind'],
        name=package['name'], description=package['description'], package_sha256=package['sha256'],
        method_version_id=method['id'], method_content_sha256=method['content_sha256'],
        publication_sha256=digest(publication), runtime_requirements=deepcopy(package['runtime_requirements']))


def freeze_skill_catalog(published_methods, *, recipient_owner, consumer_id, task_sha256, allowed_engines):
    """Freeze every compatible published v2 package, without choosing methods.

    Candidate records and frozen legacy v1 methods are not advertised. Invalid
    v2 publications are integration errors, never silently turned into no match.
    All versions remain discoverable; pagination is chosen by the Agent.
    """
    _identity(recipient_owner)
    _identity(consumer_id)
    _sha(task_sha256)
    require(type(allowed_engines) is list and bool(allowed_engines)
        and all(type(v) is str and v in ENGINES for v in allowed_engines)
        and len(set(allowed_engines)) == len(allowed_engines))
    require(type(published_methods) is list)
    skills = []
    for method in published_methods:
        require(type(method) is dict)
        if method.get('status') == 'candidate' or method.get('schema') == 'video.animation-method.v1':
            continue
        entry = _entry(method)
        if set(entry['runtime_requirements']['engines']) & set(allowed_engines):
            skills.append(entry)
    skills.sort(key=lambda row: (row['skill_id'], row['version'], row['package_sha256']))
    require(len(skills) <= MAX_CATALOG_SKILLS, 'animation_skill_catalog_too_large', 413)
    require(len({row['package_sha256'] for row in skills}) == len(skills)
        and len({row['method_version_id'] for row in skills}) == len(skills)
        and len({(row['skill_id'], row['version']) for row in skills}) == len(skills),
        'animation_skill_catalog_conflict', 409)
    value = dict(schema=CATALOG_SCHEMA, recipient_owner=recipient_owner, consumer_id=consumer_id,
        task_sha256=task_sha256, allowed_engines=list(allowed_engines), skills=skills,
        policy=POLICY, evidence_scope='frozen_published_discovery_not_agent_read_or_application')
    value['sha256'] = digest(value)
    return validate_skill_catalog(value)


def validate_skill_catalog(snapshot):
    require(type(snapshot) is dict and set(snapshot) == {'schema', 'recipient_owner', 'consumer_id',
        'task_sha256', 'allowed_engines', 'skills', 'policy', 'evidence_scope', 'sha256'}
        and snapshot.get('schema') == CATALOG_SCHEMA and snapshot['policy'] == POLICY
        and snapshot['evidence_scope'] == 'frozen_published_discovery_not_agent_read_or_application')
    _identity(snapshot['recipient_owner'])
    _identity(snapshot['consumer_id'])
    _sha(snapshot['task_sha256'])
    require(type(snapshot['allowed_engines']) is list and bool(snapshot['allowed_engines'])
        and all(type(v) is str and v in ENGINES for v in snapshot['allowed_engines'])
        and len(set(snapshot['allowed_engines'])) == len(snapshot['allowed_engines']))
    rows = snapshot['skills']
    require(type(rows) is list and len(rows) <= MAX_CATALOG_SKILLS)
    for row in rows:
        require(type(row) is dict and set(row) == _ENTRY_FIELDS)
        for key in ('skill_id', 'method_version_id'):
            _identity(row[key])
        for key in ('package_sha256', 'method_content_sha256', 'publication_sha256'):
            _sha(row[key])
        require(type(row['version']) is int and 1 <= row['version'] <= 1000000
            and type(row['kind']) is str and row['kind'] in KINDS
            and type(row['name']) is str and len(row['name']) < 64 and _NAME.fullmatch(row['name']))
        _text(row['description'], 1200)
        require(set(_engines(row['runtime_requirements'])) & set(snapshot['allowed_engines']))
    require(len({row['package_sha256'] for row in rows}) == len(rows)
        and len({row['method_version_id'] for row in rows}) == len(rows)
        and len({(row['skill_id'], row['version']) for row in rows}) == len(rows)
        and rows == sorted(rows, key=lambda row: (row['skill_id'], row['version'], row['package_sha256'])))
    require(_sha(snapshot['sha256']) == digest({k: v for k, v in snapshot.items() if k != 'sha256'}),
            'animation_skill_catalog_changed', 409)
    require(len(_json(snapshot)) <= MAX_CATALOG_BYTES, 'animation_skill_catalog_too_large', 413)
    return deepcopy(snapshot)


def skill_file_manifest(package):
    package = validate_skill_package(package)
    result = {key: deepcopy(value) for key, value in package.items() if key != 'files'}
    result.update(schema=MANIFEST_SCHEMA, package_schema=PACKAGE_SCHEMA,
        files_bytes={name: len(text.encode('utf-8')) for name, text in package['files'].items()})
    return result


def _packages(snapshot, packages):
    require(type(packages) in (list, dict))
    values = list(packages.values()) if type(packages) is dict else packages
    checked = [validate_skill_package(value) for value in values]
    result = {row['sha256']: row for row in checked}
    require(len(result) == len(checked), 'animation_skill_catalog_conflict', 409)
    require(set(result) == {row['package_sha256'] for row in snapshot['skills']},
            'animation_skill_bundle_mismatch', 409)
    for row in snapshot['skills']:
        package = result[row['package_sha256']]
        require(all(package[key] == row[key] for key in ('skill_id', 'version', 'kind', 'name',
            'description', 'runtime_requirements')), 'animation_skill_bundle_mismatch', 409)
    return result


def bundle_files(snapshot, packages):
    """Return exact task InlineFiles, mounted as inputs/animation-skills.

    The new profile's main-input reader does not expand this directory. The
    separately admitted Skill tools progressively read its protected contents.
    """
    snapshot = validate_skill_catalog(snapshot)
    packages = _packages(snapshot, packages)
    result = [InlineFile.from_bytes(PREFIX + '/catalog.json', canonical_bytes(snapshot))]
    for checksum in sorted(packages):
        package = packages[checksum]
        root = PREFIX + '/packages/' + checksum + '/'
        result.append(InlineFile.from_bytes(root + 'manifest.json', canonical_bytes(skill_file_manifest(package))))
        result.extend(InlineFile.from_bytes(root + name, text.encode('utf-8'))
                      for name, text in sorted(package['files'].items()))
    require(len(result) <= MAX_BUNDLE_FILES and sum(len(row.content()) for row in result) <= MAX_BUNDLE_BYTES,
            'animation_skill_bundle_too_large', 413)
    return result


def _tokens(text):
    # Match JavaScript toLowerCase exactly for the ASCII/Chinese token grammar.
    value = text.lower()
    words = set(re.findall(r'[a-z0-9][a-z0-9_-]*', value))
    for phrase in re.findall(r'[\u3400-\u9fff]+', value):
        words.update(phrase[i:i + 2] for i in range(max(1, len(phrase) - 1)))
    return words


class SkillCatalogTools:
    """Pure counterpart of the fixed Pi extension, useful to validate receipts."""

    def __init__(self, snapshot, packages):
        self.snapshot = validate_skill_catalog(snapshot)
        self.packages = _packages(self.snapshot, packages)

    def _binding(self, schema):
        return dict(schema=schema, snapshot_sha256=self.snapshot['sha256'],
            task_sha256=self.snapshot['task_sha256'], recipient_owner=self.snapshot['recipient_owner'],
            consumer_id=self.snapshot['consumer_id'])

    def search(self, query='', *, kind=None, engine=None, offset=0, limit=32):
        _text(query, 512, empty=True)
        require(kind is None or type(kind) is str and kind in KINDS)
        require(engine is None or type(engine) is str and engine in ENGINES)
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 64)
        terms = _tokens(query)
        ranked = []
        for row in self.snapshot['skills']:
            if kind is not None and row['kind'] != kind or engine is not None and engine not in row['runtime_requirements']['engines']:
                continue
            words = _tokens(' '.join(row[key] for key in ('name', 'description', 'skill_id')))
            score = len(terms & words)
            if terms and not score:
                continue
            ranked.append((-score, row['skill_id'], row['version'], row))
        ranked.sort(key=lambda item: item[:3])
        selected = [deepcopy(item[3]) for item in ranked[offset:offset + limit]]
        receipt = dict(self._binding(SEARCH_SCHEMA), query=query, kind=kind, engine=engine,
            offset=offset, limit=limit, total=len(ranked),
            next_offset=offset + len(selected) if offset + len(selected) < len(ranked) else None,
            skills=selected, evidence_scope='catalog_discovery_not_skill_read_or_application')
        receipt['sha256'] = digest(receipt)
        return receipt

    def read(self, package_sha256, path='SKILL.md'):
        _sha(package_sha256)
        require(package_sha256 in self.packages, 'animation_skill_not_in_snapshot', 404)
        package = self.packages[package_sha256]
        require(type(path) is str and path in package['files'], 'animation_skill_file_not_found', 404)
        text = package['files'][path]
        receipt = dict(self._binding(READ_SCHEMA), package_sha256=package_sha256,
            skill_id=package['skill_id'], version=package['version'], path=path,
            file_sha256=package['files_sha256'][path], bytes=len(text.encode('utf-8')),
            evidence_scope='returned_exact_bytes_not_visual_application_or_quality')
        receipt['sha256'] = digest(receipt)
        return dict(text=text, receipt=receipt)

    def validate_read_receipt(self, receipt):
        require(type(receipt) is dict and type(receipt.get('package_sha256')) is str
            and type(receipt.get('path')) is str)
        expected = self.read(receipt['package_sha256'], receipt['path'])['receipt']
        require(receipt == expected, 'animation_skill_read_receipt_changed', 409)
        return expected

    def tool_result(self, name, params):
        """Rebuild the exact successful native result, without claiming delivery.

        The host can compare this with a captured Pi tool_execution_end event.
        Agent result text or an independently copied receipt is not that event.
        """
        require(type(params) is dict, 'animation_skill_tool_invalid')
        if name == 'skill_search':
            require(set(params) <= {'query', 'kind', 'engine', 'offset', 'limit'},
                    'animation_skill_tool_invalid')
            receipt = self.search(**params)
            return dict(content=[dict(type='text', text=canonical_bytes(receipt).decode('utf-8'))],
                        details=dict(receipt=receipt))
        require(name == 'skill_read' and set(params) <= {'package_sha256', 'path'}
                and 'package_sha256' in params, 'animation_skill_tool_invalid')
        returned = self.read(**params)
        return dict(content=[dict(type='text', text=returned['text'])],
                    details=dict(receipt=returned['receipt']))

    def validate_tool_result(self, name, params, result):
        expected = self.tool_result(name, params)
        require(result == expected, 'animation_skill_tool_result_changed', 409)
        return expected
