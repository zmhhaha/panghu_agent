# 八类讲解动画效果

2026-11-07，开发分支 `codex/video-animation-lab`。本轮在现有本地开发者工作台增加八类固定效果，原有七例扩为十五例。限定范围的实现、技术检查及浏览器操作验收已完成，表现力仍待开发者比较和标注。

## 本轮样例

| 案例 | 表达用途 | 重点检查 |
| --- | --- | --- |
| `condition-branch` | 同一请求按条件进入不同处理路径 | 分支条件、路径和请求去向是否对应 |
| `parallel-wait` | 多个任务并行后等待汇合 | 是否能辨认并行、完成和仍在等待的对象 |
| `architecture-expand` | 保留整体结构并展开局部层级 | 展开后是否仍理解上下层关系 |
| `focus-shift` | 随讲解把注意力移到有关对象 | 重点迁移是否清楚，其他信息是否仍可定位 |
| `data-compare` | 在同一数据尺度下逐项对照 | 图形与数值是否一致，是否夸大差异 |
| `code-execution` | 有限代码行与执行状态关联 | 当前行和变量/输出变化是否一致 |
| `local-replace` | 保留上下文，仅替换有关内容 | 局部改变是否连续且易追踪 |
| `svg-morph` | 通过真正的SVG路径变化解释对象改变 | 是否实际改变路径，往返定位能否恢复 |

每例使用18秒静音时钟、同内容的两个独立比较版本。A/B区别限于表达手法，不能通过改变结论、数据或音频时长制造优势。编译器为 `animation-lab-effects-compiler-1`，固定计划为 `video.animation-lab-effect-plan.v1`，时序引擎为 `svg-effects`。这轮只接受八个内置配方的固定计划，拒绝任意文本、对象或代码注入。这些是开发者可以反复比较、标注的小样；代码执行例也不是任意代码解释器。

## 运行

继续使用已有工作台、数据库、GSAP和字体资源目录，从应用仓库根目录启动：

```sh
python -m video_workbench.animation_lab \
  --data-dir video_workbench/rendered/feedback-20261106/animation-lab/data \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets \
  --evaluations-dir video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation \
  --port 8766
```

Windows PowerShell可写为一行。打开 `http://127.0.0.1:8766/`，在相应分类选择效果。新八例使用已有本地GSAP/CJK字体，不增加模型、Agent、TTS或公网CDN依赖；Three.js资源缺失应只影响其原案例。

## 比较与标注

先看同一时刻的两侧画面，再播放至中段或末尾，返回首帧和早期时刻。原先隐藏、替换或形变的对象应恢复该时刻的状态，不能保留上次播放留下的样式、文本或路径。两个版本使用同一个外部时钟，不以墙钟、独立RAF或未冻结随机性自行推进。

优先描述“哪一秒、哪个区域、哪里难理解”，再选择偏好。时间/区域标注、刷新、撤回和完整JSONL导出沿用现有流程。旧七例的字节、身份和历史不会因为加样例而迁成新效果的意见；不同新效果之间也不能相互继承标注。技术验收标注应注明用途，并在结束后撤回，避免进入用户偏好。

同属“框图”分类不代表支持同一种编辑器。这些新固定配方只比较其基线和候选版本；旧 `video.motion-plan.v1` 框图的有限 `flow`/`focus` 布局修改不自动适用于新例。

## 技术证据与范围

新例使用真实HTML/SVG/GSAP和HyperFrames绝对时间定位。观察与检查应绑定源计划、时间、固定依赖、字体、浏览器和截图摘要，读取实际DOM几何、可见文本、状态及SVG路径。图形存在或任意对象发生移动，不能代替对声明动作及有关目标的检查；`svg-morph`必须观察真实路径变化。

计划内原生采集限定为八例各两个版本，不自动重试或运行模型。失败回执保持原样；修复后用新的源revision重新冻结并记录范围，不能把旧失败改写为通过。原生采样和浏览器操作分别记录，合成夹具另行标注。采样通过只说明记录窗口的技术状态，不证明逐帧流畅、审美、真实旁白同步、音色自然度或MP4编码出口。

最终完整工作台Python回归519项通过，用时333.23秒；Node检查30项通过。新增 `tests/video_workbench/test_animation_lab_effects_integration.py` 的50项、原引擎集成15项均包含在相关回归中。它们保存原七例在 `e4366c39fceefbf053c715606af49891465a148f` 的精确输入、产物和预览身份，检查十五例目录、同语义/音频A/B、新历史隔离、冻结资源、陈旧写入、依赖错误和局部编辑范围。八项检查逐一核对新例的A/B在实际目录API中提供全部动作前/中/后观察点和正确时刻，防止新动作类型被旧过滤条件漏掉。反向合同使用明确标记的合成状态/摘要描述，验证状态、图像或reset声明不一致会被拒绝；不将夹具通过视为浏览器或实际SVG执行成功。

