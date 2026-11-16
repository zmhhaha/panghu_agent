# 跨领域动画需求集

2026-11-08：用户指出同一机制换内容对创作能力优化价值有限。后续以[能力实验设计](animation-capability-lab-redesign.md)为准，将这套固定配方需求降为参数/技术回归，不再用领域或样例数量表示表现力进步。本页保留当前实现、导出命令和真实发布记录；目录迁移尚未执行。

开发者动画工作台当前代码目录以 `cross-domain-requirements-v2` 为基线：11个能力模块、39条需求，覆盖13个领域。早期15个固定案例及其标注历史已退役，不再和新需求混用。本轮从27条扩为39条，沿用同一 corpus 版本并保留已有 v2 标注；新增样例的实际采样与发布状态应以对应回执为准。这里仍是独立开发者 Lab，未接入普通用户的视频制作流程，也不自动更新生产模板。

## 样例覆盖

| 模块 ID | 能力 | 数量 | 需求变化示例 |
| --- | --- | --- | --- |
| `diagram` | 框图与信息图 | 3 | 雨水利用、学习反馈、订单履约；节点数、关系和结论保留不同 |
| `process` | 二维过程动画 | 3 | 包裹正常转运、实验样本复检、设备工单完成；返回与重试路径不同 |
| `continuity` | 跨镜衔接 | 3 | 包裹交接、三镜作业修订、凭证副本处理；镜数及保留/复制语义不同 |
| `canvas` | Canvas 2D | 3 | 门诊排队、仓库拣货、设备采样；4/8/12个对象及输入节奏不同 |
| `three` | Three.js 三维动画 | 3 | 电商、学校、工厂平台；3/4/5个层级及职责不同 |
| `charts` | 动态图表联动 | 4 | 系统积压、能源供需等；数据、单位、趋势及守恒关系不同 |
| `algorithms` | 算法与数据结构 | 4 | 商品选择排序、图书二分查找、地铁广度优先搜索、设备 LRU 缓存 |
| `interface` | 软件界面操作 | 4 | 图书检索、商品筛选等；输入、选择、结果和提示状态不同 |
| `math` | 数学公式与符号推导 | 4 | 商品单价方程、三角形面积、检测条件概率、车辆单位换算；题设与推导规则不同 |
| `spatial` | 三维剖切与装配 | 4 | 螺栓连接、建筑剖面、地层展开、电池封装；实体、裁剪轴与装配语义不同 |
| `fields` | 波形、粒子与场 | 4 | 声波叠加、电偶极方向场、河道示意粒子、传感器均值滤波；计算与观察方式不同 |

原有五个模块各3条需求，其余六个模块各4条。领域 ID 为 `architecture`、`commerce`、`computing`、`education`、`energy`、`environment`、`geology`、`healthcare`、`industry`、`logistics`、`mechanics`、`science`、`transport`；本轮新增机械、建筑、地质三个领域，健康科普与门诊样例统一使用 `healthcare`。引擎 ID 为 `html-svg`、`canvas`、`three`、`svg`、`html`；模块和引擎分别筛选，例如 `math` 使用 `svg`，`spatial` 使用 `three`，`fields` 使用 `canvas`。这些统计来自 `catalog()` 与 `coverage()`，不是根据模块名称推测覆盖。

每条需求包含受众、表达目标、需要解释的问题、必要实体、预期结果和边界，并附对应文稿、数据及动作。A/B 版本保持相同需求、语义和时钟，比较表达方式。需求不是仅替换标题：例如算法使用不同算法轨迹，Canvas 改变对象数量，跨镜改变镜数与对象关系。数字均为示意输入，不能当作真实业务测量。

当前是经过审校的有限需求池，`seed` 只决定可复现的执行顺序，不会调用模型无限生成新需求。静音音频只提供技术时钟，不证明旁白自然度或真实配音同步。

## 本轮新增的表达能力

