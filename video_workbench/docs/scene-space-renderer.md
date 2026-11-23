# 场景空间渲染适配

阶段 2 新增独立的 `SceneSpaceRenderer` 和 `scene_space_host.mjs`。图片、视频学习和
效果渲染在收到独立 `scene_space` 计划时选择新路线；没有计划的旧调用继续使用原宿主。
历史操作只查其原输入和宿主字节，不将旧任务改为新场景。新宿主、公共合成器和
计划 sidecar 分别以 SHA-256 固定，source 仍保持原有文件与 strategy 合同。

## 绘画与镜头分工

Canvas 路线的 source 注册绘画回调，并提供填满输出画幅的 Canvas：

```html
<canvas data-scene-space-output></canvas>
<script src="scene.js"></script>
```

```javascript
window.__sceneSpaceArtwork = {
  paintObject(ctx, object, world) {
    // 在 object.bounds 的局部坐标中绘画；world.seed 可供固定随机种子。
    // 宿主负责父子变换、镜头、视差、图层透明度及局部遮罩。
  }
};
```

回调同步运行，每个世界中的每个对象只画一次。初始化前，宿主加载最多 64 个声明的
FontFace，避免缓存首帧文字的回退字体。宿主根据全片最大投影倍率准备有界
对象位图，镜头平移、推拉及局部 x/y、旋转、缩放和透明度轨道复用这些像素。
初版运行准入仅支持 `cache_policy: static`；`dynamic` 和 `none` 会在昂贵绘画前拒绝。
公共世界仍可以描述其他策略，不能用描述本身扩大本宿主的运行能力。

### 实体覆盖与局部体面工具（绘画 API 第 2 版）

新的冻结能力包含 `artwork_api_revision: 2`、体面 helper 和其 canonical 预算模块的
独立 SHA。世界、计划及方法的 v1 字段没有增加；对象轮廓、山脊、谷线及笔墨参数
仍保存在作者源代码的对象局部数据表中。第四参数 `tools.surface` 提供版本固定的
局部 2.5D 工具，具体 API 见 `scene_space_surface.mjs`。它们表达作者生成的体面假设，
不是从来源单图观测到的标定三维几何或物理光照。

可选的 `paintCoverage(maskCtx, object, world, tools)` 在同一对象色稿画好后调用一次。
未提供回调，或对该对象返回 `null`/`undefined`/`false` 时，保留原色稿 alpha 行为。
需要实体覆盖时，在 `maskCtx` 画局部轮廓的 alpha，并返回严格的
`{substrate: [r, g, b]}`（三个 0..255 整数）。宿主先在色稿下补该不透明底色，再用
轮廓 alpha 裁切一次。浅墨亮面由此也能挡住后景，轮廓外仍露出后景，底色不会作为
整个矩形盖住画面。墨色浓淡应由对象内部色调/颜料控制；对象或图层整体透明度仍按
原计划应用一次，局部图层 mask 和有意的云雾半透明边缘也保持有效。

```javascript
window.__sceneSpaceArtwork = {
  paintObject(ctx, object, world, tools) {
    // 工具在 tools.surface；只画当前对象，保持 Canvas 状态平衡。
  },
  paintCoverage(ctx, object, world, tools) {
    // 可只对实体山/石画实际轮廓，其他对象返回 null。
    // 蒙版颜色不代表墨色，只有 alpha 表示覆盖。
    return null;
  }
};
```

覆盖回调必须同步且不能改变缓存尺寸。宿主只建立一个临时蒙版 Canvas，逐对象重置，
初始化后释放像素；预检保守预留最大单对象蒙版、一个额外原生上下文，以及 helper
最多 16 MiB 累计保留数值缓冲与 2 MiB 单次数值工作空间。原色稿像素缓存额度保持
独立。helper 每个页面 realm 最多 64 次表面场预留，累计数值字节最多 16 MiB；不同
helper URL fragment 共享固定的 canonical 预算模块，失败预留不退款，没有 reset/
raise API。普通作者 JS、浏览器 allocator 和 Canvas native 开销仍受既有容器总内存
及执行时限约束；这些数值额度不充当实测 RSS。

预演记录 `coverage_callbacks`、`coverage_objects`、临时蒙版峰值像素、helper 调用
计数和数值缓冲预留。它们证明执行及资源边界，不等同轮廓、体面或水墨艺术质量通过。
旧操作仍使用自身保存的宿主、请求及证据做只读恢复，不补写新 helper 计量。

渲染输入将源码身份、冻结计划、全片对象分辨率、最大缓存像素、临时表面、执行帧
范围和宿主/runtime 摘要共同固定。不同执行区间引用同一份全片缓存分辨率与绝对帧，
不会因截取局部区间改变对象细节。绘画超出对象局部 bounds 的部分会被裁掉，预期
溢出的笔画应先纳入对象 bounds，再由公共空间校验处理有意越界。

## 合成、资源与恢复

宿主按图层和对象顺序合成，区域 mask 只约束自己的图层。它不在最终画布上擦透明洞。
模糊层的工作表面包含显式边缘余量；图层透明度只应用一次，父对象透明度向子对象继承。
所有 seek 从绝对帧重新求值，包含重复、逆序和重新加载的真实像素摘要比较。

