# 分支验证记录

## 2026-11-06 校验补丁预检、发布与真实浏览器技术闭环通过

源码仍为 `139295401b24dd1440c6df227d79776eeaa7036b`。下方保留此前尚未发布、浏览器待验和原失败记录，当前结果以本节为准；独立分支未合主线，3.2b技术完成，OpenSpec已实际同步并回读验证通过。

| 检查 | 实际结果与边界 |
| --- | --- |
| 完整固定输入预检 | `preflight-r5` 返回 `exact_process_continuity_preflight_passed`；四段编码及四段观察沿用既有执行身份，编译／复核／组合／媒体检查新执行。retain与copy两片仍各19.625秒／471帧，MP4 SHA与r4完全相同，不宣称新执行了复用阶段。 |
| 发布操作器历史 | wrapper首试在导入摘要校验阶段停止，未造成线上变更。服务器现存较早base与本地增强版有差异，分别保存为 `release_continuity_patch_base_r1.py` 和 `release_continuity_patch_remote_r2.py`，服务器原件保持；现存base不被当成已证明的release-r1历史执行身份。新版发布工具101项通过，只说明工具范围。 |
| 实际发布与独立核验 | `release-r2` prepare／apply／postcheck通过；工作台及执行器实际imageID与 `ba9a9ba2b67fa79fca7715de80f9fb3e90afc9d9465fc59f902445004b8ea5a6` 匹配，Ready、0重启。渲染器和 `video-motion-runtime-6e2ad4a143eff46537e2` 保持；旧14项目、17＋2表及媒体保持，发布核验当时额外行0、active0。该零新增结论不包括随后真实草稿。 |
| 新真实生产草稿 | `20951f68-5849-4610-b823-64f4ab4fbe5c` 已完成；候选3 token copy、原录音、默认 `end_hold=4`。成片23.625秒、1280×720／24fps、567实读帧，MP4 SHA `7c364330b99f3d80e0124f0fae4778574beb88e2a38a16a4ab8f48b5c7e298bc`。 |
| 媒体与原声 | 新草稿完整解码、十个AAC解码原声窗口相关性通过，最低 `0.9999441150885439`。这次草稿未测组合主PCM逐样本一致，不复用预检主PCM证据冒充；声音自然度与准确人工时序仍未核准。 |
| 草稿后只读audit | `process_continuity_browser_read_only_audit_passed`：全部原typed行和BLOB保持，仅新增工作台video_renders1条、执行器operation1条及scene2条。原失败及采用记录保持；audit工具35项通过。只读audit本身不证明浏览器播放或下载。 |
| 浏览器完整播放与跳转 | 实际使用原生控件从0播放到23.625秒，ended=true、未静音、error=null；正反跨镜跳转 `12.617066` 秒与 `11.922213` 秒成功。3.2b的浏览器技术闭环通过，不代替效果人工认可。 |
| 下载入口与响应边界 | 点击下载按钮后，对应媒体GET200已由工作台访问日志确认，播放器媒体请求206。服务端导出MP4另取回并核验SHA／完整解码；浏览器操作系统最终文件落盘未观察，不声明已保存成功。 |
| 刷新恢复与正式导出门禁 | 刷新后完成历史、候选3采用及旧失败保留；同一render ID重新打开仍为23.625秒、无媒体错误。再次正式导出仍被pending时序门禁拒绝，未勾选人工旁白核对。 |
| 独立待办 | 3.3、准确人工旁白时序、声音自然度与整片讲解效果保持待办；服务器媒体技术结果与有限UI检查不替代用户效果认可。 |

本地 `continuity-media/preflight-r5/summary.json` SHA256为 `e2efcf43f6cdc0a08904711df374e87376fbfbca0e4d3b43af2366400a37588e`；`release-r2/summary.json` 为 `443fae0a8b0bbe2a186aa806064b202b3e28976e786b6bd619b1d1fdf2f21032`，独立 `release-r2/postcheck.json` 为 `0bf6cf5598de1eb85f8bfd1b142025f31759db391f787f250922b278b1879ac8`。summary的 `postcheck_pending` 为apply刚结束的历史状态，以独立postcheck通过回执为准。草稿后audit回执为 `continuity-media/browser-acceptance/final-audit-20261106T092601Z-160afb38a26b/summary.json`，SHA256 `1d41e59b3ba0f0ee8e42b039c4cc4d8f46bc5bbd9ca2a354ae15cc2ecd2d5c0c`。所有路径仍在原有ignored证据根下。

最终浏览器[实际回执](rendered/audio-clock/process/pc-browser-passed-r2.json) SHA256为 `76d7a12e115efec8e8bc5d52a702d88798041b0b088a80dc3a4d0a931361a153`，状态passed；[23.625秒连续草稿本地媒体](rendered/audio-clock/process/continuity-media/browser-acceptance/final-audit-20261106T092601Z-160afb38a26b/draft.mp4) SHA与上述新草稿一致。3.3、声音自然度、准确人工时序和讲解效果仍保持待办，浏览器技术通过不自动核准这些项目。

