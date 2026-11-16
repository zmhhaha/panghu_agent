# HyperFrames 整片模块生命周期宿主

2026-11-14，承接 [整片工程包与音频时钟合同](hyperframes-film-project-contract-20261114.md)。新增纯 ESM `motion_renderer/hyperframes_film.mjs`，当前提供可测试的模块执行调度与 core 时钟接线，不提供根页面、实际资源加载、画面捕获或生产准入。没有修改旧 creative／engine guard、Agent profile、业务编排或部署。

## 生命周期执行

`createHyperFramesFilmHost` 接收：

```javascript
const host = createHyperFramesFilmHost({
  verifiedManifest,       // 由独立 Python 工程包校验器验证后的确切副本
  manifestSha256,        // 上层快照冻结的外部可信根身份
  trustedLoadModule,     // async (moduleMetadata, {fresh_instance: true}) => exports
  createRoot,            // async (moduleMetadata, frozenContext) => confinedRoot
  removeRoot,            // async (root, moduleMetadata) => void
});
await host.renderFrame(globalFrame);
await host.dispose();
```

本模块只复核与调度直接相关的 schema、协议、场景、模块身份引用、转场窗口和激活区间，不重新检查源码／WAV／资产字节，也不计算 manifest SHA。`verifiedManifest` 的名称表达调用方责任，不是本模块授予的可信性。调用前必须完成 `verify_film_project`，后续真实 loader 仍须从同一确切包解析已验证的源码字节。不能向此接口传入“当前项目最新模块”。

宿主复制并递归冻结输入。每次定位，不论正向、反向还是重复相同帧，都先等待旧参与者反序 `dispose()` 和 `removeRoot`，再为当前全局帧重新加载实例、创建独立 root、等待 `mount(root, context)`。所有参与者 mount 完成后，按确定顺序等待每个 `renderAt(context)`，才记录该帧完成。

load、root 工厂、mount、renderAt、dispose、移除 root 全部可以返回 Promise。一个未完成定位期间，另一次定位和关闭会明确拒绝，避免并发覆盖状态。

根节点的默认装载顺序为 film 场景顺序，再是当前边界的转场顺序。宿主不改 Agent 的样式、z-index 或转场绘制内容；该装载顺序是运行约定，不是对最终叠放效果的视觉认可。不同同时活跃模块不得复用同一个 root。

`trustedLoadModule` 必须真正返回适用于本次定位的新模块实例，并由可信加载器约束模块级状态、源码副作用、全局样式和资源释放。`fresh_instance: true` 是明确请求，不是已证明隔离。任意 Agent 源码是否确定性重建，须在后续实际加载和独立观察中验证。旧包仅含 `seek` 时会因缺少 mount／renderAt／dispose 而失败，不会自动桥接成新协议。

## 全局帧与上下文

scene 的 context 包含 `global_frame`、精确有理数 `time`、film SHA、module_id、namespace、role、engine、scene_id、基础 start／end frame、local_frame、phase 与结构化 states；transition 另带已有 boundary_index、关联 scene ID、at_frame 和对象交接声明。上下文均为冻结副本。

同一转场窗口的两侧场景和转场模块使用完全相同的 global_frame。提前入场可收到负 local_frame，延后退场可收到超出基础场景长度的 local_frame；phase 明确为 entering／active／exiting，宿主不会钳制为另一套局部时钟。

画幅与根入口没有被加入第一步工程 manifest。此纯调度器不使用它们；后续真实根页面必须从调用方已验证、冻结的配置取得，接入完整 snapshot 与 runtime 身份后再形成可运行工程。不能用调用者临时填入的尺寸冒充已冻结工程。

## HyperFrames core 时间接线

`connectHyperFramesFilmCore` 使用既有单场景适配器已验证的相同 core 时间原语：

```javascript
const clock = connectHyperFramesFilmCore({
  host,
  eventTarget: window,
  player: capturedTrustedPlayer,
  waitForSeekCompletion: capturedTrustedCompletion,
  forceReseek: capturedTrustedReseek,
});
await clock.seekFrame(globalFrame);
await clock.close();
```

调用方先校验实际 core 字节并捕获可信方法。适配器执行 `renderSeek(frame / 24, {exact: true})`；收到 `hf-seek` 后，在该事件同步回调中用 `detail.waitUntil(promise)` 注册生命周期工作，再等待 core completion 和本地所有操作。core 对相同时间去重时，使用显式 reseek 再等待完成。实际 core 时间、目标帧和最后完成帧必须一致，缺少事件或时间错误都会失败。

这一接口接受注入函数，测试替身也能调用，所以返回 `core_binding=supplied_trusted_core_methods` 不构成真实 core 来源、镜像准入或媒体证据。已有 `hyperframes_project.mjs` 的真正字节加载／页面安装功能没有被删除或放宽；本次没有把新的多模块宿主伪装接进旧单镜 bridge。

## 失败与清理

定位开始即清空当前 completed_frame。任何异步阶段失败都停止宿主，等待已获得实例的反序清理，并抛出原错误；清理同时失败时返回 AggregateError。不会将上一帧仍留在页面上的画面报告成此次成功。

一次清理会尝试所有参与者，成功 dispose 的实例不会因 root 移除失败而重复 dispose。`live_roots` 和 `pending_cleanup_modules` 分别暴露未移除根与未完成清理实例；明确的后续 dispose 可重试尚未完成部分。core 接线失败会移除事件监听、等待已有操作并清理宿主；再次 seek 会拒绝。

未返回的第三方 Promise 可能使此纯模块持续等待。本实现不把 Promise.race 超时当成已终止执行或已回收资源；未来隔离浏览器／Job 必须提供硬截止、取消与进程终止，并独立记录清理证据。

## 验证范围与未完成项

`tests/video_workbench/hyperframes-film.test.mjs` 使用调度 fixture、异步生命周期和 core/event doubles，验证：多镜窗口、统一帧、全部 mount 后 render、正向／反向／乱序／重复定位、冻结副本、并发拒绝、异步失败、反序清理、root 隔离、旧协议拒绝、精确 core 事件与重复定位 fallback。

本轮 30 项新增 Node 测试和既有 9 项项目宿主测试通过。另将真实 Python 编译并独立校验的混合录音／无旁白三镜包交给新宿主，以不绘图的生命周期替身进行 114 次完整正向、反向和重复查询，场景／转场激活、local_frame 和 phase 与 Python 离线查询逐项一致，清理后没有待处理实例。这条跨语言检查也没有执行 Agent 原始源码或真实 core。

这些不是实际 HyperFrames、真实 Agent 三引擎、真实受限 loader、DOM/WebGL 观察、音频混音或整片视频捕获的通过证明。第一步工程包中的 `execution.status=not_executed` 始终保持不变；本模块单独返回生命周期调用状态，证据范围明确是 `lifecycle_calls_not_capture_visual_proof_or_runtime_admission`。

H.3 仍需真正的新模块创作／角色准入、可信资源加载与 root 工程、实际 core 多模块加载、整片 snapshot／capture／独立观察、音轨与音效排程、正式编排和完整样片验收。此纯宿主不会使现有逐镜 MP4 拼接自动成为统一整片工程。
