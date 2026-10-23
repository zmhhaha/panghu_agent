# PPT Service Web

React / TypeScript / Vite 中文私有项目工作台。仅使用同源 `/api/v1` 与 `/oauth2/start`、`/oauth2/sign_out`，没有演示数据、模型直连或部署脚本。页面明确显示开发测试状态，PowerPoint 人工验收仍待完成，正式发布与邀请仍未开放。

## 本地安装、验证、构建

本环境使用显式捆绑 Node/pnpm 路径，在本目录执行：

```bash
"C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe" "C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs" install
"C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe" "C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs" test
"C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node.exe" "C:/Users/4770_2070super/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs" run build
```

`pnpm-workspace.yaml` 将 workspace 限制在本应用，只允许 esbuild 的必要安装脚本；不改全局配置。提交锁文件可复现安装。输出 `dist/index.html` 与 `dist/assets/*`，由父任务的 API 同源静态挂载。API、OAuth 路由必须优先于 SPA fallback；`/assets` 应挂载构建目录，未知 `/api/*` 不应返回 HTML。没有修改现有 DSH GUI。后续经父任务授权，仅刷新现有开发 API 容器的只读静态资源挂载；单次脚本在 `integration/refresh_development_assets.sh`，不操作数据库或正式服务。

## 接口边界

`src/api.ts` 是唯一网络与 DTO 适配层，`src/domain.ts` 是 UI 模型。列表为数组；项目后端使用平铺的 `page_count/language/audience/purpose/style/requirements`，UI 适配为 `settings`。GET 项目提供 `assets` 与 `plans`。`plans[].phase=plan_initial|plan_options` 适配为沟通/方向阶段。

- 项目：GET/POST `/projects`，GET/PATCH/DELETE `/projects/{id}`。创建包含默认 8 页、`language:zh` 和 `external_model_ack:false`。PATCH 使用完整项目字段：`title`、全部设置、现有 `external_model_ack` 与 `expected_revision`，避免请求校验失败或默认值覆盖现有设置。
- 资料：POST `/projects/{id}/assets`，multipart `file`；明确同意数据传输后先 PATCH `external_model_ack:true`，再上传。服务端仍须执行全部大小/解析/身份限制。
- 规划：POST `/projects/{id}/plans` `{expected_revision,feedback,phase}`。确认 initial 后且无新反馈的下一步为 `plan_options`；反馈启动新 `plan_initial`。
- 确认：POST `/projects/{id}/plans/{revision}/confirm` `{expected_revision,input_snapshot_hash,selection}`。第一次 `selection={design_mode:'free_design'|'managed_template',template_id:null|id}`；第二次 `{direction_id}`。模板只有服务返回已验收列表时可选。方向只有真实返回三套时可确认。
- 方向输出适配 `plan.design.directions` 或旧 `plan.design.options`；真实官方投影每项保留原 `id/name/note/page_expression`，`page_plan` 为带 `pages` 数组的完整共享对象，`production_parameters` 为原始共用制作参数。逐页适配 `ordinal/title/core_message/content[].text/source_refs`，保留完整元数据供纯文本核对。也兼容旧 `{number,title,purpose,points,sources}`。不虚构候选或改写 ID。
- 生成：POST `/projects/{id}/generations` `{expected_revision,plan_revision}`。页面同时核对已确认 revision、当前输入快照、具体方向。
- 修改：POST `/projects/{id}/revisions` `{expected_revision,base_version_id,pages,feedback}`。逐页意见以明确页号汇入 `feedback`；`pages:[]` 表示整份。选中的基线不随新版本到达改变。
- 任务：GET `/jobs/{id}`，`event_cursor` 适配 UI `last_event_seq`。POST `/jobs/{id}/cancel` 请求后以实际任务状态显示 cancelling，不能乐观宣告 cancelled。
- SSE：GET `/jobs/{id}/events`，fetch 流携带真实 `Last-Event-ID`；消费 `id` / `seq` 单调事件，忽略重复。410/409 先刷新权威快照再连接。仅保留允许的 status/stage 字段，绝不渲染原始日志、推理与命令。
- 版本：GET `/projects/{id}/versions` 数组 `{id,created_at,parent_version_id?,slides:[{number,title,purpose?}]}`。
- 预览：`<img>` 同源授权 GET `/versions/{id}/slides/{n}/preview`；API 须固定 PNG/JPEG Content-Type 且禁止 SVG。下载 GET `/versions/{id}/download` 真实成功响应后才创建下载 Blob；不伪造 PPTX。
- 账号：GET `/me` `{issuer,sub,name?,quota:{enabled,...},usage?,deployment_mode:'test',manual_acceptance:'pending'}`。用量缺失显示未知，费用缺失绝不当零，未配置额度显示“未启用”。

