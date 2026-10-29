# 内部开发记录

记录日期：2026-10-30（Asia/Shanghai）。给用户看的进度和待办在 [NEXT_STEPS.md](NEXT_STEPS.md)；本文件供后续开发时查询。

## 文档表达约定

用户明确要求：OpenSpec 只是我记录需求、任务和验证结果的工具。说明工作内容时直接讲要做什么、达到什么效果，不把工具编号、项目 UUID、登录问题或工具操作写成用户需要理解的业务步骤。不要使用“OpenSpec 5.1”“OpenSpec 续作入口”等标题。

用户已明确授权在独立分支完成该任务，并补充要求全部真实跑通、具备上线条件后才请示合并。沿用已有 `codex/svg-video-workbench` 分支及工作树，完成真实 Agent 路由、实际登录入口、持久化部署和真实文稿的拆镜、生成、修订、采用、导出全流程，校验权限、取消与重启恢复，并记录质量、耗时和费用。全部达到上线条件后，再询问是否合入主线。在用户明确批准合并前，不合入主线。阶段性工作和测试继续在该分支进行，无需为日常开发反复请求确认。

## 代码与当前状态

- 工作目录：`D:\github\armbianbegin\.video-workbench-worktree`。
- 分支：`codex/svg-video-workbench`。实现、真实接入与上线校验都在该分支完成；可以记录阶段性提交，全部真实跑通且具备上线条件后才请示合入主线。尚未正式部署。
- 该工作树基于现有通用 Agent 服务版本。父仓库的 `panghu_agent` 默认检出较旧，继续前确认目录、分支和 Git 状态，保留其他工作。
- 上一阶段完整 Python 回归与失败修复后的定向复测覆盖最终 1437 项（合计 1388 通过、49 条件跳过），10 项前端测试通过。本轮统一定向复测 88 项通过，覆盖 catalog、verifier、业务及独立登录 renderer；UI 12 项通过，共享 c673 回归 158 通过、5 条件跳过。具体原始结果与复测范围见 [VALIDATION.md](VALIDATION.md)。离线浏览器验证了项目创建、拆镜、生成、采用、修订保留旧版本、ZIP 下载和独立播放器切镜。
- ARM 服务器上的工作台镜像构建、依赖检查与禁网容器验证通过，项目/镜头/CSRF 在重启后保留。工作台和视觉 Pi 镜像已按 latest 推送并取得摘要；控制面仍在构建，未正式部署。
- 离线示例带 OFFLINE FIXTURE 标识，未调用真实模型；新视觉路由未在现网启用。

## 本轮服务器检查点

- 独立源码目录为 `/root/video-workbench-release-20261029/source`。服务器共享 checkout 已变化到 `d8f593`，不能把它当作本功能的固定发布源码，也不能覆盖其中其他工作。
- 工作台镜像已推送：`latest@sha256:46c8de4bd64137fc299286d1a8903dd8d04dd79a408fecc71638c626a20445ef`，源码 revision `39156bc`。
- 视觉 Pi 镜像已推送：`latest@sha256:2d440c564e2ec98538ed2a0c1166aa75fe52d260d069cac629a486b357e47f04`，源码 revision `254e748`。
- 通用服务控制面构建仍在进行；在取得成功回执前，不能将它记为已经发布。工作台与新视觉运行组合均未正式部署。
- 候选执行已暂停，尚未发起：0 模型请求、0 新增 UID、0 新临时资源、无残留、未改变生产服务。没有候选执行进程需要停止，也没有完成真实模型验收。
- 用户要求先休息并做阶段性收尾，停止新增线上操作。此时只完成离线登录资源、文档和需求记录；下次从实际镜像与服务状态重新核对后继续，不盲目恢复临时验收进程。

## 后续实现时核对

