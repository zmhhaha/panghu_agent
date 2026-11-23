# 共享三维网格与绘画投影引导

2026-11-23，绘画 API 第3版增加可选的 `tools.geometry`。它让同一共同基座、主脊、
支脉、鞍部、坡肩与前脚由 XYZ 顶点和三角连接表达，再把固定视向投影交给 Canvas
作者组织笔墨。本模块是几何辅助，不是新的 Three.js/WebGL 像素后端。

世界中的 `ordinal/geometric/unknown` 深度标签仍是其原语义，世界矩阵、锚点与布局
仍为二维。`tools.surface` 是对象局部2.5D体面；它也不会自动把独立山峰连成一块。
实际三维网格、投影相机和笔墨参数保存在 `scene.js`，不扩充严格世界、计划、方法、
source.strategy 或作者源文件集合。只有任务冻结能力提供 API3 及新工具字节时才用
本工具；旧冻结任务不能在恢复中静默升级。

## 作者流程

先组织共同结构，再以同一网格完成可见性投影，最后按岩面分区落墨。峰尖是共同
山体的局部高点；给各尖锥单独加Z值，或为每个峰分别建立深度缓冲，都不建立共同
山腰，也不能测试峰之间的真正遮挡。`mesh.connected_components` 可帮助检查已引用
顶点的连接分量；它不证明封闭流形、真实体积或山势好看。

正交投影提供覆盖、可见深度、可见面身份、XYZ位置、法线与投影下坡方向。作者可
据此安排斜劈、横折、长皴、局部积墨与留白，不要求把法线转换成连续灰阶底稿。
几何、覆盖、笔墨与云雾分别表达；粗模/深度图正确不能替代实际成画检查。

网格与投影在每个页面实例准备一次，绘画与覆盖回调共用其句柄，随后复用宿主静态
缓存。此相机不改变计划的二维取景/视差协议，也不自动授予逐帧重投影、环绕观看或
背面生成。作者生成的三维数据是空间假设，单图仍是单图证据，抽帧仍保持原观察范围。

## API 与坐标

第四参 `tools.geometry` 是冻结的工具集：

- `createMesh({vertices:[[x,y,z],...],triangles:[[a,b,c],...]})` 创建有界索引三角网。
  顶点必须有限、面不能退化，法线按三角顶点绕序定义，不自动朝相机翻转。
- `createTerrainMesh({bounds:{x,z,width,depth},grid:{columns,rows},heights:[...]})`
  以按行排列的Y高度生成相连的XZ高程表面。该表示不提供悬挑或封闭体积重建；通用
  三角网入口可表达不同表面。
- `projectMesh(mesh,{width,height,camera:{position,target,up?,viewHeight,near?,far?}})`
  生成固定视向正交深度缓冲。相机向量都是XYZ三分量；每个网格顶点必须严格位于
  near/far之间，本版不隐式裁剪跨近裁面的三角形。
- `sampleProjection(projection,x,y,{bounds?})` 返回 `covered`、`coverage`、`depth`、
  `position`、`normal`、`flow`、`flow_valid`、`triangle`。深度越小越近；法线位于作者
  网格XYZ坐标空间。`flow` 是世界向下 `[0,-1,0]` 在面切平面上的分量，经相机投到
  XY再归一化；水平面或投影方向退化时为 `[0,0]`，且 `flow_valid=false`。
- `projectPoint(projection,[x,y,z],{bounds?,depthTolerance?})` 返回 `x,y,depth,in_view,
  visible,triangle`，便于投影作者脊谷及检查它是否被同一网格挡住。几何可见性不等于
  在来源图像中实际观察。默认深度容差按投影像素尺度设置，可显式提供非负容差。
- `paintCoverage(ctx,projection,{bounds?})` 以alpha扫描行画覆盖，保留原局部变换、
  clip与alpha，不创建Canvas；`bounds` 是对象局部矩形映射，不是任意相机矩阵。

无命中时 `covered=false,coverage=0`，深度/位置/法线/面身份为null，方向为零且无效。
投影depth是相机距离，越小越近；不要直接把它写成世界层级中“越大越近”的深度值。
作者应处理无效方向，不把零向量归一化。覆盖可与既有 `paintCoverage` 回调结合，让
淡墨实体继续遮住远景；局部云边由作者另行安排，不在最终合成画布上挖洞。

## 限额与固定身份

网格最多16,384个顶点、32,768个三角形。投影每边最多1,024像素、总262,144像素；
光栅三角bbox累计像素工作最多33,554,432，先检查再分配/遍历投影缓冲。每页canonical
预算累计最多32个网格/投影句柄、16 MiB数值存储，另保守预留1 MiB数值工作空间。
分配前扣额，不因失败或GC退款；没有reset、raise或释放后再用的额度接口，fragment
导入共用预算。这些数值不等于作者JS堆、Canvas/native分配或浏览器RSS已被实测覆盖，
容器原有资源上限继续生效。
各上限须同时满足，像素上限不是越过累计存储额度的分配授权。

capability、binding、request、输入挂载与receipt固定 geometry helper、canonical预算
及API3身份；运行观测分别记录调用和数值预留。旧API1/2操作按自己保存的字段、宿主
和字节只读恢复，不能补写新工具证据。未调用新工具的旧画稿保持其原像素行为。

图片/抽帧视频学习、效果和正式绘画共用指导。换内容只接收一般原则，不自动继承
参考网格、布局和活对象；效果采用与WikiSkill发布仍为显式操作。本轮先验证固定
视角样例及其结构诊断/实际笔墨，不据此宣称完整三维电影、部署或用户艺术通过。

可运行作者样例见 [共同山体研究](../examples/shared-mountain-study/README.md)，桌面原生
浏览器证据见该例 `evidence.json`；底层遮挡与非整数缩放验证见
[运行时证据](evidence/scene-space-shared-geometry-native-20261123.json)。helper 数值额度
和作者额外保存的数组分别记账，不能把前者当作整个页面内存实测。
