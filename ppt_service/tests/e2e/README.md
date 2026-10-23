# P5：无模型集成与恢复测试

这组测试组合运行 PPT 服务的 HTTP 接口、身份校验、数据库、控制器与文件存储，用来发现单个模块测试不容易暴露的状态、权限和恢复问题。它不请求模型，也不产出可用于验收的正式演示文稿。各次运行的实际结果、失败堆栈和来源摘要见同目录的 `results.md` 及其指向的证据文件；本说明不代替运行报告。

## 验证范围与替身边界

| 部分 | 实际运行的内容 | 测试边界 |
| --- | --- | --- |
| 身份与 HTTP | `create_app`、真实 RS256 JWT 签名和验证、issuer/sub/audience、来源与 CSRF 校验、FastAPI `TestClient` 请求 | RSA 密钥临时生成，JWKS 查询使用本地提供器；不访问真实 Casdoor，不覆盖浏览器、Ingress 或代理转发 |
| 数据与状态 | 真实迁移、Repository、SQLite/PostgreSQL、幂等请求、项目修订号、任务与事件记录 | 每例临时文件根目录；PostgreSQL 每例独立 schema |
| 控制与检查点 | 真实 Dispatcher、Stager、解析与来源投影、检查点收集、Publisher 和发布就绪判定 | `FakeWorker` 只读取实际暂存的任务并写入标明为 synthetic 的产物；不启动模型进程或真实执行容器 |
| prepare 与生成门禁 | 两次真实 HTTP 显式确认、prepare 检查点、后续生成的发布门禁 | prepare 的外部工具检查由明确标记的测试替身模拟；无图表来源使用独立声明的空图表 oracle；这些记录不代表官方工具或 PowerPoint 验收。生成仍应保持门禁待验收，不得晋升正式 Version |
| 历史版本权限与恢复 | 项目/任务/SSE/下载/预览的跨账号隔离，历史记录与文件恢复 | `seed_synthetic_historical_version` 直接种入 **ACL/备份测试专用**历史 Version 和文件，含显式 fixture 标记；不证明发布流程通过 |
| Pi 引擎边界 | 实际 Pi 检查点重放、暂存、prepared profile/runtime 准入与错误拒绝 | 不执行 Pi 模型；不覆盖生产 Dispatcher 的完整双引擎路由 |

`FakeSandbox` 和 `FakeWorker` 是进程边界替身。取消、停止证明、租约与 fencing token 的测试使用它们制造可重现的时序，再检查真实控制器、数据库和文件状态。它们不能证明 Linux 容器、网络策略或操作系统杀进程行为。

## 测试文件

- `test_workflow.py`：上传、两次确认、prepare 检查点、生成门禁、幂等冲突、素材变更与来源校验。
- `test_recovery.py`：租约接管、暂存后崩溃、取消与晚到结果、显式检查点选择、损坏检查点、权限审批阻塞和删除等待停止。
- `test_security.py`：JWT/代理信任/CSRF、跨用户与跨 issuer 访问、删除撤权、SSE 游标/过滤/分页、用量去重。
- `test_idempotency_authorization.py`：plans、confirm、generations、revisions、cancel 五种写入的缓存重放授权；验证有效重试不重复副作用、跨账号拒绝，以及删除后相同或更改请求体均返回 404。计划和历史 Version 是明确标记的 ACL fixture。
- `test_sse_pagination.py`：终态与运行中任务的整页/非整页事件续传、游标优先级、分页间删除撤权与保留窗口推进；HTTP 验证和直接迭代生成器的时序测试分别标明，后者不代表浏览器断网验收。
- `test_postgres_concurrency.py`：并发 HTTP 幂等提交、同修订号竞争、用户队列上限和多数据库连接争抢全局执行槽。SQLite 下明确跳过。
- `test_backup_restore.py`：静止状态的 SQLite 数据库与文件备份、新实例恢复、哈希/元数据/历史/权限/幂等校验；拒绝活动写入和被篡改的备份。
- `test_engine_boundaries.py`：Pi 引擎、profile、运行时固定值、检查点格式及暂存后篡改的拒绝，停止证明与旧执行结果隔离。

## 本机运行

工作目录为仓库内的 `panghu_agent`，使用已安装服务依赖的 Python 环境。运行器使用 `unittest`，无需启动 Web 服务或配置模型密钥。

SQLite 是默认后端。PowerShell 示例：

```powershell
Set-Location D:\github\armbianbegin\panghu_agent
Remove-Item Env:PPT_E2E_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:PPT_E2E_TEST_ONLY -ErrorAction SilentlyContinue
python -m ppt_service.tests.e2e.run_suite --report .research-temp/ppt-p5/sqlite.json
```