- 按父仓库 AGENTS.md，构建、推送、部署使用 `latest`；发布身份记录在 labels、annotations 和 digest 中。
- 新任务为 `video.storyboard.v1`、`video.scene.v1`，revision 均为字符串 `"1"`；资源包为 `video-workbench-v1`，工具为 read/write。
- 注册任务契约、资源包、工具环境、镜像 identity、execution binding 和专用 caller，完成组合验收后启用对应路由。核对 capabilities、并发、配额和超时，视觉请求不回退到科研任务。
- 工作台目前仅支持单进程、单副本协调；持久化 SQLite 及 WAL，检查 UID `10001` 的写权限。
- 生产访问通过认证代理；代理清洗并注入 `X-Forwarded-User`，配置 Host、同源访问和 CSRF。凭证通过部署环境注入。
- 连接设置见 README 中的 `VIDEO_AGENT_*`、`VIDEO_WORKBENCH_DB` 和 `VIDEO_WORKBENCH_HOSTS`；环境凭据与凭据文件二选一，轮换后重启读取。健康探针不调用模型。
- catalog、镜像资源打包与独立 caller 的配置代码已补齐。工作台提供白名单构建器和离线 Kubernetes renderer，仍须发布真实验收组合才能启用视觉路由。
- 本分支更新了 Agent 平台 Vault 来源副本；父仓库规范 inventory 还需在获准合并后的平台发布中同步。发布命令需显式指定本分支来源副本，见 README。工作台业务 Secret 投影由 renderer 可选生成。
- 真实验收覆盖拆镜、生成、修订、采用、导出，以及取消、提交响应丢失和重启恢复。记录质量、排队与执行耗时、token、费用和人工修改次数。
- 上层业务管理项目、镜头、版本和采用决定，通用 Agent 负责执行。当前协调器没有 LangGraph 依赖；后续按实际编排需要评估。

## 上线入口只读核对：2026-10-30

本节依据父仓库现有清单和服务器实时公开配置。调查仅查询 Deployment、Service、NetworkPolicy、TunnelRoute、代理回调和会话 DB 编号；没有读取 Kubernetes Secret、环境密码或修改线上资源。

- 现有入口模式为业务域名 → Cloudflare Tunnel → `oauth` 命名空间中的业务专用 oauth2-proxy → 内部业务 Service。TunnelRoute 使用 `cf.armbianbegin.io/v1`，位于 `default` 命名空间。科研入口实际为 `scientific-agent.panghuer.top` → `oauth2-proxy-scientific-agent.oauth.svc.cluster.local:4180`，公开登录跳转返回 302，认证站点为 `auth.panghuer.top`。
- 调查时未发现名称含 video、workbench 或 studio 的线上 Deployment / Service，也没有工作台专用代理或对应 TunnelRoute。用户随后确定公开域名为 `video-service.panghuer.top`，并明确只由他在 WebUI 后台添加 Casdoor 回调与对外域名配置；不能由 Agent 创建或修改这些外部入口。
- 线上认证代理实际使用的 Redis DB 为：游戏业务共用 2，百家争鸣 3，Hublog 7，图片工具 11，文献下载 12，科研 13，游戏评审 14，research-agent 15。父仓库还登记了应用运行时 DB0、dsh DB4 和 hermes DB5。未查到工作台专属预留；其他编号是否可用仍需核对完整使用情况，不能由这份代理清单推断为空闲。现有脚本对未登记 target 会回退到 DB15，不能直接用于新工作台。
- 现有 Casdoor / OAuth 模式为共享 OIDC 应用加各业务精确回调，部分共享代理使用域名白名单。未能通过只读公开接口确认 Casdoor 应用的完整回调列表；新域名的回调授权仍需在实际接入时核实和配置，不假定已经存在。
- 通用 Agent 的实际命名空间为 `agent-service`，Service 为 `agent-service`、TCP 8080，内部地址 `http://agent-service.agent-service.svc.cluster.local:8080`。API Pod 实际标签为 `app.kubernetes.io/name=agent-execution-service`、`component=api`，另有 `app=agent-service`；renderer 默认 Agent 选择器与线上一致。
- 线上 `agent-service-business-ingress` 当前只允许 PPT、科研和控制面 Pod，尚未包含工作台。工作台未来使用 `video-workbench/video-workbench` Deployment 与 Service 是当前 renderer 的拟定资源名称，并非已经上线的服务；其 Pod 标签为 `app.kubernetes.io/name=video-workbench`。