每条新增需求均包含完整文稿、结构化受众与目标、明确的数据和前提，A/B 使用相同题设、结论与时长。数学样例以公式项和图形的实际变化解释推导；空间样例使用真实 Three.js 网格和裁剪材质；场与波形使用实际 Canvas 绘制和固定数据。它们分别验证不同需求，不用一个模板换标题代表所有领域。

| 案例 ID | 领域 | 真实表达与独立核验 |
| --- | --- | --- |
| `math-commerce-balance` | 电商与消费 | `3x+6=30` 两边同减6、同除3，移动实际符号项，代回检验 `x=8` |
| `math-geometry-triangle` | 几何与教育 | 同比例12×8矩形沿对角线分成两块全等三角形；核对真实路径、平移与48 cm²结论 |
| `math-health-bayes` | 健康科普 | 同一千人总体推导9个真阳性与99个假阳性；实际条宽保持9:99，后验分母为108 |
| `math-transport-units` | 交通与物理 | 在实际SVG单位项中消去km和h，把72 km/h换为20 m/s，再由匀速3 s得到60 m |
| `spatial-bolt-assembly` | 机械装配 | 带孔板、螺栓、垫圈与螺母同轴装配，真实裁剪平面进入连接处 |
| `spatial-building-cutaway` | 建筑空间 | 切去前半外壳，再抬起屋顶与楼板；竖向支撑保持固定，最后复位 |
| `spatial-geology-section` | 地质与环境 | 四层真实网格剖切、展开与复位，保持层序；剖面封口对应当前裁剪平面 |
| `spatial-battery-teardown` | 电池封装 | 上盖、绝缘板、四个圆柱电芯与连接排分层观察，按原位置复位 |
| `field-acoustics-interference` | 教育与声音科普 | 同横坐标逐点相加两列波，改变相位，比较同相增强与反相抵消 |
| `field-electric-dipole` | 科学与物理示意 | 从固定正负源计算有限网格的归一化方向箭头，区分场方向与粒子轨迹 |
| `field-river-particles` | 环境与河道示意 | 固定种子的18个对象按预设速度平流，保留身份并解释显示循环边界 |
| `field-sensor-waveform` | 工业与传感器 | 65个固定示意采样及三点均值，游标联动同索引；不将平滑称为更准确 |

数学独立 oracle 使用 Python 有理数与几何计算，核对浏览器真正测得的公式文本、分数横线、单位、`d`、坐标及 `transform`，不以 JavaScript 自报的正确答案代替画面。空间原生证据需要包含实际网格、几何尺寸与孔、世界矩阵、相机投影、材质裁剪平面、渲染调用及 WebGL 像素探针。场与波形原生证据需要包含实际 Canvas 绘制、`getImageData` 像素和笔画采样、文字边界，并与独立 Python 几何计算对应。

三类均通过绝对时间重建画面，原生采样还需检查动作窗口、首末帧与严格归零。DOM说明文字变化不等于Three.js或Canvas画布真的改变，必须同时取得对应原生证据。有限技术采样不认证科学或工程有效性，也不自动核准审美、真实旁白时序或生产准入。

## 工作台筛选和需求查看

开发者登录后打开 `/dev/animations/`，按模块、领域和引擎筛选，再查看样例的完整需求。检索词和评价状态也可以缩小左侧列表，但不会改变“下载模块／领域／引擎需求集”的下载范围；下载链接显示实际需求数量，空组合不能下载。

服务器接口为 `GET /dev/animations/api/requirements`。例如 `?module=charts&domain=energy&engine=svg&seed=20261107` 只导出能源图表需求。默认本机根路径模式对应 `GET /api/requirements`，两种入口遵循同一导出协议。

## 导出与冻结命令

以下命令从 `panghu_agent` 应用仓库根目录执行，采用 Linux shell 写法。`LAB_RESULTS_DIR` 指向既有实验结果目录，`LAB_RUNTIME_JSON` 指向已采集的真实渲染器运行资源记录，`LAB_ASSETS_DIR` 指向摘要匹配的本地渲染资源目录。输出文件或目录必须尚不存在；重跑时使用新名称，保留原运行结果。

导出全部39条需求，不启动浏览器、模型或 TTS：

