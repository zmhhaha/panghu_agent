# 动态图解实现与验收

2026-11-02。用户已授权按 HyperFrames 设计实施。代码位于独立分支
`codex/video-hyperframes-motion`，基于已提交的视频应用 `de705b5`，`5e63150` 为前一 R8 底座检查点。
本轮演示改动保存在同一独立分支，最终提交号由关闭记录/OpenSpec记录。
先前暂停的 `codex/video-narration-first` 工作树及未提交草稿保持原样。

规格来源是 OpenSpec 项目 `ec663f5a-53d3-4a0e-9037-933eb788d183` 中的
`add-hyperframes-motion-video`。本文件记录实现与实测边界，不以文档校验替代视频效果验收。

## 已实现的增量模块

- 独立 `video.motion-plan.v1` profile、资源包和受限 JSON 验证；默认目录不发布新能力。
  原 `video.storyboard.v1` / `video.scene.v1` 与静态资源包保持原契约。
- `motion.py`：稳定的旁白句段、原文与实际读音映射、独立对齐证据、按实际音频解析的时间轴、
  流程／对照／结构聚焦模板，以及确定性 HTML/SVG + GSAP 工程编译。
- `motion_store.py` / `motion_gateway.py` / `motion_workflow.py`：通用 Agent 上的持久候选任务。
  提交不明确时查询原任务；任务完成只新增候选；单独、事务性采用；默认执行关闭。
- `motion_snapshot.py`：独立的 snapshot v2，冻结混合镜头、音频、时间轴和完整运行环境指纹。
  草稿可以明确引用候选；正式交付要求当前采用版本和经过核对的动态时序。
  已冻结快照不随之后的项目编辑变化；v1 继续按静态快照读取。
- `motion_review.py`：核对真实 seek 后的对象集合、动作前／中／后状态、目标位置、绘线、
  中文字体与溢出，以及反向 seek；允许计划中的静止留白。

这些模块尚未接入线上工作台界面、批量制作入口或生产渲染协调器。
离线测试通过不表示已发布新 Agent 能力，也不表示实际视频或语音自然度已经验收。

## 分阶段运行

1. 固定 HyperFrames `0.8.149`、源码参考
   `2905162d925fc87fb8242e24ed0bbf025d307ea1`，固定 Node、GSAP、浏览器与字体。
2. 在非特权、无业务凭据、无宿主／设备挂载、无外网的独立环境内运行 15 秒技术片段。
   单 worker，先按 1/2 CPU、2/4 GiB 内存、4 GiB scratch、300 秒墙钟预算测量。
3. 运行 24 秒动态图解／静态最终态对照，复用同一段 18 秒云健录音，前后各留 3 秒。
   不重新调用 Agent 或 TTS，不截断旁白。对齐仍明确标记 `estimated / pending`。
4. 核对真实媒体、动作与资源，并分别收集讲解、视觉节奏和语音自然度评价。
   效果未获认可时，不把接入框架记为效果通过，不切换默认生产流程。
5. 样片方向通过后再完善工作台入口、生产任务适配及 60 秒组合容量实验；保持项目统一音色、
   保留旁白、默认无附加字幕。上线和主线合并分别记录，不自动合入。

## 复现输入

生成器只写一个新目录，不调用模型、TTS、浏览器或外部服务：

```powershell
python -m video_workbench.examples.build_motion_samples --output D:/github/armbianbegin/.runtime/video-motion-samples-new --speech-wav D:/github/armbianbegin/.runtime/video-audio-production/presentation-redesign/narration-sample.wav
```

其中 15 秒配音是明确标记的测试提示音；24 秒对照使用严格完整 SHA-256 匹配的原云健录音。
不要把提示音片段当作语音质量样片。动作时刻来自对应录音的估算句段，不能称为强制对齐。

## 校验与边界

```powershell
python -m pytest tests/video_workbench/test_motion.py tests/video_workbench/test_motion_snapshot.py tests/video_workbench/test_motion_review.py -q --basetemp=D:/github/armbianbegin/.runtime/video-hyperframes-tests/pure-new
```

运行环境指纹包括 HyperFrames、Node、浏览器、FFmpeg、GSAP、字体、系统包和 npm lock，
另由协调器提供实际镜像 digest。冻结字段完整不等于安装环境已验证，执行器必须独立核对。
草稿与正式质量使用不同快照摘要，不共享不兼容缓存。

机械感、发音与旁白表达需要独立试听。当前改动不替换语音模型，也不能保证达到参考 UP 的艺术效果。
真实 ARM 渲染结果和部署状态以随后追加的实测记录为准。

## ARM 集成记录（2026-11-02）

在 `video-motion-experiment` 独立 namespace 实验，固定在 `arm-cluster-master`；
沿用 1/2 CPU、2/4 GiB 内存、4 GiB scratch、单 worker 的预算。
生产工作台 `video-workbench-5c69474df6-9l6rv` 在检查时仍为 Running、重启数 0；
该状态观察不等于完整的业务性能验收。

