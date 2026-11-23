# 可编辑墨层程序

第四批研究浓淡、积墨和破墨三组。`InkProcessDocument`保存调用者组织的笔组、墨层、浓度、水分参数与绝对落墨时间，并编译成独立于浏览器的`InkProcess`数据。它不预设山水构图，也不从一张贴图反推笔法。样片入口为[墨法第四批](../examples/ink-study.html)，资料和观察范围见[本批记录](ink-review.md)。

## 创建、编辑、保存与绘制

```ts
import {
  createInkProcessDocument, reviseInkProcessDocument,
  compileInkProcessRecipe, rasterizeInkProcess, renderInkProcess,
} from 'ink-landscape';

const document = createInkProcessDocument([
  {
    id: 'pale-ground', concentration: .22, water: .85,
    at: 0, dryAfter: 2, mode: 'glaze',
    strokes: [{
      points: [
        { x: 55, y: 165, pressure: 0 },
        { x: 135, y: 75, pressure: .8 },
        { x: 225, y: 115, pressure: .9 },
        { x: 340, y: 145, pressure: 0 },
      ],
      options: { id: 'ground-stroke', width: 48, dryness: .18, smoothing: 'catmull-rom' },
    }],
  },
  {
    id: 'dark-addition', concentration: .78, water: .65,
    at: .6, dryAfter: 2, mode: 'wet-mix',
    strokes: [{
      points: [
        { x: 120, y: 150, pressure: 0 },
        { x: 175, y: 100, pressure: .85 },
        { x: 240, y: 160, pressure: 0 },
      ],
      options: { id: 'addition-stroke', width: 38, dryness: .3 },
    }],
  },
], { width: 400, height: 240, seed: 261122, background: '#f5f2e9', ink: '#20251f' });

const revised = reviseInkProcessDocument(document, {
  layers: document.recipe.layers.map(layer => layer.id === 'dark-addition'
    ? { ...layer, concentration: .65 }
    : layer),
});
const saved = JSON.stringify(revised);
const restored = JSON.parse(saved);
const process = compileInkProcessRecipe(restored.recipe);

// 在Node中也可得到密度场，不需要DOM或Canvas。
const frame = rasterizeInkProcess(process, { time: 1, cellSize: 2 });
console.log(frame.columns, frame.rows, frame.activeLayers);

// context由浏览器调用者提供。
renderInkProcess(context, process, { time: 1, cellSize: 2 });
```

保存完整文档同时保留编辑源和编译结果；只重放已有墨层时也可以保存`result`。按技法设定修改或核对生成关系时，使用`recipe`重新编译。外部JSON仍需结构校验；可解析文本不自动等于有效文档。临时密度场中的`Float32Array`不是笔路编辑源，不应拿它替代文档的保存与重建。

## 文档与接口

| 接口或字段 | 作用 |
| --- | --- |
| `createInkProcessDocument(layers, options)` | 从显式墨层及场景尺寸创建文档。 |
| `compileInkProcessRecipe(recipe)` | 从编辑源重建`InkProcess`，不读取保存的结果来反推源。 |
| `reviseInkProcessDocument(document, { layers?, options? })` | 返回修改后的新文档；提供`layers`时整体替换层数组，`options`浅合并。 |
| `rasterizeInkProcess(process, { time?, cellSize? })` | 在纯TypeScript中生成指定绝对时间的密度场。 |
| `renderInkProcess(context, process, { time?, cellSize? })` | 将墨层程序绘制到Canvas 2D。 |
| `document.recipe` | 版本`1`、生成器`ink-layer-process-v1`、原始`layers`和公共`options`。 |
| `document.result` | 版本`1`的`InkProcess`：尺寸、种子、纸墨颜色与编译后的层；各层仍有普通`StrokeMark[]`。 |

创建时取得输入的独立副本，文档和编译结果冻结。修订从编辑源重新生成，原文档不变。配方不把公共默认值逐笔抄成作者配置；展开后的笔迹保存在结果层中。未知版本、生成器或不支持的字段应拒绝，不能静默改用另一解释。

## 公共设定与逐层设定

公共`InkProcessOptions`要求正数`width`和`height`，可设置`seed`、`background`和`ink`。默认种子为`1`。纸墨颜色只接受`#RRGGBB`，保存时统一为小写；默认纸色为`#f5f2e9`、墨色为`#20251f`。全程序使用同一种墨色，墨色深浅另由层浓度控制，不在每笔中切换颜料颜色。

| 层字段 | 含义与默认值 |
| --- | --- |
| `id` | 必填的层标识，供调用者选择和修改。 |
| `strokes` | 调用者显式提供的笔组，每笔包含`points`及可选`options`。 |
| `concentration` | 0–1，默认`.5`；有限光学密度模型中的浓度参数，不是实测墨粒比例。 |
| `water` | 0–1，默认`.5`；影响有效湿交区中的交换强度，与墨浓度和笔触枯润分别保存。 |
| `at` | 非负绝对秒数，默认`0`；整层落墨的事件时间。层数组须按时间非递减排列，同刻保留数组顺序。 |
| `dryAfter` | 非负秒数，默认`2`；该层用于判断后续落墨能否触发湿态交互的窗口。 |
| `mode` | 默认`'glaze'`；`'glaze'`作独立密度累积，`'wet-mix'`尝试与仍处于湿态窗口的先层局部交换。 |
| `edgeSoftness` | 可选0..1，在各笔接触内部按局部半径渐隐密度；省略或0保持原接触覆盖。只对显式启用的层生效。 |
| `edgeDeposit` | 可选0..1，整层合成后向断续内缘带重分配覆盖，并保留该层总量；省略或0保持旧结果。 |
| `edgeWidth` | 可选0..1，内缘带占局部接触半径的比例，有效默认.22；0关闭该带。 |

