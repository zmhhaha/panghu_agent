# 通用 Agent 服务的 pi-cage 接入

用户要求优先使用自己的 `https://github.com/zmhhaha/pi-cage` 代码仓库。
本次固定该仓库 `codex/agent-service-worker` 分支提交
`e0262e65e43c6924164e995394f715dc5d19c210`，实际执行其新增的
`/usr/local/lib/pi-cage/worker-cli.mjs`。入口在同一 Node 进程内加载仓库固定的
`@earendil-works/pi-coding-agent 0.99.2`，支持 JSON print、无会话、显式工具/技能和
受控 stdout 压缩。不会启动 ttyd/tmux 或等待网页登录。

## 分工与来源

- pi-cage 提供固定 Pi 包、Node、一次性 Worker 入口和源码身份元数据；正常构建入口仍是它的 `build.sh`。
- agent-service 提供统一任务 API、排队、单次任务 Pod、私有控制进程、每 attempt 独立 UID、模型临时授权、取消/超时及全 writer 停止观察。
- PPT/科研保有各自合同、能力包、版本和独立成果验收。原交互式 pi-cage 仍是独立部署。

`agent_service/pi_cage.py` 固定仓库提交、入口 SHA256、package 和 version；构建及
Worker readiness 同时检查 `/usr/local/lib/pi-cage/runtime.json`、实际包的
`package.json` 和入口文件。所有文件来自只读镜像，不接受任务指定路径/版本。
健康信息、运行 provenance 和候选证据记录真实仓库与提交；离线 fake CLI 不冒充真实来源。

Pi 绑定使用新的 `pi-cage-adapter-v1`。新镜像和 adapter 产生新的组合 fingerprint，
必须重新验收。旧 `0.73.1` Worker 的模型/工具/领域验收不能迁移为新组合的验收。

## 构建

按 [部署说明](../deploy/README.md) 先构建固定 pi-cage 镜像，取得不可变摘要后：

```bash
export PI_CAGE_IMAGE='arm-cluster-master:5000/pi-cage-agent-runtime@sha256:实际64位摘要'
export PPT_DONOR_IMAGE='审定PPT工具镜像仓库@sha256:实际64位摘要'
bash agent_service/build.sh --target pi
```

`PI_DONOR_IMAGE` 已移除。PPT donor 仅提供现阶段业务依赖的 PPT Master、Python、字体和
浏览器；Pi 执行代码来自 pi-cage。控制宿主仍是 Python PID 1，随后以任务专用 UID
启动 `node /usr/local/lib/pi-cage/worker-cli.mjs`。该派生镜像的隔离必须按
agent-service 的真实宿主流程验证，不能套用交互式 pi-cage 固定 UID10001 的结论。

## 验证进度

已完成无模型定向验证：pi-cage 入口 32 项；agent-service runtime 145 passed、
6 skipped、52 subtests；构建/目录/来源身份 74 passed、2 skipped。各自范围独立，
不累加为一次完整回归。后续完整 Linux 结果见下文。

真实包已确认是 `0.99.2`，工具扩展依赖从旧 `@sinclair/typebox` 改为 `typebox`。
该版本 `--tools` 对内置和扩展工具均使用显式名单，因此科研生成列出
`read,write,academic_search,academic_verify`，固定证据修订仍仅 `read,write`。
真实扩展签名支持现有 `(toolCallId, params, signal)`；没有额外扩大任务授权。
集成后补齐候选入口的 adapter 检查，相关一次专项 94 项通过。

服务器已按两个仓库正常 `build.sh` 构建并推送以下不可变镜像：

| 镜像 | SHA256 |
| --- | --- |
| `arm-cluster-master:5000/pi-cage-agent-runtime`，pi-cage `e0262e6` | `8c6c175ae53f0f1f6ebface6855079d7549138ef33de0320b5942dc38f6e6ced` |
| `arm-cluster-master:5000/agent-service-pi`，业务仓库 `d871717` | `76e1b4c829c6c0a2c7908aa4e9fe9861b6da48ba7c1db34b86f83a52cf616880` |
| `arm-cluster-master:5000/agent-service-control`，业务仓库 `d871717` | `4c88ffb3494c31f113c006385afff14df7ee7fed8b0a45e9b91efb0ec78f0ad9` |

Pi Worker 构建过程中已执行源码身份检查与真实入口版本探针。第一次尝试使用腾讯
APT 镜像源时，Node slim 基础镜像缺少 CA、HTTP 被重定向到 HTTPS，安装失败；
恢复仓库默认 Debian 源后正常构建，没有禁用 TLS 校验。

