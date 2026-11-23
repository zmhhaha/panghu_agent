# 可编辑清水事件程序

第五批将清水与落墨显式分开，研究水破墨、墨破水和分散加水三组。清水保存作用笔路或逐滴位置，墨事件保存笔组与浓度；它们按作者给出的先后编译为[墨层程序](ink-process.md)。样片入口为[水法第五批](../examples/water-study.html)，资料、对照责任和实际检查见[第五批记录](water-review.md)。按用户后续澄清，每批三个技法合成一张对照总览，艺术效果留到最后统一评价。

## 创建、修改与重放

```ts
import {
  createWaterProcessDocument, reviseWaterProcessDocument,
  compileWaterProcessRecipe, rasterizeInkProcess, renderInkProcess,
} from 'ink-landscape';

const document = createWaterProcessDocument([
  {
    kind: 'ink', id: 'initial-ink', concentration: .7, water: .85,
    at: 0, dryAfter: 2,
    strokes: [{
      points: [
        { x: 35, y: 135, pressure: 0 },
        { x: 125, y: 85, pressure: .9 },
        { x: 235, y: 120, pressure: .85 },
        { x: 340, y: 145, pressure: 0 },
      ],
      options: { id: 'ink-line', width: 42, dryness: .15, smoothing: 'catmull-rom' },
    }],
  },
  {
    kind: 'water', id: 'local-water', water: .9, at: .5, dryAfter: 2,
    droplets: [
      { id: 'drop-a', x: 145, y: 90, radius: 15 },
      { id: 'drop-b', x: 208, y: 110, radius: 10 },
    ],
  },
], { width: 400, height: 240, seed: 261123 });

const revised = reviseWaterProcessDocument(document, {
  events: document.recipe.events.map(event => event.kind === 'water'
    ? { ...event, droplets: event.droplets?.map(drop => drop.id === 'drop-a'
      ? { ...drop, x: 158 }
      : drop) }
    : event),
});

const saved = JSON.stringify(revised);
const restored = JSON.parse(saved);
const process = compileWaterProcessRecipe(restored.recipe);

// 纯TypeScript密度场，可在Node中取得。
const frame = rasterizeInkProcess(process, { time: 1, cellSize: 2 });
// context由浏览器调用者提供；清水本身不画成白色。
renderInkProcess(context, process, { time: 1, cellSize: 2 });
```

以上示例说明可编辑事件与清水落点，不把水滴半径、等待秒数或墨浓度当作传统材料配方。清水单独出现时不产生可见墨色；预湿先发生、后墨尚未到达时，画面仍是纸色。示例中的局部加水也不会删除已有墨量。

## 文档与事件

| 接口或字段 | 作用 |
| --- | --- |
| `createWaterProcessDocument(events, options)` | 从调用者给出的清水与墨事件创建独立、冻结的文档。 |
| `compileWaterProcessRecipe(recipe)` | 从版本化编辑源重建普通`InkProcess`。 |
| `reviseWaterProcessDocument(document, { events?, options? })` | 返回新文档；提供`events`时整体替换，公共`options`浅合并。 |
| `document.recipe` | 版本`1`、生成器`water-event-process-v1`、原始`events`与公共`options`。 |
| `document.result` | 普通`InkProcess`，可以继续使用`rasterizeInkProcess`和`renderInkProcess`。 |

公共选项沿用`InkProcessOptions`：必填正数`width`、`height`，可选`seed`、`background`和`ink`。颜色、网格及编译结果的基本限制见[墨层接口](ink-process.md)。本接口在编辑源中保留作者省略的默认字段，不把展开结果逐项抄回源配置；输入副本归文档拥有，修改外部输入不应影响已创建文档。

事件以`kind`区分：

| 字段 | 墨事件`kind: 'ink'` | 清水事件`kind: 'water'` |
| --- | --- | --- |
| `id` | 必填稳定事件标识。 | 必填稳定事件标识。 |
| `strokes` | 显式`InkStrokeSource[]`，保存笔路与接触选项。 | 可选清水笔路，使用相同接触数据。 |
| `droplets` | 不支持。 | 可选显式清水滴`{ id, x, y, radius }[]`；位置须有限，半径须为正。 |
| `concentration` | 0–1，默认`.5`。 | 禁止传入，包括`0`；编译后的墨层浓度固定为`0`。 |
| `water` | 0–1，默认`.5`。 | 0–1，默认`1`。 |
| `at` | 非负绝对秒数，默认`0`。 | 同左。 |
| `dryAfter` | 声明的湿态窗口，默认`2`秒。 | 同左。 |
| `mode` | 默认`'wet-mix'`，也可选`'glaze'`。 | 同左。 |
| `edgeSoftness` | 可选0..1的内向接触密度渐隐，省略或0保持原覆盖。 | 同左，也用于清水接触的作用权重。 |
| `edgeDeposit`／`edgeWidth` | 可选0..1的整层内缘重分配强度与带宽比例；归一保留覆盖总量。 | 同左，只改变清水接触的作用权重，不生成颜料。 |