笔组的`options`沿用[基础笔法](foundation-brush.md)的提按、用锋、动作、接触覆盖、枯润与透明度等控制；`start`、`duration`和`ink`由墨层及全程序管理，传入会被拒绝。调用者仍可自行提供笔路和裁切，层组织不自动生成山体、皴纹或随机填充。

本入口中，逐笔`clip`限制初始接触沉积；后续湿交换可以把墨量移出这个区域。它不是最终密度场的硬边界。需要严格限制最终显示范围时，调用者须另行裁切返回的栅格或最终图像；不要仅依赖源笔迹的`clip`。

`concentration`改变沉积的光学密度；笔迹`opacity`影响局部覆盖，`dryness`影响笔触接触断续，`water`影响层间交换条件。这些参数可组合，但不互相代称。把水分降低不能自动叫作浓墨，把透明度调低也不独立构成全部淡墨技法。

`edgeSoftness`用于薄染等需要相互衔接的接触。它在纯数据栅格内向接触边界渐隐，纸纹相关场调整局部渐隐宽度；不模糊最终Canvas图片，不移动源路径，也不扩出原有接触边界。接触边缘的沉积总量会随之减少，作者应独立选择叠加次数、墨量和局部深浅。显式clip仍承担区域限制，不会自动成为柔化的边界；不规则染界需要作者另行提供。

`edgeDeposit`独立于渐隐，按整层接触的内向深度和fBm门控分配边缘权重，再归一保留启用前总覆盖；它改变局部分布，不额外创造墨。相交接触的深内区不会每笔再盖一条轮廓。详见[自然分布与局部积墨](natural-authoring.md)。

## 时间、湿态与栅格边界

`at`是整层到达的离散事件；当前模型没有一层内连续行笔、驻笔积墨或真实纸内流动。`time`只决定哪些层已经落下，省略时包含全部层，有限负值可以查看落墨前空白；它不让画面按墙钟继续变干或演进。同一个绝对时间可以从任意先前查看顺序重新计算。

后层选择`'wet-mix'`时，模型检查先层的落墨时间、干燥窗口、水分与局部接触覆盖，仅在有效湿交区做有限交换。已经超过窗口的先层不因后层名字含“wet”而恢复未干互破。纸纹相关场控制方向导通与不均匀留墨，随后用湿区前沿的梯度引导少量已有颜料外移，在部分前沿段积留。这一阶段称为**有限事件沉积近似**：低频门控与局部笔路方向让浓墨段有断续，未接纳的边缘保持较弱，不额外描一整圈轮廓。

每次转移都从原单元扣除并等量加到相邻有效湿单元，清水不能凭空增加墨量。样片中的湿内区、迁移前沿和较深沉积段可有层次，但不是逐帧蒸发、连续干燥、真实纸纤维流体或墨粒沉降模拟。沉积随到达事件一次求值，之后单纯等待不会把边缘继续染黑；窗口秒数仍是作者设定，不是宣纸实验所得常数。

墨量按原始层分别保留。已干层的位置固定，后续纯水也不会重新搬动其墨量；尚湿层交换后的墨量继续沿用该层的原干燥期限，不因落到别处而重新计时。

`rasterizeInkProcess`返回画面尺寸、`columns`、`rows`、`cellSize`、`density: Float32Array`、`activeLayers`和`interactions`。后两项分别统计已到达的层数及发生有效局部湿交换的层事件数，不是艺术质量分数。`cellSize`默认`2`，单位为场景坐标；更粗的格子会丢失细接触和断白，改变它也会改变结果。结果密度不是颜色贴图；Canvas后端再用程序的纸墨颜色将密度映射为像素。

文档最多32层、总计512笔，每笔原始路径最多4096点；栅格最多262144个单元。超出上限应缩小范围、降低预览分辨率或拆分程序，不以静默截断源笔路代替校验。

固定编辑源、种子、绝对时间、网格和实际代码版本可重建同一结果；像素重放还须固定后端、画布与浏览器。生成器标识不能替代实际库版本，也不承诺跨后端逐像素相同。保存源码版本与参数比仅记录“积墨”或“破墨”的标签更有用。

## 与既有绘制的关系

新接口使用独立的墨层程序与绘制入口。普通`createInkScene`、`StrokeMark`／`WashMark`及`renderCanvas`仍可单独使用；旧`wash.passes`不自动变为新墨层，也不因此获得积墨法的艺术认可。

本批复用接触数据，但用独立网格材质取样和密度映射呈现，因此同一`StrokeMark`经新墨层入口与旧`renderCanvas`绘制不承诺像素相同。

分层、交换模型和可编辑数据为后续Agent提供执行能力；浓淡是否有层次、积墨是否留得住明处、破墨是否保留形体，需要在具体笔组和作品中分别评判。第四批的程序与工程检查不自动完成传统墨法验收。
