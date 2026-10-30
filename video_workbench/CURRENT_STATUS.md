# 视频工作台当前状态

更新：2026-10-30。工作树 `.video-workbench-worktree`，分支 `codex/svg-video-workbench`。**没有合入主线；真实制作功能链路、浏览器控制、最终备份、证据归档和验收环境清理已完成，固定画布裁切限制已记录。**

入口：[video-service.panghuer.top](https://video-service.panghuer.top)。本期制作静态、可编辑的 SVG／HTML 和审阅 ZIP。工作台保存项目与审阅状态，现有通用 Agent 执行任务。配音、动画时间轴、MP4、图片生成尚未实现。

## 当前结果

工作台已部署并能真实登录，文稿到分镜、SVG／HTML、修订、明确采用和 ZIP 导出的功能链路已跑通。修订后的 SVG V2 与新 HTML 已采用，导出包的版本、源码、预览和播放器结构核对通过。独立运行中取消及真实 Worker 停止已验证。真实浏览器已检查完整 HTML 和播放控制，包含新 HTML 的最终备份已通过。播放器存在固定画布裁切限制，见下文；最终证据归档已校验并下载，验收临时资源已清理，工作台与 PVC 保持不变。

截至 r8，生产制作共提交 **5 次、成功 4 次、失败 1 次**；另有 **1 次独立取消测试任务**。候选实验另行统计，不混入上述制作数量。没有自动重复付费提交。

## 已部署和已验证

工作台、独立登录代理、会话 Redis、1Gi SQLite PVC 均已部署。用户完成域名和 Casdoor 回调配置，并在真实浏览器登录。

登录后创建“工作台登录与编辑验收”项目 `225d6c68-c5e3-4ba7-b225-d54856bfd68b`；工作台多次重启后仍保留。另一 operator 身份读取该项目返回 404，用户页面不显示 operator 项目。匿名及伪造身份头不能绕过登录。

此前已发布两个视频 profile 和专用 caller，逐值保留旧科研／PPT 的 16 条路由和 55 个运行池。科研 6 项、PPT 5 项、视频 2 项能力已通过实际只读查询。已发布工作台精确镜像通过 ARM 禁网容器检查，涵盖非 root、只读根文件系统、Host／身份／CSRF 与数据库重启持久化，模型调用 0。这些结果不代替新镜像和新场景的验证。

## 已发布镜像与本次修复

下表记录目前已确认的部署。scene 升级经人工续接完成，分镜沿用原 Pi 绑定。

| 组件 | 源码 | 已部署 latest 镜像 digest |
| --- | --- | --- |
| 工作台 | `f90ba1c` | `sha256:55ee0651e38565b68869b557c2ae9dbd76456ce25134402d4cad5401308a575a` |
| 共享 Agent control | `f90ba1c` | `sha256:ec86cd6602df9e3783b2d0f0c768a6f01fee129385d8987f9c73da1ea2b9c7aa` |
| scene Pi | `f90ba1c` | `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75` |
| 分镜 Pi（原绑定保留） | `254e748` | `sha256:2d440c564e2ec98538ed2a0c1166aa75fe52d260d069cac629a486b357e47f04` |

镜像仓库 `arm-cluster-master:5000`，部署使用 `latest@sha256:...`。初次发布回执为 `production-video-r2`；本次 scene 升级回执为 `scene_binding_upgrade_ready`。未重置数据库。

HTML 修复源码为 `f90ba1cf00ab90ff06051e08db4b6ef2c933a7f7`。新的工作台、control、Pi 三个精确镜像已构建，并在镜像内用原始 HTML 字节验证通过。工作台 55ee 已部署，Ready Pod 为 `video-workbench-6d6499f7d4-ddddr`，UID 为 `482faee0-608b-4784-85aa-968834c8f051`；真实用户浏览器刷新后原项目仍保留。

新的 HTML 候选 `candidate-video-html-r1` 已完成，使用新 Pi `sha256:5b865fbea90fa97f59ab4b62395775526d4e5df59e5ad71185dd562744f70e75` 和 runtime `video-pi-f90ba1c-html-r1`。共 4 次模型调用，包含探针调用；候选 UID 为 44000006–44000008，Worker 与代理 Pod 均已终止、退出码 0、重启 0，独立审查／接受材料已用于新 scene 发布。候选不计入生产制作数量，也不代替正式 HTML 任务。

scene 配置发布首次执行在释放只读空闲保护锁时超时停止，原失败记录保留。当时只创建了配置、预留生产 UID 区段 46200000 并暂停 dispatcher，API 和模型代理仍为原 aff85 镜像。经审查的人工续接已完成，状态为 `scene_binding_upgrade_ready`；**原发布超时保留，不能声称全过程自动发布成功**。当前目录共 18 条路由、58 个运行池，完整保留原 57 个池，除 scene 外的 17 条路由未变。新增 scene 池为 `video-scene-pi-72fc4ecbab3cf7c8edaa`，UID 区段为 46200000–46265535。实际查询科研 6 项、PPT 5 项、视频 2 项能力全部 ready。

## 真实制作结果

验收项目 `49336596-ee04-4478-8a44-8d87aa470b28` 属于独立 operator，r8 项目修订号为 15；历史 r7 快照为 12。

| 任务 | 结果 | 实测时间 | Job |
| --- | --- | --- | --- |
| 分镜 | 完成，两镜中文分镜，输入／产物／事件核对通过 | 100.22 秒，包含业务收集 | `2b0ee239-f867-49a9-ac79-74a711e6283f` |
| SVG 初稿 | 完成，r5 人工修复后恢复唯一候选；后续已采用 | Job 创建至停止证明约 92.11 秒 | `9a264e44-e270-4c57-8a4b-79dd69f8deca` |
| HTML 首次任务 | r6 失败，`worker_output_invalid`；Worker 已证明停止，无发布产物 | 业务观测 93.35 秒 | `c27f9205-5987-4f20-bd68-9ae77a2f163d` |
| SVG 批注修订 | r7 完成，保留 V1，新增并明确采用 V2 | 77.90 秒，包含业务收集 | `d5c82bd4-4f2c-4813-870c-17f741d03454` |
| 修复后 HTML | r8 单次生成完成并明确采用；原失败记录保留 | 92.654 秒，生成＋收集；整个尾段 95.51 秒 | `26df9a50-0f75-4711-8f3e-6b7652762f0f` |

SVG V1 为 `678e5d30-92f2-4bbf-bd67-d098b4f6b9b3`，V2 为 `5632a726-b19f-4c7c-949b-2a8f076372a8`，V2 的父版本为 V1。r7 已核对原始 V1 源码不变、修订任务的输入／产物／事件和 V2 源码一致。r7 时 HTML 无候选且未采用，导出返回预期 409。r8 新 HTML 版本为 `fdf22ab7-5254-40f1-b4d1-0e333bc0d885`，采用前再次确认导出 409，明确采用后成功导出已采用 SVG V2 与新 HTML；V1／V2 源码未变。r8 状态为 `html_tail_verified_adopted_exported`，本轮只提交 1 次生成且完成 1 次。ZIP 源码、manifest、预览及播放器结构已核对。对应 operation 为 `8255189a-9dec-4ec4-9b33-a6589bdfe120`，HTML source SHA-256 为 `1bbf454b193be3e74acd1e13583423161c62f5691bc44f71410455a390af3cdf`。该次临时 token 文件已删除。

独立取消测试 Job 为 `215ea471-a9ae-4ddb-b418-5666206b29cc`。已观察到运行状态，单次取消后 HTTP 停止证明与对应真实 Pod 终止记录一致。取消子报告最初等待 Pod 证据，r7 外层流程随后补齐 `watch-stop.json`；应以完整证据链判断结果。运行超时、精确槽位释放时间尚未实测。

不能据单次分镜或单镜时间推算整部视频的速度。公开 Jobs 的 usage 仍为 null，没有可信可得的计费用量与单价，费用未知；不把空值当作零费用。

真实浏览器中，完整 1280×720 HTML 画面可读，播放器上一镜／下一镜通过；重新载入后从 1/2 自动切到 2/2，观察耗时 10930 毫秒，配置时长为 10 秒；暂停后手动切镜通过。证据为 `browser-review-final-r8.json`。**实际界面限制：HTML 使用固定 1280×720 画布，播放器可用区域为 1280×656 时会裁掉底部 footer，自适应缩放仍待优化。** 这些控制验证不代表所有视口均无裁切，也不代表帧精确时间轴。

## HTML 失败原因与修复边界

r6 原模型回包中恢复的 write 工具参数包含 `<meta charset="UTF-8">`，旧校验器拒绝了该标准字符集声明。移除该声明可离线通过，原文件未修改。恢复的原 HTML 源码 SHA-256 为 `128580c6871f8c1f37c0d36885261c721b3fcd39481c7bf2e18ee6bcd5679924`。Worker 临时目录已清理，不能把模型 write 参数另称为已独立读取的磁盘文件。

f90 修复只允许直接位于 head 内、仅有 charset 一个属性且值为 UTF-8 的 void meta。其他 meta、额外属性、body 内 meta 和 SVG 中 meta 仍拒绝。profile 测试 **82 passed**；工作台核心与 gateway 定向测试 **112 passed**。两个结果分开记录。旧失败 Job 保持 failed，离线修复通过不改变历史任务结果。

## 提交响应丢失的修复与边界

SVG 在 r4 已被 Agent 接受。测试转发丢弃 POST 回包后，lookup 返回自定义 503，旧工作台错误地把 operation 判为失败。修复将上游 5xx 识别为可继续查询的服务错误，保留原幂等键，不重发提交；当时的定向测试 **112 passed**。

r5 对该失败 operation 做受控人工修复，再验证新的固定镜像窗口：lookup 阻断时保持活动、重启后查询原 Job、恢复一个候选，新增 Jobs POST 为 0。**r4 失败原样保留；r5 包含人工修复，不能声称 r4 全过程自动恢复成功。**

## 备份与证据

最终备份 `sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3` 已通过，包含 projects **3**、operations **6**、versions **3**，已纳入新 HTML。实际 SQLite 只读快照在独立离线文件恢复，没有回写生产 PVC。完整性、全部表行数和备份／恢复 SHA-256 一致，备份临时文件清理通过。SHA-256：`6c82e59c8b8e7eecad02cfd4d89a0c967b22c38f2d1ad85c893745244456f885`。最终环境清理回执 `acceptance-cleanup-r8` 的状态为 `cleaned`，精确处理本次 11 个目标，`pvc_unchanged` 与 `workbench_unchanged` 均为 true；3 个自有端口转发已停止，剩余 0。

服务器证据根目录：`/root/video-workbench-release-20261029/evidence/`。

- 发布：`production-video-r2`、`released-routes-r2.json`。
- 分镜：`business-live-r3/verify`；SVG 原故障：`business-live-r4`。
- 人工修复后的窗口：`svg-fixed-window-r5`；HTML 失败与原回包：`business-finish-r6`、`html-admission-readonly-r6`、`html-original-output-r6`。
- SVG V2 与取消：`business-partial-tail-r7`，历史状态为部分尾段已验证、HTML 仍失败、导出仍受阻。
- 修复后生产 HTML 与导出：`business-html-charset-r8`，状态为 `html_tail_verified_adopted_exported`；不覆盖 r6／r7 报告。
- 本地归档：`.runtime/video-release/business-evidence-r7.tar.gz`，SHA-256 `ebc8cf9c3eda9421cef028091466f0d9ff10012d2651597d8486d6162eeb1b42`。
- 最终备份：`sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3`；浏览器检查：`browser-review-final-r8.json`；清理回执：`acceptance-cleanup-r8`。
- 最终归档：`business-evidence-final-r8.tar.gz`，650306 字节，144 个源文件加内置 manifest，共 145 个成员；SHA-256 `051ee361c4cd486ebb481e756e9d7356e9a85b5cc5f8327cc751e178f72bdd7e`。服务器写后逐成员核对、本地下载整包 SHA 均通过；排除数据库与凭据。内嵌旧 r7 归档的 247 个成员也已完整核对。
- 旧备份 `94d3790bb9b147c1866fbf5614c93f13` 包含 versions 2，`ebe8a19a037a4386942ea99451822fc9` 包含 versions 1，`6afaef201238429784818b4d9ae91c7e` 包含 versions 0，均为历史快照。
- 登录：本地私有记录 `.runtime/video-release/authenticated-browser-receipt-r3.json`。

OpenSpec 服务中的规格、设计与任务记录已整合，18 项任务完成，验证通过且无问题；未执行规格 apply 或 archive。后续优化和合并决定看 [NEXT_STEPS.md](NEXT_STEPS.md)，固定项目和版本见 [CHECKPOINT.md](CHECKPOINT.md)，各批次验证见 [VALIDATION.md](VALIDATION.md)。
