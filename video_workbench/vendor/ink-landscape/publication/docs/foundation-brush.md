# 可编辑基础笔法文档

首批研究提按、中锋／侧锋、枯润／飞白。`createBrushDocument`把原始笔路、可组合动作、局部控制、生成器标识与生成结果一起保存，供后续修改和重放。数据与接触几何使用纯 TypeScript，Canvas 2D只负责绘制。

样片入口为[基础笔法第一批](../examples/foundation-study.html)和[起收转折第二批](../examples/gesture-study.html)。首批当前样片已获用户视觉认可，范围和局限见[首批记录](foundation-review.md)；第二批仍单独记录工程检查和艺术判断。已确认的发展方向和验收条件见[开发方案](foundation-plan.md)。

## 创建、修改与保存

```ts
import {
  createBrushDocument, reviseBrushDocument, compileBrushRecipe,
  createInkScene, renderCanvas,
} from 'ink-landscape';

const document = createBrushDocument([
  { x: 50, y: 140, pressure: .25 },
  { x: 160, y: 80, pressure: .9 },
  { x: 300, y: 135, pressure: .8 },
  { x: 500, y: 100, pressure: .15 },
], {
  id: 'editable-stroke', seed: 261119, width: 32,
  axis: 'center', dryness: .15, opacity: .9,
  smoothing: 'catmull-rom', start: 0, duration: 2,
  controls: [
    { at: 0, pressure: .4, side: 0, dryness: .15 },
    { at: .55, pressure: 1, side: .65 },
    { at: 1, pressure: .25, side: 1, dryness: .85 },
  ],
});

// 修改技法设定并重新生成；原文档保持不变。
const revised = reviseBrushDocument(document, {
  options: { width: 36 },
});
const saved = JSON.stringify(revised);
const restored = JSON.parse(saved);
const mark = compileBrushRecipe(restored.recipe);

const scene = createInkScene({ width: 560, height: 240, seed: 261119 });
scene.stroke(mark.points, mark);
renderCanvas(context, scene.snapshot(), { time: 2 });
```

创建、编译和修改可以在Node中执行，均拥有独立数据并深冻结；`renderCanvas`需要调用者提供Canvas 2D上下文。`reviseBrushDocument`对`options`作浅合并，传入新的`controls`数组会整体替换原数组。只改一个属性时，可用`document.recipe.options.controls.map(...)`构造保留其余控制的新数组。

同一场景的笔迹ID必须唯一。`createBrushDocument`省略`id`时使用确定的默认ID，多笔创作应逐笔提供不同ID，或插入builder时让其分配新ID。

## 文档和接口

| 接口或数据 | 作用 |
| --- | --- |
| `createBrushDocument(points, options?)` | 从调用者原始笔路创建`BrushDocument`。 |
| `compileBrushRecipe(recipe)` | 仅依据编辑源重新生成`StrokeMark`，不反推保存的结果。 |
| `reviseBrushDocument(document, { points?, options? })` | 从原编辑源应用修改，返回新文档。 |
| `sampleBrushContact(mark, { spacing? })` | 采样纯接触几何，供可视化、检查或其他后端使用。 |
| `document.recipe` | `version: 1`、与动作模型对应的`generator`、原始`points`和规范化`options`。 |
| `document.result` | 已生成的普通`StrokeMark`，包括可序列化的`brush`局部接触控制。 |

文档自身也有`version: 1`。原始笔路保持在`recipe.points`，起收手势等几何处理后的路径在`result.points`；二者各有作用。编辑技法时修改`recipe`再编译，重放已有结果时使用保存的`result`。

创建文档会把默认种子、ID、墨色、宽度和动作等写入规范化设定；稀疏控制仍按调用者给定的通道保存。生成结果的接触控制会补齐首尾默认值。编译拒绝未知recipe版本和生成器标识；修改接口还校验文档版本。调用者从外部JSON恢复时，应先验证文档结构，再编译recipe；需要检查文件一致性时，将生成结果与保存的`result`比较。试验台载入功能完成这项核对。

## 起收与转折的动作模型

`gestureModel`可选`'legacy'`或`'local-v1'`。省略时沿用`legacy`，对应生成器`ink-brush-contact-v1`，保持首批与已有文档的解释。首批样片没有自动迁移到新模型。

第二批显式选用`local-v1`，对应生成器`ink-brush-local-gestures-v1`。它在起收和换向附近组织局部路径：`turn: 'round'`只用局部二次贝塞尔段处理转角，保留其余直行段；`turn: 'angular'`保留折点并组织局部提按。离散化后的结果使用`linear`，不再二次进行全局Catmull-Rom插值。该分支在满强度`strength: 1`时，`entry: 'exposed'`从零压力进入，`finish: 'open'`在收端减至零压力，`finish: 'return'`短回行后减至零压力；中间强度逐渐混合这些动作，`strength: 0`不施加动作。`turn: 'preserve'`且作者要求Catmull-Rom时，先采样作者曲线并保留源零压力节点，再输出`linear`结果。

```ts
const gesture = createBrushDocument(points, {
  id: 'local-gesture', seed: 261120, width: 32,
  gestureModel: 'local-v1',
  entry: 'concealed', finish: 'open', turn: 'round',
});
```

