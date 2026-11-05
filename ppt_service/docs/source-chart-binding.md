# Source-bound native chart contract

The PPT control process discovers numeric tables by reading exact authorized
Markdown originals through its existing no-follow regular-file reader. The file
must match the snapshot asset UUID, canonical storage location, byte count and
SHA-256. Summaries, candidate output, PDF/DOCX parsing and network data do not
provide numeric authority in this contract.

Only `plan_options` tasks optionally carry `source_table_catalog`. It contains
complete bounded tables, source asset identities, source line positions, headers,
literal cell strings and `column_types` (`number`, `text`, `unsupported`). The
catalog is at most 8192 canonical JSON bytes. A table exceeding the budget is
omitted in full and reported with a fixed unsupported reason. No authoritative
row is truncated. Tables have at most 40 data rows and 8 columns. Fenced or
indented code, duplicate headers, ragged/missing cells and ambiguous numeric
formats cannot become chart values. Decimal/scientific values have explicit
precision/exponent limits; formulas, percentages and thousands separators are
never converted or evaluated.

The site checks frozen bindings through the stdlib-only
`server/source_charts.py`; it does not import the control process or Agent
Service. Original source discovery stays in control. The total catalog has at
most 32 tables, and each source has at most 8 discovered tables.

Each Actor direction may declare only references:

```json
{
  "native_chart_specs": [{
    "table_id": "<catalog table_id>",
    "category_column": 0,
    "series_columns": [1],
    "slide": 2,
    "type": "column"
  }]
}
```

Column indices start at zero; slide numbers start at one. Supported types are
`column`, `line` and `pie` (one series for pie). At most 8 charts, 4 series per
chart and 8192 bytes per direction binding are allowed. All source rows retain
their order. Unsupported mappings are rejected before an owner can confirm a
plan or start preparation; malformed Actor references terminate collection
without repeating model work.

The stopped trusted collector strips Actor `source_chart_binding` and
`source_table_catalog` fields, rechecks the original catalog and derives each
direction's `source_chart_binding`. The binding contains schema version 1,
snapshot hash, direction ID, full source provenance/cells, verifier-shaped
`expected_charts` and `binding_sha256` (canonical JSON excluding the SHA field).
The digest detects corruption; website database/outbox lineage supplies authority.
The existing direction confirmation covers the displayed source mapping. No
additional per-chart or per-cell consent is introduced.

Typed Actor chart categories, series and values are removed from the direction
and page display projections so that they cannot conflict with the displayed
source binding. The original source plan remains audit-only. Free prose numbers
continue to require ordinary source-fidelity review.

The selected frozen binding travels in
`task.confirmations.stage2.selected_direction.source_chart_binding`. Production
generation, revision and acceptance use `task_chart_oracle` against the exact
original task. They do not read a current deployment map, latest plan, current
file content, `snapshot.expected_charts` or Actor output to reinterpret dispatched
expectations. Native chart roster, page/type, cache and embedded workbook checks
continue to use the existing independent verifier.

Chart-free plans use an empty chart expectation list. Declared but unbound chart
plans require replanning and the normal direction confirmation.

The owner confirmed on 2026-11-05 that the service has not formally launched and
old tasks/configuration do not require compatibility. Replace the PPT runtime
configuration directly: do not add parallel legacy routes/pools, migrate old
tasks, or maintain per-project chart approval maps for this rollout. This does
not require deleting historical business records or changing other services'
Agent configuration.

The optional catalog is a coordinated profile/API/control/Pi wire extension.
Update the participating profile readers together before enabling production. All
execution checks, tests and rendering are run on the server from committed Git;
the implementation does not require production database edits or new user tasks.

## Verification recorded on 2026-11-05

Server Git source `fefd714c7a9face373d08e592f6ceb37a845f1f8` passed 165 focused
checks covering source-table parsing, connector/frozen task construction, the
planning catalog, PPT output contracts and the latest-tag build entry. Failures,
errors and skips were all zero; the container stopped and was removed, and source
hashes remained unchanged. Receipt:
`/root/ppt-source-chart-regressions-fefd714c-d6836b9e/receipt.json`.

The source-chart UI previously passed 73 focused tests on unchanged frontend
source, and its full source build subsequently passed. These checks do not claim
a successful production chart draft, an edited PowerPoint chart, owner visual
approval or publication; those require evidence from the resulting version.
