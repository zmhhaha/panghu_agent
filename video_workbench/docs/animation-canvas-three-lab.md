# Canvas 2D 与 Three.js 开发者小样

2026-11-07，开发分支 `codex/video-animation-lab`。本轮按用户新确认的方向，先在 video-service 的本地动画开发者工作台补充两种真实引擎，方便分别比较和标注。公共渲染服务、公共库及迁入 panghu-tool 的重构继续延后。

## 本轮用途

Canvas 2D 用于大量请求的进入、排队、积压和分发；Three.js 用于分层结构展开、视角观察和重新组合。两类核心动画分别由真实 Canvas 绘制和 Three.js/WebGL 渲染，HTML 可以提供标题和可访问说明，不代替对应引擎。

每类小样以同一内容、音频时钟和有限对象生成比较版本。使用现有工作台的同步播放、时间跳转、正反定位、问题区域、偏好、审阅和历史导出。新样例身份与旧框图、过程、跨镜及冻结评测分开；更新编译器或实际依赖资源后，不把旧意见自动当作新版本的认可。

## 运行与依赖

从应用仓库根目录启动现有工具，继续使用已有数据和资源目录：

```sh
python -m video_workbench.animation_lab \
  --data-dir video_workbench/rendered/feedback-20261106/animation-lab/data \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets \
  --evaluations-dir video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation \
  --port 8766
```

打开 `http://127.0.0.1:8766/`。两种编译器分别为 `animation-lab-canvas-compiler-1` 和 `animation-lab-three-compiler-1`。Three.js 固定为 npm `three@0.170.0`，锁文件位于 `video_workbench/animation_lab_webgl/package-lock.json`；播放期间不通过公网 CDN 临时下载代码。

Three.js 使用单一本地模块 `three.module.min.js`，691,648 字节，SHA-256 为 `08fd7545d13d2c7fb65ab691530a802dafefd638596501854f267d0fb13c39e7`。锁文件保留官方包的 SHA-512 integrity；运行时检查的是实际提供给浏览器的模块字节，不能只靠包版本号判断资源一致。

已有 GSAP/字体资源后，从应用仓库根目录安装并导出 Three.js：

```sh
npm --prefix video_workbench/animation_lab_webgl ci --ignore-scripts --no-audit --no-fund
python video_workbench/animation_lab_webgl/export_assets.py \
  --assets-dir video_workbench/rendered/feedback-20261106/animation-lab/assets
```

Windows PowerShell 可将 `npm` 换为 `npm.cmd`，并将第二条命令写成一行。安装需要 npm 网络访问；导出脚本没有网络调用。它检查锁文件、安装版本、模块摘要和 MIT 许可，输出模块、`three.LICENSE.txt` 和 `three-assets.json` 回执；若目标同名文件内容不同则拒绝覆盖。导出后重启工作台，让内存资源快照重新读取。

Canvas 不需要 WebGL；Three.js 必须在实际浏览器中创建可用的 WebGL2 上下文。资源缺失或摘要错误时，只有 Three.js 案例显示不可用；浏览器初始化失败或上下文丢失时，画面显示具体失败，不能展示二维替代画面并报告 Three.js 成功。启动参数请求软件渲染不等于已证明实际使用哪一种软件后端，采集记录应分别保留请求方式和实测上下文。

常见诊断：`three_package_identity_mismatch` 表示安装包与锁文件不符，应先重新核对安装；`three_asset_digest_mismatch` 表示模块内容不符；`three_asset_destination_conflict` 表示目标已有不同内容，需检查后再处理，脚本不会覆盖。工作台报告本地资源不可用时检查资源目录和摘要；浏览器报告 WebGL2 不可用时检查该浏览器的图形能力，旧二维模块和 Canvas 可以继续使用。

## 如何核看

先比较同一时刻的两侧画面，再前进到中段和末帧，返回首帧及任意早期时刻。对象、队列状态、相机和画面都应恢复到该时刻应有的状态，不能保留上次播放的残影、相机偏移或累计进度。

Canvas 应检查请求身份与数量、排队顺序、进出路径、画面边界和可见变化。Three.js 应检查层级间距、对象保留、相机视角、遮挡及重组后结构；视角运动应帮助理解结构。对应采集需要真实像素或场景状态，单纯 DOM 方框和脚本存在不能证明引擎成功运行。

