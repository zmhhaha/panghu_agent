# 项目删除与恢复验证

2026-11-13，OpenSpec变更 `add-video-project-trash-and-restore`。源码先以c635221实现可恢复删除，再以b53202c修正真实浏览器发现的新建结束状态重绘遗漏。保持开发分支 `codex/video-capability-experiment-redesign`，不合主线。

## 行为与边界

当前项目标题右侧增加“删除项目”。确认框明确说明移到回收站，原稿、画面、录音和制作记录保留。左侧“我的项目／回收站”提供恢复并打开。删除后退出该项目并停止播放器、预览与轮询；恢复沿用原项目、场景、采用指针和历史。

接口为 `GET /api/projects?deleted=true`、`POST /api/projects/{id}/trash` 和 `/restore`，写入提交 `expected_revision`。使用独立墓碑表，owner、CSRF及版本验证与普通制作一致；旧删除请求不能在恢复后重新删除。同一SQLite写事务检查所有任务通道、新准入及删除状态，运行中、draining、未知提交、停止未确认均拒绝删除，不主动取消。已有付费幂等键可回读旧终态摘要，不会产生新制作；正常资源与制作历史入口在恢复前隐藏。

删除不物理回收媒体，也不变更平台存储治理任务。整片状态、Premiere式布局、时间线和批注只整理建议与可点击提案，未在本轮重排正式工作台。

## 自动化与独立审查

后端最新版生命周期专项26项通过；原工作台、音频、批量、整片编排、联合策划和历史讲解组合136项通过；动画存储及音频制作媒体访问回归73项通过。各组含重叠，不能直接相加。覆盖按owner隔离、CSRF、版本和丢响应重放、已结束历史失败可删除、未知上游停止阻止、独立SQLite实例并发、源码／WAV／视频访问及恢复、媒体和采用指针不变，以及真实StaticFiles提供新模块。

前端相关52项通过，覆盖删除确认取消、未保存稿件、整片与历史方案未提交表单、未知写入只GET恢复、晚响应与项目切换隔离、所有播放器清理、回收站恢复。独立审查发现的方案表单草稿遗漏已修正，只用于项目管理的未保存检查。

真实浏览器在c635221版本发现：新建操作的pending锁已经释放，但未再次渲染，导致新项目删除及策划按钮沿用忙碌状态。刷新可验证数据并不忙，但该现象不能视作通过。b53202c在创建的finally释放锁后重绘；新增真实app“新建空项目→直接可删除／策划→删除”回归，旧实现失败、修正后通过。

## 发布与网页验收

两次都从推送的完整Git源码普通Dockerfile构建，只使用工作台latest加digest，只更新video-workbench Deployment。发布前SQLite在线备份，核对传输摘要和原业务文档；实际UID/GID10001、断网、只读根文件系统导入应用后才发布。不修改Service、共享Agent、TTS、渲染器、实验室、域名或登录。

首版c635221的digest为 `sha256:c1bd9e7da083b629da76485a6d438ec1fcab5ffa83f905527a3fb6f8b6280ef9`，Ready及数据保全通过。使用新建空项目 `c4103856-e1d9-494c-b31b-2def06353247` 验证取消、删除后列表隐藏、回收站出现，以及恢复同一身份／标题／原文／0镜头；未对用户原有项目执行删除。

最终修正版本b53202c的工作台digest为 `sha256:6ccf800287659945c057b07041446aa09aaefcd0a5609ee473bd2d3e6a1a1550`，源码上下文摘要 `c02cb50f926d0dd89dc0fdef8690d483fdc9d3a65d8547b864c2288fc08c5600`。新Pod UID `d00368e8-98e4-41db-bb71-af628a1cc843`，Ready和原业务文档保全通过，发布回执为 `deployed_ready`。此次SQLite备份摘要为 `a49a8a92366f358f814e3b26a5a802b4bf3efb49fcf0fa4a6a28cc43bdc6f1e8`。

最终浏览器新建项目 `166a714e-3dd4-47c7-ab6d-de7aed4f3542` 后，未刷新页面，删除按钮和策划按钮立即可用；确认删除后退出原项目，回收站出现，恢复后仍为同一个ID、标题、原文和0镜头，删除按钮仍可用。两个本轮空验收项目最终均移入回收站；正常列表13个项目ID与验收前逐一一致，未删除任何原有项目，未提交模型、配音或渲染。最终浏览器记录为 `project-lifecycle-browser-final.json`，确认框截图为 `project-trash-confirm-final-20261113.png`。

证据使用既有D盘目录 `D:/github/armbianbegin/.research-temp/video-agent-development-20261112/`，包含 `project-lifecycle-backend-test-receipt.json`、`project-lifecycle-browser-r1.json`、`project-trash-confirm-20261113.png`、`project-lifecycle-r2-build-output.json`。服务器发布证据位于既有creation-product-r1下 `project-lifecycle-release-r1`／`project-lifecycle-release-r2`，发布脚本摘要 `6aac97f61b1183230c335454d03b71b4eca74af9bb4bcb419d74e0feb6b1b21d`。

OpenSpec变更已完成7/7，精确回读一致且校验通过，revision为 `2a08b9b9bd9ada5424dad886fa8bd247d7d7518d`；同步回执为D盘证据目录下 `openspec-project-lifecycle-completed.json`。本完成只涵盖回收站及交互提案整理，不把尚未实施的完整工作区改版标为完成。
