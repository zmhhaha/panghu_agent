# P4 逐版本验收工作台

本次交付独立的逐页检查界面、验收记录 API 和持久化模块。尚未挂入正式网站；主实现完成证据存储、数据库迁移和页面接线后才能用于真实项目。交互演示全部使用合成页面和内存记录，不代表样稿通过验收。

## 用户可以做什么

1. 选择明确版本，查看逐页 PNG 预览；检查文字、版式、图片和图表。
2. 为每页选择待检查、检查通过或需要修改，填写问题类别、严重程度和具体说明。未完成的检查也可以保存。
3. 在桌面 PowerPoint 实际打开对应 PPTX，编辑文字或形状、检查原生图表编辑、保存并关闭重开，记录软件版本、操作系统和各项结果。
4. 查看带检查人和时间的历史记录。撤销必须填写原因，原记录保留；撤销最新结论后不会恢复更早的通过结论。

所有结论由人主动填写。静态预览、程序检查和桌面操作分别记录。原生图表为零时可以明确选择该项不适用，文字或形状编辑仍需实际检查。尚未检查的步骤保持待检查。

## 文件与运行入口

| 路径（相对 ppt_service） | 用途 |
| --- | --- |
| `web/src/acceptance/AcceptancePanel.tsx` | 独立 React 组件 |
| `web/src/acceptance/client.ts` | 复用现有登录、CSRF 和幂等请求 |
| `web/src/acceptance/demo.html` | 本地合成数据演示，不进入生产入口 |
| `server/acceptance/` | 请求模型、可信上下文接口、记录服务和独立路由 |
| `server/acceptance/contracts/` | Python 生成的 JSON Schema 和前后端共用样例 |
| `tests/server/test_acceptance.py` | 独立鉴权、持久化、并发和失效测试 |
| `tests/server/run_acceptance_checks.sh` | ARM64 现有固定镜像中的隔离测试 |

在 `web/` 中运行：

```sh
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 18506 --strictPort
```

打开 `http://127.0.0.1:18506/src/acceptance/demo.html`。演示只在本机监听，页首明确标识模拟数据；刷新清空记录。演示的 service worker 仅控制 `/src/acceptance/` 文档，拦截两个合成版本的精确预览路由，不处理真实项目或其他域名。生产 App 未引入演示代码。

