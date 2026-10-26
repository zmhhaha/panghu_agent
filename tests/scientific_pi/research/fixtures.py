"""Controlled research fixtures. All paper/study facts are synthetic test data."""
from __future__ import annotations

import json
from pathlib import Path

from pi_worker.research_tools.bridge import normalize_paper
from pi_worker.research_tools.schemas import RECORD_SCHEMAS

FIXTURE_TIME = "2026-01-01T00:00:00Z"


def zero_evidence():
    return {
        "searches": [{"source": "fixture", "query": "controlled empty result",
                      "searched_at": FIXTURE_TIME, "status": "success",
                      "result_count": 0, "error": None}],
        "papers": [], "screening": [], "evidence": [], "claims": [], "references": [],
        "report": "# Scope\nControlled fixture only.\n# Methods\nOne fixture search returned zero rows.\n# Findings\nInsufficient evidence.\n# Limitations\nNo scientific conclusion is supported.\n# References\nNone.\n",
    }


def abstract_only():
    data = zero_evidence()
    paper = normalize_paper({"title": "Synthetic fixture paper", "provider": "fixture",
                             "doi": "10.1234/fixture", "authors": "Fixture Author",
                             "url": "https://example.org/fixture", "date": "2025-01-01",
                             "abstract": "Synthetic abstract finding for validation tests."}, FIXTURE_TIME)
    paper_id = paper["paper_id"]
    data.update({
        "papers": [paper],
        "screening": [{"paper_id": paper_id, "decision": "include", "reason": "controlled fixture", "criteria": ["synthetic fixture"]}],
        "evidence": [{"evidence_id": "e1", "paper_id": paper_id, "finding": "Synthetic abstract finding",
                      "method": "Synthetic fixture", "limitations": "Abstract only; no real study",
                      "access_level": "abstract", "locator": "abstract, sentence 1"}],
        "claims": [{"claim_id": "c1", "marker": "<!-- claim:c1 -->", "evidence_ids": ["e1"],
                    "assessment": "supported", "explanation": "Only the synthetic abstract text is represented"}],
        "references": [{"n": 1, "paper_id": paper_id}],
        "report": "# Scope\nSynthetic fixture.\n# Methods\nOne controlled source; included one record.\n# Findings\n<!-- claim:c1 -->\nSynthetic abstract states a finding [1].\n# Limitations\nAbstract-only evidence; identifier verification unknown. This fixture proves no scientific truth.\n# References\n[1] Synthetic fixture paper. https://example.org/fixture\n",
    })
    data["searches"][0]["result_count"] = 1
    return data


def write_fixture(workspace: Path, data):
    for name in RECORD_SCHEMAS:
        text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data[name])
        (workspace / (name + ".jsonl")).write_text(text, encoding="utf-8")
    (workspace / "references.json").write_text(json.dumps(data["references"]), encoding="utf-8")
    (workspace / "report.md").write_text(data["report"], encoding="utf-8")
