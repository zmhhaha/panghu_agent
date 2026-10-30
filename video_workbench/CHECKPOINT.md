# 视频工作台交接记录

更新：2026-10-30（Asia/Shanghai）。本文件列出继续工作必须保持的项目、版本和边界；当前结果见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，执行顺序见 [NEXT_STEPS.md](NEXT_STEPS.md)。

## 工作位置与当前进度

- 工作树：`D:\github\armbianbegin\.video-workbench-worktree`。
- 分支：`codex/svg-video-workbench`。未合入主线；全部任务结束并校验后才请用户决定是否合并。
- 服务器独立目录：`/root/video-workbench-release-20261029/`，证据位于 `evidence/`。不把共享科研源码目录当作本次固定构建来源。
- 已部署工作台能真实登录；登录隔离、重启持久化、备份／离线恢复已验证。
- r7 已完成 SVG 修订与 V2 采用、独立运行中取消；原 HTML 失败记录保留。r8 新 HTML 完成并明确采用，已导出 SVG V2 与新 HTML 的正式 ZIP，源码／预览／播放器结构核对通过。
- f90 修复、新 HTML 候选、工作台 55ee、scene 人工续接发布均已完成。发布首次 release_guard 超时失败保留，不写成全自动成功。浏览器控制、最终备份、证据归档和验收环境清理已完成；固定画布裁切限制已记录，自适应仍待优化。

## 继续使用的项目与版本

| 对象 | 固定值 |
| --- | --- |
| 制作项目 | `49336596-ee04-4478-8a44-8d87aa470b28` |
| 当前 r8 项目修订号 | `15`；历史 r7 快照为 `12` |
| SVG 镜头 | `3b1e9c34-be8e-4aab-8192-155f858091a2` |
| SVG V1 | `678e5d30-92f2-4bbf-bd67-d098b4f6b9b3` |
| 已采用 SVG V2 | `5632a726-b19f-4c7c-949b-2a8f076372a8` |
| HTML 镜头 | `04c12ebc-2f6d-4d83-bd5f-e44dd07929ec` |
| 已采用新 HTML 版本 | `fdf22ab7-5254-40f1-b4d1-0e333bc0d885` |
| 新 HTML operation | `8255189a-9dec-4ec4-9b33-a6589bdfe120` |
| 已完成新 HTML Job | `26df9a50-0f75-4711-8f3e-6b7652762f0f` |
| 已完成分镜 Job | `2b0ee239-f867-49a9-ac79-74a711e6283f` |
| 已完成 SVG 初稿 Job | `9a264e44-e270-4c57-8a4b-79dd69f8deca` |
| 原失败 HTML Job | `c27f9205-5987-4f20-bd68-9ae77a2f163d` |
| 已完成 SVG 修订 Job | `d5c82bd4-4f2c-4813-870c-17f741d03454` |
| 已取消独立 Job | `215ea471-a9ae-4ddb-b418-5666206b29cc` |

V1 SHA-256：`fc850309ad3e4913574bad1767369377f2f738b57620676d51d8863d146247c4`；V2 SHA-256：`65e36c123d0d228816c60243e8dcf90857712f267001502d0aefdda36f23654d`。V2 的父版本必须保持 V1。新的 HTML 已完成，现有分镜、SVG、修订、HTML 与取消均无需重复生成。

## HTML 修复与最后一次制作

r6 原 HTML 输入 SHA-256 为 `9655f6fd4b450fd1527a2722669ad96a4116c34967296b3262687e08980fc28a`。旧幂等键为 `live-video-live-r1-html`。r8 已用新的幂等键完成生产 HTML，旧失败任务保持原状态。

原失败的直接原因是校验器拒绝 `<meta charset="UTF-8">`。f90 仅增加 head 中单一 charset=UTF-8 声明的支持，其余 meta／属性／位置限制不变。修复源码为 `f90ba1cf00ab90ff06051e08db4b6ef2c933a7f7`；profile 82 通过、工作台与 gateway 112 通过，各自统计。

新工作台 image digest 为 `sha256:55ee0651e38565b68869b557c2ae9dbd76456ce25134402d4cad5401308a575a`；新 Pi 为 `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75`，runtime 为 `video-pi-f90ba1c-html-r1`。工作台 55ee 已 Ready，Pod 为 `video-workbench-6d6499f7d4-ddddr`，UID 为 `482faee0-608b-4784-85aa-968834c8f051`，真实用户刷新后项目保留。新 HTML 候选 `candidate-video-html-r1` 已完成，4 次模型调用含探针；UID 44000006–44000008，Worker／代理均实际终止、退出码 0、重启 0，独立审查材料已准备。scene 已经人工续接发布，状态为 `scene_binding_upgrade_ready`；当前 control digest 为 `sha256:ec86cd6602df9e3783b2d0f0c768a6f01fee129385d8987f9c73da1ea2b9c7aa`，源码 f90。新池 `video-scene-pi-72fc4ecbab3cf7c8edaa` 使用 UID 46200000–46265535。18 条路由、58 个池，原 57 池和其他 17 条路由完整保留；科研 6／PPT 5／视频 2 项能力实际查询全部 ready。

