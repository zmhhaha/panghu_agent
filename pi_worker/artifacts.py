"""Controller-owned validation and immutable whitelist copies."""
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from urllib.parse import unquote
from scientific_agent.pi_contracts import ContractError, canonical_json, result_digest, report_sha256

FILES = {'report': ('report.md', 'text/markdown'), 'searches': ('searches.jsonl','application/x-ndjson'), 'papers': ('papers.jsonl','application/x-ndjson'), 'screening': ('screening.jsonl','application/x-ndjson'), 'evidence': ('evidence.jsonl','application/x-ndjson'), 'claims': ('claims.jsonl','application/x-ndjson'), 'references': ('references.json','application/json'), 'validation': ('validation.json','application/json')}
REVISION_FILES = dict(FILES, revision_output=('revision-output.json','application/json'))
TOTAL_LIMIT = 50*1024*1024
REPORT_LIMIT = 2*1024*1024


def safe_read(root, relative, limit=TOTAL_LIMIT):
    root = Path(root).absolute()
    if not isinstance(relative, str) or unquote(relative) != relative or '\\' in relative or ':' in relative or relative.startswith('/') or any(p in ('..','.') for p in relative.split('/')):
        raise ContractError('invalid_artifact', 'Unsafe artifact path', 422)
    path = root / relative
    current = root
    if root.is_symlink():
        raise ContractError('invalid_artifact', 'Symlink artifact root', 422)
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ContractError('invalid_artifact', 'Symlink artifact rejected', 422)
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            raise ContractError('invalid_artifact', 'Artifact escapes workspace', 422)
        fd = os.open(path, os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0) | getattr(os,'O_BINARY',0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ContractError('invalid_artifact', 'Artifact must be a private regular file', 422)
            if info.st_size > limit:
                raise ContractError('artifact_too_large', 'Artifact exceeds byte limit', 413)
            data = stream.read(limit+1)
            if len(data) > limit:
                raise ContractError('artifact_too_large', 'Artifact exceeds byte limit', 413)
            return data
    except ContractError:
        raise
    except (OSError, ValueError) as exc:
        raise ContractError('invalid_artifact', 'Required artifact unavailable', 422) from exc


def validate_and_copy(workspace, results_root, job, attempt, validator=None):
    workspace, results_root = Path(workspace), Path(results_root)
    revision = job.get('task_kind') == 'revision'
    file_definitions = REVISION_FILES if revision else FILES
    revision_validation = None
    if revision:
        contents, revision_validation = _revision_contents(workspace, job)
    elif validator is None:
        from pi_worker.research_tools import validate_research_outputs
        validator = validate_research_outputs
    # Read once into bounded controller memory before invoking external validator.
    contents = contents if revision else {}
    total = 0
    for artifact_id, (name, _) in file_definitions.items():
        if revision or artifact_id == 'validation':
            continue
        data = safe_read(workspace, name, REPORT_LIMIT if artifact_id == 'report' else TOTAL_LIMIT)
        total += len(data)
        if total > TOTAL_LIMIT:
            raise ContractError('artifact_too_large', 'Total artifacts exceed byte limit', 413)
        contents[name] = data
    private_inputs = {}
    try:
        for line in ([] if revision else contents['papers.jsonl'].decode('utf-8').splitlines()):
            if not line.strip():
                continue
            paper = json.loads(line)
            relative = paper.get('full_text_path')
            if relative is None:
                continue
            if not isinstance(relative,str) or not relative or relative in contents or relative in ('manifest.json','provenance.json','validation.json') or any(part.startswith('.') for part in relative.split('/')):
                raise ContractError('invalid_artifact','Unsafe acquired full-text path',422)
            if relative not in private_inputs:
                data = safe_read(workspace,relative)
                total += len(data)
                if total > TOTAL_LIMIT:
                    raise ContractError('artifact_too_large','Total acquired evidence exceeds byte limit',413)
                private_inputs[relative] = data
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        raise ContractError('validation_failed','Paper records are not valid JSON objects',422)
    provenance_data = safe_read(workspace, 'provenance.json', 64*1024)
    try:
        provenance = json.loads(provenance_data)
        required = ('model','pi_version','node_version','image_digest','skill_bundle_revision','runtime_revision','retrieval_started_at','retrieval_finished_at')
        if not isinstance(provenance, dict) or any(not isinstance(provenance.get(k),str) or not provenance[k] for k in required):
            raise ValueError()
        if provenance['skill_bundle_revision'] != job['skill_bundle_revision'] or provenance['runtime_revision'] != job['runtime_revision']:
            raise ValueError()
        canonical_json(provenance)
        provenance['acquired_full_text'] = [dict(path=name,size=len(data),sha256=hashlib.sha256(data).hexdigest()) for name,data in sorted(private_inputs.items())]
        if revision:
            provenance.update(material_policy='recorded_evidence_only', retrieval_performed=False,
                              full_text_restored=False, retrieval_interval_basis='not_performed_recorded_evidence_only',
                              revision={key:job[key] for key in ('intent_id','parent_version_id','root_report_id',
                                        'base_sha256','section_id','section_sha256','input_sha256')})
    except (ValueError, TypeError):
        raise ContractError('invalid_artifact', 'Missing or invalid execution provenance', 422)
    # Optional runtime manifest is untrusted; validate every reference even though
    # the controller independently creates its own whitelist manifest.
    if (workspace/'manifest.json').exists() or (workspace/'manifest.json').is_symlink():
        try:
            candidate = json.loads(safe_read(workspace,'manifest.json', 1024*1024))
            ids = set()
            for entry in candidate['artifacts']:
                artifact_id = entry['artifact_id']
                relative = entry.get('path', entry.get('relative_path'))
                if artifact_id in ids or artifact_id not in file_definitions or relative != file_definitions[artifact_id][0]:
                    raise ValueError()
                ids.add(artifact_id)
                safe_read(workspace,relative)
        except (KeyError,TypeError,ValueError):
            raise ContractError('invalid_artifact', 'Untrusted manifest is invalid', 422)
    results_root.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        results_root.chmod(0o700)
    staging = Path(tempfile.mkdtemp(prefix='.validate-', dir=results_root))
    try:
        # Validator sees exactly the bytes that will be published, never mutable
        # runner files. The runner has exited before this function is called.
        for name, data in {**contents, **private_inputs}.items():
            destination_file = staging/name
            destination_file.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            destination_file.write_bytes(data)
        validation = revision_validation if revision else validator(staging)
        if not isinstance(validation,dict) or validation.get('valid') is not True:
            raise ContractError('validation_failed', 'Research output validation failed', 422)
        contents['validation.json'] = canonical_json(validation).encode('utf-8')
        (staging/'validation.json').write_bytes(contents['validation.json'])
        artifacts = []
        total = sum(len(data) for data in private_inputs.values())
        for artifact_id, (name, media) in file_definitions.items():
            data = contents[name]
            total += len(data)
            artifacts.append(dict(artifact_id=artifact_id,path=name,media_type=media,size=len(data),sha256=hashlib.sha256(data).hexdigest()))
        if total > TOTAL_LIMIT:
            raise ContractError('artifact_too_large', 'Total artifacts exceed byte limit', 413)
        try:
            report = contents['report.md'].decode('utf-8')
        except UnicodeDecodeError:
            raise ContractError('validation_failed','Report must be UTF-8',422)
        artifacts.sort(key=lambda a:a['artifact_id'])
        result = dict(schema_version='1',task_id=job['task_id'],attempt=attempt,report_markdown=report,report_sha256=report_sha256(report),artifacts=artifacts,provenance=provenance,validation=validation)
        result['result_digest'] = result_digest(result)
        manifest = dict(schema_version='1',task_id=job['task_id'],attempt=attempt,artifacts=artifacts,provenance=provenance,result_digest=result['result_digest'])
        result['manifest'] = manifest
        (staging/'manifest.json').write_text(canonical_json(manifest),encoding='utf-8')
        destination = results_root/job['task_id']/result['result_digest']
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ContractError('publication_conflict','Immutable result directory already exists',409)
        for item in staging.rglob('*'):
            if item.is_file():
                with item.open('r+b') as stream:
                    os.fsync(stream.fileno())
                item.chmod(0o440)
            else:
                item.chmod(0o700)
        os.replace(staging,destination)
        if os.name != 'nt':
            directories = [p for p in destination.rglob('*') if p.is_dir()] + [destination, destination.parent]
            for directory in directories:
                fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return result, destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _revision_contents(workspace, job):
    """Compose only from queue-owned input and the stopped child's bounded JSON.

    Historical full_text_path values are never followed. The revision validator
    explicitly validates recorded evidence, whereas the research validator
    requires the originally acquired files for a newly retrieved report.
    """
    from scientific_agent.pi_revision_contracts import RevisionJob, validate_revision_materials
    from scientific_agent.pi_revision_validation import compose_revision
    wire = {key:value for key,value in job.items()
            if key not in {'attempt','_runtime_uid','_revision_input'}}
    checked = RevisionJob.from_dict(wire).to_dict()
    material = validate_revision_materials(checked, job.get('_revision_input'))
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    try:
        candidate_bytes = safe_read(workspace,'revision-output.json',16*1024*1024)
        candidate = json.loads(candidate_bytes,
                               object_pairs_hook=pairs,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        composed = compose_revision(material['report'],material['items'],material['artifacts'],
                                    checked['section_id'],checked['base_sha256'],candidate)
    except ContractError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as exc:
        raise ContractError(getattr(exc,'code','revision_validation_failed'),
                            'Fixed-evidence revision validation failed',422) from exc
    contents = {'report.md':composed['report_markdown'].encode('utf-8'),
                'revision-output.json':candidate_bytes,
                'searches.jsonl':b'', 'screening.jsonl':b''}
    for identifier, raw in composed['artifacts'].items():
        contents[FILES[identifier][0]] = raw
    return contents, composed['validation']


def artifact_bytes(store, task_id, artifact_id):
    result, root = store.artifact_record(task_id)
    if artifact_id == 'manifest':
        data = safe_read(root,'manifest.json', 1024*1024)
        if data != canonical_json(result['manifest']).encode('utf-8'):
            raise ContractError('artifact_corrupt','Artifact integrity check failed',409)
        return data, 'application/json'
    entry = next((a for a in result['artifacts'] if a['artifact_id']==artifact_id),None)
    if entry is None:
        raise ContractError('artifact_not_found','Artifact not found',404)
    data = safe_read(root,entry['path'])
    if len(data)!=entry['size'] or hashlib.sha256(data).hexdigest()!=entry['sha256']:
        raise ContractError('artifact_corrupt','Artifact integrity check failed',409)
    return data, entry['media_type']
