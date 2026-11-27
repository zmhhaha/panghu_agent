"""Ordinary clean context actually includes the new profile's pinned resources."""
from pathlib import Path
import hashlib
import tarfile

from agent_service.build_image import context_archive
from agent_service.profiles import video_picture_review_v2 as review


def test_clean_pi_context_contains_exact_v2_bundle_and_cold_probe_closure(tmp_path):
    service=Path(__file__).parents[2]/'agent_service'
    destination=tmp_path/'worker-context.tar'
    context_archive(service,'pi',destination)
    closure=['profiles/video_picture_review_v2.py','visual_review_findings.py','deploy/picture_review_v2_catalog.py',
        'deploy/acceptance_probe.py','deploy/candidate_scenario.py','model_proxy.py',
        'picture_learning_tools.py','picture_learning_extension.mjs','read_inputs_extension.mjs']
    with tarfile.open(destination) as archive:
        for name in ['manifest.json',*review.BUNDLE_FILES]:
            member='agent_service/resources/picture-review-v2/'+name
            assert archive.extractfile(member).read()==(review.BUNDLE_ROOT/name).read_bytes()
        raw=archive.extractfile('agent_service/resources/picture-review-v2/manifest.json').read()
        assert hashlib.sha256(raw).hexdigest()==review.BUNDLE_MANIFEST_SHA256
        for name in closure:
            assert archive.extractfile('agent_service/'+name).read()==(service/name).read_bytes()
        # This is an offline source context, not an invocation of the builder or model.
        assert not any('/secrets/' in m.name or m.name.endswith('credentials.json') for m in archive.getmembers())
