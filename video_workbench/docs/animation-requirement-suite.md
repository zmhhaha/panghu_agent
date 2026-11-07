# 跨领域动画需求集

开发者动画工作台以 `cross-domain-requirements-v2` 为当前基线：8个能力模块、27条需求，覆盖10个领域。早期15个固定案例及其标注历史已退役，不再和新需求混用。这里仍是独立开发者 Lab，未接入普通用户的视频制作流程，也不自动更新生产模板。

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

原有五个模块各3条需求，新增三个模块各4条。领域 ID 为 `commerce`、`computing`、`education`、`energy`、`environment`、`healthcare`、`industry`、`logistics`、`science`、`transport`。引擎 ID 为 `html-svg`、`canvas`、`three`、`svg`、`html`；模块和引擎分别筛选，例如 `charts` 的引擎为 `svg`，`interface` 的引擎为 `html`。

每条需求包含受众、表达目标、需要解释的问题、必要实体、预期结果和边界，并附对应文稿、数据及动作。A/B 版本保持相同需求、语义和时钟，比较表达方式。需求不是仅替换标题：例如算法使用不同算法轨迹，Canvas 改变对象数量，跨镜改变镜数与对象关系。数字均为示意输入，不能当作真实业务测量。

当前是经过审校的有限需求池，`seed` 只决定可复现的执行顺序，不会调用模型无限生成新需求。静音音频只提供技术时钟，不证明旁白自然度或真实配音同步。

## 工作台筛选和需求查看

开发者登录后打开 `/dev/animations/`，按模块、领域和引擎筛选，再查看样例的完整需求。检索词和评价状态也可以缩小左侧列表，但不会改变“下载模块／领域／引擎需求集”的下载范围；下载链接显示实际需求数量，空组合不能下载。

服务器接口为 `GET /dev/animations/api/requirements`。例如 `?module=charts&domain=energy&engine=svg&seed=20261107` 只导出能源图表需求。默认本机根路径模式对应 `GET /api/requirements`，两种入口遵循同一导出协议。

## 导出与冻结命令

以下命令从 `panghu_agent` 应用仓库根目录执行，采用 Linux shell 写法。`LAB_RESULTS_DIR` 指向既有实验结果目录，`LAB_RUNTIME_JSON` 指向已采集的真实渲染器运行资源记录，`LAB_ASSETS_DIR` 指向摘要匹配的本地渲染资源目录。输出文件或目录必须尚不存在；重跑时使用新名称，保留原运行结果。

导出全部27条需求，不启动浏览器、模型或 TTS：

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

只读评测目录可能仍存在早期文件。导入时要求案例属于当前目录，且冻结输入携带当前 `corpus_version`；旧 corpus 报告不再进入当前评测列表，也不会重新注册旧快照或旧意见。该检查不等于自动删除磁盘上的历史报告。

清理范围只覆盖这个开发者 Lab 数据库中的 `lab_*` 表，不涉及业务项目数据库、用户项目、音频、视频或其他文件。后续普通标注仍绑定冻结身份；实现或输入改变后，不能把一份画面的意见静默转给另一份。当前发布身份、真实采样与部署验收按实际回执另行补充。