`run_suite.py` 输出每例结果，并保存 JSON：测试标识、失败/错误堆栈、跳过原因、耗时、数据库与平台。真实失败使退出码为非零；不使用 expected-failure 掩盖契约缺陷。PostgreSQL 并发测试在 SQLite 下跳过是明确的覆盖限制。

Windows 下曾发现 `control/publisher.py` 的 `check_session_database` 未显式关闭 SQLite 连接，使临时目录删除发生 `WinError 32`。本轮已使用 `contextlib.closing` 保证校验成功和异常路径都先关闭连接，并补上连接关闭回归；历史失败仍保留在原证据中。本机运行验证 Windows 文件句柄行为，Linux 冻结来源运行验证目标平台；两者应按各自来源清单和报告判断。

### 独立 PostgreSQL

仅连接专门创建、可丢弃的测试数据库。例如在隔离环境中运行：

```bash
export PPT_E2E_TEST_ONLY=isolated-p5
export PPT_E2E_DATABASE_URL='postgresql+psycopg://postgres@/ppt_p5_contract?host=/pgsocket'
python -m ppt_service.tests.e2e.run_suite --report /evidence/postgres.json
```

连接校验要求数据库名匹配 `ppt_p5_[a-z0-9_]+`，地址只能是回环地址或精确的 `/pgsocket` Unix socket，并限制连接参数。每例生成独立 `p5_<uuid>` schema，结束时清理该 schema。`SQLiteBackupRecoveryTests` 始终使用 SQLite；引擎边界测试以文件与运行时契约为主，因此 PostgreSQL 报告不表示每一例都使用了 PostgreSQL。

这些保护不能把业务数据库变为安全测试目标。不要使用线上数据库、已有业务 schema、真实凭据或模型配置。

## Linux 冻结来源运行

`run_isolated_linux.sh` 在 Linux Docker 主机上运行 SQLite、PostgreSQL、原有 pytest 回归和实际 `pg_dump`/`pg_restore` 恢复探针。脚本绑定已存在的 ARM64 测试镜像摘要，使用 `--pull=never`；换环境前应审阅镜像摘要、架构和依赖是否符合预期。

每次运行需要一个新的、仅属于本次测试的目录，内含 `source.tar`，且尚无 `source/`、`evidence/` 子目录：

```bash
bash /path/to/run_isolated_linux.sh /path/to/new-p5-case
```

来源包包含服务端、控制器、worker、Pi runtime、所需 contracts、`tests/e2e`，以及原有 `tests/server/test_api.py`、`test_auth.py`、`test_postgres.py` 和 `tests/control/test_control.py` 回归。持久化打包入口如下，输出目录内不得已有同名包或清单：

```bash
python -m ppt_service.tests.e2e.package_source --output /path/to/new-p5-case
```

脚本生成 `source.tar` 和逐文件 `source-hashes.json`，对同一组字节计算哈希和写包；并行作者修改时仍需先协调冻结。打包前应检查选择范围；不包含 `.env`、私钥、证据目录、虚拟环境、用户数据或本机状态。传输前后对比 tar 的 SHA-256，并保留逐文件清单。后续源码有改动时必须重新冻结，不能把旧报告归到新源码上。历史 r4 的来源与失败证据只对应当时的代码；本轮修复须由新的冻结包和报告验证，具体批次见 `results.md`。

隔离运行的具体约束如下：

- API 测试容器使用非 root UID、只读根与来源目录、私有临时目录、资源限制、移除 capabilities 和 `no-new-privileges`。
- API 与 PostgreSQL 容器均为 `--network none`；PostgreSQL 禁用 TCP，仅经专属卷中的 Unix socket 访问，数据库目录位于 tmpfs。
- API 容器只获得来源目录、专属证据目录和 socket 卷，不挂载 Docker socket、宿主凭据或业务数据。
- SQLite 与 PostgreSQL 套件分别保留日志、JSON 和退出码；一个套件失败后仍运行另一个。原有 API、auth、PostgreSQL 并发及 control 测试由独立 pytest 命令运行，使用新建的 `ppt_p5_regression` 数据库，避免与 E2E 的 `ppt_p5_contract` 库和备份恢复库混用；备份恢复另行记录。
- 资源使用本次运行的唯一名称与 owner label。退出时只清理标签相符的容器/卷，清理成功后才写 `cleanup.json`；证据目录保留用于审阅。

主要证据为 `sqlite.json`、`postgres.json`、对应 `.log`、pytest 的 `regression.xml` 与 `regression.log`、`status.json`、`source-tar.sha256`、`images.txt`、`cleanup.json` 和 `pg-backup/verification.json`。`status.json` 分别记录 `sqlite_exit`、`postgres_exit`、`regression_exit`、`pg_backup_seed_exit`、`pg_restore_verify_exit`，并记录 `model_requests: 0`。脚本最终非零退出可能来自真实契约失败或执行错误，应查看各项退出码与堆栈，不能仅依据终端最后一行判断。

