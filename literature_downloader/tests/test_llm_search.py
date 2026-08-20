from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from literature_downloader.config import Settings
from literature_downloader.db import Database
from literature_downloader.relevance_ranker import rank_candidates
from literature_downloader.search_planner import LLMJsonClient, create_search_plan, matches_plan_scope


class FakeClient:
    provider = "deepseek"
    model = "test-model"

    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, system_prompt, user_payload):
        return self.payload


class LLMSearchTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings(root, root / "literature.db", root / "pdfs", root / "reports", llm_enabled=True)

    def test_missing_llm_key_returns_deterministic_plan(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = self._settings(root)
        with patch.dict("os.environ", {"PROVIDER": "deepseek"}, clear=True):
            plan = create_search_plan("InP 干法刻蚀", config=config)
        self.assertFalse(plan["llm"]["used"])
        self.assertEqual(plan["llm"]["status"], "fallback")
        self.assertTrue(plan["query_variants"])

    def test_llm_plan_is_schema_checked_and_cached(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = self._settings(root)
        payload = {
            "core_concepts": ["InP", "plasma etching"],
            "synonyms": ["indium phosphide", "ICP-RIE"],
            "query_variants": ["InP plasma etching", "indium phosphide ICP-RIE"],
            "inclusion_criteria": ["主题直接相关"],
            "exclusion_criteria": ["仅命中泛化词"],
            "scope_requirements": [
                {"name": "material", "terms": ["InP", "indium phosphide"], "required": True},
                {"name": "process", "terms": ["dry etching", "plasma etching"], "required": True},
            ],
            "target_count": 30,
        }
        fake = FakeClient(payload)
        with patch("literature_downloader.search_planner.LLMJsonClient.from_environment", return_value=fake):
            first = create_search_plan("InP 干法刻蚀", config=config)
            second = create_search_plan("InP 干法刻蚀", config=config)
        self.assertEqual(first["llm"]["status"], "used")
        self.assertEqual(second["llm"]["status"], "cached")
        self.assertEqual(second["query_variants"], payload["query_variants"])
        self.assertTrue(second["scope_filtering"]["active"])

    def test_scope_is_generated_plan_data_and_rejects_missing_required_group(self) -> None:
        plan = {
            "llm": {"used": True},
            "scope_requirements": [
                {"name": "material", "terms": ["InP", "indium phosphide"], "required": True},
                {"name": "process", "terms": ["dry etching", "plasma etching"], "required": True},
            ],
        }
        self.assertTrue(matches_plan_scope({"title": "InP plasma etching"}, plan))
        self.assertFalse(matches_plan_scope({"title": "lithium niobate plasma etching"}, plan))
        fallback = {"llm": {"used": False}, "scope_requirements": plan["scope_requirements"]}
        self.assertTrue(matches_plan_scope({"title": "lithium niobate plasma etching"}, fallback))

    def test_invalid_llm_plan_falls_back(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = self._settings(root)
        fake = FakeClient({"query_variants": []})
        with patch("literature_downloader.search_planner.LLMJsonClient.from_environment", return_value=fake):
            plan = create_search_plan("InP 干法刻蚀", config=config)
        self.assertFalse(plan["llm"]["used"])
        self.assertIn("回退", plan["llm"]["reason"])
        self.assertTrue(plan["query_variants"])

    def test_relevance_judgement_cannot_change_source_fields(self) -> None:
        root = Path(tempfile.mkdtemp())
        config = self._settings(root)
        records = [
            {"title": "InP plasma etching", "abstract": "InP plasma etching study", "doi": "10.1000/real", "relevance_score": 8},
            {"title": "Unrelated finance study", "abstract": "finance", "doi": "10.1000/other", "relevance_score": 1},
        ]
        plan = {"llm": {"used": True}, "inclusion_criteria": ["direct topic"], "exclusion_criteria": ["unrelated"]}
        fake = FakeClient({
            "judgements": [
                {"index": 0, "relevance_score": 0.95, "included": True, "reason": "direct match"},
                {"index": 1, "relevance_score": 0.01, "included": False, "reason": "unrelated"},
            ]
        })
        with patch("literature_downloader.relevance_ranker.LLMJsonClient.from_environment", return_value=fake):
            ranked, meta = rank_candidates(records, topic="InP plasma etching", plan=plan, config=config)
        self.assertTrue(meta["used"])
        self.assertEqual(ranked[0]["doi"], "10.1000/real")
        self.assertEqual(ranked[0]["relevance_method"], "llm")
        self.assertFalse(ranked[1]["llm_included"])
        self.assertEqual(ranked[1]["doi"], "10.1000/other")

    def test_relevance_metadata_persists_in_database(self) -> None:
        root = Path(tempfile.mkdtemp())
        db = Database(root / "literature.db")
        task_id = db.create_task("topic", 1)
        paper_id = db.upsert_paper(task_id, {
            "title": "A paper", "doi": "10.1000/a", "provider": "OpenAlex",
            "relevance_method": "llm", "llm_included": True,
            "llm_relevance_score": 0.9, "relevance_reason": "direct match",
            "source_record": {"title": "A paper", "source": "api"},
        })
        paper = db.get_paper(paper_id)
        self.assertIsNotNone(paper)
        self.assertEqual(paper["relevance_method"], "llm")
        self.assertTrue(paper["llm_included"])
        self.assertEqual(paper["source_record"]["source"], "api")


if __name__ == "__main__":
    unittest.main()
