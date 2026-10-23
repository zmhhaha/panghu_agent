# P5 修复验证结果 — 2026-10-23

D1、D2、G1、G2 四项缺陷已修复，最终冻结来源的无模型验证全部通过。共有 **62 个独立集成场景**，另有 **45 项原有 API/身份/数据库/控制器回归**及独立数据库恢复探针。P5 总项仍待 P1/P2 就绪后的真实部署验收。

## 最终修复结果

| 运行 | 场景数 | 通过 | 失败 | 错误 | 跳过 | 耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Windows SQLite | 62 | 58 | 0 | 0 | 4 | 43.677 秒 |
| Linux SQLite | 62 | 58 | 0 | 0 | 4 | 33.844 秒 |
| Linux PostgreSQL | 62 | 62 | 0 | 0 | 0 | 60.431 秒 |
| 原有 pytest 回归（独立运行） | 45 | 45 | 0 | 0 | 0 | 7.853 秒 |

三个平台/数据库执行的是同一组 62 个场景，不能相加宣称 186 个独立场景；45 项原有回归另列，覆盖有重叠。SQLite 的 4 项跳过均为 PostgreSQL 锁与并发测试。PostgreSQL 轮的 3 项 SQLite 备份测试仍明确使用 SQLite，11 项引擎边界测试以文件/运行时契约为主。没有 expected-failure；最终各阶段退出码均为 0。

场景组成：workflow 7、recovery 15、security 10、PostgreSQL concurrency 4、SQLite backup 3、engine boundary 11、idempotency authorization 5、SSE pagination 7。

- [Windows 逐项结果](evidence/20261023-fixes/windows.json)与[日志](evidence/20261023-fixes/windows.log)
- [Linux SQLite 逐项结果](evidence/20261023-fixes/sqlite.json)与[日志](evidence/20261023-fixes/sqlite.log)
- [Linux PostgreSQL 逐项结果](evidence/20261023-fixes/postgres.json)与[日志](evidence/20261023-fixes/postgres.log)
- [45 项 pytest 回归 XML](evidence/20261023-fixes/regression.xml)与[日志](evidence/20261023-fixes/regression.log)
- [各阶段退出码](evidence/20261023-fixes/status.json)

## 四项修复

| 编号 | 实现及验证后的行为 |
| --- | --- |
| P5-D1 | `Repository.idempotent` 强制提供授权回调，在 admission/quota 锁之后、缓存读取及请求哈希比较之前重查并锁定资源。plans、confirm、generations、revisions、cancel 五种入口均接入：有效重放返回原响应且完整数据库元数据无变化；跨账号及删除后的相同/不同请求体均返回 404，避免缓存绕过撤权。 |
| P5-D2 | SSE 每页使用独立短事务，重查 owner、删除状态和保留窗口；终态仅在游标达到持久化末尾后关闭。积压页立即连续排空。覆盖 99/100/101/199/200/201/207 条边界、多页续传、事件 100 处受控断开再经 HTTP 重放、游标优先级、分页间删除和窗口推进；窗口过期发出 `snapshot.required`。 |
| P5-G1 | 仅对同一事务内新认领、尚未执行外部操作、无 job_uid/run_id 的任务，纯 attempt 构建失败可原子终结并释放槽及项目活动引用，让后续任务推进；保留取消/删除意图。旧 token/attempt/phase 或已附加执行者不能使用此路径，已有执行仍须真实停止证明；保持 `stopped=False`，不伪造停止。 |
| P5-G2 | `check_session_database` 使用 `contextlib.closing` 显式关闭 SQLite 连接，成功和校验异常都先关闭再删除临时目录。原有控制器关闭回归及完整 Windows 工作流均通过。 |

核心改动在 `server/repository.py`、`server/app.py`、`control/dispatcher.py`、`control/publisher.py`；配套修改 `tests/server/test_postgres.py`、`tests/control/test_control.py`，新增/扩展 e2e 授权、分页与恢复测试。打包器和 Linux runner 纳入上述原有回归，使用独立 `ppt_p5_regression` 库并分别记录结果。未改动其他作者的 Pi 主线实现。

## 独立恢复与真实边界