```sh
python -m video_workbench.animation_lab_matrix export \
  --destination "$LAB_RESULTS_DIR/requirements-v2.json" \
  --seed 20261107 --module all --domain all --engine all
```

导出一个明确的跨领域子集：

```sh
python -m video_workbench.animation_lab_matrix export \
  --destination "$LAB_RESULTS_DIR/energy-charts-v2.json" \
  --seed 20261107 --module charts --domain energy --engine svg
```

冻结全部需求及其 A/B 编译产物，为后续实际采样生成输入：

```sh
python -m video_workbench.animation_lab_matrix prepare \
  --destination "$LAB_RESULTS_DIR/requirement-suite-v2" \
  --runtime "$LAB_RUNTIME_JSON" --engine-assets-dir "$LAB_ASSETS_DIR" \
  --seed 20261107 --module all --domain all --engine all
```

`prepare` 写入 `suite.json`、`prepared.json` 和各案例的冻结子运行；它不执行采样。`LAB_RUNTIME_JSON` 应来自准备采样的渲染器，不能用网页预览的 runtime 或虚构版本替代。包含 Three.js 的集合还要求提供匹配其固定摘要的本地模块资源。选中的组合没有需求、资源不匹配或输出已存在时明确失败，不自动换方案或重试。

数学与场样例复用本地 GSAP 和 Noto CJK 字体；空间样例还使用固定的 `three@0.170.0` 与本地 `three.module.min.js`。编译清单的 `required_assets`、`engine_assets` 记录实际资源及摘要，冻结与采集时分别复核。运行时不依赖公网 CDN，也不因目录中有同名文件就跳过字节校验。空间样例的初始化与原生状态检查由受限引擎宿主负责，Canvas与WebGL采集使用对应上下文；普通业务的编译器准入保持独立。

真实采样后才能收集技术证据。只有准备输入、没有真实采集回执与图片的目录不能生成技术通过结果或视觉评审请求。技术结果、模型意见、开发者偏好和生产采用分别记录；下载或冻结需求不代表任何一项已通过。

## 可复现身份

导出协议为 `video.animation-lab-requirement-suite.v2`。

每个需求通过 `sample_identity` 绑定输入及两个版本的编译产物；CLI和网页导出的同一样例使用相同值。网页导出还可带 `lab_snapshot_identity`，用于定位当前开发者数据库中的快照。两个字段不能互换，需求集不再使用含义混杂的 `case_identity`。

需求集提供两种摘要：

- `semantic_identity` 绑定种子、筛选、需求、文稿、输入摘要、样例身份及编译产物。相同需求和产物在CLI与网页保持一致；运行环境与数据库快照不参与此摘要。
- `identity` 绑定完整导出，包括运行环境和可选的工作台快照。因此网页与CLI的完整导出摘要可以不同。

`export_scope` 表示导出来自编译样例或带工作台快照的样例。它不表示采样已经执行。下载需求集与 `prepare` 都保持 `prepared_not_executed`，没有模型、TTS或生产写入。

准备执行时，每个子运行重新核对冻结的输入、`sample_identity` 和A/B产物；准备回执为 `video.animation-lab-suite-preparation.v2`，同时记录需求集的两种摘要。实际采集数量从冻结的子运行读取，不根据样例名称推算。后续技术结果、人工意见和采用状态各自记录。

## 新基线与标注保留

首次使用新 corpus 启动时，工作台只在专用 `animation-lab.db` 内重建 `lab_*` 表，清除旧样例快照、标注、撤回、评测分诊和迭代任务等 Lab 历史，并记录 `cross-domain-requirements-v2` 标记。这是一次性基线切换，不是每次启动清空。之后保持相同 corpus 版本重启，新的快照、标注和迭代记录继续保留。

本轮11模块、39条需求的扩展继续使用相同的 `cross-domain-requirements-v2`，不是再次重建基线。已有v2样例的快照、有效标注与迭代记录应保留；新增样例各自注册冻结身份。确有输入或产物变化时仍产生新身份，不能把另一幅画面的意见自动迁移过来。

