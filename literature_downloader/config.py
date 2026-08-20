"""Configuration for the standalone literature downloader."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PACKAGE_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Paths can be redirected for tests or deployment."""

    data_dir: Path
    db_path: Path
    pdf_dir: Path
    reports_dir: Path
    max_rounds: int = 3
    search_limit: int = 100
    per_provider: int = 20
    max_search_variants: int = 6
    min_pdf_bytes: int = 10 * 1024
    min_text_chars: int = 200
    download_timeout: int = 30
    max_pdf_bytes: int = 50 * 1024 * 1024
    contact_email: str = ""
    semantic_scholar_api_key: str = ""
    download_concurrency: int = 6
    llm_enabled: bool = True
    llm_timeout: int = 30
    llm_max_candidates: int = 40

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    data_dir = Path(os.getenv("LITERATURE_DATA_DIR", str(PACKAGE_DIR / "data"))).resolve()
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.getenv("LITERATURE_DB_PATH", str(data_dir / "literature.db"))).resolve(),
        pdf_dir=Path(os.getenv("LITERATURE_PDF_DIR", str(data_dir / "pdfs"))).resolve(),
        reports_dir=Path(os.getenv("LITERATURE_REPORTS_DIR", str(data_dir / "reports"))).resolve(),
        max_rounds=_env_int("LITERATURE_MAX_ROUNDS", 3, 1, 10),
        search_limit=_env_int("LITERATURE_SEARCH_LIMIT", 100, 1, 100),
        per_provider=_env_int("LITERATURE_PER_PROVIDER", 20, 1, 25),
        max_search_variants=_env_int("LITERATURE_MAX_SEARCH_VARIANTS", 6, 1, 13),
        min_pdf_bytes=_env_int("LITERATURE_MIN_PDF_BYTES", 10 * 1024, 512, 10 * 1024 * 1024),
        min_text_chars=_env_int("LITERATURE_MIN_TEXT_CHARS", 200, 20, 100_000),
        download_timeout=_env_int("LITERATURE_DOWNLOAD_TIMEOUT", 30, 5, 180),
        max_pdf_bytes=_env_int("LITERATURE_MAX_PDF_BYTES", 50 * 1024 * 1024, 1_000_000, 500 * 1024 * 1024),
        contact_email=os.getenv("ACADEMIC_CONTACT_EMAIL") or os.getenv("EVIDENCEGATE_MAILTO", ""),
        semantic_scholar_api_key=os.getenv("SEMANTIC_SCHOLAR_API_KEY", ""),
        download_concurrency=_env_int("LITERATURE_DOWNLOAD_CONCURRENCY", 6, 1, 16),
        llm_enabled=os.getenv("LITERATURE_LLM_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"},
        llm_timeout=_env_int("LITERATURE_LLM_TIMEOUT", 30, 5, 180),
        llm_max_candidates=_env_int("LITERATURE_LLM_MAX_CANDIDATES", 40, 1, 100),
    )


settings = load_settings()