## PostgreSQL 备份恢复探针

`pg_backup_probe.py` 是隔离 runner 的两阶段配套工具，不自行调用 Docker 或数据库备份命令：

1. `seed --evidence /evidence/pg-backup` 只接受专属 `ppt_p5_backup` 库。通过真实 API 建立项目、上传、规划与确认，添加明确标记的历史版本 fixture；确认无执行者和占用槽后，保存数据/文件清单。
2. runner 在独立 PostgreSQL 容器内执行真实 `pg_dump -Fc`，恢复到新建的 `ppt_p5_restore` 库。
3. `verify --evidence /evidence/pg-backup` 只接受恢复库，将文件复制到新的数据根目录，创建新的应用实例，对比表记录、文件哈希与元数据，验证 JWT/ACL、历史、SSE、幂等重放与冲突，以及恢复后的写入。

探针要求证据路径位于专属 `/evidence` 子目录。seed 成功后有意保留测试 schema 和文件供 dump，最终由隔离 runner 清理数据库容器。它验证静止状态快照，不覆盖业务运行中的一致性备份、PVC 快照或灾备切换。

## 本轮修复与回归契约

本轮四项修复已通过最终冻结来源验证：62 个集成场景在 PostgreSQL 下全部通过；Windows 和 Linux SQLite 各通过 58 项，明确跳过 4 项 PostgreSQL 专属并发测试。另有 45 项原有 API、身份、PostgreSQL 与控制器 pytest 回归全部通过，独立 `pg_dump`/`pg_restore` 恢复也通过。各平台执行的是同一组 62 个场景，不能相加计算；原有回归另列。详细结果见 [results.md](results.md)，逐项证据见 [20261023-fixes](evidence/20261023-fixes/)。历史失败及初次 Windows 混合来源运行报告均保留。

| 问题 | 修复后的行为 | 验证范围 |
| --- | --- | --- |
| D1：删除后幂等缓存绕过资源重查 | `Repository.idempotent` 强制传入 `authorize`，在 admission/quota 锁后、读取缓存和比较请求哈希前重查并锁定当前项目或任务；授权失败返回 404。缓存命中不重跑 action、修订号检查或其他副作用 | 五种写入口均验证原响应重放、数据库无重复变化、跨账号拒绝；删除后原请求及更改请求体均不能返回旧成功或暴露哈希冲突 |
| D2：终态 SSE 超过 100 条时截断 | 已持久化的事件分页连续排空后才关闭终态流；运行中流先排空积压再等待。每批重查资源授权与保留窗口；删除时停止，窗口越过游标时提示刷新快照 | 100 条边界及多页重连无遗漏、无重复；受控生成器迭代覆盖分页间删除和保留窗口推进，真实浏览器断网仍待集成验收 |
| G1：缺失或无效显式检查点导致队列反复阻塞 | 仅在 `claim_next` 新认领的同一事务内，纯 attempt 构建失败且尚未执行任何外部操作时，将该任务置为失败（已取消/删除时置为取消），原子释放活动引用与执行槽；后续任务可继续 | `test_missing_explicit_checkpoint_fails_once_and_next_job_can_run` 及相关恢复断言覆盖失败终态和后续推进。此路径保持 `stopped=False`，不伪造进程停止证明；旧 token/attempt/phase 或已附加执行者被拒绝，已附加执行仍须取得真实 stop proof 才能释放槽 |
| G2：Windows SQLite 校验句柄未关闭 | `check_session_database` 在删除临时目录前显式关闭只读 SQLite 连接，成功及校验异常路径均适用 | `tests/control/test_control.py` 的关闭回归和损坏/缺会话等拒绝断言，以及本机完整工作流 |

上述修复保持付费执行、停止证明和正式发布门禁。独立回归的计划、检查点与历史 Version fixture 不代表模型、官方导出工具或 PowerPoint 验收。

## 尚待完整集成验收

P5 的独立测试不能单独完成整个工作台的部署验收。仍需主线 P1/P2 与引擎集成就绪后验证：

- 真实 Kubernetes Job/Pod、数据库迁移、持久卷和控制器重启接管。
- 真实 Casdoor/SSO、Ingress/代理身份边界、浏览器 SSE 断网与重连。
- Pi `production_ready`、生产执行器的双引擎路由与双向检查点隔离、禁止静默切回 Hermes。
- 操作系统级崩溃、OOM、强制终止、网络故障和基础设施恢复；替身制造的状态不替代这些证据。
- 真实官方导出/质量检查、正式发布、预览与 PowerPoint 验收。当前合成文件、prepare 替身和历史 Version fixture 均不能用于这项验收。

因此，独立用例完成或部分通过时，OpenSpec 的 P5 总项仍须按真实缺陷与依赖状态判断，不能仅凭本套件标记整体完成。
