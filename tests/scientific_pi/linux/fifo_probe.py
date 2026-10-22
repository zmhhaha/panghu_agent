"""Bounded subprocess probe: a FIFO must fail before any artifact validator runs."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pi_worker.artifacts import safe_read, validate_and_copy
from scientific_agent.pi_contracts import ContractError


def forbidden_validator(*args):
    raise AssertionError("FIFO reached research validator")


def main():
    workspace, results = map(Path, sys.argv[1:])
    outcomes = {}
    for name, call in (
        ("safe_read", lambda: safe_read(workspace, "report.md")),
        ("validate_and_copy", lambda: validate_and_copy(workspace, results, {}, 1, forbidden_validator)),
    ):
        try:
            call()
        except ContractError as exc:
            outcomes[name] = exc.code
        else:
            raise AssertionError(f"{name} accepted a FIFO")
    print(json.dumps(outcomes))


if __name__ == "__main__":
    main()