标注和验收操作继续保存在现有 `data/animation-lab.db`。技术测试记录须注明用途，结束后撤回或暂缓，避免被当作用户偏好。完整 JSONL 和原冻结资源在整体清理前与长期数据库一起保留。

## 验证状态与范围

自动检查：开发者工作台相关 Python 测试319项通过；Node 测试30项通过（工作台4、评测界面7、渲染器19），新增或修改的三个 JavaScript 文件通过语法检查。

其中，`tests/video_workbench/test_animation_lab_engine_integration.py` 的15项检查覆盖可选资源错误隔离、旧案例身份和标注保留、本地资源路由、两类新案例标注/刷新/导出、冻结资源及采集前篡改拒绝。这组检查使用明确标记的合成 JavaScript/字体资源，不执行浏览器，也不证明 WebGL 能力。实际引擎检查与这组接口测试分开记录。

固定运行环境中的真实采样已完成：`auto-evaluation/canvas-queue-r2/technical.json` 和 `auto-evaluation/three-layers-r2/technical.json` 均为 `passed`。每种引擎各两个版本：Canvas 每版采集75帧、检查24个动作窗口；Three.js 基线采集9帧/2个动作窗口，候选版采集12帧/3个动作窗口，共171帧、53个动作窗口。检查绑定实际计划/时序、真实引擎上下文、可见对象、像素和动作变化，以及反向定位后的状态恢复。它们没有运行模型评审，审美和旁白时序仍待人工评价。

采样使用已有 `video-motion-renderer:latest` 的固定摘要 `sha256:a437f7cb8c3f50bfd0cf08441bd341ec153568e418246c484b9dd64d22f33800`，实际记录 Node 22.20.0、Chromium 149.0.7827.0、HyperFrames 0.8.149 和 GSAP 3.14.2。新开发者观察器随冻结 overlay 提供，不需要发布新生产镜像。原始采集、资源/运行身份、清理回执和报告保留在统一忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/auto-evaluation/`。

早期 Canvas 基线采集因观察器未挂载失败，原失败回执保留在 `canvas-queue-r1/`，没有改写成通过；修复采集挂载后的结果单独存入 `canvas-queue-r2/`。

真实浏览器验收使用 Windows 的 Codex 内置 Chromium 157.0.0.0：两类案例的双侧预览均就绪；Canvas 播放并同步定位2.125/6.9秒，Three 同步定位9.36秒、播放至18秒自动结束，两侧末帧定位17.958秒；均完成反向归零。两类技术标注分别在6.9秒和9.36秒保存、刷新保留、完整 JSONL 导出并撤回。导出的两条标注均为 `retracted=true`、`effective=false`，不进入后续用户偏好。最终重启加载当前源码后，两类原生报告仍显示技术采样通过。

实际浏览器回执 `engines-browser-acceptance.json`、完整操作历史导出 `engines-browser-annotations-before-withdraw.jsonl`、撤回记录 `engines-browser-test-export-records.json`、最终页面 `engines-final-dom.txt` 及两类截图同样保留在统一证据目录。上述技术验收不替代开发者对表现力的评价。

本轮不扩大生产 Agent 模板准入，不修改普通用户制作流程，不部署 Worker 或合入主线。静音小样不证明真实旁白同步或语音自然度；浏览器预览和原生采样也不自动证明 MP4 编码、任意文稿能力或用户审美批准。

OpenSpec 变更为 `add-video-canvas-three-developer-capabilities`，本轮限定范围的实现、技术检查和浏览器验收已完成。旧音画表达变更的 Canvas/Three 规划项增加本变更的交叉引用；旧生产全面视觉闭环6.3、框架用量/费用和生产存储治理保持原职责记录。

OpenSpec 最终读回8/8项完成、校验通过；旧7.1/7.2只添加交叉引用，全部原勾选状态保持。完成回执为统一证据目录中的 `openspec-canvas-three-completion-receipt.json`，对应存储修订 `0ff9f09fe127daa552ddb7e1d4e00a95ac81a782`，未执行 apply 或 archive。
