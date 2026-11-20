# 散锋、破笔点与抱石皴局部回修

本项改进既有 `splitBristles()` 的接触束组织。显式选择 `dynamics: 'contact-lag-v1'` 后，副毫沿主笔路保留一段方向记忆，在转折后逐渐换向；束的展开随局部压力变化，提笔末段向主毫收束。主毫保持连续，副毫较细、较淡、起得晚、收得早，并在不同区间断墨。

缺省不选择 `dynamics` 时继续使用既有算法，旧输入的输出保持不变。新行为是有界的几何近似，不是毛笔受力、弹性或流体求解。

```ts
import { splitBristles, createStrokeGroupDocument } from 'ink-landscape';

const path = [
  { x: 30, y: 220, pressure: .2 },
  { x: 120, y: 195, pressure: .9 },
  { x: 175, y: 100, pressure: .75 },
  { x: 305, y: 45, pressure: 0 },
];
const options = {
  seed: 20261120, spread: 40, width: 7, opacity: .85, variance: .55,
  dynamics: 'contact-lag-v1' as const,
  lag: 28, release: 1, breakup: .7,
};
const count = 7;
const contacts = splitBristles(path, count, options);
const document = createStrokeGroupDocument([{
  id: 'turning-brush',
  strokes: contacts.map(contact => ({
    id: contact.id,
    points: contact.points,
    options: { width: contact.width, opacity: contact.opacity, dryness: .65 },
  })),
}], { width: 360, height: 280, seed: 20261120 });

// 同时保留原始调用和普通笔组配方；前者可重新生成，后者可逐笔修改。
const saved = { path, count, options, groups: document.recipe };
```

`lag` 是沿主路径向前回看的长度，单位与路径一致，默认 `spread * .85`；`0` 关闭方向滞后。它延迟副毫的接触朝向，不移动主笔路或在拐点添加几何块。`release` 为末段收束强度，范围 0–1、默认 1；`breakup` 为副毫断墨强度，范围 0–1、默认 `.65`。这三个字段只在新模型显式开启时有效，未开启而传入会报错。

横向展开随原始路径压力变化。压力为零的落点在全部接触束中保留为零；断口通过缺失的路径区间和端部提笔表达，不以白色涂盖实现。主毫宽度与不透明度为原始 `width`、`opacity`；副毫的宽度、墨量、起止与断口由独立的固定种子流生成。输出是冻结的普通点列，编辑时复制后调整。

单次仍最多 64 根毫、100000 个输出点，每个输出段最多 4096 点。生成器会加大有效采样间距以限制长路径的工作量。128 点以内的稀疏作者路径保留全部控制位置，较密路径使用有界采样；显式零压力位置仍单独保留。不能把生成点数、段数或某次采样间距当作真实毛笔的物理数量。

## 三项同题与换形

`examples/split-texture-revision-fixtures.mjs` 的 `createSplitTextureRevisionCases()` 导出三项普通笔组文档。每项同时保存旧模型、新模型和两者的换形版本，画幅均为 360×280、种子均为 20261120。原始束路径及完整调用参数另存于 `authoring`，普通笔组配方保存实际生成的路径、样式和稳定标识。

| 项目 | 固定条件与观察 |
| --- | --- |
| 散锋 | 主笔路、压力、束数与材质固定。观察换向后副毫接触朝向逐渐跟进、末端聚拢以及长短断续。换成反向斜势复检。 |
| 破笔点 | 每次接触仅约十几单位长，采用短促按提；比较同一组落点与方向下，压力开合和断白是否形成短触而非长排线。换一组聚散与方向复检。 |
| 抱石皴局部 | 固定岩形、底墨、骨线、聚点和五条主笔路，只替换束接触。顺坡长线与偏向交线保持错位根部和少量交叠，避免同心放射。换成倾斜反向岩形复检。 |

断口将一根副毫拆成多个可编辑段，因此新旧总笔段数不同，主束数和主笔路数量相同。增加段数不表示额外添加主束或统一叠黑。

破笔点在本项仅为分束短触研究，不等同破墨。抱石皴有独立的画家与综合用笔语境；这里改进其中散锋和线、面、点组合的一部分，不以一个噪声笔刷代表傅抱石画风。传统资料边界沿用[第十一批](pepper-broken-pine-review.md)及[新增墨法研究](remaining-ink-review.md)。

专项检查覆盖旧输出快照、显式版本、原始主笔路、方向记忆、压力开合、提笔收束、刚体换形、细窄提笔、JSON 重建、逐段修改隔离与预算。实际画面仍需在浏览器中与上述基线及换形逐项查看。
