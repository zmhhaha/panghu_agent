# Preserve live Workbench recovery in the spatial release

The running Workbench image `b50b22226379e5c58ee94cf72dda0ef10e51db9ad20ac3be2db088c90c077f8b`
added narration recovery, resource-deadline renewal and stopped-module retry after
the spatial release's `d19b518` source was prepared. This integration keeps those
fixes when rebuilding the Workbench from complete source.

The actual running Pod supplied hashes for887 files. Against the earlier live
Workbench/auth snapshot,511 of516 original files are byte-identical; the five
changed runtime files are `app.py`, `audio_store.py`, `audio_workflow.py`,
`production_run_store.py` and `speech.py`. The three audio/speech modules in this
integration match the actual running source after line-ending normalization.
The live image's Git label alone omits existing authentication and API-prefix
changes, so it was not used as a substitute for the actual file comparison.

The source applies commits `63297b5`, `19e2ba0` and `d2f29f9`, in order, onto
`d19b518`. One conflict in `ProductionRunStore.public` was resolved by retaining
both the SceneSpace projection and the new resource-deadline resume receipts.
The shared authentication SDK, web source, original10800-second film limit,
spatial configuration and production coordinator remain unchanged. The existing
SceneSpace route/service helpers and validated empty-resource-plan routing have
identical Python ASTs to the prepared spatial release.

Validation covered117 distinct cases in the affected speech, production,
recovery and spatial-routing suites. The first run passed114, skipped1 and
found2 fixture/assertion failures. Focused rechecks passed both after configuring
the HTTP fixture for the existing HTTPS/issuer/CSRF contract and comparing the
complete resource graph by stable node IDs. This gives116 passed and1 skipped
with no unresolved failures; a complete second suite run is not claimed. The
skip concerns creating a projected-secret symlink without Windows privileges.

Machine-readable evidence is in
`evidence/workbench-live-recovery-source-20261127.json`. Original failed JUnit,
focused recheck and full live-file manifests remain in the private operations
directory. This document records source integration and local validation only;
it does not claim an image build, deployment, WikiSkill publication or main merge.
