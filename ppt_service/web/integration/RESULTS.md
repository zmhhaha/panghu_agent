# 开发站真实浏览器 DOM 集成结果

测试时间：2026-10-22 01:34（Asia/Shanghai，按运行时间约记）。用户入口 `http://127.0.0.1:18505`。测试浏览器运行于既有 `ppt-service-worker:arm64-c350bc31-preview5` 镜像的临时 Chromium 容器，加入仅开发站网络。`page.route` 只将浏览器同源 URL 转发至真实开发 API 容器，并保留浏览器 Origin；没有构造 API 响应或使用 mock 数据。

## 最终结果

**11 项实际检查通过，0 JavaScript 页面异常，0 非预期 console error。** 唯一 console error 是浏览器报告预期规划 HTTP 503。

| 检查 | 真实结果 |
| --- | --- |
| React 加载与门禁 | 显示开发测试、PowerPoint 验收待完成、本地账户 / SSO 未接通 / 执行器未接通 |
| 创建私有项目 | POST projects 201，实际工作区加载 |
| 未同意上传 | 文件输入保持禁用 |
| 同意与上传 | PATCH 200；multipart POST assets 201 |
| 关闭页面等效刷新恢复 | reload 后仍显示所选项目及实际上传文件 |
| 保存要求 | PATCH 200，按钮恢复已保存禁用状态 |
| 尚未接通执行器 | POST plans 503，显示可操作说明；仍无方案、无生成按钮，无虚构成功 |
| 返回列表 | 新建项目出现在真实列表 |
| 390px 响应边界 | 三个工作区 document.scrollWidth=390，越界元素数组均为空 |
| 320px 响应边界 | 三个工作区 document.scrollWidth=320，越界元素数组均为空 |
| JavaScript / console | page_errors=[]；unexpected_console_errors=[] |

成功 fixture：`56662aa0-c231-4a49-9e24-964664302275`，标题 `DOM-INTEGRATION-1792604083058977591`，文件 `dom-integration-material.md`。资料仅说明本次集成测试，不含私人信息。新建测试项目保留供父任务核查，没有删除任何原有项目。

## 本轮发现并修复

1. 首测真实 PATCH 返回 422，detail 指出必填 `title` 缺失。更新 adapter：ack 与设置保存均发送完整项目字段，并保留原设置与已同意状态。新增两项回归测试。首次失败 fixture `05fcfb5e-1646-4347-a4fb-b9975e350737` 只创建，未成功上传，保留未删除。
2. 320px 下真实 document.scrollWidth=369，越界为刷新按钮。窄屏标题区允许换行，长标题可断词。修复后六个 tab/viewport 组合均无溢出，没有通过隐藏 overflow 掩盖问题。

## 最终构建与服务

`pnpm test`：3 files / **27 tests passed**。`pnpm run build`：TypeScript + Vite **passed**，最终资源 `index-DsAXq6Uo.js`（268.71 kB）和 `index-C0RgSHL_.css`（15.78 kB）。所有 pnpm 命令通过本环境捆绑 Node + pnpm 显式绝对路径执行，详见上级 README。

经父任务授权，只刷新开发 API 容器：更新前确认 state=running、label `app=ppt-service-webdev`、固定镜像 `sha256:8cc3f72b86756b1644e850047b548fe91cef656f3b920bf5b72e7a62d1e40e93`。沿用该镜像重建同名容器，保持 user10000:10000、cap-drop ALL、no-new-privileges、只读根、/tmp tmpfs、768MiB、原项目数据 bind；新增最新 dist 的只读 bind。env-file 作为不透明路径使用，未读取或输出凭据；未操作数据库 / volume。

新开发 API 容器 ID `02254e98443cc3357d23455800a7807337d6da464e091da03cad95bed9c532a5`，IP 保持 `172.18.0.3`，PortBindings `{}`，ReadonlyRootfs `true`。原父任务 SSH managed tunnel job `dfede5167d30432b` 继续使用，不需要调整。

临时浏览器容器 `ppt-web-dom-smoke-20261022`、布局探针容器均 `--rm` 同步运行并已退出；本子任务没有新增 managed job。没有模型调用、真实 PPT 生成、截图视觉验收或 PowerPoint 人工验收。正式 SSO 与完整生成执行链仍待后续接通。

## 复现浏览器检查

从 web 目录运行（既有专用开发网络和容器必须由父任务管理）：

```bash
scp integration/browser_smoke.py root@192.168.137.101:/tmp/ppt-web-browser-smoke-20261022.py
ssh root@192.168.137.101 docker run --rm --name ppt-web-dom-smoke-20261022 --network ppt-service-webdev-20261021 --entrypoint python -v /tmp/ppt-web-browser-smoke-20261022.py:/tmp/browser_smoke.py:ro ppt-service-worker:arm64-c350bc31-preview5 /tmp/browser_smoke.py
```

每次运行会创建一个唯一前缀的新 fixture 项目；规划请求仅用于验证开发执行器未接通的 503，运行此脚本前必须维持该开发模式，不能对接入真实模型的生产站运行。
