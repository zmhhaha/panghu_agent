# 普通笔组文档

`createStrokeGroupDocument(groups, options)` 把作者提供的普通笔组编译为 `InkScene`，数据与几何核心不依赖 Canvas 或浏览器。用于衣纹、书法局部及其他原创组合，不按名称推断图式或动作。

```ts
const doc = createStrokeGroupDocument([
  { id: 'fold', role: 'structure', strokes: [
    { id: 'edge', points: [{ x: 40, y: 30 }, { x: 80, y: 160 }],
      options: { width: 3, controls: [{ at: .4, pressure: .7 }] } }
  ] }
], { width: 320, height: 240, seed: 42 });
```

组含 `id`、`strokes`，可选 `role: 'structure' | 'mark'` 与 `parentId`。父组必须存在且不能成环，关系只作编辑元数据，不改变位置、前后顺序或材质。绘制顺序就是组序、组内笔序。每笔含独立 `id`、`points` 及可选 `options`；局部压力、用锋、枯润、墨量、起收与绝对时间沿用 `BrushProgramOptions`，但 `id`、`seed` 由组合身份管理，不能覆盖。

公共选项含 `width`、`height`，可选 `seed`、六位十六进制 `background`／`ink`、`strokeWidth`、`dryness`、`opacity`。默认局部动作模型为 `local-v1`，未提供控制时保持普通接触；无需选择一个传统名称。每份文档最多512组、512笔，每笔2–4096个源点。

输出保存 `{ version: 1, recipe, result }`，配方生成器为 `ink-stroke-groups-v1`。稀疏作者设定按原粒度拥有并冻结，结果独立保存。`compileStrokeGroupRecipe(recipe)` 重建普通场景；`reviseStrokeGroupDocument(document, { groups?, options? })` 从源重新生成，组数组整体替换、公共选项浅合并，忽略旧结果。局部编辑用 `map` 替换目标组或笔，其他源可直接复用。

组合身份采用带长度的组ID／笔ID，避免斜线拼接碰撞；材质种子由组合身份和公共种子确定，插入和重排不会给其他笔换材质。组的父子关系不参与种子。未知字段、版本、身份覆盖、重复ID或无效数据明确报错。
