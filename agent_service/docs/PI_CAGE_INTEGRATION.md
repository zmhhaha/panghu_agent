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
不累加为一次完整回归。Windows 跳过项需在 Linux 验证。

真实包已确认是 `0.99.2`，工具扩展依赖从旧 `@sinclair/typebox` 改为 `typebox`。
服务器固定镜像构建、真实 CLI/工具/Responses 无模型验证和领域候选结果继续记录于此。
目前仍未开放生产 route/pool，PPT/科研尚未切换；此前 1071 项 Linux 结果仅覆盖旧提交。
