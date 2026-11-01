# 最终文件与独立构建的等价核验

`control/export_equivalence.py` 在独立核验沙箱内重新运行固定版本的原生导出器，再核对实际交付的 PPTX 与这次独立构建的完整内容。它证明的是命名的独立构建等价合同，不补造原执行者的历史导出过程或原始构建证明。

入口要求同一进程真实签发的 `PostflightEvidence`，以及该次核验新建的工作项目目录：

```python
report = run_export_equivalence(
    snapshot, completed_manifest, request, bundle / "equivalence",
    postflight_evidence=postflight_evidence,
    postflight_root=bundle / "postflight",
)
```

模块复核 nonce、输入快照、manifest、完整来源指纹、source-index 与 source-binding。原始来源和核验副本均在构建前后重新计算哈希。缺失、伪造、错源、被修改的来源证据不得降级为可接受结果。现有未解决的 quality/resource 警告保留待处理状态。

实际命令使用镜像内固定 Python 与官方 `svg_to_pptx.py`：显式 `-s output --native-charts-and-tables --no-animations -o ...`。固定版本 CLI 的原生 release 默认也是 `svg_output`；`svg_final` 是诊断覆盖项，本模块不会选择它。完整规范、锁、主题、结构和备注由未变的项目来源与严格 release 路由读取；不使用 Quick、危险兼容模式、roundtrip 或 Worker 保存的旧 postflight 结果。备注遵循官方默认启用；仅可信请求显式 `enable_notes=false` 时增加 `--no-notes`，不从候选反推需求。

固定 builder 在实际成功构建后检查内部关系、结构化模板合同、转场、Morph、动画与严格 SVG 转换合同。模块读取这次构建新生成的 postflight，复核当前来源指纹、输出路径/大小、严格策略、质量和资源结论，并对候选与独立成品分别进行原生 OOXML/图表数据检查。

随后比较 ZIP 内完整文件名单、全部 XML、关系、文本、图形坐标、样式、备注、媒体字节，以及嵌入 XLSX 的递归内容。内容差异不得用相似度阈值忽略。允许规范化的内容仅限：

- ZIP 容器的顺序、压缩方式与时间戳。
- `docProps/core.xml` 中格式正确的创建和修改时间；其他作者、标题等属性保留。
- 固定 builder `_reseed_p14_creation_id` 生成的 `p14:creationId` 值。
- 固定 builder 为占位字段、页码字段生成的 `a:fld` GUID；字段类型、文字、几何和全部其他属性保留，非法或重复字段标识拒绝。
- OOXML 元素间的纯排版空白，以及关系/Content Types 声明的无语义顺序；实际文本叶节点和 `xml:space` 内容保留。

新增或缺失文件、不同的原生文本/图形/图表/备注/图片、未知差异均使等价门保持失败。私有 `comparison.private.json` 只列出差异文件与哈希，便于服务器定向排查，不向浏览器输出原始文档内容。

成功报告为 `ppt-service.export-build-equivalence.v1`，保存在 `equivalence/export-contract.json`。只完成 `independent_export_build_contract`，并明确：

```text
export_build_equivalence_verified = true
original_export_provenance_verified = false
full_exporter_postflight_verified = false
publication_authorized = false
```

`independent_postflight_and_delivery` 必须由外层可信核验流程结合本次 source quality/resources、现有独立 delivery/native 检查后决定；最终 PPTX 实际渲染、逐页人工审核、桌面 PowerPoint 实操和明确发布仍是各自独立的要求。完整停止证据和持久只读证据冻结继续由外部控制器负责。

合同测试使用明确标注的合成 OOXML 与导出命令替身，不证明官方工具真实运行。真实等价与渲染核验只在 Linux 服务器执行，并保留独立重建成品、脚本哈希、退出码、源文件未变和整个沙箱停止记录。
