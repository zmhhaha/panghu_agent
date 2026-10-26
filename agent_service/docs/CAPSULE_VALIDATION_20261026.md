# 按任务运行层验证

日期：2026-10-26，Asia/Shanghai。分支：`codex/agent-execution-service`。
本轮在 `111a78b` 同步 main 后实现 K8s provider、Worker 事件汇入、停止恢复与 Pi
宿主候选。首批中立 API 的历史证据保留在 [VALIDATION_20261026.md](VALIDATION_20261026.md)。

## 最终结果

| 范围 | 结果 |
| --- | --- |
| Linux 最终完整 `tests/agent_service`，含真实 PostgreSQL | 456 通过，0 失败、错误或跳过；pytest 37.779秒 |
| Linux root/PID1 私有容器内核探针 | 10 项检查全部通过；生产验收标志为 false |
| Windows 修复后的 Pod manifest 定向复核 | 48 通过；2.77秒 |

完整 Linux 结果是最终代码的权威离线回归。PG 的10项测试确实连接一次性 PostgreSQL，
覆盖跨连接幂等、跨进程领取/创建 CAS、持久 UID、事件事务/游标、取消/租约、隔离
收尾，以及未创建任务收尾与迟到创建的竞态。上述测试范围不可相加为一个总数。
两种 engine 的 Worker 协议集成使用显式 HTTP fixture，不调用真实供应商。

Windows 本轮完整运行在 Pod 测试模板尚未填写 UID 范围时得到388通过、19跳过、
48项相同的 setup 错误，以及28个 subtests 通过。错误均来自模板缺少启用池必填的
UID 范围，已补齐该 fixture 并定向复核48项全部通过。最终 Linux 快照包含这一修复
和新增的第10项 PG 测试；不把各轮 Windows 结果合并宣称为一次全套通过。

## 已验证的运行层行为

- 每 attempt 固定 Pod/Secret 名称和创建意图，分别以持久 CAS 领取 Pod 创建与原生
  Worker 接收权。丢失响应只检查原资源，不重放不确定的创建或模型执行。
- 两个连续任务使用不同 Pod UID、运行 UID、私有认证和工作目录。UID 在数据库中
  冻结且永不复用，池范围耗尽提前报不可用，已排队任务不会卡住全局槽。
- 领取事务冻结静态/K8s provisioning。修改当前部署类型不能接管原静态执行；
  领取后、资源意图写入前的崩溃，可在有效租约内为原 attempt 完成首次分配。
- 根本未领取创建权时，收尾与创建 CAS 使用同一事务锁；未创建计划先退休，禁止
  迟到创建。已领取创建权且资源不可见仍保持隔离，`404` 不构成停止证据。
- 严格测量实际 Pod 的命令、参数、环境、镜像、权限、挂载及资源限制，允许无害
  API 默认字段和资源数量格式，拒绝额外执行入口、权限、挂载或容器。
- 先认证并持久保存候选结果，再带精确 UID 请求终止。全部容器具备有效停止记录
  后才清理 finalizer/凭据并交付；未知、缺失、未来时间或被替换的资源不能放行。
- 候选保存、Pod 删除、finalizer/Secret 清理、资源退休或终态响应丢失均有恢复覆盖。
  隔离任务只有原资源停止且退休后才释放槽，不导出隔离前候选，也不启动替代执行。
- Worker 事件按精确 attempt/请求摘要、连续序号和允许字段汇入；事件与持久 cursor
  同事务提交。重连去重，事件空间饱和仍推进游标，保留终态事件空间。
- Pi 宿主要求可信 scope、显式 Responses 协议、固定 CLI/资源、独立操作员验收证据
  和短期任务模型授权。缺少这些条件时不提供原生 readiness。验收布尔值严格校验。
- Kubernetes transport 使用固定 in-cluster TLS、轮换 SA token、有界 JSON 和固定
  错误码，无环境代理或重定向。构建上下文排除业务包、配置和密钥，资源哈希固定，
  仅成功推送且核对 registry manifest 后记录可部署镜像摘要。

## 内核探针与发现的问题

探针运行于独立 Docker 容器：只读根、无网络、私有 PID namespace、受限 root caps、
NNP、256MiB 内存、64个 PID 上限和三个私有 tmpfs。只使用离线子进程 fixture，
没有启动 Pi CLI 或调用模型，不生成可用于开放生产的 acceptance 文件。

