# 可编辑皴笔文档

`CunDocument`保存调用者逐笔组织的原始路径、稳定标识、接触覆盖和公共设定，编译为普通`InkScene`；Node中可创建、修改、保存和重编译，浏览器继续使用`renderCanvas`绘制。第六批研究披麻、斧劈和折带，见[样片](../examples/cun-study.html)与[记录](cun-review.md)；第七批研究解索、乱柴和牛毛细线形态，见[样片](../examples/line-cun-study.html)与[记录](line-cun-review.md)；第八批研究环抱线、短点与分歧筋脉，见[样片](../examples/branch-cun-study.html)与[记录](branch-cun-review.md)。每批三组合成一张对照总览，艺术最后统一评价。牛毛、卷云／云头及雨点／豆瓣的部分支持范围不因新增接口而升级。

## 创建、修改与保存

```ts
import {
  createCunDocument, reviseCunDocument, compileCunRecipe, renderCanvas,
} from 'ink-landscape';

const document = createCunDocument([
  {
    id: 'slope-main',
    points: [
      { x: 95, y: 45, pressure: .3 },
      { x: 126, y: 88, pressure: .85 },
      { x: 115, y: 142, pressure: .7 },
      { x: 152, y: 200, pressure: .15 },
    ],
  },
  {
    id: 'slope-crossing',
    points: [
      { x: 144, y: 62, pressure: .25 },
      { x: 119, y: 108, pressure: .8 },
      { x: 150, y: 157, pressure: .55 },
      { x: 177, y: 177, pressure: 0 },
    ],
    options: { width: 4, dryness: .58, start: .5, duration: 1.5 },
  },
], { technique: 'pima', width: 400, height: 240, seed: 261124 });

const revised = reviseCunDocument(document, {
  strokes: document.recipe.strokes.map(stroke => stroke.id === 'slope-crossing'
    ? { ...stroke, points: stroke.points.map(point => ({ ...point, x: point.x + 12 })) }
    : stroke),
});

const saved = JSON.stringify(revised);
const restored = JSON.parse(saved);
const scene = compileCunRecipe(restored.recipe);
// context由浏览器调用者提供；绝对时间与纸纹由绘制端指定。
renderCanvas(context, scene, { time: 2, paperTexture: .04 });
```

源笔组中的错落、交搭、长短及石面关系由作者设计。示例只说明数据如何创建和修改，不是完整披麻配方；改变`technique`也不会自动把同一构图变成另一种传统皴法。需要坡面方向场时，可由调用者先采样成显式路径，再保存到文档；本配方不序列化函数或预置山形。

## 文档与覆盖责任

| 接口或字段 | 作用 |
| --- | --- |
| `createCunDocument(strokes, options)` | 从显式皴笔及技法设定创建文档。 |
| `compileCunRecipe(recipe)` | 从版本化原始笔组重新生成普通场景。 |
| `reviseCunDocument(document, { strokes?, options? })` | 返回新文档；笔数组整体替换，公共选项浅合并。 |
| `document.recipe` | 版本`1`、生成器`ink-cun-guides-v1`、原始`strokes`与公共`options`。 |
| `document.result` | 版本`1`的普通`InkScene`，每笔仍为可保存、检查的`StrokeMark`。 |

每条`CunStrokeSource`包含必填`id`、`points`及可选`options`。`points`为普通`InkPoint[]`，原有压力与零压力离纸段继续参与生成。源与生成结果分别保存；局部动作可以改变生成路径，不能把结果路径反当作者原始骨线。

逐笔`options`沿用`BrushProgramOptions`，但禁止`id`、`ink`、`seed`和`gestureModel`：身份由源顶层`id`管理，种子由公共种子及笔ID稳定派生，墨色由公共选项管理，本生成器固定使用`local-v1`。线宽、透明度、枯润、起收、转折、局部`controls`、`clip`及`start / duration`等可逐笔设置。

逐笔配置覆盖技法与公共默认值。提供`controls`时整个数组替换技法的默认局部控制，不在背后追加另一套控制；各稀疏通道仍按[基础笔法接口](foundation-brush.md)的规则插值，压力乘以原始路径压力。作者显式覆盖后可能不再保留某个技法默认表现，应按实际配置判断。

| 公共选项 | 含义 |
| --- | --- |
| `technique` | 必填`'pima' / 'axe' / 'zhedai' / 'jiesuo' / 'luanchai' / 'niumao' / 'cloud-study' / 'point-study' / 'heye'`，选择接触与局部动作默认；不自动生成对应的笔组组织。 |
| `width / height` | 必填场景尺寸，不是单笔线宽。 |
| `seed` | 默认`1`，与稳定笔ID共同确定逐笔种子。 |
| `background / ink` | 默认`#f5f2e9 / #20251f`；只接受六位十六进制`#RRGGBB`，保存与生成时归一为小写。 |
| `strokeWidth / dryness / opacity` | 公共单笔控制，省略时采用下表的技法默认。 |
| `strength` | 默认`1`，控制`local-v1`几何动作强度；设为`0`不会同时关闭默认接触控制。 |