已构建并推送 `video-motion-renderer:latest`，A/B-r8 使用的实验镜像固定为
`sha256:54cfd9182d9384cf9bdac96d0c7234ecbbc879d909c93f65bd4ea5f7765e2dc9`，
release 为 `motion-experiment-20261102-r7`，构建输入清单摘要为
`72fe3a0f3719c54a9bef54312530e3ef0f874e9e88c46785b3f41a73c4a5e27a`。
已实测 Node 22.20.0、Chromium 149.0.7827.0、FFmpeg 5.1.9、GSAP 3.14.2。
执行器逐项核对安装环境指纹，镜像 digest 由协调器独立传入。

集成期间保留的失败证据：

| 实验 | 结果与修复 |
| --- | --- |
| a-r3 | strict 拒绝进度条 CSS/GSAP 变换冲突及未声明备用字体；修复编译输出，保留 strict。 |
| a-r4 | 编码、完整解码成功；独立观察器引用了不存在的 Puppeteer 私有路径。 |
| a-r5 | 编码成功；观察器等待导出专用的 `__hf.seek` 桥接接口超时。改为已实测的原生 `__player.renderSeek(...,{exact:true})`。 |
| a-r6 | 收集包超过 6 MiB，原实现未能返回诊断包；截图改为 JPEG，超限时返回明确失败的诊断包并排除媒体。 |
| a-r7 | render/observe 均成功，49.42 秒；独立质量检查拒绝中文行框溢出和绘线中间态缺失。该 MP4 不计整体技术通过。 |

上述失败与后续新实验分开保留，不覆盖旧输入或将中间编码状态发布为成功。
原生 exact seek 的 DOM 观测是动作证据，不能代替实际 MP4 抽帧；两者分别核查。
两个阶段的 cgroup CPU 与峰值内存属于同一容器的累计指标，不相加。

另修复了非整帧 WAV 被视频帧取整后误拒的问题：只补齐最后一帧，不裁断或拉伸旁白。
Agent 取消请求采用持久化的最多 4 次有界退避；网络失败、崩溃或重启后仍可确认同一个任务的真实终态。

本地相关完整回归曾为 1077 passed / 3 skipped（Windows 条件），前端 30 passed。
后续候选与取消修复聚焦回归 228 passed / 1 skipped；最终 renderer/收集/观察检查另行通过。
这些集合有重叠，不相加作为测试总数。真实取消、OOM、Job 截止清理与生产任务协调器仍待验收。

实验输入、构建日志、Pod/Job 状态和媒体保存在本机
`D:/github/armbianbegin/.runtime/video-hyperframes-build/` 与对应的
`video-motion-samples-*-result/` 目录；不包含在应用镜像或 Git 提交中。

## 已通过的技术样片

`motion-technical-a-r8`：15 秒，1280×720 / 24fps / 360 帧，视频与音频均为 15 秒，
完整解码通过。7 个计划动作的前／中／后状态、实际 seek 落点、中文行框与反向 seek 均通过。
已抽查真实 MP4 的连线中间态及后续移动帧；审核使用未修改的原始观测。

整个 render + observe 为 48.932 秒，render 阶段 43.679 秒。
同一容器累计 CPU 72.794 秒，cgroup 峰值内存 413564928 字节（约 394 MiB）。
4 秒采样的各目录最大占用：work 20392 KiB、tmp 21008 KiB、input 744 KiB；
这些是采样值，不是连续监测的精确临时盘峰值。

该片使用测试提示音，不能作为 TTS 或讲解效果验收。
24 秒旁白动静对照及用户的清晰度／节奏／声音评价独立记录，
对齐仍为 estimated / pending，未把技术通过改写为时序已人工核对或用户满意。

## 第一版动静对照与用户反馈

24 秒动态样片 `motion-history-b-r8` 的 11 个动作与布局检查通过，
同旁白静态样片 `static-history-b-r8` 的对象状态恒定且等于动态最终状态。
两版均为 576 帧、24 秒有声 MP4，复用完整 18 秒云健 WAV，前后各 3 秒留白，
无附加字幕；没有新 Agent 或 TTS 请求。已核查实际动态 MP4 的多处关键帧。

| 任务 | render+observe | render | 累计CPU | 峰值内存 |
| --- | ---: | ---: | ---: | ---: |
| 动态 B-r8 | 70.736895 s | 64.652252 s | 108.793470 s | 398020608 B |
| 静态 B-r8 | 69.217364 s | 63.097388 s | 107.622690 s | 410038272 B |

表中不含 K8s 排队、容器准备及操作者收集时间。动态任务首次 client-side apply
因 last-applied 注解超过 256 KiB 而未创建 ConfigMap；单独 create 输入后原 Job 完成，
因此该任务的 Job 时长还包含等待挂载的时间。后续先创建输入再创建 Job；没有重复渲染。