只读评测目录可能仍存在早期文件。导入时要求案例属于当前目录，且冻结输入携带当前 `corpus_version`；旧 corpus 报告不再进入当前评测列表，也不会重新注册旧快照或旧意见。该检查不等于自动删除磁盘上的历史报告。

清理范围只覆盖这个开发者 Lab 数据库中的 `lab_*` 表，不涉及业务项目数据库、用户项目、音频、视频或其他文件。后续普通标注仍绑定冻结身份；实现或输入改变后，不能把一份画面的意见静默转给另一份。当前发布身份、真实采样与部署验收按实际回执另行补充。

下方为首轮27条需求的已完成发布记录，保持当时的源码、镜像、采集数量和结果。不能用它证明本轮新增12条需求已经采样或上线；本轮扩展需按新的冻结输入和实际回执另行记录。

## 2026-11-07 实际发布与验证

已部署到 `https://video-service.panghuer.top/dev/animations/`。源码为 `8d00bc8345b421e4215ac493a83a5e4a81f0100b`，开发分支为 `codex/video-cross-domain-animation-matrix`，未合主线。独立 ARM64 Lab 镜像为 `arm-cluster-master:5000/video-animation-lab:latest@sha256:b43f3770fc262af96910763ae71b785e5c1691e605d255b407b6c7b7c39d21d6`，177,182,737 字节；构建上下文 SHA-256 为 `630d57427005a1459c7f2b47919d63bc9e20bc8b74e7c399d4eef8b1ecc10767`。实际 Pod imageID、镜像 revision label 和 Deployment 注解均绑定该版本。业务视频 Deployment spec 保持原值。

真实原生采样最终覆盖27条需求、54个A/B版本、62个独立片段及1,747张回执绑定的JPEG，逐例核对当前样例身份、输入、产物和观察器摘要，技术检查全部通过。初轮62次采集有6个失败案例：3个历史过程基线缺少完整首帧/归零DOM状态，3个跨镜案例的空详情文本框被检查器误判。补齐开发者专用观察路径后，为3个过程案例另采6段；跨镜使用原始图像重审，不重复采集。实际共68次采集，隔离容器均确认清理；原失败报告完整保留。没有模型、Agent或TTS调用，没有重新编码MP4。

定向Python回归覆盖各家族、独立几何/语义oracle、基线重置、旧报告与候选旁路拒绝、HTTP/CSRF、反馈/runner及冻结适配器。关键分组分别有333项、62项、121项及迁移/权限检查通过；这些分组存在交集，不作为独立总数相加。工作台Node28项、过程/连续观察器Node25项通过。Windows HTTP连接中断与本地长路径解包失败的首轮现场保留，后续使用同一冻结输入和原始采集归档完成核验，不重写为首次成功。

服务器后端实际检查27条当前目录、54条可用预览、需求集v2及CLI/HTTP一致的 `semantic_identity=cc8b88cdb846473ac1e944f4372b56449882b7874dbbdd7ea3be0bd592365840`。首次上线后专用数据库只有27个新样例快照、1个当前corpus标记；旧标注、评测分诊和迭代任务均为0，旧公开预览和报告URL被拒绝。原只读报告目录不再导回旧数据。

登录浏览器验收包括目录/覆盖、需求说明、筛选空态及恢复、真实HTML商品筛选排序、双侧定位/归零，以及明确标记为技术用途的标注保存。相同镜像重启后，新Pod核对该标注原始文档SHA完全一致、快照仍27条，旧历史未复活；随后撤回技术记录，避免将其当作用户效果意见。本地浏览器另核对五层真实WebGL展开及领域职责。需求下载由实际服务器HTTP核对内容与身份，浏览器核对同源前缀链接，不把链接检查记为浏览器全量下载。

证据统一位于既有忽略目录 `video_workbench/rendered/feedback-20261106/animation-lab/`：`auto-evaluation/scenarios-final-native.json`、原始及修复后技术报告、`server-deploy/matrix-*` 发布/后端/持久化回执。Windows短路径采样工作目录位于既有 `.research-temp` 范围，仍归后续统一临时文件清理任务。技术采样通过不等于人工审美批准或真实旁白同步，也没有启用真实Agent到GitHub PR的自动执行环境。

