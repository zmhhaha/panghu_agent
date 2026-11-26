# 同一共同山体的通用表面引导与十秒复用样片

本例从保留的35版 `shared-mountain-unity-study` 复制作者源码与完整世界，保留山体高程、
两张相连网格、固定正交机位、十个对象的位置、种子、近岸、石松、云海、红日和飞鸟。
旧例与其冻结来源保持原件。`baseline-source.json` 是原始来源快照，source身份
`1b6693b620f576dacf2b328367999a00458b8440f3f86ff825e996b153734be0`。

本轮增加的通用输入只引导已有毛笔接触：

- `tools.surface.evaluateLighting` 使用与网格同坐标系的法线和统一作者光向，保留原稿
  的光向映射区间；它控制留白、落墨和笔路提收，不绘制一张平滑灰阶阴影底。
- `sampleMaterial` 在固定世界位置采样三个尺度的变化，轻微调节墨量和干湿；不改变
  实体coverage，不随时间重新抽样，不冒充完整PBR材质或真实石面重建。
- `sampleAtmosphere` 按明确声明的作者距离压低远处颜料对比。返回的transmittance
  只用于颜料着色，山体实体alpha与纸色substrate仍由原coverage回调独立决定。

`STUDY_OPTIONS` 是冻结来源内的开关，包含lighting/material/atmosphere和diagnostic。
runner为每种条件生成新的source与binding，不修改旧例/旧binding。诊断模式coverage、
depth、normal、light独立成图；它们不进入最终ink画稿的合成。

## 镜头计划

1280×720、24fps、240帧、10秒，无旁白/音乐。0–0.75秒保持起幅，0.75–9秒缓慢推进，
zoom从1到1.07，中心从(640,360)到(648,350)，9–10秒保持落幅。十个对象与所有图层
仍静态缓存一次，所有层parallax=1；山体内部XYZ正交机位不变，不绕到未绘制背面。

镜头选择在计划中预先确定，并不形成所有影片都必须推进的默认规则。完整241个
求值帧（含终点）均检查取景矩形包含于原1280×720世界，最大放大倍率也用于宿主
缓存密度计算；不需要补画边缘或生成新内容。没有用两张静帧交叉淡化冒充镜头动画。

## 复现与证据

在仓库根目录执行，输出目录必须是新目录：

```powershell
python video_workbench/examples/spatial-foundation-study/render_preview.py --out D:/codex-work/operations/video-spatial-foundation-20261126/preview/replay --only all --publish-artifacts
```

依赖路径通过`--chrome`、`--playwright-package`和`--hyperframes-dist`可覆盖；Node、
FFmpeg、ffprobe在PATH中。Pillow用于结果拼图；Windows默认字体为微软雅黑。
执行使用仓库既有 `tests/video_workbench/scene_space_browser_probe.mjs`，调用真实
`scene_space_host.mjs`、SceneSpace compositor、HyperFrames定位、截图与FFmpeg。
这是本地原生host验证，不是已部署新容器/生产任务；绑定中沿用的历史容器摘要仅作
本地请求能力身份，不代表新代码已在该镜像中。

`--only stills`输出第120帧相同取景/相同时间的35版、全开、分别关闭一种引导的对照，
以及四张独立诊断。`--only film`导出240帧真实逐帧样片。每个case均执行重复、逆序、
重载检查，记录实际对象绘制次数、缓存命中和geometry/surface helper调用。

`evidence.json`记录实际验证范围与结果。没有调用生产模型、没有运行独立模型视觉
审阅（`model_review=not_run`），没有修改生产默认、部署、效果采用或WikiSkill发布。
艺术认可待用户判断；技术复现、完整解码和帧数检查不代表画面已达到艺术目标。

## 仍然存在的画面限制

本轮为通用机制进入既有内容的最小样片，并未重新创作山水。35版原有的削木状笔簇、
峰顶露纸、近岸颗粒与亮肩阶面仍可能可见。没有新增完整自阴影传输、HDR环境反射、
物理景深或运动模糊；不能把这十秒微推进称为完整三维环绕或五分钟影片验收。

原`scene.js`内的笔路MIT声明、Lingdong Huang的`stroke()`思路来源和准入绘画库许可
均保留。原始素材与许可随冻结资源闭包加载。
