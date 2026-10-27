# P4 逐页检查组件

此目录的人工验收界面已由 `App.tsx` 的版本页接入，选择具体审核草稿后读取其真实服务端记录。保存、撤销和读取检查记录均不触发模型调用或发布；版本页另提供需要明确同意和提交的审核稿修改操作。

```tsx
import { AcceptancePanel } from './acceptance';

<AcceptancePanel projectId={project.id} versionId={selectedVersion.id} />
```

宿主还可传入 `onSnapshot` 和 `onUseFeedback`。前者接收已读取/已保存的服务端快照，读取或来源冲突时置空宿主可提交状态；后者只带入最新、当前有效的已保存视觉问题，并传递精确 `binding`、`reviewRevision`、页号和问题文本。未保存草稿和被撤销/失效的记录不会带入。组件本身不提交模型修改任务。

由宿主先完成既有 `/me` 登录与 CSRF 初始化，再挂载此组件。默认 client 复用 `../api` 的 `request` 和 `mutate`，保留同源 Cookie、内存 CSRF、同一提交的幂等重试。可注入 `AcceptanceClient` 用于隔离测试。

接口：

- `GET /api/v1/projects/{project_id}/versions/{version_id}/acceptance`
- `POST /api/v1/projects/{project_id}/versions/{version_id}/acceptance/records`

提交完整 `binding` 和 `expected_revision`。逐页检查提交完整页列表；浏览器加载 PNG 后才能选择“检查通过”。桌面检查要求填写实际 PowerPoint 版本和平台，并分别记录打开、编辑内容、编辑原生图表、保存、重新打开五项结果；无图表仅允许图表编辑“不适用”，内容编辑仍须实际验证。新增或撤销均由服务端返回完整快照。前端展示服务端结论；当前独立模块的 `programmatic_status` 固定 `pending`，`publication_authorized` 固定 `false`。人工通过不会变成完整验收通过。

选择不同版本时会清空未保存草稿，并忽略上一版本在途请求的晚到结果。同来源刷新会保留未保存的草稿并提示核对最新记录；来源修订改变时清空旧来源草稿并说明原因。撤销最新记录后不会恢复更早的通过结论，也不会清空另一种检查的草稿。来源失效时保留历史但禁止新检查；可撤销已有记录。预览只接受所选版本的固定同源 PNG 路由，不接受任意 URL、SVG、data URL 或带查询参数的替代地址。

宿主后端必须提供经过鉴权、锁定和来源核验的上下文、正式数据库迁移与路由。缺证据时不可用演示 fixtures 充当项目证据，也不可从人工检查记录推断发布权限。正式部署和服务器集成检查以本轮发布记录为准。

## 历史合成交互演示（不用于线上验收）

从 `web/` 运行：

```sh
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 18506
```

打开 `http://127.0.0.1:18506/src/acceptance/demo.html`。所有数据均为合成数据，保存和撤销只保留在当前标签页内存，刷新即清空。演示 Service Worker 的控制范围仅为 `/src/acceptance/`，只将演示版本的预览请求映射到本目录实际 PNG；它不控制网站首页，不会访问模型或写入服务端。PNG 由 `demo-assets.py` 用 Pillow 和微软雅黑字体绘制。

## 服务器验证

```sh
node node_modules/vitest/vitest.mjs run src/acceptance
node node_modules/typescript/bin/tsc -b --pretty false
```

测试在服务器执行，覆盖版本切换、晚到的读取与保存结果、来源失效、撤销、未完成桌面检查、错误与重试、恶意文本与预览地址、只带入当前已保存问题，以及 Python 后端生成的共享 JSON fixtures。未经过实际 PowerPoint 人工验收。
