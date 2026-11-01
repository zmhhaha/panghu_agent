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

保留的 `final-pptx-evidence.json` 使用 `ppt-service.final-pptx-render.v1`，包含控制器 nonce、原 manifest/输入快照/PPTX 哈希、渲染器版本、工具哈希、字体清单哈希、实际 PDF 哈希，以及 `pages/N.png` 的完整顺序、大小、尺寸和哈希。`deck` 同时记录原生形状、非空文本形状、图片节点和语义连接器数量；普通箭头形状不会计作可随模块移动的语义连接器。

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

离线合同测试位于 `tests/control/test_final_pptx_validation.py`，使用标明的合成 PPTX 和工具替身，覆盖错源、复用输出、工具失败、缺页/多页、错误页面尺寸/旋转和渲染期间篡改。这些合同测试不构成真实 Office 渲染验收；实际渲染必须在 Linux 服务器上的生产等价沙箱核对。
