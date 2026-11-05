# 科研报告 Word / PDF 原生表格

2026-11-05 的改进将合法 Markdown 表格导出为 Word 可编辑表格和 PDF 矢量表格。
已有报告无需重新生成，选择原报告版本重新下载即可使用；原始 Markdown、版本内容、
引用来源、审阅和定稿记录保持不变。

## 支持范围

- 用表头及分隔行识别表格，支持可选外侧竖线、转义竖线和行内代码中的竖线。
- 左、中、右对齐；浅色表头和边框；根据内容分配列宽，限制在 A4 可打印范围内。
- Word 使用原生 `w:tbl` 单元格，可在 Word/LibreOffice 中继续编辑；正文行允许跨页。
- PDF 支持中文及长 URL 换行、长行跨页和重复表头，文字可选择和搜索。
- 单元格内的报告引用仍跳到参考文献；代码示例不转换为表格，HTML/XML 保持文字。

不足列补空，多余列按 GFM 表格规则忽略。导出保留现有行内文本处理规则，不执行 HTML
或加载远程资源。异常表头本身高过一页时，PDF 对该表关闭重复表头，允许表头分页，
避免无法排版或截断内容。

## 验证和复现

新表格测试与原导出/API 测试联合执行，使用生产相同版本 `reportlab==4.4.3`：

```sh
python -B -m pytest -q -p no:cacheprovider \
  tests/scientific_workspace/test_report_tables.py \
  tests/scientific_workspace/test_report_exports.py
```

结果为 **46 passed**。覆盖原生表格结构、页宽、对齐、120 行续表、8000 中文字符单元格、
超高表头、长 URL、分段边界引用、代码、XML 转义、指定旧版下载与跨用户拒绝。

真实渲染采用独立固定样稿，不使用用户资料或模型调用。Office 工具仅用于验收，
不加入科研 API 运行依赖。结构检查不替代实际 Office 渲染；精确镜像、工具版本、
样稿页数及生产下载结果见本文件的发布验收记录。

## 发布验收记录

实现提交：`fbac301c4fe6dc07121f2849a778074337c9c507`。

候选 API 按既有 `bash scientific_agent/build.sh --target api` 构建推送 `latest`：

```text
arm-cluster-master:5000/scientific-api@sha256:b1d5c4f15d26f6d0d837b49420eccab429a49f14aa667e48c334e2ffcc78055d
```

精确镜像在无网络、只读文件系统、非 root 且丢弃全部 capabilities 的容器中成功导入
实际 API；ReportLab 为 4.4.3，导出模块 SHA256 为
`549efbb3a7600b6b2813ebc82fe03aa4e522f0ce16c029c5b7004a6b2a901cb4`。

真实渲染使用 LibreOffice **25.2.3.2 520(Build:2)**、Noto CJK 和 PyMuPDF 1.26.7。
工具镜像为 `scientific-table-writer-tools:latest`，本机精确 image ID：
`sha256:f7e6b5d3bd7f1b013817dd4f37b6d8bedc7a4588ab10c33bdc665716c2869fdb`。
最终样稿直接由上述 API 摘要生成，没有挂载开发源码；DOCX 实际经过 Writer 打开及
`writer_pdf_Export` 转换，再检查八份 PDF。

| 固定合成样稿 | 原生 PDF 页数 | Word 经 Writer 转 PDF 页数 | 核对结果 |
| --- | --- | --- | --- |
| 中文、转义、代码与引用 | 1 | 1 | 两处内部引用可跳转；文字和非表格代码保留。 |
| 120 行续表 | 5 | 5 | 每个行标记恰好一次，每个表格页有完整表头。 |
| 单个跨页长单元格 | 3 | 3 | 240 句中文全部保留，后续行及文末标记完整。 |
| 超长 URL | 1 | 1 | 完整 URL 恰好一次，换行后仍在页内。 |

八份 PDF 的文字及内部链接均在页面内，没有丢失标记或非法链接。另实际查看 Word
转换结果的普通页、续表中间及末页、长单元格中间及末页、长 URL 页，以及原生 PDF
样稿截图，确认中文无明显缺字、表格无明显重叠或溢出。本次实际使用的是 LibreOffice；
没有声称另做了 Windows Microsoft Word 实机验收。

可复用工具及命令见
[rendering/README.md](../tests/scientific_workspace/rendering/README.md)。该目录的
`Dockerfile.office` 按用户要求采用中科大 Debian 软件源；本次工具安装完成后才收到
此偏好，本次已验收镜像保留其真实工具摘要，不重新标记成由新配置构建。

2026-11-05 已发布上述 API 精确摘要，Deployment 及 Pod template annotation 记录
源码 `fbac301` 与摘要；仅更新 API 镜像，保留现有配置。发布后以实际业务 HTTP 只读
检查 **10 个项目、11 个版本、55 次下载**（其中 3 个版本含表格），Word 均包含对应
原生表格，原稿/阅读稿/来源/Word/PDF 均对应指定版本。另一用户的 55 次同样请求全部
返回 404；全部项目 document SHA256 前后相同。后端仍为 `agent-service`，Worker 就绪。

本次证据目录为服务器 `/root/scientific-table-exports-20261105`：

- `api-build.log`、`release.json`、`api-before-tables.json`、`api-tables-patch.json`。
- `evidence/exports/{exports.json,rendered-checks.json,screenshots/}` 及八份实际 PDF。
- `production-smoke.stdout`：55 次下载和 55 次跨用户拒绝、项目未变的汇总。
- `completion.json`：实际镜像/annotation、八份渲染文件、人工看图及 API/UI 各 1/1 就绪的汇总。

工具准备时发现既有 PPT Office 镜像只有 Impress、缺 Writer，故 DOCX 初次无法加载；
在独立工具镜像补齐 Writer 后实际通过。默认软件源下载停滞时改用可访问的镜像源。
既有基础镜像层数接近上限，因此工具 Dockerfile 不额外添加末尾 LABEL 层。这些工具
准备问题不作为 Word 文件验收成功，最终以实际转换文件、文本和截图为依据。

OpenSpec：`improve-scientific-report-table-exports`，4/4 任务完成，服务端校验 valid=true，
issues 为空；未自动 apply/archive。这一项不代替新建研究项目的完整
生产网页五阶段验收，也不代替共享 Agent 的真实故障恢复演练。
