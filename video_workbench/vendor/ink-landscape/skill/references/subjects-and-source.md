# 物象、组合与可编辑源

三种宏返回 `{version:1,kind,groups,attachments}`。`groups` 是普通 `StrokeGroupSource[]`，可拼进 `createStrokeGroupDocument()`，不是不可修改的贴图。它们帮助创建源，不接管整幅构图。

## 造形入口

| 函数 | 已有输入与边界 |
| --- | --- |
| `createTreeMacro` | `start,angle,size,width,depth,style:'dry'/'wet'`；可用 `branches` 全替换递归枝架 |
| `createRockMacro` | `position,size` 或自有绝对坐标 `outline,region,ridges,mossAnchors`；`flow(point)` 控制局部皴笔方向 |
| `createBirdMacro` | 当前仅 `type:'crow'`，`pose:'sitting'/'looking-up'/'looking-down'`，`facing:'left'/'right'`；`parts` 可替换整类部件 |

树的每枝为 `{id,points,width?,parentId?,attachAt?}`。子枝从其首点整组平移到父枝归一弧长 `attachAt`（默认 0.78），不是自动旋转到父枝。声明父子时库会共享交点，宽度要比父枝小；父子元数据本身并不决定遮挡。

树深度 0–6、最多 127 枝。默认递归树是可选骨架，临摹特定树势时用自有枝架。各枝提供 `<branch-id>:root/tip` 附着点。石头的 `outline/region/ridges` 都是绝对坐标，给定后不再按 `position/size` 缩放。默认石面及内部撒笔不适合自动承担所有山体造形，应按亮暗面和裂隙另设计源。

鸟按 `tail/back/wing/head/beak/feet/eye` 七类部件保存。`attachment:{point,tangent?}` 以爪定位并可旋转整鸟；斜枝上想保持鸟身竖直，就只传 `point` 或水平 `tangent`。自有 `parts` 路径和笔宽已是场景坐标，宏不再次缩放、旋转或自动重接爪。空数组 `[]` 明确移除该部件；`paper` 应与纸色一致。

## 自有枝架、附鸟和局部重绘

```javascript
function makeSubjectStudy(ink) {
  const tree = ink.createTreeMacro({
    id: 'perching-tree', seed: 31,
    branches: [
      { id: 'trunk', width: 14, points: [
        { x: 85, y: 260, pressure: 0.9 },
        { x: 110, y: 162, pressure: 0.7 },
        { x: 96, y: 74, pressure: 0.2 },
      ] },
      { id: 'perch', parentId: 'trunk', attachAt: 0.6, width: 6,
        points: [{ x: 0, y: 0, pressure: 0.8 }, { x: 90, y: -10, pressure: 0 }] },
    ],
  });
  const anchor = tree.attachments.find(item => item.id === 'perch:tip');
  const rock = ink.createRockMacro({ id: 'small-rock', seed: 32,
    position: { x: 222, y: 224 }, size: 62, textureCount: 9, mossCount: 4 });
  const bird = ink.createBirdMacro({ id: 'perched-crow', seed: 33,
    size: 43, pose: 'looking-up', paper: '#f5f2e9',
    attachment: { point: anchor.point, tangent: { x: 1, y: 0 } } });
  const document = ink.createStrokeGroupDocument(
    [...rock.groups, ...tree.groups, ...bird.groups],
    { width: 360, height: 300, seed: 34, background: '#f5f2e9' },
  );
  const saved = JSON.parse(JSON.stringify(document.recipe));
  const changed = ink.reviseStrokeGroupDocument(document, {
    groups: saved.groups.map(group => group.id.endsWith('/wing') ? {
      ...group, strokes: group.strokes.map(stroke => ({
        ...stroke, options: { ...stroke.options, dryness: 0.84 },
      })),
    } : group),
  });
  return { recipe: changed.recipe, scene: changed.result, attachments: tree.attachments };
}
```

这是连接与改源演示。实际鸟爪是否踩住、树石尺度是否合理，要看图确认；附着点不会替你补画枝条或修正身体比例。想画其他鸟、山形、松冠或飞鸟，直接设计自己的普通笔组；不要把唯一模板反复改标签充当不同能力。

## 源、身份与重建

`createStrokeGroupDocument(groups, options)` 要有画幅 `width,height`。每组 `{id,role?,parentId?,strokes}`，每笔 `{id,points,options?}`；组内 ID 唯一，多个同类宏应给不同物体 ID。逐笔可以设置墨色与笔意，但 `id/seed` 由稳定的组／笔身份和公共种子派生，不要塞入逐笔 `options`。

数组顺序就是绘制顺序，`parentId` 是作者关系，不能自动建立形变、附着或遮挡。普通笔组文档最多 512 笔；多个宏各自未超预算，不代表组合后必然合规。

保存源时同时记录 `recipe` 和宿主给出的真实库修订号／内容哈希；生成结果可另存以便核对。`compileStrokeGroupRecipe(savedRecipe)` 重建 `InkScene`。`reviseStrokeGroupDocument(document,{groups?,options?})` 返回新文档；`groups` 整体替换，保持未改组的身份和原始源即可局部调整。对象被冻结，不要就地修改。

`flow` 等函数只在生成源时运行，JSON 中保存的是结果点列。需要继续按原方向场生成新笔时，额外保存自己编写的生成函数源码与参数，不要期待 JSON 自动保存闭包。

库内完整剑峰与枯木寒鸦画例可用来理解组合，但它们位于 examples，尚非顶层 API；不能假定宿主一定打包了示例代码。方法应内化成自己的结构与笔组，成图不依赖参考网址仍在线。
