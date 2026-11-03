# Actual final-validator failure components

`final_failure_probe.py` is an opt-in Linux/server probe. It invokes the actual
`verify_final_pptx` with fixed image tools, LibreOffice conversion, Poppler raster
and Fitz glyph checks. It never supplies `commands_factory`, patches a function,
calls a model, alters a production record or accepts/publishes a presentation.
No local test, build or render is required or permitted for this operator.

Run only in a disposable, bounded, networkless validator container whose image,
read-only root, capabilities, resources, process stop and cleanup the enclosing
operator records. Obtain source by Git and mount it read-only. Mount the exact
retained synthetic revised sample **read-only**, for example its `revised`
directory at `/baseline`. The probe requires read-only filesystem flags, no
symlink/hardlink input, a fixed expected PPTX hash and existing six-page final
render evidence that binds the same PPTX. It fingerprints the retained PPTX,
evidence JSON, layout diagnostics, PDF and all six preview PNGs before/after.
Outputs must be new and separate. Failed attempts are never overwritten.

Example application invocation inside that reviewed container:

```sh
PPT_RUN_FINAL_FAILURE_PROBE=true python -B -m \
  ppt_service.tests.acceptance.final_failure_probe \
  --case quality \
  --baseline /baseline/delivery/presentation.pptx \
  --baseline-render /baseline/final-render \
  --expected-baseline-sha256 88622f30db49dcc4550be4f52f12e82b271834b2e751a4ea1b5c6422ead8bfc5 \
  --output /out/quality-r1
```

Cases:

- `quality`: in a fresh candidate copy change only slide 1's first two direct
  native text frames, retaining every other ZIP part byte-for-byte. Different
  labels have explicit overlapping positions, identical 24-point font and no
  fill. Actual PDF glyphs must produce `text_collision` for those exact shape
  IDs, and the real validator must raise `final_pptx_text_layout_failed` without
  writing `final-pptx-evidence.json`. The same probe then actually renders a new
  copy of the original healthy baseline and requires six-page success.
- `renderer-unavailable`: root creates a small ordinary nonexecutable 0444
  diagnostic file and mounts it **read-only** over `/usr/bin/pdfinfo` in this
  disposable container only. This direct executable path avoids the
  LibreOffice launcher symlink. The probe verifies the fixed tool is really
  nonexecutable and read-only, then requires the unmodified validator to raise
  `final_pptx_renderer_unavailable` with no successful evidence. No function or
  command return is simulated. The root/host tool is never changed.
- `healthy`: in a separate normal validator container, actually render the
  unchanged baseline into a new output and require source/nonce-bound success.
  Use after the renderer-unavailable container has stopped; do not try to
  restore the mounted tool from inside that container.

The runner records per-candidate controller nonce/source/input hashes, original
exception and traceback, real private renderer/layout outputs and baseline
preservation. `passed:true` means the requested component rejection/recovery
was observed, not that a faulty candidate passed quality. Root must separately
record container/Pod stop and owned-resource cleanup. These components do not
prove the full Agent/PPT business recovery workflow, real-owner revision,
desktop PowerPoint acceptance or production/PVC disaster recovery.
