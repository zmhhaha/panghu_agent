# 可编辑源的微扰工具

`createNoise1D`、`jitterPath`、`varyPressure` 与 `splitBristles` 是纯 TypeScript 的可选作者工具，从包入口导出。它们使用显式种子，不读取时间或全局随机状态，不改变已有渲染默认值。输出的点、压力、束线宽度与透明度都可直接保存进普通笔组源；保存生成数据后，重建不再依赖隐藏噪声场。

```ts
import { sampleStrokePath, jitterPath, varyPressure, splitBristles } from 'ink-landscape';

const sampled = sampleStrokePath(source, { spacing: 2, smoothing: 'catmull-rom' });
const textured = jitterPath(sampled, .4, {
  seed: 37, wavelength: 8, tangentRatio: .1, preserveEndpoints: true,
});
const points = textured.map((point, i) => ({
  ...point,
  pressure: varyPressure(point.pressure ?? 1, .25, {
    seed: 37, at: sampled[i]!.distance, wavelength: 19,
  }),
}));
const bundle = splitBristles(points, 7, {
  seed: 39, spread: 20, width: 5, opacity: .8, variance: .35,
});
const strokes = bundle.map(part => ({
  id: part.id, points: part.points,
  options: { width: part.width, opacity: part.opacity },
}));
```

| 工具 | 数据约定 |
| --- | --- |
| `createNoise1D(seed)(position)` | 有界在 −1..1 的一维梯度噪声，位置以安全整数格点范围为限；同一位置的结果与调用顺序无关。 |
| `jitterPath(points, intensity, options)` | 按弧长相关的法向／切向位移，总位移不超过 intensity。不插点，不更改压力；默认两端渐隐并保持端点。wavelength 使用路径坐标单位，默认12。 |
| `varyPressure(base, variance, options)` | 以 base 为基准的相对连续变化，并裁在0..1；原始零压力始终为零。at 是显式绝对位置，wavelength 与 at 使用相同单位。 |
| `splitBristles(path, count, options)` | 中心较重，外束短长、偏角、宽度、墨量与断口不同。count 指束数，断口拆成独立 part 后返回条数可能更多；各 part 有稳定ID。最多64束，单笔不超过4096点，全束点预算100000；过多的显式零压力锚点会拒绝生成。 |

所有变换都返回拥有自身点对象的冻结数据。调用者修改时建立副本，或通过现有文档修订接口保存新源。路径微移前应先选择适当采样密度；仅抖动几个稀疏控制点无法产生细小摩擦。固定种子是可复现的程序不规则性，并不等于真实纸纤维、笔毫受力或流体模拟。

默认保留锚点只表示端点位置。分支的共享根、叶片与枝干的结合、对象形态和笔序仍需调用者显式组织。负面对照可以保持规整，机制与组合样片再逐项使用微扰，避免所有部位无差别加噪。
