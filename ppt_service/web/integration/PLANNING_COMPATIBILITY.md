# 真实规划投影兼容与 CSRF 前端验证（2026-10-22）

本轮只修改 web 源码、测试与文档；没有部署、SSH、服务操作、模型请求或新的真实浏览器运行。解析工作流源码未改。此前 41 项前端测试、旧浏览器 11 项和 parse-only 浏览器 10 项证据保持为各自历史记录，不作为本轮规划 / 正式认证端到端验收。

## 实际源与 DTO

读取冻结的公开样稿 `.runtime/recovered-options/result/manifest.json` 中相关字段。官方原始方向位于 `plan.options.design_directions.candidates`：

- `evidence-handbook` / A 证据手册（推荐）
- `acceptance-console` / B 验收工作台
- `guided-walkthrough` / C 分步验收讲义

原 `selected:0` 明确带有 `selection_is:agent_recommendation_only_not_user_confirmation`，不是用户确认。本轮没有把 ID 改成 A/B/C，也没有预选推荐。

依父任务确认的源保留投影契约：outline 保留原 `plan.page_plan`；design.directions 每项保留完整 candidate、共享 `page_plan:{pages:[]}` 与 `production_parameters`。公共页纲使用 ordinal/title/core_message/content[].text/source_refs，包含完整六页中文文案与来源。recommendation 与服务器 confirmation_selection 分离。

`integration/freeze_planning_fixture.py` 从真实公开 artifact 机械生成仅测试使用的 `src/test/fixtures/official-stage2.json`，带原 manifest hash。它没有模型调用，也不是产品 demo/fallback；应用入口不导入该 fixture。

## 产品变化

- `src/planning.ts` 严格适配原始 ID、name/note、object.pages、ordinal/core_message、完整内容文本与 source_refs；缺失或非法页数不再导致 `.map` / `.toString` 崩溃。没有从页序号猜测或虚构缺失页纲。
- 每套卡片显示原名称和 ID、完整说明、逐页视觉表达和六页内容；完整候选风格、页纲元数据及制作参数提供可展开核对。复杂值全部作为 React 转义纯文本 / JSON 显示，不注入 HTML、SVG、URL 或 CSS。
- 推荐区明确提示只是建议；单选默认空。只有当前 snapshot / confirmed revision 一致且服务器确认 ID 属于实际候选，才恢复已确认单选。
- 用户必须真实点选并单独确认。请求仍只提交 `selection.direction_id`、精确 expected_revision 与 input_snapshot_hash，不回传重写的候选内容。展开详情不会隐式选中方向。
- confirm 仍可能返回 partial DTO；原操作流程随后刷新 GET project，因此没有把 partial 当成完整项目使用，也没有扩展服务接口。

## CSRF 修复边界

- 只使用成功 GET /me 返回的真实 csrf_token，存于 API 模块内存，从给 UI 的 profile 中剔除。
- request 对 POST/PATCH/PUT/DELETE 统一附 X-CSRF-Token，包含 JSON 操作与 multipart 上传、确认、取消、删除；调用者自带的同名 header 先移除，不能覆盖真实 token。
- 无 token 时不伪造 header，仍由服务端认证 / CSRF 策略决定是否接受；不使用开发身份绕过策略作为正式验收证据。
- 普通 API、SSE、下载遇到 401 都清除内存 token，之后重新 GET me 更新。GET/SSE/download 不自动附写令牌。
- 不写 localStorage，不打印真实 token，不修改服务端认证。

## 实际验证

使用 README 中捆绑 Node / pnpm 的显式绝对路径，在 web 目录运行：

- `pnpm test`：**6 files / 49 tests passed**，最终 4.65 秒。原 41 项全部保留。
- 新 5 项实际投影测试：三个原 ID / note / 六页完整内容与生产参数、推荐不预选、精确用户选择提交、有效服务器确认恢复、未知复杂值安全显示且展开不选卡。
- 新 3 项 CSRF 测试：真实 /me 契约令牌到 JSON / upload / delete / cancel / confirm，401 清除及重新读取恢复，缺 token / 伪造 header / GET 行为。所有请求仅为测试隔离的 fetch fixture，没有实际 API / 模型调用。
- `pnpm run build`：**TypeScript + Vite passed**，最终 Vite 2.75 秒。
- 最终冻结：`dist/index.html` → `assets/index-C66NMsHg.js`（279.24 kB / gzip 87.71 kB）与 `assets/index-C0RgSHL_.css`（15.78 kB / gzip 4.32 kB）。

没有新 managed job。该构建尚未启用到开发站；等待父任务部署与后续真实规划 / 正式认证验收。不存在新图片、视觉或 PowerPoint 人工验收结论。