| 技法默认 | `strokeWidth` | `dryness` | `opacity` | 当前执行范围 |
| --- | --- | --- | --- | --- |
| `pima` | 6 | .48 | .74 | 沿作者曲线组织细韧线的提按与接触；不自动布置错落交搭。 |
| `axe` | 26 | .55 | .84 | 侧用、较重起接及扫拖收窄；横扫方向、大小与石面由作者选择。 |
| `zhedai` | 13 | .60 | .75 | 保留作者方折骨线并组织局部侧用和轻重；层理与下接关系由作者组织。 |
| `jiesuo` | 5 | .52 | .76 | 中用接触与局部提按；作者提供屈曲骨线、穿插和松紧关系，不从直线自动卷曲。 |
| `luanchai` | 7 | .66 | .76 | 直折笔路保折与轻重接触；粗密暗处、疏细明处和交错方向由作者指定。 |
| `niumao` | 3 | .45 | .66 | 细轻接触的形态研究；曲线、长短、留脉和交叉比例由作者给出，尚无可靠独立笔序验证。 |
| `cloud-study` | 6 | .55 | .74 | 作者环抱曲线上的轻中侧接触与提按研究；开面、前后搭接及留白由作者组织，不裁定卷云与云头的名称边界。 |
| `point-study` | 10 | .48 | .80 | 作者短笔路上的轻触、按腹与收提接触研究；方向、长短、阴阳聚散由作者指定，不以数值界线划分雨点与豆瓣。 |
| `heye` | 7 | .50 | .76 | 作者筋脉笔路上的上重下轻和开收；峰顶、坡面、分歧、主次及山脚留气由作者给出。 |

上述数值是本库当前版本的程序设定，不是实测压力、水墨配比或传统技法通用标定。长短披麻、大小斧劈不能仅凭线长或缩放倍数获得传统范围认可。

第七批的`jiesuo / niumao`默认沿作者骨线用`catmull-rom`采样及`preserve`转折，`luanchai`默认`linear / angular`保留直折。细线、屈曲和直折的区别同时依赖作者源路径，不能只切换名称便声称获得另一种传统皴法。牛毛的细短、曲率与疏密是假设性观察变量，不把它与解索划出统一数值界线；三项仍为显式选用、艺术未验收的原型。

第八批三项默认均使用`catmull-rom / preserve / open`，沿用`local-v1`的平滑采样和开收，不提供真实运腕。`cloud-study`与`point-study`是共享研究入口：卷云、云头各自的资料记录保留，雨点、豆瓣也各自保留；共用接触基础不表示已证同义，也不虚构两套互斥预设。`heye`保留普通独立源路径，父子筋脉、接点、遮让和笔序由作者组织；文档不含自动分支拓扑或固定山形。

## 编辑、时间与绘制边界

文档拥有原始输入的独立冻结副本，外部继续改动输入不会改变既有文档。修订从源重新生成，不反推保存结果。移动或改宽一笔时保留其ID，可以保持其余笔的源、编译数据和材质种子；数组顺序仍决定作者的叠压与播放顺序。

最多512条源笔路，每条最多4096个输入点，逐笔`clip`最多4096个顶点；局部控制沿用含自动首尾端点的128个上限。折带默认最多为路径上前42个有效折角各生成三处局部控制，再加首尾控制以满足该预算；全部路径顶点仍保留，后续折角不会被删除。作者提供的`controls`整体替换这套默认控制。未知版本、生成器、不支持字段、重复ID和非法配置应拒绝，不静默截断或改用另一技法解释。

每笔`start / duration`继续采用普通`StrokeMark`绝对时间语义。固定输入、种子和绝对时间可乱序重放，绘制核心不读取墙钟或`Math.random()`；它不模拟实际行笔速度、驻笔或纸内干燥。逐像素重放还需固定代码版本、画布、浏览器及`paperTexture`等绘制选项。

这些皴笔文档均使用普通Canvas接触绘制，逐笔`clip`对最终该笔绘制生效；没有第四批湿态交换把墨搬出区域的步骤。它也不因此提供真实纸性、墨粒流动或物理笔毫。数据与几何核心继续可脱离Canvas使用。

## 与旧皴擦原型的关系

`builder.cun(outline, options)`仍是原有区域生成接口，保留其`pima / axe`、方向场和参数语义。本批另用作者显式骨线建立可编辑文档，没有把旧均匀布笔结果换名为已经成立的传统皴法，也不自动迁移旧场景。

本库执行笔路、提按、接触和材质，作者负责山石结构、疏密、交搭、留白及组合。传统名称只描述当前研究的程序近似，艺术适用范围待最后统一评价。
