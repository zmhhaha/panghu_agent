# 分支验证记录

更新：2026-10-31（Asia/Shanghai）。以下先记录 `codex/video-audio-production` 的音频阶段证据；该分支尚未合主线。历史静态素材首版结果留在后半部分，不将历史测试累计为本次回归，也不以组件检查代替完整制作验收。

## 音频阶段已确认结果

| 检查 | 已确认结果与边界 |
| --- | --- |
| 本地 Python 测试 | **471 passed、1 skipped**；跳过项为 Windows Secret 符号链接权限；包括工作台、音频、媒体、构建与部署测试，部署定向项已计入，不重复相加 |
| 前端 Node 测试 | **25 passed**；本地浏览器检查候选不自动采用、明确采用、估算字幕保存、上传采用后时长和字幕失效、音频时钟驱动播放，无 console error |
| 线上部署 | 工作台源码 `e5b375b02e8b639b0b4a0eecaa17c67145c005bc`，latest digest `sha256:ccd2509379ad55029760cf5229d09dd33b0577213172a20e791d9818ad06c6fe`；主动 rollout restart 后新 Pod `video-workbench-6454f4845f-bj52n` Ready、实际 imageID 不变、异常重启 0 |
| 语音修复部署 | 源码 `c506bb3c105c81200bc3b86577b65fb96f3361e8`，latest digest `sha256:adb9d87b15b461db56a2f4aca5fd6999221fe4c715ee3751250b3a4b21423634`；Pod `speech-service-758cbd54f9-bfczb` Ready，实际 PID1 capabilities 五组全 0、NoNewPrivs=1 |
| ARM 静态画面 | 最终镜像在非 root、只读根、capdrop ALL、NNP、禁网、2CPU／1536MiB 环境中，10 张已有 SVG／HTML 输出均为可解码 1280×720 PNG；已目视检查旧 HTML 和 DSH 第一镜的中文、图示、箭头 |
| ARM 带声音 MP4 | 两镜共 7 秒，H.264／AAC，全文件解码通过；真实云健录音片段的主 PCM 旁白逐字节匹配，前后留白正确；MP4 SHA-256 `736fb742ea80ded66b8892865e47ed51a272ce95db5e521434f9168d74bc074b`；这不是八镜头整稿验收 |
| 静态渲染资源限制 | data／file／http 图片资源 URI 三个反例均以 78 退出且无 PDF；不加载外部资源 |
| 生产浏览器 | 新界面、原 DSH revision 27 与缺资源镜头定位通过；真实 audio clock 驱动第 1→2 镜播放通过；原项目内容未修改 |
| 生产上传与历史 | VPS 视频上限 34m；第二镜真实 WAV（1,248,044B、26 秒）上传为 A2 候选、明确采用、恢复 A1 全部通过，A2 历史保留 |
| 八镜真实 TTS | 副本 `1e2a822d-c4c1-45de-a8b9-b72a82f714bc` 的 8 镜真实合成并明确采用完成；复用 8 张 SVG，新增视觉 Agent 调用 0；保留两次旧失败 |
| 完整 MP4 | render `5641dc62-01d7-4540-8e79-501ec801cced`，206 秒，H.264／AAC、1280×720@24fps、4944 帧；完整解码通过；SHA-256 `75ab83eba053d20b0d8beee8687709ee5aadd10eabef8549dfc4b55122358c31` |
| 音频与画面独立检查 | 8 镜音频首尾匹配通过，全片相关系数 0.9999445、NRMSE 0.010601；8 张参考 PNG 匹配画面与镜头顺序通过；第 03／06 镜中心帧已目视检查，中文正常 |
| 字幕 | 44 条字幕时间／文本与 timeline 一致；仍为 estimated，需人工复核，不记作强制对齐或听感认可 |
| 冻结与局部失效 | render 冻结 revision 67；编辑后恢复 revision 69，局部失效检查通过，冻结成片不随编辑改写 |
| ZIP／owner／坏文件 | ZIP 19 个文件含 8 个画面 source；其他 owner 返回 404；损坏 WAV 返回 422 且项目不变 |
| 重启持久化 | 旧 Pod `video-workbench-7889b8d5b5-ml22n` 已替换；8 条采用音频逐一 SHA 匹配、上传 A2 历史保留、MP4 SHA 匹配、SQLite integrity ok、原 DSH revision 27 保留，全部通过 |
| 重启后权限与容量快照 | 实际 PID1 CapInh／Prm／Eff／Bnd／Amb 全 0、NoNewPrivs=1；PVC 当时可用 988,618,752B，只作该时刻快照；speech Pod `speech-service-758cbd54f9-bfczb` 仍 Running、异常重启 0 |
| 人工质量边界 | 完整稿声音自然度、术语发音及 estimated 字幕的人工复核尚未获用户认可 |

