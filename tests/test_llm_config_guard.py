import importlib
import sys

import pytest
from fastapi.testclient import TestClient

from tools import llm_config
from tools import sqlite_client as db


API_CASES = [
    ("app.api.daofaziran_agent", "/daofaziran_agent-health", "/daofaziran_agent", {"text": "test"}),
    ("app.api.fofawubian_agent", "/fofawubian_agent-health", "/fofawubian_agent", {"text": "test"}),
    ("app.api.zhongkuifumo_agent", "/zhongkuifumo_agent-health", "/zhongkuifumo_agent", {"text": "test"}),
    ("app.api.yimaneili_agent", "/yimaneili_agent-health", "/yimaneili_agent", {"text": "test"}),
    ("app.api.zhenzhuzhida_agent", "/zhenzhuzhida_agent-health", "/zhenzhuzhida_agent", {"text": "test"}),
    ("app.api.research_agent", "/health", "/research", {"topic": "test"}),
    (
        "app.api.scientific_agent",
        "/scientific-health",
        "/scientific-research",
        {"topic": "test"},
    ),
    ("app.api.game_review_agent", "/health", "/game_review", {"game_url": "http://test"}),
    (
        "app.api.zhougongjiemeng_agent",
        "/zhougongjiemeng_agent-health",
        "/zhougongjiemeng_agent",
        {"text": "test"},
    ),
    (
        "app.api.xiaotanrenjian_agent",
        "/xiaotanrenjian_agent-health",
        "/xiaotanrenjian_agent",
        {"text": "test"},
    ),
]


CREW_MODULES = [
    "daofaziran_agent.crew",
    "fofawubian_agent.crew",
    "zhongkuifumo_agent.crew",
    "yimaneili_agent.crew",
    "zhenzhuzhida_agent.crew",
    "research_agent.crew",
    "scientific_agent.crew",
    "game_review_agent.llm_config",
    "zhougongjiemeng_agent.crew",
    "xiaotanrenjian_agent.crew",
]


def _configure_missing_deepseek_key(monkeypatch):
    monkeypatch.setenv("PROVIDER", "deepseek")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_shared_guard_rejects_missing_provider_key(monkeypatch):
    _configure_missing_deepseek_key(monkeypatch)

    error = llm_config.get_llm_config_error("test_agent")

    assert error is not None
    assert "DEEPSEEK_API_KEY" in error
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        llm_config.require_llm_config("test_agent")


def test_custom_provider_allows_keyless_local_endpoint(monkeypatch):
    monkeypatch.setenv("PROVIDER", "custom")
    monkeypatch.delenv("CUSTOM_API_KEY", raising=False)

    assert llm_config.get_llm_config_error("test_agent") is None


@pytest.mark.parametrize("module_name", CREW_MODULES)
def test_crew_modules_fail_before_constructing_llm(monkeypatch, module_name):
    _configure_missing_deepseek_key(monkeypatch)
    sys.modules.pop(module_name, None)

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        importlib.import_module(module_name)


@pytest.mark.parametrize(
    "module_name,health_path,submit_path,payload",
    API_CASES,
)
def test_api_reports_degraded_and_rejects_submission(
    monkeypatch,
    tmp_path,
    module_name,
    health_path,
    submit_path,
    payload,
):
    _configure_missing_deepseek_key(monkeypatch)
    monkeypatch.setenv("GAME_OUT_DIR", str(tmp_path / "game-review"))
    monkeypatch.setattr(db, "init_db", lambda service: db.set_service(service))
    monkeypatch.setattr(db, "clear_stale_tasks", lambda: None)
    sys.modules.pop(module_name, None)

    module = importlib.import_module(module_name)
    client = TestClient(module.app)

    health_response = client.get(health_path)
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "degraded"
    assert health_response.json()["llm_configured"] is False

    submit_response = client.post(submit_path, json=payload)
    assert submit_response.status_code == 503
    assert "DEEPSEEK_API_KEY" in submit_response.json()["detail"]