实际执行 `pg_dump -Fc`，从专属 `ppt_p5_backup` 恢复到新建 `ppt_p5_restore`，复制文件到新根目录并创建新 API 实例。**12 张表、27 个文件**的数据库记录、SHA-256、大小、权限与修改时间一致；JWT/跨账号 ACL、历史、SSE、幂等重放/冲突及恢复后的写入通过。

- [恢复核验 JSON](evidence/20261023-fixes/pg-backup/verification.json)
- [seed 日志](evidence/20261023-fixes/pg-backup-seed.log)与[恢复日志](evidence/20261023-fixes/pg-backup-verify.log)

验证使用真实 HTTP/JWT、数据库、控制器与文件存储；Worker/进程和 prepare 外部工具检查为明确标记的 synthetic 替身，历史 Version 为 ACL/恢复 fixture。SSE 分页间时序由直接迭代生成器控制，续传另经真实 HTTP 验证。这些结果不证明浏览器断网、真实 Pi/模型、官方导出、视觉审查、PowerPoint 或正式发布验收。恢复只验证停止合成 writer 后的静止快照，未覆盖生产并发备份与 PVC 快照。

## 来源、隔离与后续并行改动

基线 `panghu_agent` HEAD：`75ee2e699c5af71a337a5434c0b554defd4dbbf8`；整个 `ppt_service/` 为既有并行未跟踪开发树。保留其他作者的 README、Pi adapter 和科学 runtime 测试修改。

本轮冻结包包含 **79 个源码/测试文件**，SHA-256：`968aa4ddf2ff5c0c4cf7f6e978053bc0474f05fb4f312dbb77facd24df0b8614`。[逐文件清单](evidence/20261023-fixes/source-hashes.json)与[远端包哈希](evidence/20261023-fixes/source-tar.sha256)已保留；本地包为仓库根 `.research-temp/ppt-p5-fixes/linux-r1/source.tar`，远端目录为 `root@192.168.137.101:/root/ppt-service-p5-fixes-20261023-r1`。最终文档与证据整理发生在冻结之后，不追溯声称它们已打入源码包。

整理报告时逐项核对，79 项中仅 `control/pi_container.py` 出现后续漂移，且唯一变化是并行 Pi domain-smoke 工作将镜像 pin 从 `sha256:250b5cbd45beac1965d0d4023f0dd9842a93b6680498af38721130fb103e12b4` 更新为 `sha256:2a09398d26a387ac79b3b7b635202062ee7856f151b47eba0cb773b7e020e1a1`。本轮 P5 证据只对应冻结旧 pin；新镜像及其实际 CLI 验证由 Pi 主线另行记录，本报告不替它验收。四项修复源码无漂移。

测试使用既有 ARM64 API 镜像 `sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7`、PostgreSQL 镜像 `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368`，不下载依赖。容器禁网、API 非 root/只读根与源码/drop ALL/NNP/资源限额；数据库禁 TCP、tmpfs 数据和专属 Unix socket。未接业务库、模型密钥或已有服务。[镜像记录](evidence/20261023-fixes/images.txt)。

run ID `ppt-p5-20261023041555-1429366` 的专属容器和卷已全部清理，[cleanup.json](evidence/20261023-fixes/cleanup.json)为成功回执。模型请求数为 0；本次未部署生产服务、未修改宿主网络、未 apply/archive。

本轮首次 Windows discovery 在并行编辑期间导入旧的 queued 行为测试，而 Dispatcher 已实现失败终态，因此当次 57 项中有 1 项旧期望失败；[原始报告](evidence/20261023-fixes/windows.initial.json)保留。随后以协调后的最终代码完整重跑，结果为上表的 58 通过、4 个 PG 专属跳过。旧 r4 的实际缺陷与失败证据也原样保留如下，不能用它们描述当前修复状态。

## 尚待真实部署验收

P5 总项保持未勾选，当前四项代码缺陷已关闭；剩余部分依赖 P1/P2 和引擎主线就绪：真实 K8s Job/Pod、SSO/Ingress/代理、PVC 与数据库迁移/重启接管、浏览器 SSE 断网、实际进程崩溃/OOM/停止超时/存储满/网络策略，以及生产 Pi 双引擎路由和停止证明。官方导出、独立质量与视觉审查、PowerPoint 人工及正式发布门仍须各自验收。

