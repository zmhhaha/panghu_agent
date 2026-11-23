# 墨层与清水事件

普通 `renderCanvas` 绘制笔触／铺染；`renderInkProcess` 绘制光学密度场。这是两个明确入口，不能把 `InkProcess` 直接传给 `renderCanvas`，也不能认为普通 `wash.passes` 自动具备水墨交换。

## 数据和参数

`createInkProcessDocument(layers, options)` 返回 `{recipe,result}`。每层有 `id,strokes`，笔源为 `{points,options?}`。笔的时间由所属层统一负责；不要在这些笔的 `options` 中设置 `start/duration/ink`。公共 `options` 必须有画幅 `width,height`，可含 `seed,background,ink`；纸色和整套颜料色为六位 `#RRGGBB`。

| 层字段 | 含义 |
| --- | --- |
| `concentration` | 0–1 的光学颜料量，越大不一定越好；留出透纸与明处 |
| `water` | 0–1 的作者设定湿接触量，与透明度、颜料量分开 |
| `at` | 整层到达的绝对秒数，瞬时事件 |
| `dryAfter` | 非负的有效湿窗秒数，不是测得的真实纸张干燥时间 |
| `mode` | `glaze` 叠加，`wet-mix` 才尝试有效湿交区内的有限交换 |
| `edgeSoftness` | 0–1，接触内部渐隐，不模糊最终截图；可能减少覆盖量 |
| `edgeDeposit` | 0–1，重分配已有覆盖到不等内缘带，保留启用前该层总覆盖 |
| `edgeWidth` | 内缘带占局部接触半径比例 0–1，默认 0.22；0 关闭内缘带 |

源笔迹 `clip` 限制初始沉积，不是湿交换后密度场的硬边界。需要严格剪裁时，在宿主合成阶段对最终结果另做裁切。`edgeSoftness` 不会自动柔化 `clip` 边线。

## 先后关系的可编辑研究

```javascript
function makeInkStudy(ink) {
  const stroke = (x, dryness) => ({
    points: [
      { x, y: 28, pressure: 0.25 },
      { x: x + 20, y: 76, pressure: 0.95 },
      { x: x + 6, y: 132, pressure: 0 },
    ],
    options: { width: 42, dryness, axis: 'side', seed: 17 },
  });
  const document = ink.createInkProcessDocument([
    { id: 'pale-shape', strokes: [stroke(74, 0.2)],
      concentration: 0.3, water: 0.75, at: 0, dryAfter: 2,
      edgeSoftness: 0.5 },
    { id: 'dark-contact', strokes: [stroke(89, 0.58)],
      concentration: 0.8, water: 0.5, at: 0.5, dryAfter: 1.2,
      mode: 'wet-mix', edgeDeposit: 0.35, edgeWidth: 0.25 },
  ], { width: 180, height: 160, seed: 17, ink: '#292d27', background: '#f5f2e9' });
  const recipe = JSON.parse(JSON.stringify(document.recipe));
  const process = ink.compileInkProcessRecipe(recipe);
  const frame = ink.rasterizeInkProcess(process, { time: 1, cellSize: 2 });
  return { recipe, process, frame };
}
```

该例只证明可以生成密度数据；黑白关系、湿边和笔意仍需绘图评价。`reviseInkProcessDocument(document,{layers?,options?})` 返回新文档，传入 `layers` 时替换整个层数组。

## 清水不是白色颜料

`createWaterProcessDocument(events, options)` 将水墨事件编译为相同的 `InkProcess`。`kind:'ink'` 提供笔组与浓度；`kind:'water'` 提供清水笔路或逐滴 `{id,x,y,radius}`。清水的颜料浓度固定为零，不会因命名为云或水就变成橡皮擦。

```javascript
function makeWaterStudy(ink) {
  const document = ink.createWaterProcessDocument([
    { kind: 'ink', id: 'wet-ink', at: 0, dryAfter: 2, water: 0.8,
      concentration: 0.6, strokes: [{
        points: [{ x: 44, y: 45, pressure: 0.6 }, { x: 106, y: 94, pressure: 0.8 }],
        options: { width: 34, dryness: 0.15, axis: 'side' },
      }] },
    { kind: 'water', id: 'water-contact', at: 0.4, dryAfter: 1,
      water: 0.9, mode: 'wet-mix',
      droplets: [{ id: 'drop-left', x: 74, y: 64, radius: 18 }] },
  ], { width: 160, height: 130, seed: 19 });
  const recipe = JSON.parse(JSON.stringify(document.recipe));
  const process = ink.compileWaterProcessRecipe(recipe);
  return { recipe, process, frame: ink.rasterizeInkProcess(process, { time: 1 }) };
}
```

当前交换局部、非负且守恒，已经干的层不会被后续清水重新搬动。这里没有逐帧蒸发、真实毛细扩散或连续流体；单纯等待不会持续长出湿边。想研究“水先墨后”时，显式交换事件先后并对照，不能只改标签。

云海留白可以是构图中从未落墨的区域，也可用独立 alpha 遮罩。不要用大量白墨盖住所有层，或把清水事件当云海擦除指令。