## 2026-11-08 三类新能力的发布与验证

本轮新增12条需求，目录扩为11模块、39条需求、13领域，已部署到同一开发者入口。源码为 `944b6acd0ce2d52f1ff39fec3e46aa43d793402a`，分支 `codex/video-math-spatial-field-capabilities` 已推送，未合主线。ARM64镜像为 `arm-cluster-master:5000/video-animation-lab:latest@sha256:a72f1303f7f5456f4e6b055689568d2b9e09ee148333e3083cc987eb88a52b8f`，177,295,611字节；构建上下文SHA-256为 `6014631c68a32a677ba5fe8d88e7104846d672e94cdbb8afcea8dd4b2d07d77d`。实际Pod imageID、revision注解和label、60个运行源文件及4份固定资产均与构建内容一致。

新增12例最终全部通过真实ARM Chromium采样与独立数据/几何/像素检查，最终绑定24个A/B片段、360张JPEG。实际共执行26次采集，所有容器均确认清理。初轮4例技术失败的记录保留：3个fields例的请求分数帧时间与GSAP实际六位小数绘制时钟存在不足一微秒差异；现在先严格绑定实际时钟，再计算预期几何，原120条Canvas原生样本复核通过，像素与几何门禁未放宽。螺栓剖切后模型确实过小，改为随装配平滑拉近镜头（zoom从1到2.2），仅为该例另采两侧，末帧实测91/1100个前景样点、42种颜色且无GL错误。24次初轮及2次复测的原始回执、图像与检查分别保留，不覆盖失败报告。最早的冻结输入在发起采样前因标记遮挡修正而替换；调度时超过210秒上限的参数也在创建容器前被拒绝，未产生额外采集。

最终针对性Python组293项通过；公共资源/HTTP/反馈等8文件组247项、runner组59项、矩阵扩展组8项及目录编译组34项通过，分组有交集，不相加为独立总数。工作台Node28项通过。原生检查包含实际SVG公式/单位/图形，真实Canvas绘制与像素，以及Three.js网格、部件矩阵、剖切平面、相机投影、GPU读回和反向归零。样例采集没有调用Agent、模型或TTS，也没有编码MP4。

发布前对专用SQLite做在线备份，服务器私有release目录保留完整备份，SHA-256为 `58a9785868f807ef014cff67276d72faa3c4825170b18a8d058c173924437f70`。继续使用同一corpus版本，原27个快照、1条已撤回的技术标注及其撤回事件逐列摘要均保留，新样例只追加记录。后端核对39条当前需求、78个可用预览、未登录或非开发者身份403、20份挂载旧报告仍排除。需求集 `semantic_identity=8a04225520cdcfe980dd5774f52994f92e71dee1550712521f01189f79402822`；业务视频Deployment spec保持原值。

登录浏览器已检查三个新模块的代表画面、双侧就绪、动作检查点和归零：几何面积推导得到48cm²，声波相位推进到180°，螺栓模型随装配放大并显示真实剖面。新增fields技术标注保存后真实刷新仍可读取，随后撤回；服务器回读核对39个快照、全部原有行保留，技术记录不计入有效效果意见。技术通过仍不表示用户已认可审美效果或真实旁白时序；这些能力当前供独立开发者工作台分别改进，普通用户视频生成不自动采用。

本轮汇总为既有忽略目录中的 `auto-evaluation/extended-final-native.json`，原失败/复核/螺栓复测记录为 `extended-native-r2-review.json`、`extended-fields-recheck.json`、`extended-native-r3-review.json`，发布回执为 `server-deploy/extended-*`。短路径冻结证据位于既有 `.research-temp/ext-native-*`，服务器备份位于 `/root/video-process-20261105/feedback-20261106/animation-extended-release/944b6acd0ce2d52f1ff39fec3e46aa43d793402a/`；仍纳入此前约定的整体清理计划。
