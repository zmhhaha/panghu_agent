# 通用 Agent 服务首批验证

日期：2026-10-26，Asia/Shanghai。分支：`codex/agent-execution-service`。
基线：`panghu_agent/origin/main`，`64a61883587a5f5a55dfeb8dd6c4ced26ed34271`。

已验证中立 API、持久队列、Worker 交接和两种引擎驱动的实现合同。
本记录不代表生产 Worker 宿主、真实供应商模型或业务迁移已经验收。

## 结果

| 环境与范围 | 结果 |
| --- | --- |
| Linux 一次性隔离容器，完整 `tests/agent_service`，含真实 PostgreSQL | 154 通过，0 失败，0 错误，0 跳过；测试用时17.385秒 |
| Windows 完整 `tests/agent_service` | 146 通过，8 跳过，28 subtests 通过；61.09秒 |
| Windows 对齐 `workspace-task.v1` 默认名后的引擎/profile 定向复核 | 33 通过，28 subtests 通过，0 跳过；3.01秒 |

Windows 的8项跳过为6项未配置 PostgreSQL 和2项缺少符号链接权限；这8项在
Linux 完整套件中均已执行。Windows 另有一条 Starlette/httpx 弃用提示。
各行是独立测试范围，数量不能相加。最终 Linux 快照已包含名称对齐；其后只补文档。

## 已覆盖的行为

- 身份绑定 tenant/caller，跨所有者访问拒绝；profile 撤权后的历史读取拒绝及取消权限。
- 幂等提交、请求冲突、路由变化后重投、首次 deadline 和执行 pins 固定。
- 真实 PostgreSQL 的跨连接幂等、两个独立进程竞争单槽、取消/完成冲突、过期 lease
  隔离、输入/事件/产物持久化，以及缺少停止证据时保持占槽。共6项真实 PG 测试。
- 统一 API → dispatcher → Worker API → 候选产物下载的无模型交接，两种 engine
  采用相同业务请求。该层使用 TestClient/HTTP transport fixture、SQLite 及显式
  SyntheticDriver；它不伪装为真实 Pi/Hermes 的模型回合。
- PiDriver 使用实际中立 PiRuntime 启动离线 CLI fixture；检查空运行目录、固定
  工具、输入准备、显式 Responses 模型配置和无业务包依赖。
- HermesDriver 使用实际 loopback HTTP 协议 fixture；持久 admission 意图与 CAS、
  丢失付费接收响应不重复提交、继续查询原 run、取消、已知用量与未知费用。
- Worker 写盘成功后才启动/暴露状态，持久状态重启、控制租约失联、签名回执精确
  绑定、产物篡改拒绝、停止未知时 quarantine。
- 确认停止后若产物缺失、损坏、重复键或结构不符，返回 failed、不给产物并释放槽；
  无停止证据仍保持占槽。SQLite 连接关闭另有文件重命名回归覆盖。

## Linux 证据与复现

验证主机为 `root@192.168.137.101`。测试使用现有固定镜像，`--pull=never`，
无网络、只读根文件系统、`cap-drop ALL`、`no-new-privileges`；测试进程 UID/GID
为10000，PostgreSQL 为70。PGDATA 为 tmpfs，数据库仅通过独立 Unix socket 可达。
仅挂载本服务源码和测试，不挂载生产配置、数据库、Agent HOME 或模型凭据。

```bash
python3 tests/agent_service/run_isolated_postgres.py \
  --code-root /absolute/path/to/checkout \
  --report-dir /absolute/path/to/new-report-directory \
  --suite all
```

该命令要求 Linux 本机 Docker/root、两份固定镜像已缓存，以及一个尚不存在的报告
目录；不会拉取镜像、操作 Kubernetes 或删除源码/报告。镜像只是离线依赖环境，
不是待上线的 Agent Worker 候选镜像。

最终运行：`agent-pg-20261025170512-d05a353ad549`。
服务器证据位于 `/root/agent-service-validation-20261026-r3/linux-evidence/`：
`report.json`、`postgres-tests.log` 和 `postgres-container.log`。
服务器采用 UTC，run ID 的20261025与本文的上海日期对应。

源码快照位于 `/root/agent-service-validation-20261026-r3/source/`，35个文件；
上传工具输出的源文件清单摘要为
`06b0df16fe74687b8406fd76454d94c2bd9645f566d5dd9d103d84b25e4c28fc`。
对应 `source-hashes.json` 保留每个文件的 SHA-256；本文与后续纯文档补充不在该快照内。

| 镜像 | 固定身份 |
| --- | --- |
| 离线测试依赖 | `arm-cluster-master:5000/ppt-service-site@sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e` |
| PostgreSQL | `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368` |

最终报告：`passed=true`，`model_requests=0`，`cleanup_complete=true`；
本轮临时容器与 socket 目录均已清除，源码及报告保留。

Windows 全套命令（从 worktree 根目录）：

```powershell
python -m pytest tests/agent_service -q -p no:cacheprovider --basetemp=D:/github/armbianbegin/.runtime/agent-service-independent-final2 --tb=short
```

名称对齐后的定向命令：

```powershell
python -m pytest tests/agent_service/test_engines.py tests/agent_service/test_engine_profiles.py -q -p no:cacheprovider --tb=short
```

## 继续接入前的边界

- 当前 capsule 只执行一个 attempt；尚无按任务分配 K8s capsule、UID/资源池、固定
  Worker 镜像及独立观察全部 writer 的生产宿主。fixture 中停止证据来自受控模拟宿主。
- `WorkspaceTaskProfile.production_ready=False`，配置模板默认禁用；当前 Worker
  profile 仅接收 prompt，`supports_inputs=false`。上传接口已实现，领域文件挂载未接入。
- Worker 净化事件尚未通过持久 cursor 汇入业务流；没有业务 outbox、artifact replay、
  业务引用删除、真实模型/工具/图像验收或正式发布门验证。
- 现有 PPT/Hermes、科研 API 与服务部署未迁移。PPT 两次确认、版本和发布权限仍由
  业务拥有；通用 Job completed 只表示候选结果交接。

下一步按 OpenSpec 完成运行宿主与真实引擎验收，再接 PPT 连接器及其他业务。