r8 本轮提交 1 次、完成 1 次，状态为 `html_tail_verified_adopted_exported`。累计生产制作提交 5、完成 4，原失败 HTML 1；另有独立取消任务 1。额外 HTML 候选与生产各一次额度已使用，不自动重试。

本地最后尾段工具为 `.runtime/video-release/finish_html_after_charset.py`，默认只显示计划。本轮已执行完成，只允许一次 HTML generate 和一次 adopt；不应重复运行。它核对实际部署、历史证据、未采用前导出 409，以及采用后 ZIP 中的 SVG V2／新 HTML。临时 token 文件已删除；HTML 生成＋收集 92.654 秒，整个尾段 95.51 秒。HTML 源码 SHA-256 为 `1bbf454b193be3e74acd1e13583423161c62f5691bc44f71410455a390af3cdf`。真实浏览器上一／下一、重新载入后 1/2 自动至 2/2（配置 10 秒，观察 10930 毫秒）及暂停后手动切镜通过，记录于 `browser-review-final-r8.json`。完整 1280×720 HTML 可读；固定画布在 1280×656 播放器区域会裁底部 footer，自适应缩放仍待优化，不能声称全视口无裁切。

## 不能改写的历史

r4 丢弃 SVG 提交回包后，自定义 lookup 503 被旧工作台误判为失败。r5 先做受控人工修复，再在固定修复镜像窗口验证查回原 Job 与重启恢复，新增 Jobs POST 为 0。不得将此写成 r4 全程自动恢复通过。

scene 发布首次执行在 release_guard 超时停止；当时只创建配置、预留 UID 区段 46200000 并暂停 dispatcher，API／模型代理仍为 aff85。经审查的人工续接已完成并 Ready；原失败记录保留，不能声称全程自动发布通过。

r6 HTML Job 保持 failed／worker_output_invalid，无发布 manifest。原模型 write 参数中恢复的 HTML 可供离线复核，但 Worker 临时目录已清理，不声称独立读取到了原磁盘文件。r7 仍为部分尾段通过、HTML 失败、导出受阻；后续成功另作记录，不覆盖原报告。

## 证据和备份

- r7 证据：`evidence/business-partial-tail-r7/`，包含修订与真实取消停止证明。
- r8 证据：`evidence/business-html-charset-r8/`，包含新 HTML、明确采用、正式 ZIP 与结构检查；原 r6／r7 不覆盖。
- 本地归档：`.runtime/video-release/business-evidence-r7.tar.gz`，SHA-256 `ebc8cf9c3eda9421cef028091466f0d9ff10012d2651597d8486d6162eeb1b42`。
- 最终备份：`evidence/sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3/`，projects 3、operations 6、versions 3，包含新 HTML；备份／恢复 SHA-256 均为 `6c82e59c8b8e7eecad02cfd4d89a0c967b22c38f2d1ad85c893745244456f885`。只读快照、独立恢复、全部表行数、完整性与 hash 均通过，未回写生产 PVC，备份临时文件已清理。
- 历史备份 94d 包含 versions 2、operations 5，继续保留，不作为最终快照。
- 最终归档：`evidence/business-evidence-final-r8.tar.gz`，650306 字节，144 个源文件加内置 manifest，共 145 个成员；SHA-256 `051ee361c4cd486ebb481e756e9d7356e9a85b5cc5f8327cc751e178f72bdd7e`。服务器写后逐成员验证和本地下载整包 SHA 已通过，数据库／凭据排除；内嵌 r7 归档全部 247 个成员也已校验。
- 清理回执 `acceptance-cleanup-r8` 为 `cleaned`，精确处理 11 个本次目标，PVC 与工作台均保持不变；3 个自有端口转发停止、剩余 0。
- 公开 Jobs usage 仍为 null，费用未知；不留没有可信来源的 token 汇总待办。运行超时和精确槽位释放时间未实测。
- OpenSpec 规格／设计／任务已整合，18 项完成，验证通过且无问题；未执行 apply 或 archive。

入口为 `https://video-service.panghuer.top`，Casdoor 回调为 `https://video-service.panghuer.top/oauth2/callback`。这些配置已由用户完成，后续不重复要求配置。实际工具和测试范围见 [VALIDATION.md](VALIDATION.md)。
