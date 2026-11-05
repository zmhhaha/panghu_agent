# 科研原生表格实际渲染验收

该工具只生成合成材料，不读取用户项目、数据库、凭据或附件，不调用模型。它补充
`test_report_tables.py` 的文件结构与分页回归，不能用文件结构检查代替实际 Office 渲染。

应用环境需要已有的 ReportLab 4.4.3；独立验收环境额外需要 PyMuPDF、LibreOffice **Writer**
及中文字体。只有 LibreOffice core/Impress 不能转换 DOCX。应用运行镜像不需要安装 Office。

从仓库根目录执行，输出目录应是本轮专用目录：

```bash
PYTHONPATH=. python -B tests/scientific_workspace/rendering/generate_exports.py /work/table-exports
mkdir -p /work/table-exports/office
libreoffice -env:UserInstallation=file:///tmp/scientific-table-writer-profile \
  --headless --convert-to 'pdf:writer_pdf_Export' \
  --outdir /work/table-exports/office /work/table-exports/*.docx
python -B tests/scientific_workspace/rendering/inspect_exports.py /work/table-exports \
  --office-version "$(libreoffice --version)" --screenshots
```

推荐在隔离工具容器内将合成文件目录挂载为可写，禁用网络、以非 root 身份运行，
并为 LibreOffice 提供可写的临时用户目录。以最终应用精确镜像生成文件；使用工具
镜像中的 Writer 转换 DOCX。记录应用镜像摘要、工具镜像摘要、Writer 实际版本及字体，
不要把挂载开发源码运行的结果写成最终镜像验收。

需要补齐 Writer 的既有 Debian Office 工具镜像，可用同目录 `Dockerfile.office`
构建临时 `latest` 工具镜像；软件源使用中科大镜像，发行版签名校验仍由 apt 执行：

```bash
docker build --build-arg OFFICE_BASE=ppt-validator-office-dependency:latest \
  -f tests/scientific_workspace/rendering/Dockerfile.office \
  -t scientific-table-writer-tools:latest tests/scientific_workspace/rendering
```

该工具镜像仅供离线文档验收，不加入 API 或 Worker 部署。

四组合成材料分别覆盖中文与引用、120 行续表、单个跨页长单元格、不可断长网址。
每组都必须存在原生 PDF 和由对应 DOCX 实际转换的 Office PDF，共八份。检查器不会因
`office/` 缺失而只检查原生 PDF 后报告成功，也不以固定页数或固定页脚内容作为标准。

检查器会验证生成清单中的 DOCX/PDF 哈希、DOCX 的原生可编辑表格、120 个 `ROW-NNN`
标记各出现一次、每个有行标记的页存在完整表头、长单元格的 240 句中文及始末标记完整、
长网址完整、正文和链接在页内、正文及表内两处引用均为有效内部跳转。失败时仍写入
`rendered-checks.json` 并以非零状态退出。`--office-dir` 可调整转换输出位置。

`--screenshots` 会将每份 PDF 的首页、第二页和末页保存为 PNG；还会保留八份 PDF 的
提取文本。人工查看这些页面，检查中文是否出现方框/缺字、表格列宽、换行、重复表头、
长行续页及末页完整性。自动文本和几何检查通过不等于人工排版已审阅；验收记录应分别
记录二者。截图、DOCX、PDF、清单和收据留在输出目录，不需要加入 Git。
