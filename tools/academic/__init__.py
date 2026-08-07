"""Structured academic search primitives used by CrewAI tool wrappers."""

from .models import PaperRecord, deduplicate_papers, make_paper, rank_papers
from .query import build_query_variants
from .search import AcademicSearchResult, search_academic

__all__ = [
    "AcademicSearchResult",
    "PaperRecord",
    "build_query_variants",
    "deduplicate_papers",
    "make_paper",
    "rank_papers",
    "search_academic",
]