最终十项检查为：私有 kernel scope、Agent 降权、capabilities 清空、NNP、控制目录
不可读、观察到 setsid 后台 writer、停止前重开绑定、真实 pidfd 停止所有 UID writer、
停止后 heartbeat 不再变化，以及停止绑定可持久重开。

早期探针暴露了固定 UID10001 的 `execve` EAGAIN：`RLIMIT_NPROC` 按宿主 UID 计数，
私有 PID namespace 不隔离该计数。生产方案改为平台显式预留范围、每全局 fence
分配并持久冻结唯一 UID，启动/扫描/停止证明全部使用实际 UID。最终探针使用专用
fixture UID1000001，NPROC64与宿主默认一致；调高 fixture 限额本身不是生产修复。

另两项真实 Linux 单测曾发现失败 StopEvidence 缺少空资源列表，现已修复。复核还
修复 UID 耗尽和领取后未分配资源时的占槽问题，并加入冻结 provisioning，避免把
旧静态 Worker 缺少 K8s 记录误判为从未执行。

## 证据与复现

验证主机：`root@192.168.137.101`。最终快照目录：
`/root/agent-service-validation-20261026-r7/source/`，62个文件。
源文件清单 SHA-256：
`2eded6b373688baea8af05460de57aaa3c0cde487d0f233534622429129131ce`。
`source-hashes.json` 保存每个文件的 SHA-256；本文在验证后补写，不在该快照内。

| 证据 | 位置或身份 |
| --- | --- |
| 完整测试运行 | `agent-pg-20261026043441-56ee5a2d9893` |
| 测试报告及日志 | `/root/agent-service-validation-20261026-r7/linux-evidence/` |
| 内核探针运行 | `agent-host-bfbc7d84beff475a9fac6c21a818c5ff` |
| 探针报告及日志 | `/root/agent-service-validation-20261026-r7/host-evidence/` |
| 离线依赖镜像 | `arm-cluster-master:5000/ppt-service-site@sha256:96070732086f057bb9b653a889e134f481ecda90801352f8e143042f76a88f1e` |
| PostgreSQL 镜像 | `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368` |

完整套件使用 `--pull=never`、禁网、只读、UID10000、cap-drop ALL、NNP 和独立 Unix
socket；PG UID70，PGDATA 为 tmpfs。不挂载生产数据、模型凭据或私人 Agent HOME。
完整报告和内核报告均为 `passed=true`、`model_requests=0`、`cleanup_complete=true`。
本轮临时容器和 PG socket 已清除；源码和证据保留。未操作 Kubernetes 业务资源。

在已缓存上述镜像的 Linux/root Docker 主机，从 checkout 根目录复现完整套件：

```bash
python3 tests/agent_service/run_isolated_postgres.py \
  --code-root /absolute/path/to/checkout \
  --report-dir /absolute/path/to/new-report-directory \
  --suite all
```

内核探针入口为 `tests/agent_service/host_probe.py`，必须在受限的私有 PID1 容器里
运行，不能直接在宿主或生产 Worker 进程执行。完整构建及隔离条件见探针源码与
本轮保留的 host report；直接运行普通 pytest 不会执行该 root 探针。

## 尚未验收的边界

- Dockerfile/control 与 Pi donor 候选定义尚未实际 build/push；真实候选镜像、K8s
  创建/调度、CNI、DNS、网络阻断、Pod CIDR、资源上限仍须独立验收。
- Provider 只注入 Worker 控制认证。短期任务模型代理授权的签发/注入尚未接通，
  生产独立 Hermes 宿主尚未装配；不能仅打开 enabled 就开始真实模型执行。
- `workspace-task.v1` 当前仅接 prompt，`supports_inputs=false`；领域只读挂载、
  PPT preview 双 UID、artifact replay、业务引用删除和完整配额策略未完成。
- 预创建控制凭据 Secret 的清理为按身份尽力执行；API 故障或迟到写入留下的孤立
  Secret 需运维清理。已退休 UID 仍不能复用，Secret 不构成创建 Pod 的授权。
- 未调用真实 Pi/Hermes 模型、工具或图像能力；fixture 停止检查不替代候选镜像验收。
- PPT/科研 outbox 连接器、旧 UI/API 与业务发布门回归和 canary 尚未进行；线上仍
  使用原执行链。该服务的 completed 只表示候选交接。