## 历史首轮交付（r4，保留原文与失败证据）

以下为修复前的首轮报告。其“待修复”和“最终结果”等表述只描述历史 r4，当前状态以上述修复结果为准。

### P5 独立集成测试结果 — 2026-10-23

已交付 46 个无模型集成场景、隔离运行脚本和独立数据库恢复探针。最终 Linux 运行发现两项真实核心缺陷，P5 整项保持未完成；真实 Kubernetes/SSO/Pi 生产路径仍依赖主线接入。没有修改共享核心代码或放宽断言。

### 最终结果

| 运行 | 场景数 | 通过 | 失败 | 错误 | 跳过 | 耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Linux SQLite | 46 | 40 | 2 | 0 | 4 | 21.925 秒 |
| Linux PostgreSQL | 46 | 44 | 2 | 0 | 0 | 36.859 秒 |

两轮是同一组场景的不同数据库执行，不相加宣称 92 个独立场景。SQLite 的四项跳过全部为 PostgreSQL 行锁/咨询锁并发场景。PostgreSQL 轮中的三项 SQLite 备份测试仍明确使用 SQLite；11 项引擎测试验证文件和运行时契约。没有 expected-failure，两个套件均如实返回退出码 1。

- [SQLite 逐项结果与失败堆栈](evidence/20261023/evidence/sqlite.json)
- [PostgreSQL 逐项结果与失败堆栈](evidence/20261023/evidence/postgres.json)
- [SQLite 原始日志](evidence/20261023/evidence/sqlite.log)
- [PostgreSQL 原始日志](evidence/20261023/evidence/postgres.log)
- [各阶段退出码](evidence/20261023/evidence/status.json)

### 已验证的组合边界

7 项 workflow、11 项 recovery、10 项 security、4 项 PostgreSQL concurrency、3 项 SQLite backup、11 项 engine boundary。真实 API/JWT/CSRF/Repository、数据库事务、控制器/暂存/解析/检查点与发布拒绝门参与组合执行。

规划 Worker、执行进程和 prepare 外部工具检查为明确标记的 synthetic 替身。生成阶段走真实发布就绪拒绝门，保持 `gate_pending`，没有创建正式 Version。下载/预览/历史备份测试所种入的两代历史 Version 是专用 ACL fixture。引擎场景不运行 Pi 模型，不证明生产 Dispatcher 的完整双引擎路由。详细边界见 [README](README.md)。

四项 PostgreSQL 并发测试全部通过：同幂等键并发请求只产生一个 job/event；同修订号不同意图只有一个成功；跨项目提交仍受每用户队列上限约束；四条不同 PostgreSQL 连接只能有一条认领全局槽。用 `pg_backend_pid()` 确认连接独立。

### 独立恢复结果

实际执行 `pg_dump -Fc`，从专属 `ppt_p5_backup` 恢复到新库 `ppt_p5_restore`，文件复制到新的数据根目录，并创建新的 API/JWT 实例。**12 张表和 27 个文件**的记录、SHA-256、字节数、权限及修改时间核对一致；历史链、下载/预览、跨账号拒绝、幂等重放/冲突和恢复后写入通过。恢复目标为独立新库，原文件再次核对未变；未在恢复后再次连接源库对比记录。

- [PostgreSQL 恢复核验](evidence/20261023/evidence/pg-backup/verification.json)
- [seed 日志](evidence/20261023/evidence/pg-backup-seed.log)
- [恢复日志](evidence/20261023/evidence/pg-backup-verify.log)

这是停止所有合成 writer 后的静止快照恢复；不代表生产并发备份、PVC 快照或真实业务灾备验收。SQLite 另验证完整恢复、活动 writer 拒绝、篡改备份拒绝。

### 核心缺陷与接入请求

