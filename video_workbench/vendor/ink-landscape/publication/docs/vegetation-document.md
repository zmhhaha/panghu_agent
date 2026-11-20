# 可编辑点苔与叶片文档

`VegetationDocument`保存调用者组织的结构线、苔点和逐叶笔组，编译为普通`InkScene`，继续用`renderCanvas`绘制。第九批研究点苔、点叶与夹叶，见[样片](../examples/vegetation-study.html)与[观察记录](vegetation-review.md)。它把对象组织保存在可修改数据中，接触默认只作用于作者提供的路径，不自动生成枝架、叶簇或固定树冠。艺术效果留到最后统一评价。

## 创建、修改与保存

```ts
import {
  createVegetationDocument, reviseVegetationDocument,
  compileVegetationRecipe, renderCanvas,
} from 'ink-landscape';

const document = createVegetationDocument([
  {
    id: 'branch-main', role: 'structure',
    strokes: [{ id: 'line', points: [
      { x: 54, y: 164, pressure: .9 },
      { x: 109, y: 112, pressure: .7 },
      { x: 158, y: 63, pressure: .25 },
    ] }],
  },
  {
    id: 'leaf-1', role: 'mark',
    strokes: [{ id: 'leaf-stroke', points: [
      { x: 109, y: 112, pressure: .2 },
      { x: 124, y: 100, pressure: 1 },
      { x: 137, y: 94, pressure: .1 },
    ] }],
  },
], { technique: 'dot-leaf', width: 320, height: 220, seed: 261127 });

const revised = reviseVegetationDocument(document, {
  units: document.recipe.units.map(unit => unit.id === 'leaf-1'
    ? { ...unit, strokes: unit.strokes.map(stroke => ({ ...stroke,
      points: stroke.points.map(point => ({ ...point, x: point.x + 8 })),
    })) }
    : unit),
});
const saved = JSON.parse(JSON.stringify(revised));
const scene = compileVegetationRecipe(saved.recipe);
renderCanvas(context, scene, { time: 8, paperTexture: 0 });
```

这段示例只说明编辑源和逐叶修改，不是完整点叶配方。枝架、叶片向背、相接位置及先后笔序仍由调用者设计。调用者可自行从方向场生成路径，再把实际路径保存在文档中；运行时不需要原方向场回调。

## 单元、笔路与编译结果

| 接口或字段 | 责任 |
| --- | --- |
| `createVegetationDocument(units, options)` | 从作者组织的结构与点叶单元创建文档。 |
| `reviseVegetationDocument(document, { units?, options? })` | 返回重新生成的新文档；单元数组整体替换，公共选项浅合并。 |
| `compileVegetationRecipe(recipe)` | 从版本化编辑源重建普通场景。 |
| `createSplitVegetationUnit(id, source, bundles)` | 将一条作者母路展开为具有显式间距、笔宽与压力比例的相关分束源笔。 |
| `transformVegetationUnit(document, unitId, transform)` | 变换指定作者单元并重建文档；单元可以是一片双边叶或一个多笔叶组。 |
| `document.recipe` | 版本`1`、生成器`ink-vegetation-guides-v1`、原始`units`与公共`options`。 |
| `document.result` | 可序列化、可检查、可重放的普通`InkScene`。 |
| `unit.id` | 作者提供的稳定单元身份，例如一片叶、一组苔点或一段枝架。 |
| `unit.role` | `structure`表示结构线，`mark`表示所选技法的点笔或叶边，省略按`mark`；角色不生成对象关系。 |
| `unit.strokes` | 该单元内按作者顺序保存的逐笔路径；每笔包含稳定`id`、`points`及可选`options`。 |

一个夹叶单元可以保存同一叶片两边的独立源路径。两边的朝向、接点、间距、开合与相互遮让由作者确定；单元分组便于共同修改，不创建隐藏的配对几何规则。中间白区来自未着墨区域，不使用白色填充遮盖此前笔迹。

第十二批继续用普通源组织竹叶、梅花式树叶点簇与绘画双勾，见[本批记录](bamboo-plum-outline-review.md)。前两项使用`dot-leaf`接触，双勾使用`outline-leaf`细线接触；这些选项决定接触默认，具体物象和叶簇仍由作者路径决定。梅花式点叶不等同梅花花瓣，绘画双勾也不执行法帖摹写的廓填流程。

