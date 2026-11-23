# 自然分布与局部积墨

这些工具从包入口导出，核心不依赖浏览器。它们是固定种子的作者工具和密度近似，不会替调用者决定对象结构、笔序或传统技法。

```ts
import { getNoise1D, getNoise2D, getFractalNoise2D, poissonDiskSampling } from 'ink-landscape';

const resistance = getNoise1D(distance / 11 + .173, 37);
const paper = getNoise2D(x / 15 + .37, y / 19 + .71, 13);
const wetBoundary = getFractalNoise2D(x / 28, y / 23, 15, 4);
const anchors = poissonDiskSampling(400, 180, 32, { seed: 9, maxPoints: 30 });
```

`getNoise1D(x, seed=0)`与现有`createNoise1D(seed)(x)`相同；`getNoise2D(x, y, seed=0)`使用二维梯度与五次插值。两者和归一化fBm都输出−1..1，调用顺序不影响结果。坐标不能超过可分辨相邻整数格点的范围，种子以32位键解释。fBm的第四个参数为1–8个倍频层，默认4；每层振幅减半。整数格点上的梯度噪声为零，应选择适当坐标尺度，不能把稀疏整数索引直接当作所有噪声位置。

`poissonDiskSampling(width, height, r, options)`在`[0,width)×[0,height)`内产生距离至少为r的锚点，返回拥有自身对象的冻结数组。默认每个活动点尝试30次，最多4096点；`attempts`可设1–64，`maxPoints`可设1–100000。稀疏空间网格避免按整个画幅分配密集数组，极端格点范围会被拒绝。点数达到预算或活动点耗尽就结束，不承诺最大填充。调用者可以过滤自己的区域，并将锚点映射进山体、枝架或局部坐标。

最小间距适用于组锚。簇内副点、叶片与墨块可以重叠；主次、阴阳面、附枝根和遮挡顺序需要另外显式保存。把所有笔迹都排斥开，会破坏积墨和组内联系。

间距保证属于原采样坐标；缩放或非刚性映射之后，需要重新检验所需距离。整组移动或旋转也不会自动重附枝、重算后叶的可见边段，调用者需要修订交接与遮挡源。

## 接触内部的边缘带

墨层与水事件新增可选`edgeDeposit`和`edgeWidth`，范围均为0..1。省略`edgeDeposit`或设为0时，覆盖算法与旧结果一致，省略字段不会物化进保存源。`edgeWidth`的有效默认值为局部接触半径的.22，设0关闭边缘带。

```ts
const wash = {
  id: 'wash', strokes, concentration: .35, water: .7,
  edgeSoftness: .7, edgeDeposit: .45, edgeWidth: .3,
};
```

`edgeSoftness`控制接触内部渐隐，可能减少覆盖量。`edgeDeposit`在整层接触合成后，根据接触内向深度和三层fBm给局部边缘带分配较多密度，再归一化以保留启用前该层的总覆盖量；薄边加深同时伴随内区略减。相交接触的深内区不单独盖轮廓。它不扩展接触、也不把显式clip自动柔化。湿交换仍由既有有限湿窗和前沿迁移负责，清水本身没有颜料。

`accumulateEdge(alpha, thickness)`是可单独使用的纯边缘带权重：这里alpha表示0（边界）到1（中心）的归一化内向深度，**不是最终颜料透明度**。thickness是半径比例；在带外、深度0或1处返回0，在带内返回0..1。调用者负责选择墨量、噪声门控及是否归一化；单独调用该函数不会绘制、模糊或生成水痕。

所有可选参数均进入普通recipe并支持JSON重建、局部修订和绝对时间重放。模型没有求解蒸发、胶结化学或真实纸纤维；自然外观仍需逐项看图评价。