`prepare_scene_render` 和 `validate_request` 在执行前验证几何、覆盖、对象像素数、
原生上下文数和临时表面估算。它们不执行源码。新操作先写入单独的持久账本，响应
丢失只进入未知状态；查阅与恢复不会启动第二个容器。真实执行沿用现有无网络、只读
挂载、非特权 UID、资源上限和所有权校验的容器清理流程。

成功结果检查原源码/资产/宿主/sidecar、原生 Canvas、真实 HyperFrames seek、图片
尺寸或 H.264 帧数、fps、时长、全解码结果和采样帧摘要。结果仍为静态或抽帧证据，
不会获得连续运动、音频、全片观看或艺术通过的声明。新宿主记录单调时钟的
`measured_render_seconds`，其范围为容器内宿主进程，不包括容器准备；外层完整耗时
由服务执行账本负责。未知测量保持未知。

可选 ink-landscape 使用新宿主固定的当前 descriptor、代码和 MIT 许可摘要。仅源码
实际引用该模块时附加资产，并记录真实加载脚本和 V8 函数调用；加载或函数执行仍不
等于水墨质量通过。旧宿主保留原依赖 pin。

`ink.renderCanvas` 会重设其内部 Canvas 变换：先恢复单位变换，再按当前 Canvas 与
InkScene 的尺寸比例缩放，最后恢复调用前的上下文。直接画入对象缓存时，对象
`bounds.x/y` 应为 `0`，InkScene 文档宽高取对象 bounds 宽高，笔组点坐标也从该零
原点表达；世界位置写在对象 `transform`，由宿主在合成阶段施加。例如：

```javascript
const b = object.bounds; // {x: 0, y: 0, width, height}
const doc = ink.createStrokeGroupDocument(groups, {
  width: b.width, height: b.height, seed: world.seed
});
ink.renderCanvas(ctx, doc.result, {clear: false, paperTexture: 0});
```

非零局部 bounds 不能假设库会保留宿主预设的平移。需要时，在当前准入允许的局部
Canvas 中先画好，再在调用方上下文用 `drawImage` 放回声明的 bounds，并计入额外
上下文和像素预算。`clear:false` 保留透明对象背景；默认 `clear:true` 还会画文档纸色。
验收中的真实墨笔案例使用零原点对象，在推进后的第 95 帧检查两笔产生的深色像素
位置和投影范围；这证明该用法能实际绘制，不代表所有非零 bounds 用法或艺术质量通过。

## SVG 与 Three.js 边界

合成器导出 `applySvgFrame` 与 `applyThreeFrame`，共用对象身份和绝对帧状态。它们
支持平铺对象和声明的原生父子分组，避免嵌套对象被重复施加世界变换。局部 mask、
模糊和 Three.js 的材质透明度需要后端提供对应回调，缺失时明确拒绝。

这些矩阵适配器不授予 HTML/SVG 或 Three.js 新宿主执行准入，也不重建未知三维
结构。首版新宿主能力只列出当前已准入的 Canvas；旧三种绘制引擎路线继续保留。

## 原生浏览器验收

`test_scene_space_renderer.py` 包含纯合同/预算/恢复测试及可选真实浏览器测试。设置
`SCENE_SPACE_BROWSER`、`SCENE_SPACE_PLAYWRIGHT_PACKAGE`、
`SCENE_SPACE_HYPERFRAMES_DIST` 后，真实 Chrome、Playwright、固定 HyperFrames 与
FFmpeg 执行同一份宿主捕获函数，验证 96 帧、多镜头缓存、静态图、局部 mask，以及
当前 ink 模块加载和 V8 调用。浏览器验收不代替容器镜像准入、部署或用户艺术评价。
提供 `SCENE_SPACE_TEST_FONT` 时还会验证声明字体在第一次对象缓存绘画前已经加载。

2026-11-22 后续容器检查发现，部分 Chrome 在页面重载后保留旧页的 coverage 条目，
但其 Debugger script ID 已失效。新宿主改为在旧文档仍存活时收集并核验精确库源码，
再在重载后收集当前文档，按函数区间累加计数；缺失源码或字节不符仍明确失败。
针对脚本 ID 退休、字节篡改、未知脚本及零调用的四项 Node 回归通过，渲染器专项
22 项通过，包含六个真实浏览器案例。新宿主摘要及像素证据见
`evidence/scene-space-native-browser-cdp-fix-20261122.json`；原文件保留原提交的历史证据。
此本地复验不替代 ARM 容器复验与发布流程记录。

2026-11-23 的独立实体覆盖与局部体面扩展复验，渲染器专项 28 项通过，包含
14 个真实浏览器场景（11 个出图/视频场景及 3 个预期拒绝）。在相同浅墨与后景上，
实测实体内部能遮住后景，轮廓外保留后景，渐变覆盖和图层/对象透明度各应用一次；
异步覆盖、无效选项和改写缓存尺寸均在输出前拒绝。局部山脊/山谷场的连续整行
wash 实测明暗范围为 130，所测网格节点两侧 RGB 最大跳变为 2，原六个浏览器
场景的采样像素摘要保持一致。源码摘要、实际像素和各场景记录见
`evidence/scene-space-coverage-native-20261123.json`。这些记录验证此组样例的覆盖、
连续明暗及重复求值，不代替山水画艺术评价或其他网格、镜头、光照配置的检验。