双勾的两侧轮廓分别保存，宽窄和合拢来自显式边界。一个双边小组可包含多个对象及各自可见的轮廓片段，以稳定源笔ID区分前后、左右和分段。场景按源数组顺序绘制，`start`控制绝对时间显露，不对数组重新排序。前景仅画边界、内部透明，后画前景边线不会自动遮住后景线；需要遮挡时，调用者必须省略被遮部分，或明确提供有长度的零压力区间。单个零压力点只确定局部离纸，不能表示任意长度的隐藏区。

这些可见片段保留为作者普通源，不补白、不自动闭合，也不保存隐藏的轮廓推断规则。整组变换会一起移动现有片段；单独修改前景位置时，调用者应重新组织后景的可见片段。保存后的空缺可以重建，不表示库已经计算了对象遮挡、完整花鸟造形或传统笔序。

## 公共设定与逐笔覆盖

`width / height`为必填场景尺寸；`seed`默认为`1`，由公共种子与单元、笔的稳定身份派生逐笔材质种子。同一单元内笔ID不能重复，不同单元可使用相同局部笔ID。单元数组及单元内笔数组的顺序保留作者叠压关系；挪动或重排时保留ID，其他笔的材质身份不会随数组下标改变。

| 默认角色或技法 | 笔宽 | 枯润 | 透明度 |
| --- | --- | --- | --- |
| `structure` | 2.4 | .50 | .65 |
| `moss` | 9 | .50 | .78 |
| `dot-leaf` | 15 | .35 | .78 |
| `outline-leaf` | 2.2 | .32 | .74 |

公共`strokeWidth / dryness / opacity`覆盖上表默认，逐笔`options`再覆盖公共设定。逐笔可以设置宽度、枯润、透明度、起收、转折、平滑、裁切、局部控制和绝对`start / duration`等；显式`controls`整体替换默认数组。`strength`控制局部几何动作强度，不同时关闭接触控制；生成器固定使用`local-v1`。

逐笔禁止`id / ink / seed / gestureModel`，身份由源笔`id`管理。整份文档使用单一`ink`，默认`#20251f`，纸色默认`#f5f2e9`；二者只接受六位十六进制`#RRGGBB`。本批保持单色研究范围；如需墨与绿色点苔并用，调用者应分别创建不同墨色的文档并明确组织合成，本接口不会从点苔名称推断色彩。

最多512个单元、512条源笔，每单元包含1—512条源笔；每条路径2—4096点，裁切轮廓最多4096个顶点。局部控制沿用包括自动首尾端点在内的128个上限。未知版本、生成器、字段、重复身份或非法配置明确拒绝，不截断笔组或静默改变解释。文档保留输入的独立冻结副本，修改从源重新生成。

## 技法与对象责任

| `technique` | 程序研究范围 | 作者继续提供的内容 |
| --- | --- | --- |
| `moss` | 显式苔点短笔的接触、起按和收提。 | 石脊与坡面位置、大小间杂、聚散及与皴线的呼应。 |
| `dot-leaf` | 沿显式单叶笔路的点染接触与提按。 | 枝架、叶簇、叶片顾盼、浓淡、空隙和叠压。 |
| `outline-leaf` | 沿显式叶边的细线接触。 | 一叶两边的轮廓、枝叶连接与叶群空实。 |

点苔、点叶的阅读有明确支持；夹叶仍为部分支持的形态研究。程序默认不是传统技法的统一参数标定，也不决定某树只能用某种点式。第十批沿用`dot-leaf`执行介字、个字的作者叶组与攒三聚五的组织研究，范围见[第十批记录](leaf-cluster-review.md)；这些是不同的显式源组织，不新增同名固定笔头，也不自动认定竹叶或其他专名已经实现。第十一批另行研究胡椒点、破笔点和松针的显式组织，原型及局限见[第十一批记录](pepper-broken-pine-review.md)。

## 相关分束源

第十一批的破笔点形态研究新增纯数据辅助函数，入口导出 `createSplitVegetationUnit` 与 `VegetationContactBundle`。它不增加同名历史笔头，也不改变文档生成器与既有默认值。