GET 项目还应返回当前 `input_snapshot_hash`（不是旧方案 hash），确认后的方案返回 `confirmation_selection.direction_id`，供客户端恢复准确状态。后端仍是快照、确认失效、权限、并发、幂等与发布状态的权威。

`design.recommendation` 只显示为建议，不读取 source selected/index 作为用户选择。仅 `confirmed_at` 存在、`confirmation_selection.direction_id` 属于候选，且当前 input snapshot / confirmed plan revision 匹配时，才恢复已确认单选。用户确认只提交精确 `selection.direction_id`；方向内容仍由后端加载。候选说明、六页完整文案、源引用、制作和样式参数均用转义纯文本展示，复杂未知字段放在展开核对区，不注入 HTML/SVG/CSS。确认返回 partial DTO 时仍由既有操作流程刷新 GET project，未将 partial 当成权威完整项目。

GET `/me` 的真实 `csrf_token` 仅保存在 API 模块内存，并从返回给 UI 的 profile 中剔除。POST/PATCH/PUT/DELETE（含 multipart 上传、确认、取消）统一附 `X-CSRF-Token`，调用者提供的同名 header 不能覆盖真实值。没有 token 时不伪造 header，仍由服务端认证与 CSRF 策略判定；401（含 SSE / 下载）会清除旧 token，重新 GET me 后更新。GET/SSE/download 不自动附写令牌；不写 localStorage，不打印令牌。单元验证不代表正式 JWT/SSO/CSRF 反向代理端到端验收。

所有变更发送 `Idempotency-Key`。同一未完成意图与相同 payload 复用内存 key，成功后完成意图；改变 payload 产生新 key。上传使用项目 revision 与文件元数据绑定重试 key。按钮同步 ref 防重复提交。浏览器 localStorage 只存选中的项目 ID 和每个任务的事件游标，不保存正文、token、方案反馈或登录凭据。

## Parse-only 前端增量（2026-10-22）

AssetDTO 新增 `text_summary: string | null`、`parse_error: string | null`。文件使用独立解析状态：`queued` 等待解析、`parsing` 解析中、`stopping` 正在停止、`ready` 完成、`failed` 失败；不把材料 ready 解释为演示稿已生成。摘要仅在 ready/旧 parsed 状态按转义纯文本展示，前端进一步限制 2000 字符；缺失、null、失败或来源移除后清除旧摘要。安全错误采用 `src/parse.ts` 的允许清单中文说明，未知值不原样显示。

`src/useParsePolling.ts` 对 queued/parsing/stopping 的当前项目每 5 秒读取一次详情，不轮询版本列表，不重启任务。仅资料标签页且浏览器可见时执行；切换标签、切换项目、隐藏页面或卸载会清理定时器并中止请求，忽略迟到响应；ready/failed 或权限错误后停止。手动“刷新状态”保留。解析修订变更不会重置尚未保存的设置。

开发通知明确区分制稿执行器尚未接通。仅当真实文件状态已出现 parsing/stopping/ready/failed，才显示隔离本地解析说明，旧 queued 数据不会静态声称解析器已接通。SSO、PowerPoint 人工门和制稿 503 保护保持原样。

解析增量随后已由父任务上线，并完成 10 项真实浏览器检查，详见 `integration/PARSE_BROWSER_RESULTS.md`；首次未通过尝试亦保留。历史 `integration/RESULTS.md` 仍是更早一轮 27 测试 / 11 浏览器检查证据。规划兼容与 CSRF 的本轮构建尚未部署，验证范围见 `integration/PLANNING_COMPATIBILITY.md`。

## 验证范围与限制

Vitest + Testing Library 验证快照失效、两阶段 selection payload、无方案不虚构、幂等与错误过滤、断线恢复/游标过期、真实取消状态、旧版基线保留、未知费用和源文本转义。测试 API 仅存在测试文件，产品无 mock fallback。27 项 Vitest / Testing Library 测试通过。另使用 `integration/browser_smoke.py` 对现有开发 API 做真实 Playwright DOM 集成，11 项检查通过，覆盖创建、同意与上传、设置、刷新恢复、预期执行 503 和 320/390px 布局边界，详见 `integration/RESULTS.md`。未启动真实模型、未验证完整生成执行器、正式 SSO、PowerPoint 人工验收或视觉验收。
