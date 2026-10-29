# 内部开发记录

记录日期：2026-10-29（Asia/Shanghai）。给用户看的进度和待办在 [NEXT_STEPS.md](NEXT_STEPS.md)；本文件供后续开发时查询。

## 文档表达约定

用户明确要求：OpenSpec 只是我记录需求、任务和验证结果的工具。说明工作内容时直接讲要做什么、达到什么效果，不把工具编号、项目 UUID、登录问题或工具操作写成用户需要理解的业务步骤。不要使用“OpenSpec 5.1”“OpenSpec 续作入口”等标题。

用户已明确授权在独立分支完成该任务。沿用已有 `codex/svg-video-workbench` 分支及工作树，完成实现和最终校验后，再询问是否合入主线。在用户明确批准合并前，不合入主线。阶段性工作和测试继续在该分支进行，无需为日常开发反复请求确认。

## 代码与当前状态

- 工作目录：`D:\github\armbianbegin\.video-workbench-worktree`。
- 分支：`codex/svg-video-workbench`。本轮实现与验证完成后在该分支提交；合入主线仍需用户明确批准。尚未正式部署。
- 该工作树基于现有通用 Agent 服务版本。父仓库的 `panghu_agent` 默认检出较旧，继续前确认目录、分支和 Git 状态，保留其他工作。
- 最近验证：完整 Python 回归与失败修复后的定向复测覆盖最终 1437 项（合计 1388 通过、49 条件跳过），10 项前端测试通过。具体原始结果与复测范围见 [VALIDATION.md](VALIDATION.md)。离线浏览器验证了项目创建、拆镜、生成、采用、修订保留旧版本、ZIP 下载和独立播放器切镜。
- ARM 服务器上的工作台镜像构建、依赖检查与禁网容器验证通过，项目/镜头/CSRF 在重启后保留。仅本地构建，没有推送镜像或生成仓库发布回执。
- 离线示例带 OFFLINE FIXTURE 标识，未调用真实模型；新视觉路由未在现网启用。

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

## 工具中的记录位置

- 使用父仓库 `D:\github\armbianbegin\.openspec-project.json` 的映射。本工作树没有独立映射，沿用已有项目，不重新注册。
- MCP：`https://openspec.panghuer.top/mcp`。
- projectId：`ec663f5a-53d3-4a0e-9037-933eb788d183`。
- changeId：`add-svg-html-video-workbench`，状态 active。
- 最近核实修订：`fc1edae5a6e2a845fbf8cde27f54c4cbb8f20c78`。
- 最近读回核对了四份工件，工具校验通过；任务为 15 项完成、3 项生产验收待办。继续操作前读取最新记录和 revision，不覆盖其他会话的修改，不使用共享的 `project-a-specs`。
- 剩余待办为视觉能力正式配置、工作台部署、真实模型验收。目前没有应用到主规格或归档。同步回执在父仓库 `.runtime/video-release-openspec-sync.json`。

OAuth 已配置并登录成功，优先复用已有连接；只有实际凭证失效时再处理登录。网页首页提示 Bearer token required 不能用来判断 Codex OAuth 是否已登录。

本次曾遇到服务端会话上限，用户授权后已重启 `openspec/openspec-service` 并恢复。如复发，检查会话回收及相关修复的部署状态。

本轮已通过现有 MCP 连接直接同步并校验。早期临时 CLI 自动审批模型不可用的排查配置仅用于当时进程，全局模型设置没有修改；优先复用现有连接，不必重复该排查流程。