```ts
const split = createSplitVegetationUnit('rough-cluster', {
  id: 'dab',
  points: [
    { x: 80, y: 80, pressure: .4 },
    { x: 82, y: 91, pressure: 1 },
    { x: 81, y: 104, pressure: .2 },
  ],
  options: { start: 1, duration: .5, dryness: .45 },
}, [
  { id: 'center', offset: 0, width: 6 },
  { id: 'left', offset: -6, width: 4.5, pressureScale: .8 },
  { id: 'right', offset: 5.5, width: 5.5 },
]);
const document = createVegetationDocument([split], {
  technique: 'dot-leaf', width: 320, height: 240, seed: 261131,
});
```

每束有稳定 `id`、有限的 `offset`、正数 `width` 和可选 `pressureScale`（0—1，默认1）。允许2—32束，同组束ID不能重复。偏移量与宽度均为场景单位；正偏移沿母路切向的法向 `(-dy, dx)`，不随笔宽倍率自动改变。端点使用相邻非零段，内部使用前后单位切向的平均；局部重复点跳过零段，180°回折取出向。全零长度母路沿用核心拒绝规则。急转处偏移可能折叠，不执行轮廓并集或自动修补。

每束保留母路采样点的压力，乘以自己的压力比例；零压力保持离纸。局部接触控制、绝对起止时间及场景坐标中的显式裁切轮廓保持，不另偏移裁切区。输出成员ID采用二元长度前缀 `母路ID长度:母路ID/束ID长度:束ID`，防止名称含分隔符时合并点触发生碰撞，同一来源的束同时开始；文档仍按数组顺序绘制。分束以多条普通源笔近似相关接触，不模拟一次落笔的笔毫、墨水共享或材料守恒。

输出是可逐束编辑、可整组变换的普通 `VegetationUnitSource`。保存时记录展开后的点与设定，重建不依赖浏览器或分束辅助函数的内部状态。若调用者要从母路重新调整分束参数，应另存母路及束设定；普通文档不会从展开的源笔反推母路。第十一批样片在展开后明确修改部分束尾，使参差成为实际保存的作者点编辑。胡椒点与松针则直接保存每个细点和每条针路，三项范围见[第十一批记录](pepper-broken-pine-review.md)。

## 整组变换

```ts
const transformed = transformVegetationUnit(document, 'leaf-1', {
  translation: { x: 8, y: -3 },
  rotation: Math.PI / 12,
  pivot: { x: 109, y: 112 },
  widthScale: 1.2,
});
```

从包入口额外导入`transformVegetationUnit`。`translation`为平移量，`rotation`使用弧度；非零旋转必须提供有限坐标的`pivot`，不暗用画布中心或自动推断叶柄。先绕支点旋转，再平移；点坐标和作者显式裁切轮廓一起变换，压力、时间、身份及数组笔序保持。局部控制中的角度相对笔路表达，刚性旋转不会对它再叠加一次角度。

`widthScale`必须为正数，只缩放指定单元每笔的有效宽度，不缩放路径、组间距、画布或其他单元。有效宽度依次来自逐笔`width`、公共`strokeWidth`、对应角色/技法默认；非单位倍率会将该有效宽度的缩放结果写入该笔覆盖。单位倍率与无位移、无旋转不应凭空补齐稀疏源设定。变换后的源仍可用普通`compileVegetationRecipe`重建，不依赖浏览器保留某次变换的内部状态。

这项函数变换现有作者组，不产生叶片，不改变技法，也不自动修复与枝条的交接。调用者应从保存的原始文档计算交互中的目标变换，避免把滑块每次更新累积到上一次结果。

源笔与生成结果分别保存；修改动作时从源重新生成，重放既有结果时使用保存的场景。固定输入、种子、版本、画布和绘制条件下按绝对时间定位；时间显露不表示真实运笔速度、干燥或纸内扩散。数据与几何核心可在Node中使用，Canvas 2D只负责呈现，不要求Shader。

创建与双边编辑的检查见[第九批观察记录](vegetation-review.md)，整组变换和分向叶簇的本轮检查见[第十批记录](leaf-cluster-review.md)。