ARM 媒体验收总耗时 159.823 秒，其中编码 32.076 秒；这不用于估计整部制作速度。ARM 归档位于 `.runtime/video-audio-production/evidence-arm.tar.gz`，2285355 字节，SHA-256 `2816556a9a469ca438b61afd82389bb137990c95589c3034f3be609ebd7e42fe`；不含生产数据库或 Secret。服务器证据目录为 `/root/video-audio-release-20261031/`。

重启最终回执为 master `/root/video-audio-release-20261031/postflight.json`。完整媒体与回执归档 19,122,821B，SHA-256 `64cbe74652f62513504df98d4fa2ea483d5400fdfecd69fe02d2409b7269bec0`，已下载校验并安全解包至 `D:\github\armbianbegin\.runtime\video-audio-production\evidence-final\`；成片为其中 `live-audio/dsh-narrated.mp4`。

OpenSpec 变更 `add-video-workbench-audio-production` 已同步至 revision `49692535c5004b27a6fc4132900185422bb75f3d`，验证 `valid=true`、issues 为空。19 项任务完成 14 项，保留未完成的 1.1 整稿听感、4.2 术语人工试听、5.1–5.3 BGM；未执行 apply／archive。

构建曾因 Debian／PyPI 下载超过 900 秒而失败两次，改用可配置 TUNA 镜像后成功；失败日志保留。首轮 ARM 验收在启动容器前因 host 脚本使用不存在的 `Path.chown` 失败，改用 `os.chown` 后再次验收通过；不写成首次全自动成功。

第三镜历史 operation `0f99c230-d5b4-414c-8c4e-8175e464ea67` 因 CLI 文本 `--patch` 中 U+002D 不在 Kokoro 词表失败，工作台映射为 `speech_upstream_unavailable`；实际前端 probe 排除了“空标点误拒”的猜测。通过显式读音修正处理第 3／5／6 镜 CLI、文件名和路径，原旁白不变。第六镜历史 operation `03d700c9-e71d-4dd7-bbff-fa608acc82c1` 因首段 D=181→target 200 的 snap 约 10.497% 超过 10% 而失败；分段规划修复经 82 项单元测试及隔离 CPU probe，再部署后完成真实第 6–8 镜。修复只有限调整文本分段边界，不改文稿、语速、10% 阈值或 CPU generator 禁止策略。两个旧 operation 保持 failed，以新 intent 取得后续成功，不声称首次全程成功。

语音固定为中文男声 `zm_yunjian`，实际 NPU 路径不允许静默 CPU generator 回退。估算字幕必须保留 `alignment=estimated`、`requires_review=true`，用户人工保存后才记作 manual。完整稿声音自然度和发音质量未经用户认可；静态 WeasyPrint 不保留 CSS 动画，BGM／混音尚未实现。

## 历史记录：2026-10-30 静态素材首版

以下是当时静态素材首版的验证与主线合并记录，适用于对应历史镜像和项目。

更新：2026-10-30。开发分支 `codex/svg-video-workbench` 已获用户批准并快进合入 `panghu_agent` 的 `main`。当前结果见 [CURRENT_STATUS.md](CURRENT_STATUS.md)，后续优化见 [NEXT_STEPS.md](NEXT_STEPS.md)。各批次结果分别记录，不能相加称为一次全量回归，也不能用离线检查代替真实模型、部署或浏览器验收。合并前已核对主线祖先关系、差异格式与应用代码树；合入的应用代码和已部署 `f90ba1c` 一致，仅验收与合并记录更新，没有再次调用模型或部署。

## 当前真实环境结果：截至 r8

| 检查 | 已确认结果与边界 |
| --- | --- |
| 登录与持久化 | 真实入口登录；用户项目跨重启保留；另一身份读取返回 404；匿名及伪造头不能绕过登录 |
| 通用服务接入 | 视频专用 caller 与两个 profile 已发布；逐值保留此前科研／PPT 的 16 条路由、55 个旧运行池；科研 6 项、PPT 5 项、视频 2 项能力只读查询通过 |
| 真实分镜 | 一次生产 Job 完成，输入／产物／事件核对通过 |
| SVG 初稿 | 一次生产 Job 完成；r5 人工修复后恢复唯一候选，保留原始源码 |
| HTML 首次任务 | r6 失败，worker_output_invalid；真实停止证明存在，无发布 manifest，不作通过记录 |
| SVG 修订及采用 | r7 一次修订完成，V1 不变，V2 父版本为 V1，V2 已明确采用；输入／Job／产物／manifest／事件及源码一致 |
| 新 HTML 与明确采用 | r8 单次生成完成，采用版本 fdf22ab7-5254-40f1-b4d1-0e333bc0d885；原 r6 失败保留 |
| 未采用场景阻止导出 | r7 HTML 无候选时导出预期 409；r8 新 HTML 采用前仍为 409 |
| 正式 ZIP 导出 | r8 采用后成功导出 SVG V2 与新 HTML，manifest／版本／源码／预览／播放器结构核对通过，旧 V1／V2 不变；浏览器行为另有实际检查记录，固定画布裁切限制仍存在 |
| 运行中取消 | 独立 Job 运行后单次取消，HTTP 停止证明与对应真实 Pod 终止记录一致；r7 外层补齐源记录绑定的 watch-stop 证据 |
| 浏览器画面与控制 | 完整 1280×720 HTML 可读；上一／下一、自动 1/2→2/2（配置 10 秒，观察 10930 毫秒）、暂停后手动切镜通过；见 browser-review-final-r8.json |
| 实际界面限制 | HTML 固定 1280×720，播放器区域 1280×656 会裁掉底部 footer，自适应缩放待优化；不声称所有视口无裁切 |
| 备份与离线恢复 | 最终 ecc4 快照 projects 3、operations 6、versions 3；只读快照、独立恢复、全部表行数、完整性与 SHA-256 一致，未回写生产 PVC，备份临时文件清理通过 |
| 最终归档与清理 | 最终 r8 包逐成员校验和本地整包 SHA 通过；11 个验收目标清理完成，PVC／工作台不变，3 个自有端口转发停止、剩余 0 |
| 尚未覆盖 | 固定画布自适应缩放、运行超时、精确槽位释放时间；公开 Jobs usage 为 null，费用未知 |

截至 r8，生产制作提交 **5 次、成功 4 次、失败 1 次**；独立取消任务 **1 次**。r8 本次仅提交 1 次、完成 1 次，状态为 `html_tail_verified_adopted_exported`；HTML 生成＋收集 92.654 秒，整个尾段 95.51 秒；临时 token 文件已删除。Job 为 `26df9a50-0f75-4711-8f3e-6b7652762f0f`，operation 为 `8255189a-9dec-4ec4-9b33-a6589bdfe120`，当前项目 revision 为 15，HTML source SHA-256 为 `1bbf454b193be3e74acd1e13583423161c62f5691bc44f71410455a390af3cdf`。此前候选实验另计。取消子报告最初为 pending_pod_evidence，随后外层流程取得真实 Pod 证据并导出 `cancel/watch-stop.json`；不能只读最初子报告或省略后续证据。

主要证据为 `evidence/business-live-r3`、`business-live-r4`、`svg-fixed-window-r5`、`business-finish-r6`、`business-partial-tail-r7`、`business-html-charset-r8`。本地 r7 归档 `.runtime/video-release/business-evidence-r7.tar.gz` 的 SHA-256 为 `ebc8cf9c3eda9421cef028091466f0d9ff10012d2651597d8486d6162eeb1b42`。

## HTML 字符集修复：f90

r6 原模型 write 参数恢复的 HTML 含标准 `<meta charset="UTF-8">`，旧验证器拒绝该声明。移除该声明的离线对照可通过；原文件保持不变。原源码 SHA-256 为 `128580c6871f8c1f37c0d36885261c721b3fcd39481c7bf2e18ee6bcd5679924`。Worker 工作目录已清理，恢复内容来自模型回包，不声称独立读取到了原磁盘字节。

修复源码为 `f90ba1cf00ab90ff06051e08db4b6ef2c933a7f7`。仅允许 head 直接子元素、单一 charset 属性、UTF-8 值的 void meta；body／SVG／额外属性／其他 meta 仍拒绝。

| 本次检查 | 结果 |
| --- | --- |
| video profile 定向测试 | **82 passed** |
| 工作台核心与 gateway 定向测试 | **112 passed**；已有 Starlette 弃用提示 |
| 原 HTML 完整离线验证 | 原字节通过，源码 SHA 不变 |
| 工作台、control、Pi 三个精确新镜像 | 均完成构建；镜像内原 HTML 字节校验通过 |
| 新 HTML 候选 | candidate-video-html-r1 完成；新 Pi 5b865／runtime video-pi-f90ba1c-html-r1；4 次模型调用含探针；UID 44000006–44000008；Worker／代理均实际终止，退出码 0、重启 0；独立审查材料已准备 |
| 工作台 f90 部署 | 55ee 已 Ready；Pod video-workbench-6d6499f7d4-ddddr，UID 482faee0-608b-4784-85aa-968834c8f051；用户浏览器刷新后原项目保留 |
| Agent scene 发布 | 人工续接达到 scene_binding_upgrade_ready；control ec86／source f90、scene Pi 5b865／新 runtime；18 路由／58 池，原 57 池和其他 17 条路由不变；科研 6／PPT 5／视频 2 能力全部 ready |
| 新生产 HTML | business-html-charset-r8 已单次生成、明确采用并导出；累计生产 5 次提交／4 次完成，原 HTML 失败保留 |

82 与 112 属于两个测试集合，不相加声称一次新全量回归。新候选与真实生产任务分别记录；候选完成或镜像内校验通过不代表 scene 已发布或生产 HTML 已成功。发布超时时只创建配置、预留生产 UID 区段 46200000 并暂停 dispatcher，API 与模型代理仍为 aff85；最终已通过人工续接达到 Ready；不能反写为首次自动发布成功。新 scene 池为 `video-scene-pi-72fc4ecbab3cf7c8edaa`，UID 46200000–46265535；完整 control digest 为 `sha256:ec86cd6602df9e3783b2d0f0c768a6f01fee129385d8987f9c73da1ea2b9c7aa`。

## 提交响应丢失修复：9c7

r4 故障测试已真实丢弃 SVG 提交回包；lookup 自定义 503 被旧工作台错误判为 operation 失败。该轮故障保留为失败。

将 Gateway 上游 5xx 映射为可继续查询的服务错误后，工作台与 gateway 定向回归 **112 passed**。此处是该修复当时的测试记录，与上节 f90 后的 112 复测分开，不作累计。

r5 包含受控人工数据库修复，随后才在固定修复镜像窗口验证 lookup 阻断、工作台重启、查回原 Job 和恢复唯一 SVG 候选；新增 Jobs POST 为 **0**。这不是 r4 全程自动恢复通过，不覆盖原失败，也不宣称修复前后始终使用同一镜像。

## 备份记录

最终回执为 `evidence/sqlite-backup-restore-ecc4ee533b964ceba7ace656020bc9c3/backup-restore-receipt.json`。实际数据库以只读连接获取快照，离线独立恢复；projects 3、operations 6、versions 3，包含新 HTML。全部表行数、完整性与备份／恢复 SHA-256 一致，SHA-256 为 `6c82e59c8b8e7eecad02cfd4d89a0c967b22c38f2d1ad85c893745244456f885`。备份临时文件清理通过，未将恢复文件写回生产。最终环境清理另有 `acceptance-cleanup-r8` 回执，状态 `cleaned`，11 个目标精确处理，`pvc_unchanged` 与 `workbench_unchanged` 均为 true；3 个自有端口转发已停，剩余 0。

历史 94d 快照 projects 3、operations 5、versions 2，SHA-256 `92d4a575e97657be34c208bf5165816efa5038ab869b3ae703ea18481d26fa31`；更早 ebe8 快照含 versions 1、6afa 快照含 versions 0。均保留为历史证据，不覆盖。

## 最终证据归档与规格记录

最终归档为 `evidence/business-evidence-final-r8.tar.gz`，650306 字节，144 个源文件加内置 manifest，共 145 个成员；SHA-256 `051ee361c4cd486ebb481e756e9d7356e9a85b5cc5f8327cc751e178f72bdd7e`。服务器写入后逐成员核对、本地下载后整包 SHA 核对均通过，数据库和凭据排除。内嵌旧 r7 归档共 247 个成员，已独立完整校验，历史失败报告未覆盖。

OpenSpec 服务中的 proposal／design／tasks 已整合，旧附录移到历史记录，18 项任务完成，验证结果为 true 且无问题。未执行规格 apply 或 archive。这项工具记录不代替上述真实业务、Pod、浏览器和数据库证据。

公开 Jobs 的 usage 仍为 null，没有可信可得的计费用量与单价，费用记为未知。运行超时和精确槽位释放时间未实测，保持为验证边界。

## 2026-10-30 早期检查点：历史测试

该阶段当时尚未部署或执行真实视频候选，这只描述当时状态；当前部署及模型结果以上文为准。

| 当时的检查 | 结果 |
| --- | --- |
| 目录合并器、内部登录代理生成器、HTTP 验收工具与业务测试统一复测 | **88 passed** |
| 前端测试 | **12 passed** |
| 科研基础同步后的候选驱动、运行边界、科研上下文／工作流、部署与构建定向回归 | **158 passed、5 skipped** |

当时 HTTP 工具测试来自离线 HTTP 用例，默认只读，显式执行最多四次生成，不确定提交即停止。当时没有预留候选 UID、启动模型任务或修改生产路由；后续真实发布不能反写为当时已完成。工作台拆镜上限支持 1–12，参与输入摘要与幂等意图；OAuth 使用独立 Redis 和 Cookie 凭据引用。

当时已推送工作台源码 `39156bc00821ac453479ad33c35f5630a50c54f6` 的 digest `sha256:46c8de4bd64137fc299286d1a8903dd8d04dd79a408fecc71638c626a20445ef`，以及视频 Pi 源码 `254e74842eef97fb8eba0e70dc6667bc74465883` 的 digest `sha256:2d440c564e2ec98538ed2a0c1166aa75fe52d260d069cac629a486b357e47f04`。control 当时仍在构建，之后的真实发布另见 `production-video-r2`，不据旧“构建中”记录判断现状。

## 2026-10-29 初版验证：历史记录

本节描述最初未提交真实模型、未正式部署的阶段。

| 检查 | 当时结果 |
| --- | --- |
| 完整业务与通用服务回归，1435 项 | 1378 通过、49 条件跳过，发现 8 项候选驱动兼容失败 |
| 修复后完整候选驱动回归，含新增两个视频网络检查 | 37 通过 |
| 原 8 项失败独立复测 | 8 通过，失败缓存为空 |
| 前端状态与并发测试 | 10 通过 |

8 项失败涉及视频重复加载技能及旧任务仅渲染入口兼容；修复后视频／科研使用嵌入的已校验技能，PPT 保留独立技能加载。最终集合为 1437 项；原完整回归加定向复测合计覆盖 1388 个通过、49 个条件跳过，**不是修复后再跑一次完整回归**。Windows 缺失 Linux／原生条件所致的跳过，不代表真实引擎或模型已验收。

```sh
python -m pytest tests/video_workbench tests/agent_service -q --basetemp=.pytest-video-release-final2-20261029
python -m pytest tests/agent_service/test_candidate_driver.py -q --basetemp=.pytest-video-driver-final-20261029
python -m pytest tests/video_workbench tests/agent_service --lf -q --basetemp=.pytest-video-release-recheck-20261029
node --test tests/video_workbench/ui.test.mjs
```

初版浏览器检查使用明确标记 OFFLINE FIXTURE 的离线数据，覆盖创建、拆镜、生成、采用、修订保留旧预览、ZIP 下载和审阅播放器切镜。它不能证明新真实 HTML 或正式导出播放器已通过。

初版 ARM64 禁网容器检查使用合成凭据、只读根文件系统和独立数据卷，验证 UID/GID 10001、探针、Host／身份／CSRF、项目与镜头保存、跨重启数据及 CSRF 保留；容器和测试卷由脚本清理，模型调用 0。其本地 Image ID 为 `sha256:7d4359e7b483f2232070a9dff831745ea284f02baf15b32a801b3d36af8b1552`，不能替代后续仓库 digest 或新镜像检查。

```sh
python tests/video_workbench/container_smoke.py --image arm-cluster-master:5000/video-workbench:latest
```

本期验收范围为 SVG／HTML 素材和顺序审阅包。配音、动画时间轴、MP4 编码和图片生成仍属后续工作。
