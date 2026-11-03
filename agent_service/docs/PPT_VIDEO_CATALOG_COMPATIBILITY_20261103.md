# Shared catalog reader compatibility

The motion and explanation handlers, their validators, fixed resource bundles,
and the matching registry/resource/build allowlist additions are inherited from
the committed video worktree at `a5350a3c2a1b7f4f5fd8626dc129e617b4c7747e`.
Their original feature commits are `5e63150` and `dee1311`; motion validation also
includes `3824d2c`, whose demonstration template remains opt-in for trusted local
callers and does not widen the published Agent profile's defaults.

This lets a PPT worker and publication tooling parse the complete shared catalog
containing `video.motion-plan.v1` and `video.explanation-plan.v1`. The existing
catalog generator, video routes, acceptance records, PPT authoring/handoff rules,
PPT task-byte limits and shared execution-slot implementation are unchanged.
Unknown output schemas and mismatched reviewed task schemas remain rejected.

Server validation selectors are `tests/agent_service/test_worker_host.py` and
`tests/agent_service/test_build.py`. The mixed-catalog test uses disabled synthetic
bindings and creates no live capability, acceptance, route or readiness record.
