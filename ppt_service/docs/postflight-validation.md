# 独立 postflight 程序核验合同

本模块补充已实现的独立预览重绘、PPTX delivery 审计和原生图表数据检查：在新建副本上重新运行官方项目校验、final SVG quality，并审计这批 SVG 的资源与完整来源指纹。结果用于积累可核查的程序证据，不能授权发布生成或修订版本。

本文对应 Hermes PPT 工作台的 P3 子项。模块可独立测试；当前控制器、发布器与网站没有接入该模块。`independent_postflight_and_delivery` 仍为 pending，父任务不得仅因本模块完成而勾选。

## 固定依赖与调用合同

官方 PPT Master 固定为 `c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7`，镜像固定为 `sha256:6e9bdb213f75120171d712e9bd037759cedfce6aced2caff0dae01d5fa08583a`。官方源码在镜像内 `/opt/ppt-master/skills/ppt-master/scripts/`；其原始许可、归属与完整性检查继续保留。官方脚本来自镜像，不得从上传文件或 Worker 项目中选择程序执行。

独立入口的逻辑接口为：

```python
run_postflight(
    snapshot,
    completed_manifest,
    request,
    trusted_oracle,
    authorized_source_sha256,
    output,
    prior_evidence=prior_evidence,
) -> PostflightEvidence
```

调用方必须提供以下可信输入：

- `snapshot` 是 Worker 及其所有写入进程已停止后收集的私有只读快照。`completed_manifest` 是控制器核对实际文件后补齐的文件索引。
- `request` 中的 `validation_nonce` 由控制器生成；`source_manifest_sha256`、`input_snapshot_sha256` 必须与本次实际输入一致。请求方传来的成功标志不是证据。
- `trusted_oracle` 来自控制器批准的数据来源，包含 `expected_charts` 与 `source_sha256`；`authorized_source_sha256` 是授权输入材料的哈希集合。存在图表预期时，来源集合必须非空且全部获得授权。对图表数据的核验不等于对全部叙述事实的核验。
- `prior_evidence` 必须是同一次核验所签发的 `ProgrammaticEvidence` 实例，已完成独立 raster、delivery 和 native 检查。模块核对其 issuer、完整性封印、nonce、manifest SHA、输入 SHA、实际源文件、PPTX 哈希及 `expected_charts` 规范化哈希，拒绝 JSON 仿造、跨运行复用和被修改的证据。
- `output` 必须是新目录。不能使用 Worker 的旧报告、旧预览或已存在的验收输出作为本次结果。

测试可注入仅用于离线验证的命令适配器。生产调用必须在已限定资源、无外部网络的核验沙箱内执行固定官方程序；Python 入口自身不创建容器或网络隔离。

## 官方检查与本模块的对应关系

以下路径相对官方 `skills/ppt-master/scripts/`，描述固定版本的实际行为。

| 官方检查或实现 | 本模块的核验与证据 | 保证范围 |
| --- | --- | --- |
| `attribution_guard.py`、`project_manager.py validate` | 在新副本上运行固定程序，保存退出码与日志；失败拒绝 | 固定官方规则的归属与项目结构检查 |
| `svg_quality_checker.py --stage final --json` | 在新副本生成 `validation/svg_quality_report.json`；核对 schema、final 阶段、目标路径、逐页 roster、errors、blocking 与警告分类 | 当前源文件的程序质量规则，不是逐页视觉判断 |
| `svg_quality/checker.py::_quality_source_fingerprint` | 独立计算每页 SHA 与聚合指纹，核对报告中的完整 `files`、页数和聚合值 | 报告绑定到本次实际检查的 SVG 字节 |
| `svg_to_pptx/pptx_package/cli.py::_svg_source_fingerprint` | 与上游相同算法：按 basename 排序，逐项追加 UTF-8 文件名、NUL、ASCII SHA-256、LF，再取 SHA-256 | SVG 指纹互通；本地图片等资源另行按文件哈希绑定 |
| `svg_to_pptx/pptx_package/cli.py::_quality_report_context` 与 `_quality_gate_status` | 使用上游规定的 final 报告路径，并核对完整文件映射；不只接受其算法、聚合哈希和数量相等 | 拒绝错源、缺页、多页、旧报告与不完整质量报告 |
| `svg_to_pptx/pptx_package/cli.py::_source_resource_audit` | 对当前 SVG 重新运行模板占位符、字体和图片审计；保留资源计数与问题项 | 程序可识别的资源和可移植性问题；不保证目标 PowerPoint 字体可用 |
| `pptx_delivery_check.py` | 复用并核验本次 `ProgrammaticEvidence` 内独立执行的 delivery 结果与 PPTX SHA；保留 advisories | 当前文件的 ZIP、关系、页数、隐藏页、备注、动画等交付检查 |
| 服务自有 `worker/candidate_validation.py::inspect_pptx` | 复用并核验本次 native 结果，绑定可信 oracle 及其来源 | 原生图表、嵌入 XLSX、缓存数据与明确的预期值相符 |
| `svg_to_pptx/pptx_package/cli.py::_write_postflight_report` | **不调用**，不复制 Worker 的导出 postflight 结论 | 该函数不是对任意现有 PPTX 的独立全量验证器 |
| `svg_to_pptx/pptx_package/builder.py` 构建时的关系、结构、转场、动画与 SVG 合同检查 | 尚未取得独立构建执行或等价验证的证据，明确记为 pending | 本模块不能完成 full exporter postflight |

