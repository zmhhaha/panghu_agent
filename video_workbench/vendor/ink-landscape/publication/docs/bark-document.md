# 枝干勾皴与木结

`createBarkDocument(branches, options)` 根据作者提供的枝干路径和枝径，生成勾线、木结点皴、裂纹和少量干擦。它先生成可编辑的普通笔组，再编译为 `InkScene`；生成过程不依赖 Canvas 或浏览器。

这是“枝干勾皴与木结”的程序近似。当前用于验证细线勾骨、疏密点皴和木结留白能否服从同一干形，不能据此认定已复现某位画家的笔法。

## 使用

```ts
import { createBarkDocument } from 'ink-landscape';

const document = createBarkDocument([
  {
    id: 'trunk',
    points: [
      { x: 80, y: 290, pressure: .65 },
      { x: 105, y: 205, pressure: .85 },
      { x: 150, y: 70, pressure: .3 },
    ],
    width: [
      { at: 0, width: 70 },
      { at: .55, width: 54 },
      { at: 1, width: 24 },
    ],
    shadowSide: 1,
    knots: [
      { id: 'wound', at: .42, side: 1, offset: .35, size: .7, count: 10 },
      { id: 'hollow', at: .75, side: -1, size: .55, count: 8, kind: 'cavity' },
    ],
    junctions: [{ id: 'fork', at: .42, side: 1, span: .1 }],
  },
], { width: 240, height: 340, seed: 821735, contourWidth: .055 });

// document.result 可交给 renderCanvas；document.groups 可逐笔编辑。
```

`width`、`height` 是场景画幅，所有路径坐标与枝径都使用同一场景单位。分支的 `width` 是完整直径，可以是常数或按 `at` 递增的控制点；控制点之间线性插值，两端保持最近控制值。

`at` 是沿中心路径累计弧长的归一化位置，范围为 0–1。局部法线为 `(-dy, dx)`；`side: 1` 指向正法线，`side: -1` 指向负法线。因此向上生长的干和向下垂落的干，其正侧在屏幕上的左右方向相反。

可选 `leftEdge`、`rightEdge` 分别覆盖负法线侧、正法线侧的勾线。它们是绝对坐标的作者路径，按各自的相对弧长与中心路径对应，需要沿同一生长方向排列。字段名不表示固定的屏幕左右；例如向下的路径，其 `leftEdge` 在屏幕右边。提供双边路径可保留特定参考的干形；不提供时由中心路径和枝径计算。

## 木结、洞口与叉口

木结的 `offset` 是有符号的局部半径比例，范围为 -1–1；省略时取所选侧的 `.35`。`size` 是相对局部完整枝径的尺度，默认 `.65`，范围 `.1`–2。`count` 控制点皴数量，默认 8，范围 2–20。`side` 控制裂纹和干擦朝向，省略时沿用分支的 `shadowSide`；`offset` 若显式给出，其正负独立决定位置。

普通 `kind: 'knot'` 生成有主次的短点皴及破钩；`kind: 'cavity'` 把点皴移到洞口周边，保留内部纸白。点皴和干擦裁剪到双边勾线围成的干形内。木结自身不会生成完整树枝或填满树干。

叉口 `junctions` 只在指定侧的勾线上留下开口：`at` 是开口中心，`span` 是占整条中心路径弧长的完整比例。它不自动生出分枝；需要接枝时由调用者提供另一个分支。

`contourWidth` 是勾线接触宽度相对分支最大枝径的比例，默认 `.06`，范围 `.005`–`.3`。显式提供公共 `strokeWidth` 时，以该场景单位值作为所有勾线的接触基宽，优先于比例；点皴、裂纹和干擦仍按局部枝径计算宽度。`textureStrength` 控制点皴、裂纹与干擦的墨量，默认 `.7`，范围 0–1。`shadowSide` 只决定勾线浓淡和默认木结方向，不创建光照模型。

公共 `seed`、`background`、`ink` 沿用普通笔组文档的参数格式。`opacity` 是所有生成笔画不透明度的乘数，省略时为 1；`opacity: 0` 可让整组墨迹不可见。`dryness` 若显式给出，会覆盖所有生成笔画的枯润；省略时保留勾线、点皴和干擦各自的枯润差异。

