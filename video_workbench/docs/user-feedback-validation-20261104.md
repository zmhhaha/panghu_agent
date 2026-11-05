# 2026-11-04 用户反馈修复验证记录

本记录对应 [反馈问题与实现说明](user-feedback-20261104.md)，验证对象是用户原八镜方案和原录音，反馈来源为 [Issue #1](https://github.com/zmhhaha/panghu-service-feedback/issues/1) 及 [DSH 八镜实测](https://github.com/zmhhaha/panghu-service-feedback/blob/main/services/video-service/retest-2026-11-04.md)。开发分支仍为 `codex/video-hyperframes-motion`，未合主线。

R2 最终镜像的八镜编译、真实 Chromium 观察和严格审查、原第三镜独立 MP4 检查、生产部署、发布后只读核对及已登录工作台的本轮浏览器复测均已通过。以下结论只覆盖各自的验证范围，人工声音与时序核准继续独立。

## 最终发布身份

服务器证据根为 `/root/video-feedback-20261104-r2`。镜像使用 `latest` 标签，后续执行绑定完整 digest；源码、包与运行配置分别记录身份。

| 项目 | 身份 |
| --- | --- |
| 应用源码提交 | `80bf9417d42adc4ff499aa937f67bdf95a12e55c` |
| 发布编号 | `video-feedback-20261104-r2` |
| 应用镜像，供 workbench / executor 使用 | `arm-cluster-master:5000/video-workbench:latest@sha256:23da87075de47b5e22524cbfef82a1d81ac8cf0394d417bd73614eabe944d6a1` |
| 隔离 renderer 镜像 | `arm-cluster-master:5000/video-motion-renderer:latest@sha256:2ab834f97c4e21278eba06e4631e23bfd1354e601a1cb6abac0142f33d82dc8c` |
| 源码包 SHA256 | `b126ed5830c1b5939ce238f9d0b057a4552c3e201a8a00199f1ab7c5a04ae33c` |
| 应用 source manifest SHA256 | `34ec77b9aa58a3944a921318de22994102f70539161c446f9b0e4736344cfc14` |
| `build/runtime.json` 文件 SHA256 | `5bcab54153017b563099a1e084e0d4333942081c295ef5466177a262ba4fd858` |
| 原始八镜 fixtures 清单 SHA256 | `a400a8edd390b6637939e13e00e68c4f83468e7f0d5af7630d466cb06b126474` |
| R2 八镜预检 `preflight/summary.json` 文件 SHA256 | `c203b78a477b5f0b169a24f578ae299fc75bf7a03ecc1544cdddc278a7c1a986` |
| 新编译器 / 模板 | `motion-compiler-5` / `technical-diagrams-5` |

runtime 上项是实际文件字节的 SHA，不与旧版本 runtime 的规范化 JSON SHA 混用。实际 Deployment、Pod digest、runtime 挂载及执行器 capabilities 已在发布后核对，不以构建或推送成功替代运行状态。

## 原始输入与失败证据

原项目为 `9d492352-4e73-4c43-b050-cf6a76c0b93b`。从已有冻结制作快照只读提取原八镜的 `plan.json`、`beats.json`、`alignment.json`、`timeline.json` 和原 `audio.wav`，共 40 个输入文件。R2 从 R1 私有目录逐文件验证 SHA 后复制，R1 输入和失败现场保持；没有以重新生成的文稿或录音替换原方案。

原始业务记录中的第3、7镜为 `motion_expected_highlight_missing`，第4、8镜为 `motion_invisible_highlight`，第5镜为 `motion_text_overflow`。这些旧记录没有改成成功。第3、7镜的重复高亮，以及第4、8镜与显现同起点的高亮，先由冻结时间线定位；旧观察产物已清理，因此早期 opacity 推算明确标为 `modelled_not_observed`，不当作真实浏览器采样。

R1 使用源码 `d00333cf6f2c41b49b90d8de1af2c9991ac827b0`。真实禁网预检中原第1～4镜通过；原第5镜在编译时以 `motion_relation_layout_unavailable` 停止，第5镜尚未观察，第6～8镜尚未执行。R1 现场保留在 `/root/video-feedback-20261104-r1`，未部署其候选镜像，也没有覆盖失败回执后将同一次运行写成通过。

针对原第5镜，另外使用旧 compiler4 和真实 Chromium 定点测量复现文字溢出：`file_content` 在 token_right 的 96×28 内区形成 54px 高文字，`missing_result` 在 token_center 的同高内区形成 81px 高文字。此诊断确认了实际文字几何问题，没有以 OOM 或模糊的“内容复杂”替代根因，也不充作 compiler5 的验收。

compiler5 根据最长标记文字统一加宽移动 token，保持原文字、18px 字号和44px高度；编译和审查共享布局规则。关系标签的候选位置同时检查后续关系及已放标签的通路，保持节点12px、标签6px路由净距。原第5镜的贪心标签封路问题据此修复，溢出、可见性和连线几何门禁继续严格执行。

## 本地回归

各集合按执行范围分别记录，不与历史轮次或彼此加总，也不将先前提交上的检查描述为全部在最终提交重跑。

| 范围 | 结果与边界 |
| --- | --- |
| 前端 Node 定向集合 | 103 项通过；覆盖输入错误分类、并发草稿、历史来源及制作状态等界面行为。离线页面模拟不替代真实浏览器点击。 |
| renderer Node 定向集合 | 18 项通过；覆盖渲染输入及执行合同。 |
| 前期 Python 定向集合 | 534 项通过、1 项跳过；具体集合与后续检查有覆盖，不累计为额外独立用例。 |
| 最后一次编译 / motion / review 定向集合 | 425 项通过；覆盖 token 尺寸、避障路由、高亮与兼容审查。 |
| compiler1～4 兼容性 | 以原文件 SHA 和 snapshot golden 验证旧输出规则；新输入切换 compiler5，不重写历史冻结产物。 |

原始字段并发保存、同镜真实冲突、旧客户端严格版本门禁、未来版本及权限校验，历史来源只读、阶段回调绑定 / 脱敏 / 终态保护，空范围本地提示，以及片尾停留保留原声和动作时间，均纳入对应定向集合。编译 / 审查检查不能替代发布后的工作台实际交互。

## R2 最终镜像原八镜预检

R2 使用上表指定的应用及 renderer 镜像，依次执行每镜 `compile → observe → review`，八镜全部通过，回执状态为 `exact_feedback_image_preflight_passed`。输入清单仍为原始 40 文件；编译结果保留原 plan / beats / alignment 内容及原录音字节。

| 原镜头 | 编译 | 真实 Chromium 观察 | 严格审查 |
| --- | --- | --- | --- |
| 1 | 通过 | 通过 | 通过 |
| 2 | 通过 | 通过 | 通过 |
| 3 | 通过 | 通过 | 通过 |
| 4 | 通过 | 通过 | 通过 |
| 5 | 通过 | 通过 | 通过 |
| 6 | 通过 | 通过 | 通过 |
| 7 | 通过 | 通过 | 通过 |
| 8 | 通过 | 通过 | 通过 |

本次运行使用隔离容器、禁网、普通 UID 和明确资源上限；没有创建业务 Kubernetes Job、Agent 或 TTS 任务。重复高亮、显现与高亮同起点、文字几何和连线路由均在原方案及最终镜像下接受实际观察。这里的“观察通过”是浏览器采样与技术门禁结论，不表示八镜真实 MP4 都已生成，也不表示真人已经认可声音、口播时序或画面表现力。

## 原第三镜真实 MP4 检查

服务器 `/root/video-feedback-20261104-r2/mp4-check` 的独立检查已通过。检查沿用原第三镜已通过审查的编译输入和22秒原录音，使用上表固定 digest 的 renderer，未重新生成配音或修改原始业务制作记录。输出 MP4 为518,795字节，SHA256 为 `4f046b947ec7ef8faf2592190fc9bb6f246d654a4ac72eb0a3a5676dc0e577e1`。

`ffprobe -count_frames` 实读528帧，符合22秒、24fps；音视频同长，完整解码通过。将输出 AAC 解码后与原 PCM 在原始时间位置比较，未搜索或补偿时间偏移，三个窗口各为2秒：

| PCM 窗口 | 相关系数 | NRMSE |
| --- | --- | --- |
| 开头 | 0.9999694 | 0.00789 |
| 中间 | 0.9999738 | 0.00730 |
| 结尾 | 0.9998624 | 0.01668 |

三段均满足相关系数至少0.99、NRMSE至多0.15的门禁。解码后仅有384采样的 AAC 尾部 padding，原录音未裁切。以上支持音频时长和三段原声保持，不表示每个采样逐字节相同，也不表示真人已听评自然度或核准旁白时序。本次是原第三镜单镜真实 MP4 检查，其他七镜的八镜观察结果不据此改称全部成片通过。

## 生产发布及只读核对

本轮 `release prepare → apply --execute → postcheck` 已成功。服务器 `release/summary.json` 记录 rollout ready，`release/postcheck.json` 记录 read-only postcheck passed；原 R1 失败现场继续保留。

- workbench 和 executor 两个 Deployment 均为1/1 Ready、0重启；实际镜像 digest、应用源码身份、runtime 绑定及执行器 capabilities 匹配本轮发布身份。
- 两命名空间使用新的 immutable ConfigMap `video-motion-runtime-adbf9f2ceb7b9878960b`。原 runtime 保留。
- 发布前新鲜备份和持有写锁时的备份留在服务器，旧 Pod 退出后才继续更新。备份没有下载。
- 发布后对两服务数据库合计17表核对：旧 schema、主键行、值类型、全部值及媒体 BLOB 字节保持，额外业务行0，active任务0。
- 八音色目录及项目统一音色投影通过；health / ready 为200，匿名会话为401。该项不能代替已登录浏览器验收。
- 六个其他依赖 Deployment 的 UID / spec 保持本次发布前 fresh 核对值；不把更早一次发布的历史身份当作本轮未复核的基线。

## 工作台浏览器复测

在保持登录的内置浏览器完成以下实际操作。只使用已有两镜专用测试项目 `1c60d406-688b-415d-9572-a99ec5f28724` 创建了一条镜头草稿，没有向用户原八镜项目追加制作任务。

| 操作 | 实际结果 |
| --- | --- |
| 取消所有镜头选择 | 显示“请至少选择一个镜头”，核对按钮禁用，未报断网。 |
| 保留一个镜头、取消全部制作步骤 | 显示“请至少选择一个制作步骤”，核对按钮禁用。 |
| 仅核对单镜草稿范围 | 图解0次、配音0次、草稿1次；显示“待制作（尚未启动）”，需另行确认才启动。 |
| 播放历史批次草稿 | 显示冻结的“图解1、配音A1、文稿版本7”，未以当前版本12覆盖；12秒视频完整播放到结束，实读1280×720。 |
| 查看项目预览与成片 | 历史列表列出镜头、图解／配音版本、文稿版本、时间及1280×720／24fps；已完成旧产物仍有效。 |
| 片尾停留控制 | 默认勾选；关闭时实际checked为false，随后恢复勾选。这里只检查控件，未另做整片尾部延长任务；媒体行为由定向检查覆盖。 |
| 启动唯一一条草稿 | 只选“日志保留什么”的镜头MP4草稿，复用图解1和原录音A1；浏览器看见“渲染动态画面、第1/1镜、完成0/1镜、等待时长”，最终“视频制作完成、完成1/1镜”。 |
| 播放新草稿及刷新 | 新草稿显示文稿版本12；12秒视频完整播放到结束，实读1280×720、媒体无error。刷新后新结果及完成阶段仍在。 |
| 浏览器诊断 | 本轮查看的warning／error日志为空。 |

本轮没有重新下载WAV／MP4，没有在生产界面制造同镜或跨镜写冲突、取消或故障注入；这些路径的定向回归和此前验收记录仍按各自范围引用，不冒充本轮真实点击结果。没有勾选人工时序核对或执行采用、正式导出。

浏览器新批次为 `3f7c906a-8a7d-49ca-97ec-2be21d2378f8`，新草稿为 `f85f5797-6b9a-4a5c-8c42-ca50c9a95a32`，完成文件213,357字节，SHA256 `fb2e61ac76f3ccc28cce31d559a61d26e0a780ba46a8b3d449b07333d6079092`。实际冻结时间线为 `motion-compiler-5`，不是仅靠界面文案判断使用新编译器。

服务器 `release/browser-postcheck.json` 为 `video_feedback_browser_postcheck_passed`：对照发布后备份，两服务17表原schema、全部旧typed值与媒体BLOB保持；仅新增workbench的1条批次／1条草稿，以及executor的1条operation／1条scene。业务Job共1、Agent0、TTS0；active0，所属Job／Pod／ConfigMap已消失，renderer命名空间Job和Pod均0。原项目revision、采用与人工核准状态未改变。

本地浏览器截图为 `.runtime/video-feedback-20261104-r2/browser-history.png`，SHA256 `f8e820f80b2bbbd61da0f7c9e4cac0a4b2d5d79251f9844d7ac2b62f11c0d413`，记录新草稿来源、规格和完成状态；截图不包含凭据。

## 数据范围与人工确认边界

WAV、MP4 和数据库均未下载。八份 plan 及两张选取的 JPEG 已下载并用于目视检查；不能把本地保留范围描述为只有技术 JSON。原录音、完整冻结输入、数据库备份和 MP4 保留在服务器私有目录。

发布后初次只读核对时新增业务行为为0；随后浏览器验收仅新增上节的一次草稿制作，Agent／TTS始终为0。声音自然度、人工口播时序、审美与正式交付仍需单独确认。当前仍是720p／24fps，1080p尚未支持；尾卡完整来源URL及核对日期需要显式可信输入，复杂图解中的移动路径、隐藏对象后的旧连线与整体节奏仍可继续改善。

OpenSpec 变更为 `fix-video-user-feedback-reliability`，项目 `ec663f5a-53d3-4a0e-9037-933eb788d183`；规格服务只作为记录和验证工具使用，本文件不代替其任务完成状态。