## API 合同

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/v1/projects/{project_id}/versions/{version_id}/acceptance` | 获取指定版本的当前状态和历史 |
| `POST …/acceptance/records` | 追加视觉检查、桌面检查或撤销记录 |

POST 必须携带 `Idempotency-Key`、当前 `expected_revision` 和服务端返回的完整 `binding`。同一请求丢失响应时沿用原 key 与原 body 重试；不重复追加。其他提交已改变记录或来源时返回 409，用户重新读取核对。服务端身份和时间不接受浏览器传入。

绑定覆盖项目和版本 ID、来源修订号、输入快照、完整源文件指纹、PPTX、manifest、独立证据 ID/hash，以及执行引擎、运行环境版本、profile、检查点格式。逐页提交还需匹配完整有序页单及 PNG hash。

`source_revision` 是可信存储中单调递增的失效编号。源资料、证据、运行环境或版本资格发生变化时必须递增，恢复旧文件也不能恢复旧编号；这样旧检查不会因 hash 恢复而自动复活。

## 数据库与接入提案

独立 SQLAlchemy metadata 定义两张表，本模块不在导入或启动时创建表：

| 表 | 数据与约束 |
| --- | --- |
| `ppt_acceptance_streams` | owner issuer/sub、project/version、记录修订号；按 owner 与版本计算唯一 scope |
| `ppt_acceptance_entries` | 追加记录、请求 key/hash、身份、服务端时间、完整绑定和检查内容；`(scope, revision)` 与 `(scope, request_key)` 唯一 |

由主实现统一审阅迁移、外键及删除生命周期：项目删除先撤销访问，再按项目清理；备份恢复须覆盖版本、证据、失效编号和两张验收表。不能只恢复验收表或重置失效编号。共享核心模型及迁移此次未改。

接入代码的形状如下，`resolve_acceptance_context` 必须由核心实现提供，不能将演示 fixture 接到真实服务：

```python
service = AcceptanceService(SessionLocal, resolve_acceptance_context)
app.include_router(create_acceptance_router(service))
```

可信解析器的要求：

- 复用已验证 JWT 的 issuer/sub，核对项目、版本和证据所有权；跨用户、已删除或缺失资源返回 404。路由复用现有 `authenticate` 的代理来源、邀请名单、Origin 和 CSRF 检查。
- 在同一个事务内，按固定顺序锁定项目、版本和证据，直到读取或提交完成。读取也需要一致的来源上下文。文件必须冻结；数据库锁不能替代文件不可变性。
- 核对受信存储里的完整源文件、PPTX、独立 PNG 和证据 hash，不从 Worker 自报的 `accepted` 布尔值生成可信证据。`needs_input`、失败和规划结果不可升级为可验收版本。
- 当前核心预览路由仅服务已发布 Version。候选稿检查需新增或审阅专用授权 PNG 适配；不能为复用路由而伪造已发布记录。实际响应需为该 hash 对应的 PNG、受限大小和 `nosniff`，不能用 SVG 或可变 URL 代替。
- 核心统一挂载独立路由，在版本页面传入明确 `projectId/versionId` 使用 `AcceptancePanel`；切换版本清除旧草稿并忽略旧请求的晚到响应。

## 完成状态的边界

`visual_status`、`powerpoint_status` 和 `human_status` 只描述当前绑定下的人工记录。问题未解决、步骤缺失、来源变化或记录撤销都不能计入通过。

本模块固定返回 `programmatic_status=pending`、`publication_authorized=false`，完整验收状态仅为待完成、来源失效或暂不可检查。P3 完整 exporter 校验及控制器证据接入仍待完成；此处不从保存的 JSON 重建 P3 的进程内可信对象，不发布版本，不把旧样稿改成已通过。

后续接入仍需：核心持久证据与解析器、审阅后的迁移、PostgreSQL 事务验证、真实登录与预览接线，以及实际 PowerPoint 人工操作。独立模块交付不等于 P4 整包完成或正式发布。

## 复现验证

在仓库根目录：

```powershell
$env:PYTHONPATH='D:\github\armbianbegin\panghu_agent'
python -m unittest discover -s panghu_agent/ppt_service/tests/server -p test_acceptance.py -v
```

在 `web/` 中：

```sh
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

### 2026-10-22 验证结果

- 后端：在固定 ARM64 API 镜像中运行 **38/38** 项测试，2.156 秒。覆盖真实测试 JWT 签名、代理来源/issuer/sub/CSRF、越权、来源与运行环境失效、完整页单、旧记录撤销、丢响应重试、SQLite 持久化重启与并发、读取修订一致性。此处使用临时 SQLite，不代表 PostgreSQL 锁和迁移已经验证。
- 前端：整个现有测试集 **72/72** 通过，6.02 秒，其中新增 P4 **22 项**；TypeScript 与 Vite 生产构建通过。P4 尚未引入生产入口，因此生产 bundle 仍不包含验收页面；独立演示已在真实浏览器验证。
- 浏览器：确认 PNG 翻页、空问题阻止保存、保存逐页问题、未完成桌面检查保持待检查、撤销保留历史、版本之间记录独立、来源失效后禁写且仍可翻页。所有页面和操作数据均为合成示例，没有完成真实 PPT 的视觉或 PowerPoint 验收。
- 测试镜像：`sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7`。容器禁网、UID 10000、只读根与源码、drop ALL、no-new-privileges、资源限额，只有临时目录可写，无真实数据库/密钥/模型环境。具名容器退出后已核对无残留。
- 远端证据：`192.168.137.101:/root/ppt-service-p4-20261022/unit-results.txt`。已测源码包 SHA-256：`6550ae84cea0a51661cf06b5c623afd07e6c5b593b9b4d6ca90cc6303aad460d`，与本机包一致。上传首次因 Windows 换行解码报错，去除传输换行重传后 hash 核对成功；测试本身一次通过。
- 本地文件清单及验证收据：`docs/acceptance-workbench-evidence.json`；页面截图：`docs/acceptance-workbench-preview.png`、`docs/acceptance-workbench-review.png`。

本次先完成 P4 的独立组件与记录模块，P4 整项保留未完成。没有修改网站运行环境或发起模型请求。整个 `ppt_service/` 是并行开发的未跟踪目录；此次不将其他作者内容加入提交。
