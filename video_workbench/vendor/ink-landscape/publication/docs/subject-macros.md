# 可编辑的树、石、鸟复合源

`createTreeMacro()`、`createRockMacro()`、`createBirdMacro()`将若干部件组合为普通笔组。它们是可选的程序造形模板，尚不代表完整的传统画法或经过艺术验收的物象预设。创建与重建不需要浏览器；画面仍由既有笔触和墨层渲染器呈现。

三个函数都返回 `{ version: 1, kind, groups, attachments }`。`groups`可直接拼入`createStrokeGroupDocument()`；`attachments`保存具名位置和单位切向，供作者附鸟、接枝或定位后续笔组，不自动产生遮挡或改写别的物体。返回值独立拥有并冻结全部数据，保存JSON即可继续编辑。

```ts
import {
  createTreeMacro, createRockMacro, createBirdMacro,
  createStrokeGroupDocument, renderCanvas,
} from 'ink-landscape';

const tree = createTreeMacro({
  id: 'old-tree', seed: 17, start: { x: 160, y: 420 },
  size: 170, width: 16, depth: 3, style: 'dry',
});
const rock = createRockMacro({
  id: 'ledge', seed: 18, position: { x: 290, y: 320 },
  size: 110, moss: true, textureCount: 14,
});
const tip = tree.attachments.find(a => a.id === 'root:tip')!;
const bird = createBirdMacro({
  id: 'crow', seed: 19, size: 52, pose: 'looking-up',
  // A horizontal perch frame keeps this bird upright at the selected point.
  attachment: { point: tip.point, tangent: { x: 1, y: 0 } },
});
const document = createStrokeGroupDocument(
  [...rock.groups, ...tree.groups, ...bird.groups],
  { width: 600, height: 460, seed: 20 },
);
renderCanvas(context, document.result, { time: 0 });
```

`id`为物体身份，默认分别为`tree`、`rock`、`bird`；组合多个同类物体时应自行指定不同ID。组命名空间用ID长度作前缀，避免包含斜杠的身份发生拼接碰撞。几何扰动使用`seed + id + 部件身份`的独立随机流；材料种子仍由最终`StrokeGroupDocument`的种子和笔组身份共同决定。向场景插入其他物体不会重编号已有笔组。

## 树：递归或调用者骨架

默认`start={x:0,y:0}`、`angle=-Math.PI/2`、`size=160`、`width=size*.085`、`depth=3`。角度单位为弧度，坐标朝向与画布一致。`style`可取`dry`或`wet`，只是本模板的接触和墨色选择。

递归深度限制为0–6；每级两个分枝，长度与笔宽递减，深度6最多127条枝。每枝保留主体、少量断续纵向枯擦；部分交点添加沿枝方向的短湿笔，避免给每个节点盖同一个圆点。主体与纵纹末端压力均为0。

临摹时可用`branches`完全替换递归构形：

```ts
const tree = createTreeMacro({
  id: 'authored-tree', seed: 21,
  branches: [
    { id: 'trunk', width: 18,
      points: [{ x: 120, y: 410, pressure: .9 }, { x: 150, y: 210, pressure: .7 }] },
    { id: 'twig', parentId: 'trunk', attachAt: .72, width: 7,
      points: [{ x: 0, y: 0, pressure: .65 }, { x: 60, y: -40, pressure: .2 }] },
  ],
});
```

调用者路径以场景坐标提供。声明`parentId`时，`attachAt`为父枝折线路径的归一弧长，默认`.78`；整条子枝平移到该点，保留各点间相对位置和已写压力。这个交点也加入父枝原始点列，使平滑插值实际经过交点。两者不是只靠父子元数据“看起来相关”。所有主体的最后一点压力明确改为0，其余显式压力保留。子枝省略笔宽时取父枝`.56`，显式笔宽必须小于父枝。未知父枝、循环、重复ID和多于127条枝都会拒绝。

每条枝输出`<branch-id>:root`与`<branch-id>:tip`附着点。任意位置的复杂枝叶交接仍应由作者选择路径和笔序；模板没有植物物理求解。

## 石：骨线、淡面、皴擦与点苔

默认`position={x:0,y:0}`、`size=100`，生成一个不规则八角石面。`outline`可替换外形，`region`限定内部笔的初始裁切，省略时沿用`outline`。这些自有坐标均是绝对场景坐标；`position/size`只作用于默认外形。

`ridges: [{ id, points }]`提供作者的折线与筋脉，`[]`明确取消默认折脊。先输出结构骨线，再输出少量淡笔、破碎折面与疏密点苔。`textureCount`限制0–96，默认14；`moss`默认启用，`mossCount`限制0–32，默认8。`mossAnchors`可自行指定聚点中心；默认选择外形上部节点，点簇主点较重，副点较轻。该默认规则是构图便利工具，不能替代观察某块石的受光与裂缝。

`flow(point)`可控制生成皴笔的局部方向，返回有限、非零的`{x,y}`。方向场仅在造形时求值，结果是普通点列；JSON不保存函数。候选落点采用有界拒绝采样，狭窄或偏离外形的`region`可能得到少于所请求的笔数，不会无限重试。

## 鸟：分部件与显式附着

当前只提供`type:'crow'`研究。`position`默认原点，表示爪的承托位置；`size`默认64。`pose`可取`sitting`、`looking-up`、`looking-down`，`facing`可取`left`或`right`，`angle`为整组旋转角。

部件按`tail → back → wing → head → beak → feet → eye`保存：淡背、局部黑头、断续翅羽、长尾、尖喙和细爪分开编辑。腹部由未落墨区域留白；眼白颜色由`paper`显式指定，默认`#f5f2e9`。更换纸色时应同步传入该值。

`attachment:{point,tangent?}`替换爪的原点，可用切向建立局部横轴，整鸟随之旋转。若希望鸟在斜枝上仍竖直，应只传`point`或另选水平切向；接口不会推断身体姿态或补画承托枝。

`parts`可替换整组部件，例如`parts:{wing:[{id,points,options}]}`。替换笔的点列、宽度、裁切和控制已经处在场景坐标中，宏不再缩放或旋转它们；`[]`明确省略该部件。附着点只约束默认部件；自有爪或身体的连接由调用者负责。每个部件最多64笔，总量受普通笔组文档的512笔限制。

## 保存与组合边界

这些宏不改变旧渲染器或技法默认。可以保存全部宏的`groups/attachments`，或保存编译后的标准笔组配方，再通过`compileStrokeGroupRecipe()`重建。也可按组和逐笔ID做局部修改。跨物体的合成顺序由调用者排列数组决定，整张文档仍最多512笔；分别通过单物体预算不代表组合后一定低于上限。
