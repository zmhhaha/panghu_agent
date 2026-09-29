# 测试怎么跑

这些测试**不需要集群，也不需要真连 sqlite** —— 它们用 `monkeypatch` 替掉传输层，只检查
下发的 SQL 与 HTTP 行为。但它们需要 `pytest`，而仓库的部署环境里没有，于是**历史上从没跑过**。
2026-09-30 第一次真跑，是在开发机上建了个一次性 venv：

```bash
python -m venv /tmp/venv-test
/tmp/venv-test/bin/pip install pytest fastapi httpx2   # Windows 用 /tmp/venv-test/Scripts/pip
PYTHONPATH=. /tmp/venv-test/bin/python -m pytest tests/ -v
```

`PYTHONPATH=.` 是必需的：测试 import 的是 `baijiazhengming.*` 和 `tools.*`，都相对本目录。

2026-09-30 的结果（Python 3.13）：

| 文件 | 结果 |
| --- | --- |
| `test_baijiazhengming_framework.py` | **29 passed** —— 看门狗与 `RUNNING_TTL`、`update_task` 的终态保护、框架级鉴权，外加"每个人格都传 LLM 超时 / 都从同一个常量读温度" |
| `test_llm_config_guard.py` | 3 passed（三个仍在独立运行的服务的 API 用例）；9 条 `test_crew_modules_fail_before_constructing_llm` 需要 `crewai`（它们要 import 各人格的 crew），上面那个 venv 里没装 —— 装上才能全绿 |
| 其余文件 | 未跑（需要各自的依赖，如 `crewai`） |

两条约定，改测试时别破坏：

- **不要 import `baijiazhengming.api`**：它在 import 期就会 `TaskStore.initialize()` 并起调度线程，
  会去连真实的 sqlite。要测的东西都能直接构造（各测试文件头部都写了这一条）。
- 依赖缺失导致的失败要看清是**环境缺口**还是**真缺陷**：上面那 9 条属于前者，读错误信息里的
  模块名就能分辨（`ModuleNotFoundError: No module named 'crewai'`）。
