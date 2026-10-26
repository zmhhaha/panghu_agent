"""Academic tool CLI: JSON to stdout, nonzero exits for invalid inputs/outputs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .schemas import OUTPUT_SCHEMA
from .validation import validate_research_outputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("search", help="exact query; logs observed counts/errors")
    command.add_argument("--query", required=True)
    command.add_argument("--provider", action="append", dest="providers")
    command.add_argument("--limit", type=int, default=10)
    command = commands.add_parser("verify", help="DOI/PMID metadata lookup, no full-text claim")
    command.add_argument("--kind", choices=["doi", "pmid"], required=True)
    command.add_argument("--identifier", required=True)
    command = commands.add_parser("validate", help="offline research output checks")
    command.add_argument("--workspace", type=Path, required=True)
    commands.add_parser("schema", help="print exact output record schemas")
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            from .bridge import search
            result = search(args.query, providers=args.providers, limit=args.limit)
            exit_code = 0 if any(row["status"] != "error" for row in result["searches"]) else 3
        elif args.command == "verify":
            from .bridge import verify_identifier
            result = verify_identifier(args.kind, args.identifier)
            exit_code = 0 if result["verification"]["status"] == "verified" else 3
        elif args.command == "validate":
            result = validate_research_outputs(args.workspace)
            exit_code = 0 if result["valid"] is True else 2
        else:
            result, exit_code = OUTPUT_SCHEMA, 0
    except (ValueError, ImportError) as exc:
        result, exit_code = {"error": {"code": "invalid_input_or_missing_dependency", "type": type(exc).__name__, "message": str(exc)[:300]}}, 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