清水事件至少包含非空`strokes`或非空`droplets`之一，也可组合。清水滴是显式接触范围的程序表示，原始中心、半径与ID继续保存在配方中；它不是白色颜料、擦除笔或真实悬滴体积。水域由作者组织，不会自动全纸预湿或随机铺满均匀水点。

每滴编译为直径`2 × radius`的极短圆接触，属于局部接触近似；不记录真实滴高、体积或撞击速度。事件ID在整个文档内唯一，水滴ID在所属事件内唯一；水滴结果标识为`事件ID/drop:水滴ID`。笔路也宜显式设置`options.id`，避免依赖默认序号来表达跨重排身份；编译后标识发生碰撞时应拒绝。

水法示例的第15项还显式保存每滴周边2–4条不等长短清水笔路，中心、半径和伴随笔路共同给出不规则接触。示例 `editWaterDroplet` 只按已命名的 `drop-滴ID-wet-edge-` 来源同步平移/缩放所选滴的伴随触痕，保留其他滴、其他事件和所有材质种子；这是示例层的组合编辑工具。公共文档不建立隐式绑定，直接修改JSON里一滴的中心或半径不会自动修改其他清水笔路。第13/14项则在完成布局变换后，按弧长叠加多频连续微偏移与缓变压力；保存的仍是普通笔路，不依赖运行时随机水形。

事件数组按`at`非递减排列，同刻保留作者数组顺序。稳定事件标识与接触标识用于让无关修改保持自己的材质种子；移动一滴时应保留它的ID。修改事件数组是完整替换，不是按索引隐式合并。

每个文档最多32个事件，笔路与水滴总计最多512个接触，每条源笔路最多4096点；密度栅格最多262144个单元。容量不足时须拆分程序或降低预览分辨率，不静默丢弃作者输入。

## 湿交换与裁切边界

当前共用第四批的局部有限密度交换。后到事件在`'wet-mix'`模式下，仅与仍处于声明湿窗、有水量且作用范围相交的先事件交互。先墨后水可重分布尚湿的原墨；先水后墨可影响后到墨的局部密度分布。无相交、零水量或已过窗口的先层不能产生对应湿交换。已干墨贡献保持原位，交换后的墨仍沿用其原始事件的干燥期限。

清水笔路指定作用范围。湿交换后，有限事件沉积近似依照湿区前沿梯度迁移少量已有颜料；局部有长度的笔路提供朝向偏置，纸纹方向与多尺度门控选择断续的受墨段。水滴极短接触不作为有意义的行笔朝向。一个远处、不相交的水笔路不会给现有水滴附加全局迁移方向。

这一近似让湿内区、前沿与局部较深的停留段可分别观察，沉积从内侧现有墨量取得，不是描一圈黑边，也不应让每滴水都成为同样的黑圆圈。先水后墨与先墨后水可因后到事件笔路和种子而不同，但这些是程序选择，不能当成真实材料的先后方向机制已经成立。王诜用例中的“向内化开”仍是资料中的观察目标，不能仅凭一条向内笔路宣称已经还原。

清水不增加总墨量，也不作为白色覆盖或`destination-out`擦除。局部变浅和变深来自同一次墨量重分布；整个结果的总墨量与局部明度是不同观察项。模型不表示真实水储量、连续毛细渗透、墨粒沉降或可逆复湿，也没有物理测得的宣纸干燥秒数。事件完成后的深边作为该次结果保存其执行条件，之后只改变绝对查看时间不会继续蒸发或加深。

逐笔`clip`约束初始接触沉积；后续湿交换可把墨量移出该区域。需要最终硬边界时，调用者须裁切返回栅格或最终显示图像。清水接触辅助线只用于检查作用位置，不属于清水的可见颜色。

`at`表示整个事件瞬时到达，`time`用于绝对定位哪些事件已发生，不是连续运笔、停驻或随墙钟变干。固定输入、种子、绝对时间、网格和实际代码版本可重建数据；逐像素重放还需固定浏览器、画布与后端。生成器标识不能单独替代源码版本。

## 保存范围与技法名称

保存完整文档才能同时保留编辑源与结果。载入后按`recipe`重编译，并与保存的`result`核对；可解析JSON不自动等于有效事件文档。未知版本、生成器、不支持字段及清水事件中的浓度字段应拒绝。不要把临时密度场或导出的PNG当作可编辑事件源。

示例页载入区显示载入时刻的静态预览，不随后续时间滑块变化；继续编辑载入的事件可使用公共修订接口。载入核对比较JSON结构及数值，对象字段顺序不影响一致性，事件与笔路数组的顺序仍须保留。

水破墨采用已核实的先墨后局部清水操作作为研究入口；墨破水只取纸面先水后墨的课堂语境；分散加水保留“洒水研究”的部分支持状态。这三组共享执行机制，但资料范围和对照责任分别记录。渍水、撞水和皴水没有因为本批清水事件能力而自动实现，真实材料和艺术效果留到统一评价时判断。