官方 quality 命令只在 errors 存在时返回失败；`exit 0` 仍可能含 warnings。上游 postflight 的 `passed-with-warnings` 与 delivery 的 `passed-with-advisories` 也不是“零问题”。模块必须保留 introduced、inherited、source-import 与 delivery advisories 的分类和数量，校验计数与问题项一致；阻断项或未知报告结构应拒绝，不能自行清空警告来满足通过条件。

## 为什么仍保留完整 postflight 待验收

固定版本的 `_write_postflight_report` 在报告中将 `internal_relationships`、`structured_package`、`transitions`、`animations`、`project_svg_contract` 写作 `enforced-at-build`。这些值来自已执行构建流程的上下文，并不是该报告函数现场完成的独立检查。

仅用旧 PPTX 与人工补出的构建参数调用该函数，会把缺失的构建证据误写成通过。本模块只记录实际重跑或已验证的检查。未来完成 full exporter postflight，需要独立重导出并检查实际新产物，或实现覆盖这些构建保证的独立等价校验；新生成的 PPTX 也必须重新绑定预览、图表、交付和验收记录。

## 证据和信任边界

`PostflightEvidence` 是受 issuer 与完整性封印约束的进程内对象。其 JSON 报告可供留档与审计，但不能还原成可信实例，也不是可离线重放的发布证书。其信任前提是调用代码、官方镜像和控制器处于受信执行边界内；它不防御同一进程任意恶意 Python 代码。

每次报告至少绑定本次 nonce、manifest SHA、输入 SHA、完整源文件与资源哈希、PPTX SHA、oracle 哈希、固定官方 commit、脚本身份、命令退出码及对应日志/报告。程序在运行前后复核源文件未变，旧 cache/report 不得参与成功判断。

持久化与容器生命周期由外部控制器负责：先确认全部写入进程已停止，再限量复制普通文件，重新计算哈希并原子保留只读证据。JSON 的传输成功、对象的程序检查完成和版本发布权限是三个独立事实。

结果必须继续明确：

```text
publication_authorization = false
full_exporter_postflight_verified = false
independent_visual_review = pending
powerpoint_acceptance = pending
```

这些状态不得由请求 JSON、环境开关、Worker 文案或测试替身提升为完成。

## 后续接入核心的顺序

1. 控制器将完整可信 oracle 及授权来源哈希传入核验请求，生成唯一 nonce，并保留当前 manifest SHA。现有 Dispatcher 仅传 `expected_charts`，因此不能声称已具备新接口的全部来源合同。
2. 在同一个隔离核验运行中先取得 `ProgrammaticEvidence`，再调用本模块；不要把其 JSON 重新包装成可信对象。
3. 将新增报告、日志和源指纹纳入既有停写、限量复制、transport manifest 和只读保留机制，验证超时、失败与资源清理的实际行为。
4. 更新发布器对新增证据的识别，仅记录已完成的具体程序检查。完整 postflight、逐版本视觉验收和 PowerPoint 人工验收未闭合之前，继续拒绝 `generate`/`revise` 发布。
5. 在明确范围的样本上完成真实离线核验，再分别处理网站接线、部署和人工验收。不得根据离线单元测试直接宣布线上可用。