| 编号 | 复现与实际结果 | 期望与共享核心接入请求 |
| --- | --- | --- |
| P5-D1 | `test_deleted_project_cannot_replay_cached_write_response`：规划请求成功后删除项目，以原键和原正文重放仍返回缓存 202；SQLite/PG 均失败 | `server/repository.py` 的 `idempotent` 在缓存返回前校验当前资源存在/owner/deletion 状态；删除资源应 404。审查 plans/confirm/generations/revisions/cancel 各路由，避免只修一个接口或重新执行 action。应保留有效资源的原响应重放及 request-hash 冲突行为 |
| P5-D2 | `test_terminal_sse_drains_all_event_pages_before_closing`：终态有 107 条事件，只返回 1–100；SQLite/PG 均失败 | `server/app.py` SSE 必须先排空所有已持久化页，再因终态结束；从 Last-Event-ID 重连同样需完整排空，保持每批 ACL 与输出过滤 |
| P5-G1 | `test_missing_explicit_checkpoint_leaves_job_unclaimed` 证明拒绝启动且不占槽，但 `Dispatcher.tick()` 抛错后任务仍 queued | 共享控制器需定义有界失败隔离/可操作错误状态，避免损坏队列项反复阻断调度；禁止回退 latest。当前通过只说明拒绝策略生效 |
| P5-G2 | Windows 规划路径的 `check_session_database` SQLite 连接未显式关闭，删除临时数据库遇 `WinError 32` | 共享 Publisher 修复连接生命周期后补 Windows 回归；Linux 结果不证明 Windows 已恢复 |

本包只交付回归与合同请求，遵守已领取的 owned paths。共享 `server/`、`control/`、路由、迁移归主实现协调；没有把这些未修问题记为完成。

### 来源与隔离

基线 `panghu_agent` HEAD：`75ee2e699c5af71a337a5434c0b554defd4dbbf8`。`ppt_service/` 仍为并行开发未跟踪树，使用逐文件清单识别实际测试源码；保留 README、Pi adapter 与科学 runtime 测试的其他作者改动。

最终 tar SHA-256：`2677750b70431d85b233808c950f12ffa858e9d2d5532242f8fcc79aa71b47fb`，71 个源码/测试文件，本地与远端一致。[逐文件来源](evidence/20261023/source-hashes.json) 与 [远端 tar 哈希](evidence/20261023/evidence/source-tar.sha256) 已保留。新增 README/results/持久化打包助手是测试后交付文档/工具，不追溯声称包含在 r4 包中。并行作者的部分 control 模块在冻结后继续修改；本次结果只覆盖这份清单，不外推到后续工作树或新镜像。

API 镜像 `sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7`，PostgreSQL 镜像 `sha256:ef738a34a8651d11b2bace81c55c7e2187f786b484add6c83070884340074368`，均为本地已有 ARM64 镜像，无拉取。[镜像记录](evidence/20261023/evidence/images.txt)。API UID10000、RO root/source、drop ALL、NNP/资源限额、私有 tmp；数据库 UID70、tmpfs 数据、禁 TCP，专属 Unix socket。两者 network none，无业务 DB、模型 key、Docker socket 或宿主凭据挂入 API；仅 socket 初始化 helper 有 CHOWN 能力。

最终远端目录：`root@192.168.137.101:/root/ppt-service-p5-20261023-r4`。run ID `ppt-p5-20261023031557-1281518`；[清理回执](evidence/20261023/evidence/cleanup.json) 确认所有本次标签的容器和卷已移除，保留证据。数据库与其数据随专属容器销毁；未操作已有业务库、线上服务或网络策略。模型请求数为 0。

### 先前尝试与剩余验收

保留远端 `ppt-service-p5-20261022`、`-r2`、`-r3` 的失败/清理记录。首次及 r2 因官方 PostgreSQL entrypoint 强制默认 socket 路径而止于环境初始化；r3 已完成 SQLite 和 PG 恢复，但 PostgreSQL 套件因测试容器名称与数据库容器冲突未启动（退出 125）。修正挂载及命名后才执行上述 r4；不把初始化或脚本错误计作应用通过，之前真实失败未隐藏。

P5 总项保持未勾选：待 D1/D2 修复和 G1/G2 协调，随后接 P1/P2 验证真实 K8s/SSO/PVC/代理与浏览器断网、实际进程崩溃/OOM/停止超时/存储满/网络策略。Pi production 路由、完整引擎持久化与真实双 UID 停写证明仍未在本套件验收。官方导出/独立质量/视觉/PowerPoint 人工门仍须各自完成。本次不部署、不 apply、不 archive。
