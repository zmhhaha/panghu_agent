"""共享任务运行时。"""
from __future__ import annotations

import collections
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .plugin import AgentPlugin
from .rag import get_reference
from .registry import AgentDefinition, AgentRegistry
from .task_store import TaskStore

# 队列轮询间隔：入队时会 set 事件立刻唤醒，这个只是兜底
QUEUE_POLL_SECONDS = float(os.getenv("BAIJIA_QUEUE_POLL", "1"))
# 排队上限：超过就置为 failed，不留悬空任务。与 UI 的轮询上限（MAX_WAIT=600）对齐
QUEUE_WAIT_SECONDS = float(os.getenv("BAIJIA_QUEUE_WAIT", "600"))


class AgentBusyError(RuntimeError):
    pass


class UserTaskRunningError(RuntimeError):
    pass


@dataclass(frozen=True)
class Submission:
    task_id: str
    text: str
    status: str
    report: str | None = None
    cached: bool = False


@dataclass(frozen=True)
class _Queued:
    task_id: str
    text: str
    enqueued_at: float


class AgentRuntime:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        definitions = registry.list_enabled()
        worker_default = max(1, min(sum(item.concurrency for item in definitions), 16))
        worker_count = int(os.getenv("BAIJIA_WORKERS", str(worker_default)))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(worker_count, 32)),
            thread_name_prefix="baijia",
        )
        self._stores: dict[str, TaskStore] = {}
        self._plugins: dict[str, AgentPlugin] = {}
        self._slots: dict[str, threading.BoundedSemaphore] = {}
        # 并发满时排队而不是直接 429：受理立刻返回，由调度器在有槽位时领取。
        # 抢槽位必须发生在提交给线程池**之前**，否则 worker 会被"等某个 agent 的槽位"
        # 占满，把别的 agent 饿死。
        self._queues: dict[str, collections.deque[_Queued]] = {}
        self._queue_lock = threading.Lock()
        self._queue_event = threading.Event()
        self._dispatcher = threading.Thread(
            target=self._dispatch_loop, name="baijia-dispatch", daemon=True
        )
        for definition in definitions:
            store = TaskStore(definition.service_name)
            store.initialize()
            store.clear_stale_tasks()
            self._stores[definition.slug] = store
            self._plugins[definition.slug] = registry.create_plugin(definition)
            self._slots[definition.slug] = threading.BoundedSemaphore(definition.concurrency)
            self._queues[definition.slug] = collections.deque()
        self._dispatcher.start()

    def configuration_error(self, definition: AgentDefinition) -> str | None:
        return self._plugins[definition.slug].configuration_error()

    def submit(self, definition: AgentDefinition, text: str, user_id: str = "") -> Submission:
        text = text.strip()
        if not text:
            raise ValueError("输入内容不能为空")
        if len(text) > definition.max_input_length:
            raise ValueError(f"输入内容不能超过 {definition.max_input_length} 个字符")
        config_error = self.configuration_error(definition)
        if config_error:
            raise RuntimeError(config_error)

        store = self._stores[definition.slug]
        running = store.get_running_task(user_id)
        if running:
            raise UserTaskRunningError(definition.busy_message)
        cached = store.find_cached(text)
        if cached:
            return Submission("cached", text, "done", report=cached, cached=True)

        # 受理立刻返回：建一条 pending 任务并入队，由调度器在有槽位时领取。
        # 在这里抢槽位会阻塞 HTTP 请求线程 —— 而 UI 的提交请求只等 10 秒（ui.py），
        # 于是用户看不到"等待中"，只会看到"请求失败"。
        task_id = store.create_task(text, user_id)
        try:
            with self._queue_lock:
                self._queues[definition.slug].append(_Queued(task_id, text, time.monotonic()))
            # 事件只是唤醒调度器；入队与抢槽位之间没有竞态需要靠锁保护
            self._queue_event.set()
        except Exception as error:
            store.update_task(task_id, status="failed", error=str(error))
            raise
        return Submission(task_id, text, "pending")

    def queue_depth(self, definition: AgentDefinition) -> int:
        """还没轮到执行的任务数，给 /health 用（也是将来决定要不要扩容的信号）。"""
        with self._queue_lock:
            return len(self._queues.get(definition.slug, ()))

    def _dispatch_loop(self) -> None:
        """调度器：把排队的任务在有槽位时交给线程池。

        只有一个线程做领取，所以不需要在任务存储上做原子认领 —— 代价是**扩容前必须
        先补上原子认领**（两个副本会重复执行同一个任务）。
        """
        while True:
            try:
                self._dispatch_once()
            except Exception as error:  # noqa: BLE001 —— 调度器绝不能死
                print(f"[baijia] dispatch loop error: {type(error).__name__}: {error}", file=sys.stderr)
            self._queue_event.wait(timeout=QUEUE_POLL_SECONDS)
            self._queue_event.clear()

    def _dispatch_once(self) -> None:
        now = time.monotonic()
        for definition in self.registry.list_enabled():
            queue = self._queues[definition.slug]
            slot = self._slots[definition.slug]
            while queue:
                if now - queue[0].enqueued_at > QUEUE_WAIT_SECONDS:
                    # 排队超上限：置为 failed 并说明原因，不留悬空任务
                    expired = queue.popleft()
                    self._stores[definition.slug].update_task(
                        expired.task_id,
                        status="failed",
                        error=f"排队超过 {int(QUEUE_WAIT_SECONDS)} 秒仍未轮到，请重新提交。",
                    )
                    continue
                if not slot.acquire(blocking=False):
                    break
                item = queue.popleft()
                self._executor.submit(self._run, definition, item.task_id, item.text, slot)

    def get_task(self, definition: AgentDefinition, task_id: str) -> dict | None:
        return self._stores[definition.slug].get_task(task_id)

    def _run(
        self,
        definition: AgentDefinition,
        task_id: str,
        text: str,
        slot: threading.BoundedSemaphore,
    ) -> None:
        store = self._stores[definition.slug]
        try:
            store.update_task(task_id, status="running")
            reference = get_reference(definition, text)
            result = self._plugins[definition.slug].run(text, reference).content.strip()
            if len(result) > definition.max_output_length:
                result = result[: definition.max_output_length].rstrip() + "…"
            store.update_task(task_id, status="done", report=result)
            try:
                store.save_report(task_id, text, result)
            except Exception as error:  # 报告缓存失败不能把已完成的回答改成失败。
                print(f"[baijia] save report failed for {definition.slug}: {error}")
        except Exception as error:
            store.update_task(task_id, status="failed", error=str(error))
        finally:
            slot.release()
