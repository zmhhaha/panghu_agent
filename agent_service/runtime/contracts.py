"""Small process-runtime errors and UTC timestamps, without business contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


class ContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    try:
        if type(value) is not str or "T" not in value:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        raise ContractError("invalid_timestamp", "Timestamp must be RFC3339 in UTC") from None
