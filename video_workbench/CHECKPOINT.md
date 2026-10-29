# 视频工作台阶段检查点

日期：2026-10-30（Asia/Shanghai）。用户休息前保存；本阶段停止新增模型任务和部署，不合入主线。

## 已完成与当前边界

工作台代码、通用 Agent 接入代码、部署生成器和验收工具已经准备好；工作台与视觉 Pi 镜像已推送。仍未部署工作台、未启用视觉路由、未配置新的线上凭据，也未产生真实视频业务模型成果。SVG／HTML 是本期产物；配音、动画和 MP4 尚未实现。

- 工作树：`D:\github\armbianbegin\.video-workbench-worktree`。
- 分支：`codex/svg-video-workbench`；主线未合入。真实跑通且具备上线条件后才请用户决定是否合并。
- 业务源码最近提交：`39156bc00821ac453479ad33c35f5630a50c54f6`；基础同步提交：`254e74842eef97fb8eba0e70dc6667bc74465883`，包含当前科研服务 `c67394f` 基础修复。
- 本次新增目录合并器、登录代理生成器和真实 HTTP 验收工具；统一复测 88 项 Python、12 项前端测试通过。科研基础定向回归 158 通过、5 条件跳过。详见 [VALIDATION.md](VALIDATION.md)。
- 视频候选尚未开始：模型请求 0，新增 UID 预留 0，临时集群资源创建 0，无候选残留；生产目录、路由和控制面未修改。

## 服务器上的准备结果

服务器：`root@192.168.137.101`；Kubernetes 配置：`/etc/kubernetes/super-admin.conf`。本任务独立目录：`/root/video-workbench-release-20261029/`，源码为其 `source/`，公开证据保存到其 `evidence/`。

已推送的镜像身份、上下文与源码摘要见 [VALIDATION.md](VALIDATION.md)。服务端回执：

- `source/agent_service/rendered/pi-image.txt`。
- `source/video_workbench/rendered/workbench-image.txt`。
- `source/agent_service/rendered/control-image.txt`：收尾时尚未生成成功回执，控制面镜像正在依赖下载/构建。原命令为 `python3 -m agent_service.build_image --target control`，`IMAGE=arm-cluster-master:5000/agent-service-video-control:latest`、`SOURCE_REVISION=254e74842eef97fb8eba0e70dc6667bc74465883`。原终端会话号 5392 只作本次定位线索，不能假定下次仍可用。该构建不会启用路由或启动模型任务。

本地 `.runtime/video-release/` 保存严格白名单源码 TAR。已上传源码先为 Agent 的 `254e748`，后叠加工作台 `39156bc` 的运行文件；新加的主机侧目录合并器、登录代理生成器及 HTTP 验收工具还需从检查点提交同步到独立目录。不要据本地最新 HEAD 声称已构建镜像包含这些后加文件。

共享科研源码目录 `/root/agent-service-deploy-20261026/panghu_agent` 已被另一个任务切换到 `d8f593`，不能当作固定的 `c67394f` 来源。本次已核实 live 校验器对应 `c67394f`，差异来自共享目录变化。后续只使用本任务独立源码目录，发布前重新核对现网，保留其他任务正在推进的内容。

## 下次按顺序继续

1. 检查分支和服务器构建状态。控制面镜像若已完成，读取真实回执并核对 manifest；若失败，保留原因后从本任务独立源码重新构建，不并行重复构建。最新工作台镜像还需重新跑禁网容器烟雾验证，旧镜像通过不能代替。
2. 用新目录合并器准备**关闭**的视觉配置，保留现网科研路由、历史验收绑定和所有 caller。不要将新生成的完整目录覆盖线上目录。发布前重新读取实时配置和镜像，而不是沿用昨夜快照。
3. 准备两个有界、串行的真实候选：拆镜 `max_scenes=2` 和简单 SVG；各阶段一次尝试，失败先取证，不自动重新付费。独立候选结果不是生产验收记录，更不能伪造启用所需的通过标记。
4. 取得真实 Pod 终止、模型代理账目、停止与产物证据后，再审查和发布仅作用于视频的正式绑定、专用调用凭据和最小网络放行；保留科研服务配置。
5. 部署单副本工作台、PVC、内部登录代理和独立会话存储，然后从实际入口验证。域名和 Casdoor 回调由用户在 WebUI 配置。
6. 用真实短文稿跑拆镜、SVG、HTML、修订、明确采用和 ZIP 导出，检查中文质量；再验证用户隔离、实际取消、提交不确定恢复和服务重启持久化，记录实际耗时和模型用量。未取得费用时写“未知”。完整计划见 [deploy/ACCEPTANCE_PLAN.md](deploy/ACCEPTANCE_PLAN.md)。
7. 只有这些工作通过并具备上线条件，才向用户提交结果并询问是否合入主线。无需为恢复日常开发再次请求合并许可。

候选执行注意：原 `operator_candidate.py` 固定引用共享源码且没有 UID 文件锁；须在本任务目录复制改造，切到独立源码。共享 UID 账本为 `/root/agent-service-deploy-20261026/uid-reservations.json`，每次分配前锁定、重读并保留并发变更；拟用独立 `44000000+` 区段仍须查重，不能当作已经预留。生产 `43000000..43065535` 不可复用。

## 已确定的入口与用户手工项

- 域名：`video-service.panghuer.top`。
- Casdoor 回调：`https://video-service.panghuer.top/oauth2/callback`。
- 拟部署后交给域名入口的内部上游：`http://oauth2-proxy-video-workbench.oauth.svc.cluster.local:4180`。
- 代理后端：`http://video-workbench.video-workbench.svc.cluster.local:8089`。

以上 Service 目前尚未创建。Agent 不添加或修改 Casdoor、TunnelRoute 或公网域名配置。内部 OAuth renderer 只生成资源；专用 Cookie/Redis 凭据尚未创建，复用的 OIDC Secret 只引用既有键，不把凭据写进检查点。

## 验收工具的实际覆盖

`python -m video_workbench.deploy.verify_live --help`。默认只读预检；`--execute` 才创建标记项目并最多执行四次生成。它核对真实 Jobs、停止证明、manifest、源码及导出版本，遇到不确定提交即停止且不重发。当前 30 项测试全部为离线 HTTP 测试。

工具没有代替浏览器检查画面，也未实现实际运行取消、受控丢响应转发或第二登录用户隔离验证；这些明确保留待办，不能因为脚本返回成功而一并声称通过。详细使用边界参见源码说明。