[技术收尾总回执](rendered/audio-clock/process/process-continuity-closeout.json)状态passed，SHA256为 `a8c6bc4ecb36c0aeed7c56438f469aef2d7be09e0fa33f9c50c3882db18076ce`。OpenSpec项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 的 `improve-video-audio-clock-and-expression` 已同步至 revision `f67f5a1019a1eda4e1d8777bfeade3e7f0cfdb0a`，回读匹配、`valid=true`、issues为空；仅3.2b从unchecked改为checked，其余checkbox不变，33项从完成9项变为10项、剩余23项。[同步摘要](rendered/audio-clock/process/process-continuity-closeout-openspec/summary.json)、[回读](rendered/audio-clock/process/process-continuity-closeout-openspec/readback.json)和[验证](rendered/audio-clock/process/process-continuity-closeout-openspec/validate.json)保留实际结果。

## 2026-11-06 重复跳转截图校验补丁：诊断证据复核通过，待完整预检及发布

源码 `139295401b24dd1440c6df227d79776eeaa7036b`，独立分支 `codex/video-audio-clock` 未合主线。以下为原浏览器失败之后追加的诊断与补丁阶段，保留下节失败历史；补丁尚未部署，浏览器尚未重做，3.2b 不因此完成。细节见 [诊断补丁阶段记录](docs/process-continuity.md#重复跳转截图诊断与有界校验补丁)。

| 检查 | 实际结果与边界 |
| --- | --- |
| 原失败诊断 | `MR-8dbdfdececeee794` 对应 `process_continuity_seek_sequence_mismatch`。真实同 `.375` 秒样本的 DOM、状态、几何、transform 与文字精确一致；RGB 差异局限 token 圆角附近，1600像素、全图平均通道差 `0.0052126736111111115`、最大通道差44。文字外扩2px完全相同；底层原因仍未确定。 |
| 已检验假设 | 提前注入 runtime bootstrap、关闭 partial raster 均未改变差异；每种跳转历史后连续三次截图各自稳定，历史间差异仍在。实验证据不支持对应的初始化、partial raster 或等待截图稳定猜测，不能据此断言特定 Chromium 机制或普通编码噪声。 |
| 补丁门禁 | 同SHA保留精确路径；SHA不同必须绑定真实JPEG、保持所有测量元数据完全一致。只允许编译器固定token圆角的有界mask内差异；文字外扩2px及mask外逐像素精确一致，且平均差≤0.01、最大差≤48、差异像素≤2048。缺证据、坏SHA、格式／尺寸异常及超预算均拒绝，没有引入通用感知相似度通过条件。 |
| 新增定向测试 | `repeat-frame-tests-r2.xml`：32 passed。含真实JPEG双向对照，以及微小文字笔画、换字、细线、移位、背景、文字保护边缘、证据异常和三项预算各自独立超限负例。 |
| 既有定向回归 | `patch-review-regression-r1.xml`：444 passed／0 skipped。仅声明此集合范围，不累计历轮测试或视为最新完整Python集合。 |
| 新镜像 | `build-r6` 工作台 `latest@sha256:ba9a9ba2b67fa79fca7715de80f9fb3e90afc9d9465fc59f902445004b8ea5a6` 已构建；渲染器 `aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486` 与runtime ConfigMap保持，尚未部署。 |
| 原失败证据的真实复核 | 新工作台镜像在隔离环境重用 `diagnostic-r2` 原输入、观察与真实JPEG，实际生产复核入口返回 `exact_failed_scene_review_passed`。原观察前后SHA保持，observe／Agent／TTS均0；不是新渲染或新浏览器闭环通过。 |
| 尚未完成 | 新四段输入 `preflight-r5` 执行中，未写为通过；尚未发布补丁或重做浏览器草稿／播放／刷新／下载。原browser失败保留，3.2b、3.3及人工旁白时序、声音自然度与讲解效果仍未核准。 |

本地证据根仍为 `video_workbench/rendered/audio-clock/process/`。`continuity-media/build-r6/images.json` SHA256为 `57b420b4a169d9354c7a511cbdf96204e177ae45793f49784acd23e7e3aff0ec`；`continuity-media/patch-preflight-r1/summary.json` SHA256为 `44ff1cb21dc4f43a4bcc769ed72faa8aafac4e9c58df3d85d6d63f164683ce87`。以下保留原发布与真实失败的阶段记录。

## 2026-11-06 过程动画跨镜：技术预检与发布完成，浏览器草稿失败

本轮源码 `eab5a7e7d4d43bb66a866785038b0e55f756e5d1`，独立分支 `codex/video-audio-clock` 未合主线。采用 `motion-process-continuity-compiler-1`／`continuous-process-1`，保留旧版本快照含义；完整身份、失败修复和证据见 [过程动画跨镜衔接](docs/process-continuity.md)。以下仅记录已完成范围，不把夹具或工具测试视为真实浏览器通过。

| 检查 | 实际结果与边界 |
| --- | --- |
| 回归范围 | 完整 Python 集合 1903 passed／5 skipped，运行于位移与终点判定修复前；最新终点修复后定向 166 项通过。JS 153 项、兼容集合 125 项等范围有重叠，不累计为最新提交的完整集合。 |
| 原输入与复用身份 | retain／copy 四段的 24 个编译文件在终点判定修复前后原字节及 manifest 摘要保持；r4 四段共 28 个渲染输入精确匹配。八个旧编码／观察阶段复用，四个工作台阶段重新执行，不伪造新渲染执行。 |
| 组合与原声 | retain／copy 两片均 19.625 秒、1280×720／24 fps、471 实读帧，完整解码、真实主 PCM 逐样本核对通过；AAC 解码每片十窗口相关性最低 0.9999441150885439，不写成有损音频逐样本一致。 |
| 预览／编码帧 | 四段 draft 与两段 final 共 80／80 条对照通过，相邻 floor／ceil 时差≤1／24 秒。初始文字仍有栅格边缘差异，来源未查明；通过的是局部结构／字形和背景门槛，不是逐像素相同。原 74／80 失败、权限停止及工具修复回执保留。 |
| 生产发布 | `release-r1` prepare／apply／postcheck 通过，工作台和执行器 Ready、实际 imageID 与 digest 匹配、重启 0；八音色、health／ready 200、匿名 401、runtime 与执行器鉴权通过。发布阶段 Agent／TTS／渲染提交均为 0。 |
| 数据保持 | postcheck 比较工作台 17 表和执行器 2 表，旧 schema、原行值及已有连续候选记录保持，核验时额外行 0、active 0；两份新鲜数据库备份仅留服务器。随后新增专用浏览器夹具项目不包括在该时点额外行 0 的结论中。 |
| 浏览器已通过步骤 | 两镜图解／原录音采用；候选 1 retain 自动规划；token 不关联的未保存保护及另存候选 2；返回候选 1 改为 token copy 后另存／明确采用候选 3。正式导出被 pending 时序门禁拒绝，未自动确认人工时序。 |
| 浏览器真实失败 | 唯一一次生产连续 draft 使用候选 3 token copy、默认 `end_hold=4`，在第二镜 `validating_scene` 产物校验失败，界面问题 `MR-8dbdfdececeee794`。原失败保留，原因尚未确认，不据此推断根因。 |
| 仍待验收 | 完整浏览器闭环 failed／未完成，相关成片播放和下载尚未通过，3.2b 未勾选；3.3、准确人工旁白时序、声音自然度和整片讲解效果未核准。助手对有限 preview／decoded／diff 样本的检查不替代真人认可。 |

工作台 `latest` digest 为 `sha256:167060a2c4e923c16605b865cf47c88a98d3c5d9aef0db977cfb31a1b2df8d60`，渲染器／执行器绑定 `sha256:aaa8f1c05e141ac83c182df1a66826f9931d2d0a38278aaecf68d9f12b0ba486`。未修改共享 Agent 或新增模型；Canvas／Three.js 公共服务与 panghu-tool 迁移暂缓，可信费用与生产存储回收仍由框架统一解决。以下保留历轮原始记录，不用于扩大本轮完成范围。

## 2026-11-01 无字幕图解改进

用户认为原成片机械感明显、字幕遮挡，已选择无字幕图解。前端源码 `31cb29d514e74a81bac87f6a32aef31a5cf081ec` 已部署，最新镜像 digest `sha256:0ab44a7e62464e000247f138462bb663ff6047f42519fa2f3d2b27e9534bded3`，Pod `video-workbench-6cb86d7ccf-jxblm` Ready、0 重启。Node 25 项、语法及 diff 检查通过；真实浏览器确认字幕默认关闭、旁白保留、可编辑指引、无字幕预览和旧有字幕成片标识；未修改既有项目。

18 秒口语样片完整解码及助手目视检查通过，H.264/AAC、1280×720@24fps，SHA-256 `9a1bfcd78e37fded300413d16f8fe89c0710ec4ea334a2f09c6cdcae9644b762`。TTS 1 次、静态渲染 2 次；首版渐变变白的视觉失败已保留，第二版改用纯色并复用原 WAV。完整稿自然度未通过用户认可，不因本轮技术检查而勾选。完整边界及证据见 [PRESENTATION_REFINEMENT.md](PRESENTATION_REFINEMENT.md)。以下保留先前音频首版及静态首版记录。

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