最小接入顺序：

1. 用户在 WebUI 为 `video-service.panghuer.top` 添加对外域名配置，并将 `https://video-service.panghuer.top/oauth2/callback` 加入 Casdoor 回调。Agent 只准备内部资源与精确上游地址，不创建 TunnelRoute、不修改 Casdoor。
2. 发布经过真实验收的 Agent 视觉运行组合和专用 caller，构建并推送工作台 `latest` 镜像，取得实际仓库 digest。由既有 Vault 权威记录投影业务凭据，保留科研和其他业务的配置。
3. 用真实 Host、已分配存储类和代理标签渲染工作台资源，审阅并执行服务端校验。部署单副本 Recreate / PVC / 非 root / 只读根文件系统，配置 Agent 入站放行和业务出站策略；确认数据目录及凭据文件权限。
4. 准备内部专用代理 `oauth/oauth2-proxy-video-workbench`，标签 `app=oauth2-proxy-video-workbench` 与业务入站策略一致。上游为 `http://video-workbench.video-workbench.svc.cluster.local:8089`，保留 Host、清洗并注入稳定的 `X-Forwarded-User`。交给用户的内部入口为 `http://oauth2-proxy-video-workbench.oauth.svc.cluster.local:4180`，由用户在 WebUI 指向该代理。
5. 从真实公网入口完成登录和文稿制作，检验身份隔离、CSRF、采用版本与导出内容、取消和提交状态恢复、服务重启后的持久化，并记录实际模型质量、耗时、用量和费用。确认发布与回退资料齐全，再请用户决定是否合并。

域名偏好已明确。共享 Redis 的 CONFIG/INFO 元数据查询在未提供凭据时返回 NOAUTH；未读取密码，因此不能把未登记编号视为可用。内部代理生成器采用独立小 Redis 实例，隔离工作台会话 DB0，并要求专用 Cookie 与 Redis 凭据；复用现有 OIDC client，但不复用共享 Cookie 密钥。资源成本、Secret 引用和渲染命令见 [deploy/README.md](deploy/README.md)。调查与离线生成阶段未创建 Secret、未应用资源。

本轮按用户休息要求暂停新增真实任务与部署，阶段状态已同步；继续顺序见 [CHECKPOINT.md](CHECKPOINT.md)。尚未取得的真实验收结果保持待办。

## 工具中的记录位置

- 使用父仓库 `D:\github\armbianbegin\.openspec-project.json` 的映射。本工作树没有独立映射，沿用已有项目，不重新注册。
- MCP：`https://openspec.panghuer.top/mcp`。
- projectId：`ec663f5a-53d3-4a0e-9037-933eb788d183`。
- changeId：`add-svg-html-video-workbench`，状态 active。
- 最近核实修订：`d2d8da23e7c1c5b55736b7bf22cad46cd90b93da`。
- 最近读回核对了四份工件，工具校验通过；任务为 15 项完成、3 项生产验收待办。继续操作前读取最新记录和 revision，不覆盖其他会话的修改，不使用共享的 `project-a-specs`。
- 剩余待办为视觉能力正式配置、工作台部署、真实模型验收。目前没有应用到主规格或归档。阶段检查点回执在父仓库 `.runtime/video-release-openspec-checkpoint-sync.json`，记录了两项已推送镜像、控制面仍在构建、用户手工入口责任与候选执行尚未发起。

OAuth 已配置并登录成功，优先复用已有连接；只有实际凭证失效时再处理登录。网页首页提示 Bearer token required 不能用来判断 Codex OAuth 是否已登录。

本次曾遇到服务端会话上限，用户授权后已重启 `openspec/openspec-service` 并恢复。如复发，检查会话回收及相关修复的部署状态。

本轮已通过现有 MCP 连接直接同步并校验。早期临时 CLI 自动审批模型不可用的排查配置仅用于当时进程，全局模型设置没有修改；优先复用现有连接，不必重复该排查流程。
