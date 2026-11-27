# Three 通用外观适配与冻结接线

对应 OpenSpec `improve-video-spatial-rendering-foundation` 的 4.1。本项实现
Three 后端的第一组具体外观适配，保留 Canvas SceneSpace 的独立实现，未将 SVG、GPU、
景深、运动模糊或艺术质量推定为已验证。本文及本地执行不构成生产镜像或角色准入。

## 具体实现

`motion_renderer/spatial_three_adapter.mjs` 将现有共同语义落实为 Three 顶点 RGB：

- `createThreeAppearanceAdapter({THREE,mesh,seed,light,material?,baseColor?,atmosphere?,limits?})`
  缓存固定物体局部坐标的材质分布，并拥有独立的 geometry/material 克隆。
- `light` 为 `{direction:[x,y,z],response:'physical'|'artistic',ambient?,lightRange?,inkRange?}`。
  `update({camera,light?})` 每次按真实 `matrixWorld` 的逆转置变换法线，保持世界光向；
  相机运动不会自动旋转光源。物体的旋转和非均匀缩放会重新计算受光。
- 材质只在初始化时使用固定 seed 与局部位置采样。逐顶点 RGB 和可选法线扰动是有界指导，
  不等于像素级材质、完整 PBR 或投影阴影；返回的 roughness 未被应用到 BRDF。
- 空气使用真实相机世界距离及顶点世界 Y 混合 RGB，不修改 opacity、transparent、
  depthTest 或 depthWrite。输入 sRGB 字节颜色先转为线性 RGB。
- `transparent=false` 的材质即便 opacity 小于 1 仍按不透明实体处理，必须保留深度测试和写入。
  显式透明可按声明透过；每次更新重新检查这些属性。适配器不会把淡色或雾当透明覆盖。
- `dispose()` 恢复原始共享 geometry/material，释放自有 Three 资源，重复调用不重复释放。

首版只接受单个非 skinned、非 instanced、无 morph 的 BufferGeometry Mesh，具有 position/normal
及单一无 map/alphaMap 的 MeshBasicMaterial。初始化后修改几何版本会被拒绝，不能把固定材质
缓存冒充形变后的正确重采样。原有顶点色被独立缓存，不读取后来对共享源对象的修改。

## 预算与实际观测

单实例最多 16384 顶点、98304 索引、4 MiB 已核算数值缓冲区及 16 Mi 次累计顶点更新。
4 MiB 包含源输入、克隆全部数组、替换颜色及材质/原顶点色缓存，不假设被替换数组已被 GC。
它不声称覆盖 Three/浏览器的完整 JS 或原生堆。

canonical 模块每 realm 限 64 次实例预留、262144 顶点和 16 Mi 次顶点更新；dispose 不退款，
query/fragment 导入代理到同一 canonical 模块，不能得到新额度或漏记副本。材质计算复用
`scene_space_surface_budget.mjs` 的不可改宿主策略；缺少策略时拒绝运行。当前正式描述符策略
允许最多 3000000 次材质噪声计算，scope 为宿主 realm，既非模型 token 预算，也非整片 CPU 上限。

`adapter.observation()` 与只读 `threeAppearanceObservation()` 分别报告实例及 realm 实际调用、
预留、材质噪声/角点/导数、光照、空气、更新与释放记录。函数调用不证明原生绘制贡献或艺术质量；
原生 WebGL2/SwiftShader、实际图像与持续播放分别保留证据。

## 当前准入与历史恢复分开核对

新的 `video.animation-runtime-capabilities.v4` 用 `spatial_libraries` 绑定四项资产：

1. `spatial_three_adapter.mjs`
2. `scene_space_surface.mjs`
3. `scene_space_surface_budget.mjs`
4. 由 `spatial_three_library.read_assets()` 生成的 `spatial-three-library.json`

JSON 保存三项代码摘要和原宿主策略，不含自己的摘要；完整描述符再绑定四项摘要，避免自循环。
当前发行核对安装字节与当前完整描述符；只有完整可信声明才会给 Three 路线增加 lighting、texture、
atmosphere，不会仅凭引擎名或请求给出能力。v1–v3 字段和原来不带新资产的项目保持原协议。

新作者输入取得四资产与对应指导。浏览器编译器先验证原项目清单，再从项目 JSON 核对其原代码
摘要和策略；realm 在作者 import 前安装不可改策略，提前加载 canonical 模块，并由宿主读真实计数。
作者漏报部分资产或修改原 JSON/代码都会被拒绝。JSON 已编入冻结 realm 配置，无须浏览器再 fetch。

恢复的 `_check_spatial_binding` 使用原能力快照的 `frozen=True` 路径；旧项目重编译只读原项目
资产、原策略和原宿主字节，不读取今天的 descriptor/read_assets/material_policy。因此升级当前代码
不会重新选择旧任务的能力、预算或内容。新任务仍核对当前发行，不能借 frozen 路径授予新准入。

## 已执行验证及限制

本轮 Python 按 classname/name 去重 169 项通过，Node 适配器 14 项、原宿主生命周期/通道/guard
49 项通过，无遗留失败或 skip。原新测试的一个 worker 夹具误读当前资产，改为预先冻结清单后定向
通过；另六个旧 HTTP 夹具错误已用精确 HEAD app 独立复现 403 origin_rejected，再只补测试的 HTTPS、
Origin 和 issuer，八项定向通过。原失败报告保留，没有修改生产来源校验或反复宣称全套重跑。

`examples/three-spatial-foundation-study/` 使用已有共同山体函数、网格连接、布局、seed 631907
和预定相机，实际输出 1280×720、24fps、240 帧/10 秒无声 H.264 并完整解码。真实像素诊断覆盖
浅色实体覆盖、显式透明、雾与 alpha 分离、统一光向、非均匀法线变换、固定物体材质及重复/逆序/重载。
它保留了受控差异，未将逐顶点明暗当成原毛笔画法的艺术等价物。

另用 `examples/build_three_spatial_film_probe.py` 将相同山体移入真实正式模块生命周期，四资产、
真实 Hyperframes core、opaque realm、CSP 与 SwiftShader 完成 0/120/239/120/0 帧取景，重复、逆序及
重新构建输出一致，前后取景确实改变图像，全部 iframe 清理完成。每个实际新 realm 有 10330 次材质、
光照和空气顶点计算、30990 次材质噪声，宿主读取的策略为原 metadata 中的 3000000 上限。
该正式宿主本地探针不是用户的正式生产 run，也不补签 6.1 的评审/导出验收。

可追溯记录见 `docs/evidence/three-spatial-foundation-20261127.json` 及样例内的
`formal-realm-evidence.json`、`formal-realm-observation.json`。图片/视频学习和效果库不会因为这个
正式 Three 描述符自动获得新资产；其各自运行准入及能力快照仍需单独验证。WikiSkill 发布、
换内容验证、连续观感和用户艺术认可均不是本项的完成结论。
