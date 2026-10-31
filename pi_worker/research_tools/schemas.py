"""Versioned research-output schemas with no framework dependency."""
from __future__ import annotations

SCHEMA_VERSION = "1"
VALIDATOR_VERSION = "research-output/1.0.0"


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def arr(items):
    return {"type": "array", "items": items}


TEXT = {"type": "string", "minLength": 1}
NULL_TEXT = {"type": ["string", "null"]}
TIME = {"type": "string", "format": "date-time"}
NULL_TIME = {"type": ["string", "null"], "format": "date-time"}
AVAILABILITY = obj({"available": {"type": "boolean"}, "obtained_at": NULL_TIME})
VERIFICATION = obj({
    "status": {"enum": ["verified", "unknown", "failed"]},
    "checked_at": NULL_TIME, "source": NULL_TEXT, "error": NULL_TEXT,
})
BIBLIOGRAPHY = obj({
    "author_list_status": {"enum": ["complete", "incomplete", "not_confirmed"]},
    "author_count": {"type": ["integer", "null"], "minimum": 0},
    "source_type": {"enum": ["preprint", "journal", "other"]},
    "dates": obj({key: NULL_TEXT for key in ("first_posted", "version_updated",
        "journal_published", "online_published", "print_published")}),
    "version": NULL_TEXT, "journal_reference": NULL_TEXT,
})
RECORD_SCHEMAS = {
    "searches": obj({
        "source": TEXT, "query": TEXT, "searched_at": TIME,
        "status": {"enum": ["success", "partial", "error"]},
        "result_count": {"type": ["integer", "null"], "minimum": 0},
        "error": NULL_TEXT,
    }),
    "papers": obj({
        "paper_id": TEXT, "title": TEXT, "authors": arr(TEXT),
        "year": {"type": ["integer", "null"], "minimum": 1},
        "doi": NULL_TEXT, "pmid": NULL_TEXT, "url": NULL_TEXT,
        "sources": arr(TEXT), "retrieved_at": TIME,
        "availability": obj({"metadata": AVAILABILITY, "abstract": AVAILABILITY,
                             "full_text": AVAILABILITY}),
        "abstract": NULL_TEXT, "full_text_path": NULL_TEXT,
        "verification": VERIFICATION, "bibliography": BIBLIOGRAPHY,
    }, required=("paper_id", "title", "authors", "year", "doi", "pmid", "url",
        "sources", "retrieved_at", "availability", "abstract", "full_text_path", "verification")),
    "screening": obj({
        "paper_id": TEXT, "decision": {"enum": ["include", "exclude", "uncertain"]},
        "reason": TEXT, "criteria": arr(TEXT),
    }),
    "evidence": obj({
        "evidence_id": TEXT, "paper_id": TEXT, "finding": TEXT,
        "method": TEXT, "limitations": TEXT,
        "access_level": {"enum": ["metadata", "abstract", "full_text"]},
        "locator": TEXT,
    }),
    "claims": obj({
        "claim_id": TEXT, "marker": TEXT, "evidence_ids": arr(TEXT),
        "assessment": {"enum": ["supported", "conflicting", "insufficient"]},
        "explanation": TEXT,
    }),
}
REFERENCES_SCHEMA = arr(obj({"n": {"type": "integer", "minimum": 1}, "paper_id": TEXT}))
VALIDATION_SCHEMA = obj({
    "validator_version": TEXT, "valid": {"type": "boolean"},
    "checks": arr(obj({"name": TEXT, "status": {"enum": ["pass", "fail", "unknown"]}, "detail": TEXT})),
    "errors": arr(TEXT), "warnings": arr(TEXT),
    # Early failures have empty provenance; successful parsing supplies observations.
    "provenance": {"type": "object", "properties": {
        "validator_version": TEXT, "search_started_at": NULL_TIME,
        "search_finished_at": NULL_TIME, "source_verification": TEXT,
    }, "additionalProperties": False},
})
REQUIRED_FILES = tuple(name + ".jsonl" for name in RECORD_SCHEMAS) + ("references.json", "report.md")
OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:panghu:scientific-research:outputs:1",
    "description": "Each JSONL line follows its named record schema; references.json is an array. report.md is UTF-8 Markdown.",
    "schema_version": SCHEMA_VERSION,
    "records": RECORD_SCHEMAS, "references": REFERENCES_SCHEMA,
    "validation": VALIDATION_SCHEMA,
    "report": {"encoding": "UTF-8", "media_type": "text/markdown", "max_bytes": 2097152},
}
