# Three.js 通用外观适配：本地 CPU 验证

本样例验证已有 Three.js 后端的通用光向、物体坐标材质与距离空气适配。
它不申请生产准入、调用模型、建立学习任务或改变已经暂停的艺术验收。

内容来自 `../spatial-foundation-study/baseline-source.json` 的两座连接山体、
纸面、远山、红日、飞鸟、云带、前景坡岸与松树，沿用原 `plan.json` 和
seed 631907。山体采用原 MASS_CONFIG/authoredHeight 与同一网格连接次序。
其原始独立正交投影被映射到一个明确的 Three.js 世界，保持原画面布局，
非山体继续使用原画笔函数绘制一次后作为静态纹理。相机按原绝对帧推进。

这是固定内容的机制适配样例。山体改为逐顶点线性色彩，0.28 高程裁切代替
原山脚渐隐，因此不具备原版毛笔皴擦的艺术等价性；不得作为默认水墨效果、
艺术升级或用户认可记录。没有用 Three.js 的名字证明真实 GPU 或摄影效果。

运行（输出目录必须不存在）：

```powershell
node video_workbench/examples/three-spatial-foundation-study/render_native.mjs --out D:/codex-work/operations/video-next-work-20261127/three-native-new
node video_workbench/examples/three-spatial-foundation-study/verify_geometry.mjs D:/codex-work/operations/video-next-work-20261127/three-native-new
```

可用 `--chrome` 和 `--playwright-package` 指定已有安装。默认使用本机 Chrome、
Codex 已有 playwright-core、PATH 中已有的 ffmpeg/ffprobe，不下载运行时。
浏览器沿用正式引擎 guard 和 SwiftShader 启动选项，要求可见 WebGL2、真实软件
renderer、无 context lost/GL error。资源仅由本地静态清单提供。

像素诊断包括浅色实体覆盖、显式半透明、雾色不改变 alpha/覆盖、世界光向、
非均匀缩放的逆转置法线及固定物体坐标材质。之后验证六个时间窗的重复、
逆序和整页重载输出摘要，以及原相机推进确实改变像素；输出 240 帧、24fps、
1280×720、10 秒无声 H.264，并完整解码。几何另以原函数逐顶点比较 Float32
输入及连接数量，明确记录量化误差。

`evidence.json` 记录实际输入文件摘要、原始基线、受控差异、浏览器版本、
耗时与媒体摘要；`observation-*.json` 保留实际 helper/顶点操作及固定预算。
这些计数不等于整个浏览器/整片的 CPU 硬上限。连续观感与艺术认可保持待定。

首轮原生诊断曾把法线夹具平面转到 90 度而看到红色后体，已保留失败结果；
后改为 30 度可见取景，对应解析法线点积与实际像素比较，不放宽误差阈值。

2026-11-27 的实际 r2 结果已附：`preview.mp4`、`preview.png`、`evidence.json`、
`diagnostics.json`、`geometry-comparison.json`、`observation.json` 与 `native-engine.json`。
完整本地批次墙钟 99.04 秒，10,330 个山体顶点的原始高度函数比较通过，
最大 Float32 量化误差小于 4.77e-7。重载后的单个 realm 初始化实际 30,990 次
材质噪声、247,920 次角点操作、零法线导数；247 次每山更新累计 2,551,510 次
法线/光向/空气顶点计算。该回执不是整个批次总计，另保留重载前原回执并在
`operation-totals.json` 汇总两次实际 realm，包括诊断、重复、逆序及重载开销，
而非只把最终影片 240 帧乘一个估算值。各 realm 限额不冒充整批 CPU 硬限。

该原生样例冻结的是适配器、surface 与 budget 三个实际 JS 及已有 Three 依赖，
由本地宿主在导入前安装固定策略。正式四资产描述符及其元数据、能力准入和宿主
接线需要各自独立证据；本样例不把三 JS 的本地执行冒充正式四资产发布。