历史冻结评测复核使用该运行保存的适配器字节重建请求，继续核对冻结运行、产物、请求摘要、overlay资源和原生回执；适配器升级不会将旧证据误认成当下版本，资源篡改仍拒绝。历史冻结、证据和效果集成相关94项通过；另只读复核旧 `diagram-r1` 的candidate/layout-focus两侧和当前 `condition-branch-effects-r2` 两侧真实证据，均通过，没有覆盖历史报告或新增渲染/模型调用。

固定环境的最终有效原生证据已覆盖八例16个版本，共300帧、84个动作窗口。每份产物都与当前源码编译出的精确摘要匹配，记录真实DOM/SVG、动作目标和图片变化，首帧往返保持状态及JPEG摘要严格一致；不使用像素容差。每份成功执行的容器均确认清理。完整源身份、版本摘要及汇总见证据目录的 `effects-final-artifacts.json`。

本轮实际执行32次原生采集：首轮八例双版本16次；源修复后，第二轮四个逻辑配方8次和数据比较2次；局部替换的`r3/r4/r5`各2次。最终有效16份并非总尝试次数。初始窗口后的执行均为检查问题、修改源码、另行冻结后的手动复测，没有自动重试；旧源成功及失败证据全部保留。

| 案例 | 最终有效冻结运行 | 帧数 / 动作窗口（双版本合计） |
| --- | --- | --- |
| `condition-branch` | `condition-branch-effects-r2` | 54 / 16 |
| `parallel-wait` | `parallel-wait-effects-r2` | 36 / 10 |
| `architecture-expand` | `architecture-expand-effects-r1` | 30 / 8 |
| `focus-shift` | `focus-shift-effects-r1` | 30 / 8 |
| `data-compare` | `data-compare-effects-r2` | 30 / 8 |
| `code-execution` | `code-execution-effects-r2` | 54 / 16 |
| `local-replace` | `local-replace-effects-r5` | 36 / 10 |
| `svg-morph` | `svg-morph-effects-r1` | 30 / 8 |

`data-compare-effects-r1`曾因字号低于14像素被拒绝；`local-replace-effects-r1`至`r4`保留各自的首帧恢复检查失败。局部替换的逻辑和DOM状态保持相等，特定静态圆角背板在同状态下出现了光栅化差异；最终改为直角矩形，在`r5`通过原严格JPEG摘要检查。旧失败回执没有覆盖或删除，不把仅状态相等写成像素相等。

真实浏览器已逐项检查八例双侧就绪、动作观察点、中段、末帧17.958秒、归零、前进一帧0.042秒及播放/暂停。SVG形变例的技术标注在11.5秒保存、整页刷新保持、完整JSONL导出并撤回；导出记录 `retracted=true`、`effective=false`，没有新增偏好。最终局部替换`r5`在重启服务后重新核对双侧中段11.5秒、末帧17.958秒和归零。证据为 `effects-browser-acceptance.json`、八类截图和 `effects-browser-export-retracted.json`。`effects-final-validation.json`绑定最终测试结果和两份原始验收回执的摘要；所有技术结论保持限定范围，用户审美尚未批准。

常见诊断：

- `animation_evaluation_layout_not_supported`：新配方没有旧框图的 `flow`/`focus` 编辑能力，使用原基线/候选对即可。
- `animation_lab_effect_path_change_missing`：声明的路径动作未观察到相应属性变化；绘制核对 `stroke-dashoffset`，形变核对真实 `d`。
- `animation_lab_effect_reverse_seek_mismatch`：返回首帧后的状态或截图与首次记录不同，需检查文本、局部样式和路径是否完整重建。
- 字号、非有限数或边界检查失败：核对可见文字至少14像素、实际几何在1280×720画布内，并保留原失败证据；修复后重新冻结版本。

## 保存与后续范围

长期数据库沿用 `data/animation-lab.db`，原始证据沿用 `video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation/`。不为这轮另建散乱执行目录；完整历史、原冻结产物、实际回执及截图在整体清理前保留。

OpenSpec变更为 `add-video-explanatory-animation-sample-library`，限定效果库14/14项完成，最终校验与读回通过，存储修订 `7081ddf44cb544ad76ca4df20b38144fd55d767d`。完成回执为统一证据目录的 `openspec-explanatory-effects-completion-receipt.json`。这14项均为实现及技术交付，没有将用户审美批准作为已完成项。本轮不添加普通用户实验页，不扩大生产模板准入，不部署、不合入主线、不apply/archive。公共渲染服务、公共库及迁入panghu-tool的重构仍按原计划延期；旧生产全面视觉闭环6.3不因增加固定样例而自动完成。
