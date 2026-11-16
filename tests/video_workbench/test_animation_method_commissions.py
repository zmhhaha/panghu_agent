"""SQLite execution safety and developer continuation, without model calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import sqlite3
import threading

import pytest

from agent_service.contracts import ServiceError
from video_workbench.animation_method_commissions import AnimationMethodCommissionStore


OWNER = "developer"
MEDIA = {"sha256": "a" * 64, "duration_seconds": 13.5, "path": "/private/render/sample.mp4"}
SOURCE = {"engine": "html-svg", "files": {"scene.html": "<div>example</div>"}}


def document(**changes):
    return dict(goal="让观众看懂筛选改变群体和统计的因果关系", title="筛选表达实验",
        criteria=[{"id": "causality", "description": "统计变化能追踪到群体的变化"}],
        budget={"creator": 4, "review": 4, "render": 4, "seconds": 300}, **changes)


@pytest.fixture
def state(tmp_path):
    now = [1000.0]
    store = AnimationMethodCommissionStore(tmp_path / "commissions.db", clock=lambda: now[0])
    commission = store.create(OWNER, document(), "request-1")
    return store, commission["id"], now


def error(code):
    return pytest.raises(ServiceError, match="^" + code + "$")


def perform(store, commission_id, stage, key, result=None):
    operation = store.reserve(OWNER, commission_id, stage, {"fixture": key}, key)
    assert store.claim(OWNER, operation["id"])["submission_claimed"] is True
    return store.complete(OWNER, operation["id"], {} if result is None else result)


def candidate(store, commission_id, key, strategy, *, media=None):
    media = deepcopy(MEDIA if media is None else media)
    operation = perform(store, commission_id, "render", "render-" + key, {"media": media})
    return store.add_candidate(OWNER, commission_id, {
        "operation_id": operation["id"], "media": media,
        "source": deepcopy(SOURCE), "strategy": strategy,
    }, "candidate-" + key)


def review(store, commission_id, target, key, *, outcome="inconclusive", best=None):
    operation = perform(store, commission_id, "review", "review-" + key)
    return store.record_review(OWNER, commission_id, {
        "operation_id": operation["id"], "candidate_id": target["id"],
        "outcome": outcome, "best_candidate_id": best or target["id"],
        "remaining_issues": ["需要完整过程观察"], "evidence": {"audio_observed": False},
    }, key)


def test_create_replay_freezes_input_and_reads_never_advance(state):
    store, commission_id, _ = state
    before_events = store.events(OWNER, commission_id)
    request = document()
    original = store.create(OWNER, request, "request-1")
    request["criteria"][0]["description"] = "被调用者改变"
    original["frozen"]["criteria"].clear()
    assert store.get(OWNER, commission_id)["frozen"] == document()
    assert store.list(OWNER)[0]["id"] == commission_id
    assert store.pending() == [{"id": commission_id, "owner": OWNER, "state": "queued"}]
    assert store.get(OWNER, commission_id)["operations"] == []
    assert store.events(OWNER, commission_id) == before_events
    changed = document()
    changed["goal"] = "different"
    with error("animation_method_commission_idempotency_conflict"):
        store.create(OWNER, changed, "request-1")


def test_create_and_claim_are_atomic_across_independent_connections(state):
    store, commission_id, now = state
    barrier = threading.Barrier(6)

    def create_same(_):
        reopened = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
        barrier.wait()
        return reopened.create(OWNER, document(), "same-parallel")["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(create_same, range(6)))
    assert len(set(ids)) == 1
    operation = store.reserve(OWNER, commission_id, "creator", {"goal": "same"}, "op-1")
    barrier = threading.Barrier(6)

    def claim_same(_):
        reopened = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
        barrier.wait()
        return reopened.claim(OWNER, operation["id"])["submission_claimed"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(claim_same, range(6)))
    assert claims.count(True) == 1
    assert store.get(OWNER, commission_id)["usage"]["creator"] == 1


def test_unknown_submission_reopen_reconciles_original_id_and_budget(state):
    store, commission_id, now = state
    operation = store.reserve(OWNER, commission_id, "creator", {"input": "original"}, "original")
    assert store.claim(OWNER, operation["id"])["submission_claimed"]
    store.mark_unknown(OWNER, operation["id"])
    reopened = AnimationMethodCommissionStore(store.path, clock=lambda: now[0])
    assert reopened.claim(OWNER, operation["id"])["submission_claimed"] is False
    assert reopened.reserve(OWNER, commission_id, "creator", {"input": "original"}, "original")["id"] == operation["id"]
    with error("animation_method_operation_in_progress"):
        reopened.reserve(OWNER, commission_id, "creator", {"different": True}, "different")
    recovered = reopened.mark_submitted(OWNER, operation["id"], "original-agent-job")
    assert recovered["status"] == "submitted"
    reopened.complete(OWNER, operation["id"], {"result": "actual"})
    assert reopened.get(OWNER, commission_id)["usage"]["creator"] == 1
    with error("animation_method_operation_submission_conflict"):
        reopened.mark_submitted(OWNER, operation["id"], "replacement-job")


def test_request_keys_do_not_allow_new_inputs_or_stage_switch(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "creator", {"input": "original"}, "key")
    with error("animation_method_commission_idempotency_conflict"):
        store.reserve(OWNER, commission_id, "creator", {"input": "changed"}, "key")
    with error("animation_method_commission_idempotency_conflict"):
        store.reserve(OWNER, commission_id, "review", {"input": "original"}, "key")
    assert store.operation(OWNER, operation["id"])["status"] == "reserved"


def test_failed_and_cancelled_operations_still_consume_budget(state):
    store, commission_id, _ = state
    for index in range(4):
        operation = store.reserve(OWNER, commission_id, "creator", {"n": index}, str(index))
        store.claim(OWNER, operation["id"])
        store.fail(OWNER, operation["id"], "cancelled_attempt" if index == 1 else "failed_attempt",
                   cancelled=index == 1)
    with error("animation_method_commission_budget_exhausted"):
        store.reserve(OWNER, commission_id, "creator", {}, "excess")
    stopped = store.stop(OWNER, commission_id, "budget_exhausted",
                         {"remaining_issues": ["未得到可用作品"]})
    assert stopped["usage"]["creator"] == 4 and stopped["remaining"]["creator"] == 0
    assert stopped["state"] == "completed" and stopped["best_candidate_id"] is None


def test_only_never_claimed_reservation_can_release_its_budget(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "creator", {}, "unused")
    proof = {"reason": "validated before any external I/O"}
    store.release_unsubmitted(OWNER, operation["id"], proof)
    assert store.get(OWNER, commission_id)["usage"]["creator"] == 0
    assert store.release_unsubmitted(OWNER, operation["id"], proof)["status"] == "released"
    operation = store.reserve(OWNER, commission_id, "creator", {}, "claimed")
    store.claim(OWNER, operation["id"])
    with error("animation_method_operation_may_have_submitted"):
        store.release_unsubmitted(OWNER, operation["id"], proof)
    assert store.get(OWNER, commission_id)["usage"]["creator"] == 1


def test_owner_scope_applies_to_commissions_operations_and_continuation(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "creator", {}, "original")
    for call in (
        lambda: store.get("other", commission_id),
        lambda: store.events("other", commission_id),
        lambda: store.cancel("other", commission_id),
    ):
        with error("animation_method_commission_not_found"):
            call()
    for call in (
        lambda: store.claim("other", operation["id"]),
        lambda: store.operation("other", operation["id"]),
        lambda: store.complete("other", operation["id"], {}),
    ):
        with error("animation_method_operation_not_found"):
            call()
    with error("animation_method_commission_not_found"):
        store.create("other", document(base_commission_id=commission_id), "continue-other")
    assert store.list("other") == []


def test_deadline_is_checked_again_at_claim_and_replay_does_not_extend_it(state):
    store, commission_id, now = state
    operation = store.reserve(OWNER, commission_id, "creator", {}, "pending")
    deadline = store.get(OWNER, commission_id)["deadline_at"]
    now[0] = deadline
    with error("animation_method_commission_deadline_exceeded"):
        store.claim(OWNER, operation["id"])
    assert store.operation(OWNER, operation["id"])["status"] == "reserved"
    assert store.create(OWNER, document(), "request-1")["deadline_at"] == deadline
    store.release_unsubmitted(OWNER, operation["id"], {"reason": "deadline before claim"})
    assert store.stop(OWNER, commission_id, "budget_exhausted", "time limit")["state"] == "completed"


def test_cancel_in_flight_blocks_new_claims_and_preserves_reconciliation(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "creator", {}, "in-flight")
    store.claim(OWNER, operation["id"])
    cancelled = store.cancel(OWNER, commission_id)
    assert cancelled["state"] == "cancelling" and cancelled["cancel_requested"]
    assert store.claim(OWNER, operation["id"])["submission_claimed"] is False
    with error("animation_method_commission_stopped"):
        store.reserve(OWNER, commission_id, "render", {}, "new-work")
    with error("animation_method_operation_in_progress"):
        store.stop(OWNER, commission_id, "cancelled", "still active")
    store.mark_submitted(OWNER, operation["id"], "discovered-job")
    store.fail(OWNER, operation["id"], "stopped_by_host", cancelled=True)
    final = store.stop(OWNER, commission_id, "cancelled", "host proved stopped")
    events = store.events(OWNER, commission_id)
    assert store.cancel(OWNER, commission_id) == final
    assert store.pending() == []
    assert store.events(OWNER, commission_id) == events


def test_cancel_queued_is_terminal_and_idempotent(state):
    store, commission_id, _ = state
    final = store.cancel(OWNER, commission_id)
    assert final["state"] == "cancelled"
    assert store.cancel(OWNER, commission_id) == final
    with error("animation_method_commission_stopped"):
        store.reserve(OWNER, commission_id, "creator", {}, "new")


def test_candidates_need_actual_completed_render_and_freeze_media(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "render", {}, "render")
    body = {"operation_id": operation["id"], "media": deepcopy(MEDIA),
            "source": deepcopy(SOURCE), "strategy": "依次移出再归组"}
    with error("animation_method_candidate_render_required"):
        store.add_candidate(OWNER, commission_id, body, "candidate")
    store.claim(OWNER, operation["id"])
    store.complete(OWNER, operation["id"], {"media": deepcopy(MEDIA)})
    wrong = deepcopy(body)
    wrong["media"]["sha256"] = "b" * 64
    with error("animation_method_candidate_media_mismatch"):
        store.add_candidate(OWNER, commission_id, wrong, "wrong")
    result = store.add_candidate(OWNER, commission_id, body, "candidate")
    assert store.add_candidate(OWNER, commission_id, body, "candidate") == result
    with error("animation_method_candidate_render_already_imported"):
        store.add_candidate(OWNER, commission_id, body, "same-render-new-key")
    body["source"]["files"]["scene.html"] = "mutated"
    result["media"]["duration_seconds"] = 99
    saved = store.get(OWNER, commission_id)["candidates"][0]
    assert saved["source"] == SOURCE and saved["media"] == MEDIA


def test_review_does_not_automatically_replace_best_with_latest(state):
    store, commission_id, _ = state
    original = candidate(store, commission_id, "one", "整体布局")
    latest = candidate(store, commission_id, "two", "分组布局")
    row = review(store, commission_id, latest, "regressed", outcome="regressed", best=original["id"])
    assert store.get(OWNER, commission_id)["best_candidate_id"] == original["id"]
    assert store.record_review(OWNER, commission_id, {
        key: value for key, value in row.items()
        if key not in {"schema", "id", "commission_id", "created_at"}
    }, "regressed") == row
    with error("animation_method_commission_goal_evidence_required"):
        store.stop(OWNER, commission_id, "goal_met", "claim without evidence")


def test_stagnation_requires_distinct_attempts_and_reports_best_without_spending_rest(state):
    store, commission_id, _ = state
    original = candidate(store, commission_id, "one", "先移出再统计")
    review(store, commission_id, original, "one", outcome="equal")
    with error("animation_method_commission_stagnation_evidence_required"):
        store.stop(OWNER, commission_id, "no_progress", "one attempt")
    repeated = candidate(store, commission_id, "two", "先移出再统计")
    review(store, commission_id, repeated, "two", outcome="equal", best=original["id"])
    with error("animation_method_commission_stagnation_evidence_required"):
        store.stop(OWNER, commission_id, "no_progress", "same strategy twice")
    alternative = candidate(store, commission_id, "three", "先分组并显示对应统计")
    review(store, commission_id, alternative, "three", outcome="inconclusive", best=original["id"])
    summary = {"best_candidate_id": original["id"], "remaining_issues": ["因果关系仍不明确"],
               "reason": "两种不同策略均无可证收益"}
    final = store.stop(OWNER, commission_id, "no_progress", summary)
    assert final["state"] == "completed" and final["remaining"]["render"] == 1
    assert final["best_candidate_id"] == original["id"] and final["summary"] == summary
    before_events = store.events(OWNER, commission_id)
    assert store.stop(OWNER, commission_id, "no_progress", summary) == final
    assert store.events(OWNER, commission_id) == before_events
    with error("animation_method_commission_stopped"):
        store.reserve(OWNER, commission_id, "creator", {}, "automatic-next-commission")


def test_goal_met_and_explicit_later_commission_continue_frozen_best_candidate(state):
    store, commission_id, _ = state
    original = candidate(store, commission_id, "original", "用对象数量联动统计")
    review(store, commission_id, original, "accepted-goal", outcome="goal_met")
    old = store.stop(OWNER, commission_id, "goal_met", {"remaining_issues": []})
    newer = store.create(OWNER, document(base_commission_id=commission_id,
        feedback="保留因果结构，改善引导节奏"), "explicit-continuation")
    assert newer["id"] != commission_id and newer["usage"] == {"creator": 0, "review": 0, "render": 0}
    assert newer["frozen"]["base_candidate"] == original
    assert newer["frozen"]["base_candidate_id"] == original["id"]
    assert newer["best_candidate_id"] == original["id"]
    assert store.get(OWNER, commission_id) == old
    for index in range(5):
        later = store.create(OWNER, document(base_commission_id=commission_id),
                             "further-continuation-" + str(index))
        assert later["state"] == "queued"
    assert len(store.list(OWNER)) == 7


def test_no_budget_or_goal_stop_can_be_fabricated_without_evidence(state):
    store, commission_id, _ = state
    with error("animation_method_commission_budget_not_exhausted"):
        store.stop(OWNER, commission_id, "budget_exhausted", "none consumed")
    with error("animation_method_commission_goal_evidence_required"):
        store.stop(OWNER, commission_id, "goal_met", "no reviewer evidence")
    with error("animation_method_commission_stagnation_evidence_required"):
        store.stop(OWNER, commission_id, "no_progress", "no comparisons")
    assert store.stop(OWNER, commission_id, "developer_stopped",
                      {"reason": "developer ended this experiment"})["state"] == "completed"


def test_terminal_result_replay_never_reopens_or_changes_operation(state):
    store, commission_id, _ = state
    operation = perform(store, commission_id, "creator", "initial", {"result": "frozen"})
    store.stop(OWNER, commission_id, "developer_stopped", "sufficient")
    events = store.events(OWNER, commission_id)
    assert store.complete(OWNER, operation["id"], {"result": "frozen"}) == operation
    assert store.claim(OWNER, operation["id"])["submission_claimed"] is False
    assert store.reserve(OWNER, commission_id, "creator", {"fixture": "initial"}, "initial") == operation
    with error("animation_method_operation_result_conflict"):
        store.complete(OWNER, operation["id"], {"result": "different"})
    assert store.events(OWNER, commission_id) == events


def test_database_isolation_rejects_using_business_or_production_round_database(tmp_path):
    target = tmp_path / "business.db"
    with sqlite3.connect(target) as db:
        db.execute("CREATE TABLE projects(id TEXT)")
    with error("animation_method_commission_database_not_isolated"):
        AnimationMethodCommissionStore(target)


def test_transfer_freezes_material_changes_and_supports_non_eight_second_passage(state):
    store, _, _ = state
    value = document(role="transfer", duration_seconds=37.5,
        baseline_demonstration_id="original-demo", method_versions=["method-v2"],
        structural_changes={"object_count": {"before": 5, "after": 12},
                            "relations": {"before": "chain", "after": "branch"}})
    result = store.create(OWNER, value, "transfer")
    assert result["frozen"] == value
    assert result["frozen"]["duration_seconds"] == 37.5
    assert store.get(OWNER, result["id"])["usage"] == {"creator": 0, "review": 0, "render": 0}


@pytest.mark.parametrize("changes", [
    None, {}, {"labels": {"before": "A", "after": "B"}},
    {"domain": {"before": "finance", "after": "biology"}},
    {"object_count": {"before": 5, "after": 5}},
])
def test_transfer_cannot_be_only_renamed_content_or_unchanged_structure(state, changes):
    store, _, _ = state
    with error("animation_method_commission_structural_change_required"):
        store.create(OWNER, document(role="transfer", structural_changes=changes), "bad-transfer")


def test_a_recent_improvement_prevents_stale_stagnation_stop(state):
    store, commission_id, _ = state
    for index, strategy in enumerate(("分组后联动", "逐个迁移", "移动中更新统计")):
        result = candidate(store, commission_id, str(index), strategy)
        review(store, commission_id, result, str(index),
               outcome="improved" if index == 2 else "equal")
    with error("animation_method_commission_stagnation_evidence_required"):
        store.stop(OWNER, commission_id, "no_progress", "old comparisons are stale")


def test_continuation_can_keep_old_baseline_when_new_attempt_regresses(state):
    store, commission_id, _ = state
    baseline = candidate(store, commission_id, "baseline", "完整因果展开")
    store.stop(OWNER, commission_id, "developer_stopped", "start from this version later")
    next_run = store.create(OWNER, document(base_commission_id=commission_id), "continue-baseline")
    newer = candidate(store, next_run["id"], "newer", "同时展开全部信息")
    operation = perform(store, next_run["id"], "review", "regression")
    store.record_review(OWNER, next_run["id"], {
        "operation_id": operation["id"], "candidate_id": newer["id"],
        "compared_candidate_id": baseline["id"], "best_candidate_id": baseline["id"],
        "outcome": "regressed", "remaining_issues": ["信息过载"],
    }, "regression")
    finished = store.stop(OWNER, next_run["id"], "developer_stopped", "retain original")
    assert finished["best_candidate_id"] == baseline["id"]
    assert len(finished["candidates"]) == 1
    again = store.create(OWNER, document(base_commission_id=next_run["id"]), "continue-again")
    assert again["frozen"]["base_candidate"] == baseline


def test_operation_frozen_input_corruption_is_detected_before_claim(state):
    store, commission_id, _ = state
    operation = store.reserve(OWNER, commission_id, "creator", {"mode": "plan"}, "plan")
    import json
    with sqlite3.connect(store.path) as db:
        operation["input"]["mode"] = "create"
        db.execute("UPDATE animation_method_commission_operations SET document=? WHERE id=?",
                   (json.dumps(operation), operation["id"]))
    with error("animation_method_operation_frozen_input_changed"):
        store.claim(OWNER, operation["id"])


@pytest.mark.parametrize("budget", [
    {"creator": True, "review": 1, "render": 1, "seconds": 10},
    {"creator": 0, "review": 1, "render": 1, "seconds": 10},
    {"creator": 100, "review": 1, "render": 1, "seconds": 10},
    {"creator": 1, "review": 1, "render": 1, "seconds": float("inf")},
    {"creator": 1, "review": 1, "render": 1, "seconds": 10, "whole_video_rounds": 2},
])
def test_invalid_or_production_quota_budget_is_rejected(state, budget):
    store, _, _ = state
    value = document()
    value["budget"] = budget
    with error("animation_method_commission_budget_invalid"):
        store.create(OWNER, value, "invalid-budget")