## 保存与修改

输出为 `{ version: 1, recipe, groups, result }`。`recipe.generator` 是 `ink-bark-groups-v1`，保存的是稀疏枝干、木结和叉口控制。`groups` 是已生成的普通 `StrokeGroupSource[]`，勾线组为 `structure`，各木结组为 `mark` 并通过 `parentId` 指向本分支的勾线组。父子关系是编辑元数据；绘制顺序由组序和笔序决定。

```ts
import {
  compileBarkRecipe, reviseBarkDocument, createStrokeGroupDocument,
} from 'ink-landscape';

const recipe = JSON.parse(JSON.stringify(document.recipe));
const rebuilt = compileBarkRecipe(recipe);

const stronger = reviseBarkDocument(document, {
  options: { textureStrength: .85 },
});

// 在普通笔组层单独调一笔，随后保存普通笔组配方。
const editedGroups = document.groups.map(group => ({
  ...group,
  strokes: group.strokes.map(stroke => stroke.id === 'crack'
    ? { ...stroke, options: { ...stroke.options, opacity: .5 } }
    : stroke),
}));
const edited = createStrokeGroupDocument(editedGroups, {
  width: 240, height: 340, seed: 821735,
});
```

`reviseBarkDocument` 的 `branches` 整体替换，`options` 浅合并，从作者源重新生成，忽略旧结果和逐笔修改。需要保留逐笔修改时，应继续使用普通笔组文档，而不是重新编译枝干配方。输入和配方拥有独立冻结的数据副本；修改时创建新数组与对象。

固定源、种子和渲染绝对时间可重建相同结果。分支、木结与组身份参与材质种子；调序不会给已有身份换材质。路径、字段、版本和 ID 会校验；每份配方最多 16 个分支、24 个木结、每个分支 16 个叉口，并遵守普通笔组的 512 笔预算。

## 三组对照

运行 `npm run dev`，打开 `examples/bark-study.html`。前两组左侧来自修改前的普通笔组，第三组左侧是为换形实验编写的手工笔组；右侧均由新枝干源生成。每组保持干形、画幅、种子和渲染设置一致。两处原画局部保留原有双边勾线的路径，比较变量是点皴、裂纹、干擦及轮廓断续的组织方式。

| 样片 | 比较目的 |
| --- | --- |
| 左侧主干叉口 | 原来独立摆放的点皴，改为按局部枝径、切向与叉口组织，检查木结是否落在干形内部。 |
| 右侧悬垂朽木洞口 | 对照洞口周围的浓点、破钩和轻擦，检查有无填黑洞心、越出轮廓或发生方向倒置。 |
| 斜干与枝径变化 | 原有手工短点作为基线，将同类木结控制迁移到斜向渐细的树干，检查朝向、尺度与开口是否跟随新干形。 |

第三组为专门的换形实验，不是原画中的第三处局部。页面支持导出一张三组对照图和一份包含基线、枝干配方及生成笔组的 JSON；也可直接编辑某组配方、重绘或还原。“检查重建”会在当前浏览器以相同时间和纸纹设置比较 JSON 往返后的像素。

这一阶段的评价重点是轮廓清楚、木结有向心的聚散、洞口有纸白、淡擦服从结构。材质是否更接近参考、是否仍显得机械，需要结合完整画面和人工视觉评价继续判断。

2026-11-20 的工程验证：`npm test` 205 项通过，`npm run check` 通过。8 项枝干专项验证包括冻结源、JSON 重建、木结局部修改隔离、方向与尺度迁移、编译后断口与洞心净空、全局样式及输入预算；整画接入另有未选笔画保持不变的验证。实际浏览器完成三组 JSON 重建像素比较，以及移动一个木结、导出和还原。图片学习框架以同一代码另行生成 540×1053 静帧，重复和重载输出一致；不承诺跨浏览器逐像素相同。

独立视觉复核认为几何大块感减少，斜干换形可读，单侧洞唇较初稿加厚；仍有木结偏碎、浅点游离和整画墨面偏轻的问题。当前记录为工程试作完成、艺术效果待用户评价。下一项按设计继续提按、顿挫与落印密度控制。