新配置准备目录为服务器
`/root/agent-service-deploy-20261026/catalog-d871717-pi-cage/`。
准备时只读核对控制数据库 Job 数和 Worker Pod 数均为 0，保留实际运行配置中的
调用方、配额及运维设置，全部 route/pool 继续关闭，既有 Hermes 镜像保留。
最新 `d871717f77678ffbb1f5038757b635aa67dc4512` 完整 Linux/真实 PostgreSQL 回归为
**1124 passed**：Agent 1079、科研连接器 22、PPT 连接器 15、真实 PPT schema 迁移 8。
零失败、错误、跳过和模型请求；测试容器与临时 socket 均清理。
报告 `/root/agent-service-linux-evidence-20261028-pi-cage-r1/report.json`，
run ID `agent-pg-20261028045325-240e9083698d`；同目录保存完整日志、1099 文件源码清单及
Git 归档摘要。此前 1071 项 Linux 结果仍只属于旧提交，不与本次结果累加。

真实固定 `0.99.2` CLI 已在禁外网容器中通过 r3 两组协议验证，分别为普通文本任务和
科研工具任务，均正常退出。使用的是本地 Responses/工具响应夹具，真实模型请求为 0。
科研场景实际加载固定扩展并执行 `academic_search`、`academic_verify`，随后将工具结果、
assistant `phase` 及自包含 reasoning 历史带入第二轮请求；JSONL 压缩和完成事件检查通过。
三条捕获请求进一步通过 agent-service model proxy 规范化，以及平台 `e5351c4` 的
请求 schema 和原生 Responses 准备检查，未向模型或真实学术站点发送请求。

r3 使用与当前生产配置相同的模型声明字段结构，provider/model 改为本地夹具名，
没有显式设置 `reasoning` 能力。
该轮只检查 `auth_present`，未验证 Authorization 中的 token 与任务环境变量等值，
因此不能据此声称认证正确。后续真实科研候选发现 Pi 0.99.2 将裸变量名当作密钥字面值，
`7379e4c` 改为 `"apiKey": "${AGENT_MODEL_API_KEY}"`；真实 CLI 的四组禁网对照确认
新写法精确读取环境变量，并由真实生成 r4 继续验证。详见
[科研验证记录](RESEARCH_VALIDATION_20261028.md)。
另一次 r2 压力样例显式设置 `reasoning: true` 后会发送顶层 `reasoning/include`，
当前共享网关尚未支持该组合；本次通过结果不覆盖这一配置。
服务器协议报告为
`/root/agent-service-deploy-20261026/pi-cage-protocol-audit-20261028-r3/report.json`；
本机 ignored 副本为 `agent_service/rendered/pi-cage-protocol-r3.json`，
网关格式检查结果为同目录的 `pi-cage-protocol-r3-gateway.json`。

正常 `deploy.sh` 已执行完成并退出 0。2026-10-28 13:25:20（Asia/Shanghai）的独立检查
确认 `agent-service-api`、`agent-service-dispatcher`、`agent-model-proxy` 三个 Deployment
均为 generation=observedGeneration=6，desired/ready/available 各 1；三个 Pod 均
Running、Ready、重启数 0。实际控制面镜像摘要为上表的
`4c88ffb3494c31f113c006385afff14df7ee7fed8b0a45e9b91efb0ec78f0ad9`。
七个 Pi binding/pool 均引用 `pi-cage-adapter-v1` 和上表的 `76e1b4…616880` Worker 镜像；
共十四个 pool，启用 route/pool 数均为 0，Worker Pod 数为 0。
`healthz` 返回 200，未认证 capabilities 返回 401；本次检查模型请求为 0。
公开记录已保存至服务器
`/root/agent-service-deploy-20261026/pi-cage-rollout-d871717.json`，`passed=true`。

生产 route/pool 继续关闭，PPT/科研尚未切换。上面的 `d871717` 镜像、回归和部署结果
保留为该提交的历史记录，不覆盖之后的修复。按用户最新要求先跑科研，真实生成 r4
及独立产物验收、隔离数据库发布已通过；修订 r1 的校验误判已在 `162ed53` 修复，
已有真实输出离线复核通过。修订 r2 又因输出包含选区外 claim 被校验器正确拒绝，
`772bef1` 明确提示词后，修订 r3 已真实执行 completed，独立产物、隔离业务发布与
候选版本比较均通过。验收工具 `f263796` 核对真实容器终态，原始证据保持不变；
实际运行与部署镜像仍为 `772bef1`。
该版本的关闭配置已按正常 `deploy.sh` 更新，三个控制面 Deployment 均 1/1 Ready，
七条 route 与十四个 pool 仍关闭，Worker Pod 为 0。正式 Jobs HTTP 交付链与
科研入口切换尚未验收，下一步按科研业务接入，不以 PPT 或 Hermes 为前置。
最新证据和边界见[科研验证记录](RESEARCH_VALIDATION_20261028.md)。
