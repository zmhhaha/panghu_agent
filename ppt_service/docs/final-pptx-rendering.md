# 最终 PPTX 的独立渲染证据

`control/final_pptx_validation.py` 对实际交付的 PPTX 字节生成独立的 LibreOffice Impress → PDF → Poppler PNG 预览。它与 SVG 源稿预览并存，不替换已有逐页审核记录绑定的预览哈希，也不代表 Microsoft PowerPoint 的实际打开、编辑、保存和重开已经通过。

入口只应在独立核验沙箱内调用：

```python
report = verify_final_pptx(
    pptx=private_snapshot_pptx,
    expected_pptx={"sha256": measured_sha256, "bytes": measured_bytes},
    request={
        "validation_nonce": controller_created_nonce,
        "source_manifest_sha256": measured_manifest_sha256,
        "input_snapshot_sha256": confirmed_input_snapshot_sha256,
        "pages": confirmed_page_count,
    },
    output=fresh_private_output,
)
```

核验前后均检查原始 PPTX 哈希；复制后只转换副本。固定的镜像内工具是 `/usr/bin/libreoffice`、`/usr/lib/libreoffice/program/soffice.bin`、`/usr/bin/pdfinfo`、`/usr/bin/pdftoppm` 和 `/usr/bin/fc-list`。工具缺失、超时、非零退出、PDF 缺失、页数/比例/旋转不匹配、PNG 缺页或多页、源文件变化均不得签发成功报告。

每次使用新建的独立用户配置、HOME 和临时目录；不继承模型、代理、数据库或用户环境变量，禁用宏执行和文档链接更新。外层仍必须提供非 root、只读镜像、无网络、资源与总时限限制，以及此核验 Pod 的精确停止证据。进程组清理不能替代整个 Pod 的停止证明。

`images/Dockerfile.validator-office` 提供现有固定 validator 镜像之上的 Office/Poppler 依赖层。构建必须以当前不可变 digest 为基础，使用 `latest` 标签并记录最终 digest、Git 来源、工具和软件包身份。核验模块不会在运行时安装依赖，也不会自动回退至 SVG 预览。

新的 `final-pptx-evidence.json` 使用 `ppt-service.final-pptx-render.v2`，包含控制器 nonce、原 manifest/输入快照/PPTX 哈希、渲染器版本、工具哈希、字体清单哈希、实际 PDF 哈希，以及 `pages/N.png` 的完整顺序、大小、尺寸和哈希。`deck` 同时记录原生形状、非空文本形状、图片节点和语义连接器数量；普通箭头形状不会计作可随模块移动的语义连接器。历史 v1 收据保持不变，并继续按原合同读取；不能通过删去新 v2 收据中的文字检测记录来降级核验。

`control/text_layout_validation.py` 使用镜像中已固定的 PyMuPDF，从最终 LibreOffice PDF 提取实际字符位置，结合最终 PPTX 的原生文本框、内边距与未旋转分组变换，检查短词异常断行、短中文标签孤字、文字超出文本框或页面、独立文字区域明显碰撞。PNG 页数、尺寸和源 SVG 边界不作为这些检查的替代证据。空格允许正常英文换行；显式换行保留，但把短标识符末尾一个字母挤到新行，即使已写成硬换行也会提示。

结果保存在私有 `text-layout.json`，绑定 nonce、manifest/输入快照以及最终 PPTX/PDF 的精确哈希。每个问题给出固定中文说明、修复建议、页码、数值形状 ID 与实际坐标，不回显候选提供的路径、形状名称或任意文本。明确问题返回 `final_pptx_text_layout_failed`，保留诊断与渲染产物，不签发成功收据；不能读取实际 PDF 时返回 `final_pptx_text_extraction_failed`。新收据的严格读取器核对该文件的固定路径、完整字节哈希、绑定和检查语义，完整停止传输清单同时保护诊断文件。

此检测仅覆盖保守的文字几何规则。旋转/竖排、继承而未声明坐标的文本框、无法唯一对应的文字和合理的同字阴影叠加会保留 `incomplete` 覆盖说明；不会猜测其视觉已通过。碰撞检测用坐标筛选，并限制每页形状对与字符对比较次数；超出预算时同样明确保留覆盖不足。问题最多展示 100 项，同时保留实际总数及截断标记。无错误或覆盖不足均不完成独立人工视觉或实际桌面操作验收。

唯一完成的门是 `independent_final_pptx_rendering`。该报告继续明确：

```text
publication_authorized = false
full_exporter_postflight_verified = false
human_visual_acceptance = false
powerpoint_acceptance = pending
validator_stop_proof_requires_controller = true
```

完整 exporter 构建保证、源稿与最终文件的独立等价核验、人工视觉和桌面 PowerPoint 验收仍分别保留。JSON 仅为审计结果；持久接线必须验证绑定、冻结文件和独立执行/停止来源，不能把客户端或 Worker 提交的同形 JSON 作为可信验收。

对已有审核版本补做核验，应新建一份绑定同一 PPTX 的追加证据，不改写旧 validator 收据和旧逐页记录。正式发布应由独立验收状态与所有者明确操作决定。

合同测试位于 `tests/control/test_final_pptx_validation.py`、`tests/control/test_text_layout_validation.py` 和 `tests/server/test_acceptance_evidence.py`，覆盖错源、复用输出、工具失败、缺页/多页、错误页面尺寸/旋转、字形排版规则及其正常反例、比较预算、渲染期间篡改，以及新诊断文件的字节/语义/路径绑定。它们使用明确标注的合成数据与工具替身，不能代替真实 Office 渲染验收。测试、实际导出与渲染统一在 Linux 服务器上的生产等价沙箱执行。