本次工作不发起新模型请求，不做逐页视觉或 PowerPoint 人工验收，不修改生产服务或网站的执行开关，也不部署新服务。

## 验证记录

2026-10-22 已完成本模块验证：

- ARM64 Linux 离线合同测试 **68/68 通过**，0.60 秒。测试使用合成 PPTX/SVG 和明确标识的官方命令替身，覆盖来源/nonce/oracle、完整源索引、缺证据、错版本、篡改、页数与清单、警告汇总、超时失败、旧输出及发布限制。这些测试不是实际 PPTX 交付验收。
- 固定官方镜像对已有 V1 私有只读快照执行 attribution、project validate、final SVG quality 与 source resource audit：6/6 页通过，0 errors、0 warnings，所有警告分类为 0，资源审计无待复核项。核对快照索引的 **184 个文件哈希和大小，全部保持不变**。未重复预览重绘或旧 delivery/native 检查。
- V1 本次 manifest SHA-256：`cef634bae4e6b9892c03b72f71c6a2f29b8c0c06defd3a147cff82b066d57471`。这次独立进程没有先前的可信 `ProgrammaticEvidence` 实例，因此只验证新加入的 source/quality 检查；没有把持久 JSON 包装为可信实例。完整入口的缺证据分支正确保持 pending，没有签发全流程通过证明。
- 测试容器均为 network none、UID/GID 10000、只读根目录和源挂载、drop ALL、no-new-privileges，并限制进程数、内存、CPU。只写专用验证目录；没有 DB/socket/上游凭据挂载，模型请求为 0。两只具名容器均已自动删除。
- 第一次运行因镜像当前目录优先于 `PYTHONPATH` 而导入旧模块，已通过显式 `/code` 工作目录修复；第二次暴露文件系统时间戳精度导致新报告误判为过期，已改为与同一文件系统上的新标记文件比较时间。保留两次失败原始记录，最终上述 68 项全部通过。

验证命令与入口：

```bash
# 专用目录放置 source.tar 后执行；已有 official-v1 输出时拒绝覆盖。
PPT_P3_CASE_ROOT=/root/ppt-service-p3-20261022 \
  bash ppt_service/tests/control/run_postflight_checks.sh

# 离线合同套件，在已装依赖的 Linux 环境中执行：
python -m pytest ppt_service/tests/control/test_postflight_validation.py -q -p no:cacheprovider
```

`source.tar` 包含 `ppt_service/__init__.py`、`control/{__init__,execution,staging,render_validation,publisher,postflight_validation}.py`、`worker/{__init__,preview}.py` 及本次 `test_postflight_validation.py`、`live_postflight_check.py`，保持仓库相对路径。测试副本与网站运行目录隔离。

服务器证据目录：`/root/ppt-service-p3-20261022/`，含 `unit-results.txt`、`unit-results.initial.txt`、`unit-results.timestamp-failure.txt`、`official-v1/verification.json`、官方日志、报告及 `source-hashes.json`。离线测试镜像实际 ID 为 `sha256:8148cd5182e718d3f0c51b513a00ea0f04edb2b7db1b21fae5d9d0a21aa6f7c7`；官方检查镜像为本文固定的 `6e9bdb…`。

基线 `panghu_agent` commit：`64a61883587a5f5a55dfeb8dd6c4ced26ed34271`。PPT 服务整体尚处未跟踪开发树，本次交付新增模块、合同测试、实际源检查脚本、运行脚本及本文；没有提交整个服务或其他作者代码。已测模块 SHA-256 为 `353b8a894d4035c6b4f8e87fba59d4720ca0ddd2b3d3a2ca1e0cb8afa7251b19`，测试文件为 `44c3ebb9620eed46de65b8813f83a23b5f8e2f9e8e5cc172f5bec02730c15338`。

P3 的独立来源/quality 检查模块已交付。完整 exporter 构建保证的等价核验、可信 oracle 接线、同进程组合验证及主控制器持久证据接入仍待完成，故 P3 整项保持未勾选。后续 Pi 执行器/新镜像的适配需要重新核对运行版本与证据合同；不能把这次固定 V1/镜像的结果直接用于新 runtime。
