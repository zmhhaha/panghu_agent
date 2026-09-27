"""共享任务运行时。"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .plugin import AgentPlugin
from .rag import get_reference
from .registry import AgentDefinition, AgentRegistry
from .task_store import TaskStore


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
        for definition in definitions:
            store = TaskStore(definition.service_name)
            store.initialize()
            store.clear_stale_tasks()
            self._stores[definition.slug] = store
            self._plugins[definition.slug] = registry.create_plugin(definition)
            self._slots[definition.slug] = threading.BoundedSemaphore(definition.concurrency)

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
            raise UserTaskRunningError("上一个任务还在处理，请稍候。")
        cached = store.find_cached(text)
        if cached:
            return Submission("cached", text, "done", report=cached, cached=True)

        slot = self._slots[definition.slug]
        if not slot.acquire(blocking=False):
            raise AgentBusyError("当前请求较多，请稍后再试。")
        task_id = ""
        try:
            task_id = store.create_task(text, user_id)
            self._executor.submit(self._run, definition, task_id, text, slot)
        except Exception as error:
            if task_id:
                store.update_task(task_id, status="failed", error=str(error))
            slot.release()
            raise
        return Submission(task_id, text, "pending")

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
