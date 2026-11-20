"""Bounded inline reading of an exact published library for laboratory v2.

The laboratory protocol accepts immutable knowledge snapshots, not the formal
production Skill tools. This adapter faithfully copies the frozen release's
files into that existing wire format; it never reads the mutable working Wiki
and never represents inline delivery as a tool read or successful application.
"""
from copy import deepcopy
from pathlib import Path

from agent_service.animation_knowledge_contract import (
    EXPERIENCE_SCHEMA, PAGE_SCHEMA, SNAPSHOT_SCHEMA, validate_snapshot,
)
from agent_service.contracts import canonical_bytes, digest
from agent_service.profiles.video_animation_skill_method import INPUT_MAX_BYTES
from .animation_skill_production import _require
from .animation_wikiskill_production import LibrarySkillProductionAdapter
from .animation_wikiskill_store import AnimationWikiSkillStore


SOURCE = 'published-release-inline'
_CHUNK_CHARACTERS = 16000


class LaboratoryLibraryAdapter(LibrarySkillProductionAdapter):
    def laboratory_snapshot(self, owner, binding, *, drawing_libraries=None):
        """Return complete bounded source files from the already frozen release.

        Deterministic derivation preserves the same snapshot across restarts
        and technical retry without a second mutable knowledge ledger. Policy
        and enabled-with-no-release are distinguished by the separate binding;
        both intentionally yield a valid empty snapshot when no files apply.
        """
        drawing_libraries = {} if drawing_libraries is None else drawing_libraries
        _require(type(drawing_libraries) is dict and all(type(name) is str and type(spec) is dict
            for name, spec in drawing_libraries.items()), 'animation_laboratory_library_dependency_invalid', 422)
        release = self._batch(owner, binding)['release']
        pages, experiences = [], []
        created_at = release['created_at'] if release is not None else 0
        if release is not None:
            source_id = SOURCE + ':' + release['id']
            receipt = dict(schema=EXPERIENCE_SCHEMA, owner=owner, kind='published_release_inline',
                source_id=source_id, created_at=created_at,
                document=dict(source=SOURCE, library_binding=deepcopy(binding),
                    observation='Exact published file bytes prepared for inline input; not proof of Agent reading or quality.'))
            receipt['content_sha256'] = digest(receipt['document'])
            receipt['id'] = 'exp_' + digest(dict(owner=owner, kind=receipt['kind'], source_id=source_id))
            receipt['sha256'] = digest(receipt)
            experiences.append(receipt)
            for package in release['packages']:
                required = package['runtime_requirements'].get('drawing_libraries', {})
                _require(type(required) is dict and all(type(name) is str and type(spec) is dict
                    for name, spec in required.items()), 'animation_laboratory_library_dependency_invalid', 422)
                if any(drawing_libraries.get(name) != spec for name, spec in required.items()):
                    continue
                for path, text in sorted(package['files'].items()):
                    for offset in range(0, len(text), _CHUNK_CHARACTERS):
                        chunk = text[offset:offset + _CHUNK_CHARACTERS]
                        # File parts carry exact text rather than summaries or
                        # an invented working-Wiki interpretation of the Skill.
                        file_identity = dict(package_sha256=package['sha256'], path=path, offset=offset)
                        body = dict(title=f"{package['skill_id']} / {path} / {offset}",
                            content=f'File {path}, character offset {offset}:\n' + chunk,
                            applicability=[SOURCE, canonical_bytes(file_identity).decode('utf-8'),
                                package['name'], package['description']],
                            counterexamples=[], limitations=[
                                'Inline published knowledge; delivery is not tool reading, execution or observed quality.'])
                        page = dict(schema=PAGE_SCHEMA, owner=owner,
                            page_id='published_file_' + digest(file_identity), revision=1,
                            previous_revision_sha256=None, body=body, status='active',
                            evidence_ids=[receipt['id']], relations=[], change_kind='create',
                            change_note='Immutable published release file, copied without changing its text.',
                            created_at=created_at)
                        page['content_sha256'] = digest({key: page[key] for key in
                            ('body', 'status', 'evidence_ids', 'relations')})
                        page['sha256'] = digest(page)
                        pages.append(page)
                        _require(len(pages) <= 256, 'animation_laboratory_library_too_large', 413)
        pages.sort(key=lambda row: row['page_id'])
        identity = dict(owner=owner,
            pages=[dict(page_id=p['page_id'], revision=p['revision'], sha256=p['sha256']) for p in pages],
            experiences=[dict(id=e['id'], sha256=e['sha256']) for e in experiences])
        snapshot = dict(schema=SNAPSHOT_SCHEMA, id='snapshot_' + digest(identity), owner=owner,
            created_at=created_at, pages=pages, experiences=experiences)
        snapshot['sha256'] = digest(snapshot)
        # The complete task still receives its own stricter final preflight;
        # no file is silently truncated to make a release fit this protocol.
        _require(len(canonical_bytes(snapshot)) <= INPUT_MAX_BYTES,
            'animation_laboratory_library_too_large', 413)
        return validate_snapshot(snapshot, owner=owner)


def make_laboratory_library_adapter(root, *, wikiskill_store=None):
    """Use the same method-lab publication DB with an isolated consumer ledger."""
    root = Path(root)
    store = wikiskill_store if wikiskill_store is not None else AnimationWikiSkillStore(root / 'wikiskill.sqlite3')
    return LaboratoryLibraryAdapter(store.active, store.get_release, store.list_recent,
        root / 'wikiskill-consumption.sqlite3',
        import_release=lambda owner, binding: store.get_release(
            binding['publisher_owner'], binding['release_id'], include_archived=True))