模型名标识程序几何语义，不表示真实笔毫已经复原。编译时生成器与模型必须匹配；不能只替换保存的生成器字符串来迁移旧文档。需要尝试新模型时，从原始编辑源显式修改模型并重新生成，保留旧结果用于对照。

## 局部控制的含义

`controls`按`at`严格递增，禁止重复位置。生成结果最多128个控制，包含自动补入的`at: 0`和`at: 1`端点；只给内部控制时最多126个。每个控制可以只写需要改变的通道，各通道独立进行缓动插值。

`at`表示**生成后笔路的归一化弧长**：0为起点，1为终点。它包含起收等手势增加的路程，使用平滑时也计入曲线弧长；它不表示源点编号、播放秒数或运行速度。修改笔路、平滑或起收手势后，相同`at`可能落在新的空间位置。

| 通道 | 范围与默认值 | 程序作用 |
| --- | --- | --- |
| `pressure` | 0–1，默认1 | 乘以生成路径已有的压力。保留调用者原有提按及零压力间断；同时设置`pressureProfile`时也与其结果相乘。 |
| `side` | 0–1；`axis: 'center'`默认0，`axis: 'side'`默认1 | 改变锋尖相对接触区的偏置及接触展开，用于中央接触与偏侧接触的连续转换。 |
| `angle` | 有限弧度值，默认`Math.PI / 2` | 相对局部切线的展开方向，影响偏侧接触展开与偏置；不是笔杆的实测姿态，也不是画布固定方向。 |
| `dryness` | 0–1，默认整笔`dryness`，省略时为0.25 | 改变连续沉积与束状断续，形成枯润和露纸的视觉近似。 |
| `inkLoad` | 0–1，默认1 | 改变接触覆盖；0时相应位置不沉积。它不是物理含墨量、含水量或储墨耗减模型。 |

未提供某通道的内部位置不重置该通道。端点未显式设置的值使用整笔默认值，所以只在中途设置一个值时，该通道会在末段返回端点默认值；希望保持到笔尾，应在`at: 1`显式给值。角度按连续数值插值，跨越正负π时由调用者给出期望的连续角度序列。

新接口接受宽度、墨色、透明度、裁切、种子、平滑、绝对时间，以及原有可组合几何手势。`nib`、`tipAngle`、`brush`不属于`BrushProgramOptions`，传入会报错；接触关系通过`axis`和局部控制表达。旧接口`createBrushStroke`及普通`builder.stroke`继续保留原来的笔头参数与行为，详见[旧版几何手势接口](brushwork.md)。

## 接触几何与材质边界

`sampleBrushContact`返回锋尖轨迹`x/y`、接触中心`centerX/centerY`、单位法线、`halfWidth`、左右边界和局部状态。压力改变接触展开；偏侧状态让接触区相对锋尖偏移。转向处在有限接触范围内调整朝向，同时保留作者的锋尖路径。关键控制锚点会加入采样，避免短促的提起或状态变化被等距采样漏掉。

这是一套有明确输入关系的程序接触模型。中锋／侧锋的传统范围还要结合真实笔迹判断，接触轮廓本身不能证明笔毫运动已被还原。现有起收、转折等几何手势可组合使用，但这批三组样片也不替它们完成艺术验收。

Canvas渲染器沿接触几何生成连续沉积和断续墨束，空白随笔路和局部状态组织。`dryness`与`inkLoad`共同影响覆盖，墨色及`opacity`仍单独设置。层间叠加只表示既定的合成行为，不据此命名为传统积墨或破墨。

## 缓存、时间和版本

新渲染路径使用`WeakMap`缓存**沉积几何计划**。只有mark、路径及路径点、`brush`及其控制数组和控制都被冻结时才复用计划；可变JSON每次重新准备，防止原地修改后继续显示旧缓存。相同冻结结果在不同绝对时间重放时共享完整计划，再截取到相应路径进度。

该缓存保存可绘制的墨带数据，不是纹理图集、像素截图或GPU资源。每次绘制仍会执行Canvas路径填充，因此不能由存在缓存推断任意笔数都能实时播放。性能结果应同时记录尺寸、后端、上下文、是否预热、生成与绘制范围。

`start`、`duration`和渲染`time`使用绝对秒数，控制路径显露；控制位置`at`使用弧长。它们尚不表达实际笔速、原地驻笔、扩散或干燥过程。

生成器标识和数据版本界定了数据解释方式；像素重放还需要固定库源码／构建版本、场景、种子、尺寸和同一浏览器后端。仅保存`ink-brush-contact-v1`或`ink-brush-local-gestures-v1`等生成器标识不能代替保存实际使用的代码版本。跨浏览器、跨后端或更换材质实现不承诺逐像素一致。

## 评审与后续消费

首批试验台提供三组同题样片、直线／弧线／急转换形、锋尖与接触中心叠线、局部控制、两笔组合、PNG／JSON导出和JSON载入核对。该批当前样片已经用户认可，但工程检查、用户对样片的判断和传统技法的适用范围分别记录，测试通过不会自动扩大认可范围。

来源、观察标准和已知问题见[开发方案](foundation-plan.md)、[首批观察与评判记录](foundation-review.md)、[第二批观察标准](gesture-review.md)及[100项学习总册](learning/README.md)。按用户最新指示先以每批3–5组继续实现和展示，计划内各批完成后由用户安排其他Agent查看；真正Agent的调用、按技法含义修改、保存和重放闭环保留为后续任务，需另存实际执行证据。