用户对第一条动态草稿的实际评价为：**“有帮助，但还需要明显提升表现力”。**
这是本轮真实效果反馈，不能记为满意或批准上线。继续先改样片：用受控的日志／记录／
上下文图形表现保存、复制与筛选过程，改善分阶段布局和重点变化；工作台接入与 60 秒组合仍待后续验证。
语音自然度未因本次改动提升，精确句段对齐仍未验收。

两版音频 SHA-256 同为
`3fa64b4a6800984b4b87719adca38a2eee533d19a5616d7a79305c08b566bce8`；
独立技术审查 passed，时序仍为 `estimated / pending`。工作清单仅新增动静对照一项，
当前为 11/21；用户尚未认可效果，不能勾选效果验收。

## 受控演示：R9 误报与 R10 技术通过

`motion_demo.py` 提供独立的 `resolve_demo_timeline` / `compile_demo_project`；
版本对为 `motion-demo-compiler-1` / `record-demonstration-1`。
本地实验须显式启用 demonstration；默认 Agent 契约与既有 snapshot 入口仍只接受流程、
对照、结构聚焦三模板，未发布新 profile。原 `motion.py` 未修改，R8 三份工程的六项
编译产物已分别重编译并逐字节核对一致。

这版用大纸面日志接收完整回答和尝试记录，随后让日志与两条记录同步左移，
再将完整回答的副本移入右侧“下一轮历史（局部）”；原回答和橙色尝试记录都留在日志。
受限动作只有 reveal/hide/move，`relations=[]`，不使用连接线或改变橙色记录的颜色，
计划不能输入任意代码、URL、CSS、坐标、摄像机或音频秒数。

本地生成器：

```powershell
python -m video_workbench.examples.build_motion_demo --output D:/github/armbianbegin/.runtime/video-motion-demo-next --speech-wav D:/github/armbianbegin/.runtime/video-audio-production/presentation-redesign/narration-sample.wav
```

生成器要求新目录；示例目录名不表示它已运行。真实 R10 绑定输入为
`D:/github/armbianbegin/.runtime/video-motion-demo-r4-bound-r9`，结果为
`D:/github/armbianbegin/.runtime/video-motion-demo-r10-result`。

R9 已渲染，但独立技术审查拒绝 `motion_text_overflow`：结果说明的空 detail 被
`display:none` 隐藏，返回 (0,0) box，子节点严格边界检查把它误测为父元素以外。
失败记录保留在 `video-motion-demo-r9-result`，编码成功未被写作技术通过。
修复为不生成空或纯空白 detail，保留所有非空文字的 scroll 尺寸和四边界检查。

R10 使用新输入，沿用 R9 镜像而未重建：release `motion-experiment-20261102-r9`，
digest `sha256:43964c72bebb96b12e2cf57248120f6a1e4eeeccd7206508f4f360948fb80932`。
其独立技术审查已真实通过：

| 项目 | R10 实测结果 |
| --- | --- |
| 媒体 | 1280×720、24fps、21秒、504帧；音视频流均21.000秒，完整解码通过。 |
| 音频 | 原完整18秒录音加1秒片头、2秒片尾；不裁断、不加速。原段相关系数0.9999584194。 |
| 动作 | 11个动作窗口，共33个前/中/后采样时刻，每个时刻核对全部7对象。 |
| 墙钟 | render+observe 63.709151641秒，render 57.650607502秒；Job生命周期69秒。 |
| 资源 | cgroup峰值403398656 B，同容器累计CPU97.433442秒，阶段累计值不相加。 |

墙钟渲染数据不含排队和容器准备。R10 临时盘采样启动早于 renderer 就绪，失败后退出，
日志只有首时间戳，没有有效样本；不能记为 0 或沿用 R9 数据，亦未为此重跑。
R9 的 work 约21036 KiB、tmp 约21012 KiB、input 约884 KiB 仅是该失败实验的采样观察值，
不是精确峰值，也不填补 R10 临时盘证据的缺口。

独立 review 增加了全场景状态检查，复制之后原回答或尝试记录在晚期消失、移位均会被拒绝。
最新联合本地回归为 281 passed / 1 skipped、Node 17 passed，与历史集合重叠，不相加。
Agent/TTS 调用仍为 0，时序 `estimated / pending`。用户已看到新版但尚未评价，
主观效果仍 pending，清单保持 11/21，不新增效果勾选。
演示改动继续保存在独立 feature 分支；生产/UI与整片入口、新 profile 发布、60秒容量、
真实取消/OOM/超时及清理仍待验收，没有生产接入、主线合并或 apply/archive。

本轮两个实验 Job 及对应 ConfigMap 已按精确名称删除，namespace 与 deny-all 策略保留。
生产工作台在收尾检查时仍 Running、Ready=true、restart=0；正常实验结束的清理已完成，
故障取消/OOM/超时自动回收及完整业务性能验收仍待验证。
