# 笔路、接触与皴笔

本页的示例接受宿主供应的 `ink` 模块对象，只生成可保存数据；绘制入口见 [runtime-and-validation.md](runtime-and-validation.md)。

## 选择接口

| 接口 | 返回结果与用途 |
| --- | --- |
| `createInkScene({width,height,seed,background})` | builder；`stroke(points, options)`、`wash(outline, options)`、`snapshot()` 适合自由组合 |
| `createBrushDocument(points, options)` | `{recipe,result}`；保留作者原始路径和动作设定，`result` 为一条 `StrokeMark` |
| `reviseBrushDocument(document, {points?,options?})` | 从源创建新文档，选项浅合并，传入 `controls` 时整体替换 |
| `compileBrushRecipe(recipe)` | 重建 `StrokeMark` |
| `createCunDocument(strokes, options)` | 显式皴笔源编译成 `InkScene`；不自动规划山体 |
| `reviseCunDocument` / `compileCunRecipe` | 修改源／重建普通场景 |
| `createDotDocument(positions, options)` | 结果是 `StrokeMark[]`，每个点保留自己的源文档；需要时逐条加入 builder |
| `createVegetationDocument(units, options)` | 点苔、点叶、夹叶的显式笔组编译成 `InkScene` |

`InkPoint` 使用 `{x,y,pressure?}`，不是示例场景中私有的 `p` 字段。场景坐标的 y 向下，角度为弧度。给石脊保留折角，给柔韧线自行组织曲线；`catmull-rom` 可能越出控制点，需要保折时用 `linear`。

## 参数之间的关系

| 控制 | 实际作用 |
| --- | --- |
| 点的 `pressure` | 0–1 的原始笔压；与局部 `controls[].pressure` 相乘，零压力段不会被乘回有墨 |
| `width` | 单笔接触宽度；文档的 `width/height` 是画幅尺寸，两者不能混用 |
| `axis: 'center' / 'side'` | 中用／侧用的接触默认，不是真实毛笔物理求解 |
| `dryness` | 0–1，控制干湿接触断续；不等于墨色浓度或清水量 |
| `opacity` | 接触覆盖透明度；淡墨层的光学浓度另用 `concentration` |
| `controls` | 按归一弧长 `at` 放置 `pressure/side/angle/dryness/inkLoad`；稀疏通道独立插值 |
| `entry` / `finish` | `preserve/exposed/concealed` 与 `preserve/open/return`；按需要调整起收 |
| `turn` / `rhythm` / `drag` | `preserve/round/angular`、`continuous/hesitant`、`forward/reverse`，为局部形态近似 |
| `start/duration` | 绝对秒数的路径显露，默认 0；不等同落笔速度或真实吸墨时间 |

`createBrushDocument` 使用 `gestureModel:'local-v1'` 可采用局部动作模型；原始路径仍是主体。`strength:0` 只关闭该类几何动作，不会清空接触控制。该文档不接受 `nib/tipAngle/brush`，应使用语义选项和 `controls`。

## 自创一笔并保留可编辑源

```javascript
function makeBrushStudy(ink) {
  const original = ink.createBrushDocument([
    { x: 40, y: 38, pressure: 0.15 },
    { x: 80, y: 64, pressure: 0.9 },
    { x: 116, y: 122, pressure: 0.6 },
    { x: 162, y: 156, pressure: 0 },
  ], {
    id: 'ridge-stroke', seed: 37, width: 22, ink: '#282d26',
    gestureModel: 'local-v1', axis: 'side', smoothing: 'linear',
    dryness: 0.66, opacity: 0.83, start: 0, duration: 1.5,
    controls: [
      { at: 0, inkLoad: 0.9 },
      { at: 0.4, pressure: 0.85, dryness: 0.44 },
      { at: 1, dryness: 0.88, inkLoad: 0.4 },
    ],
  });
  const edited = ink.reviseBrushDocument(original, { options: { width: 25 } });
  const builder = ink.createInkScene({ width: 240, height: 200, seed: 37 });
  builder.stroke(edited.result.points, edited.result);
  return { recipe: edited.recipe, scene: builder.snapshot() };
}
```

`builder.stroke(result.points,result)` 将生成结果加入场景。要再次修改笔意，应保存并修订 `recipe.points`，不要把已被起收动作改变的结果点冒充原始笔路。

## 按石面设计皴笔

`createCunDocument(strokes, options)` 要求每笔 `{id,points,options?}`。公共 `options` 要有 `technique,width,height`，可含 `strokeWidth,dryness,opacity,ink,seed,strength`。当前选择有 `pima/axe/zhedai/jiesuo/luanchai/niumao/cloud-study/point-study/heye`，它们设置接触与动作默认；换一个字符串不会自动把同一组直线变成另一种传统皴法。

逐笔可覆盖宽度、干湿、起收、局部 `controls`、`clip`、`start/duration`；不能逐笔传 `ink/seed/gestureModel`，它们由公共选项、稳定 ID 和生成器负责。每次提供的 `controls` 替换该笔原有默认控制，不能假定还叠着一套隐藏的技法控制。

```javascript
function makeCunStudy(ink) {
  const document = ink.createCunDocument([
    { id: 'dark-face', points: [
      { x: 95, y: 40, pressure: 0.75 },
      { x: 112, y: 87, pressure: 0.95 },
      { x: 88, y: 145, pressure: 0 },
    ], options: { width: 24, dryness: 0.72 } },
    { id: 'adjacent-plane', points: [
      { x: 119, y: 55, pressure: 0.3 },
      { x: 148, y: 101, pressure: 0.63 },
      { x: 162, y: 129, pressure: 0 },
    ], options: { width: 12, opacity: 0.45 } },
  ], { technique: 'axe', width: 240, height: 190, seed: 41 });
  const saved = JSON.parse(JSON.stringify(document.recipe));
  return { recipe: saved, scene: ink.compileCunRecipe(saved) };
}
```

这是两笔的接口演示，不是完整斧劈配方。完整山体需额外组织骨线、层叠、浓淡、留白和遮罩。给定方向场时先采样成普通路径，再存源数据；JSON 不保存函数。

## 点、叶与自然分布

`createDotDocument` 的每个落点可设 `id,x,y,pressure,shape,size,angle,controls,start`，形态为 `round/horizontal/vertical/oblique`。位置由作者给定，库不替你随机铺满整块区域。

`createVegetationDocument` 的单位为 `{id,role?,strokes:[{id,points,options?}]}`，公共选项的 `technique` 为 `moss/dot-leaf/outline-leaf`。一个夹叶可在同一单位中保存两边路径，内部保持透明；遮挡后的边线缺段由作者明确提供。

`transformVegetationUnit` 可平移、绕显式支点旋转、缩放笔宽；`widthScale` 不缩放路径。移动叶组后仍需核对附枝根和遮挡。`createSplitVegetationUnit` 按显式束偏移与束宽生成可逐束编辑的源，不是真实散毫模拟。

`getNoise1D/getNoise2D/getFractalNoise2D`、`jitterPath/varyPressure/splitBristles` 可辅助不均匀接触；固定种子并将生成后的源保存。`poissonDiskSampling` 更适合限制组间锚点距离，簇内允许叠墨；不要把所有墨迹强制排斥开。
