"""Generate synthetic native DOCX/PDF files for real Office rendering acceptance.

This script never reads project databases, credentials, or user documents.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


FIXTURE_NAMES = ("overview", "multipage", "tall-row", "long-token")
ROW_COUNT = 120
TALL_SENTENCE = "中文长单元格跨页内容必须完整保留。"
TALL_REPETITIONS = 240
LONG_URL = "https://example.org/" + "LongToken0123456789" * 40 + "/END-LONG-URL"


def fixtures():
    yield "overview", r"""# 中文原生表格验收

验收范围：表格可编辑、中文换行、引用跳转和页宽；参考资料 [1]。

| 能力 | 执行路径 | 结论 |
| :--- | :---: | ---: |
| 规划 | 业务确认后提交 Agent 任务 | 已验证 |
| 证据与大纲 | 保留资料来源，检查引用 [1] | 中文可编辑 |
| 分隔符 | 转义 a\|b 与 `x|y` | 不增加列 |
| 安全文本 | <w:object> & <script>literal</script> | 仅作文字 |

```text
| code | example |
| --- | --- |
| left | right |
```

## 参考文献

[1] 中文工程文档。https://example.org/reference
"""
    rows = ["# 多页续表验收", "", "| 编号 | 中文工程说明 | 检查标记 |", "| :--- | :--- | ---: |"]
    for number in range(1, ROW_COUNT + 1):
        rows.append(f"| {number:03d} | 原生表格应在页内换行，跨页重复表头并保留完整行。 | ROW-{number:03d} |")
    rows.extend(["", "表格之后仍保留正文。END-MULTIPAGE"])
    yield "multipage", "\n".join(rows) + "\n"
    yield "tall-row", ("# 单行跨页验收\n\n| 项目 | 内容 |\n| --- | --- |\n"
                       "| 长单元格 | BEGIN-TALL " + TALL_SENTENCE * TALL_REPETITIONS + " END-TALL |\n"
                       "| 后续行 | AFTER-TALL |\n\nEND-DOCUMENT\n")
    yield "long-token", ("# 长网址列宽验收\n\n| 类型 | 内容 |\n| --- | --- |\n"
                         "| URL | " + LONG_URL + " |\n"
                         "| 中文 | 不允许长网址将后续列推出页面。 |\n\nEND-LONG-DOCUMENT\n")


def generate(out):
    # The separate Office inspector imports fixture constants without having
    # the application installed. Only generation needs the actual application.
    parents = Path(__file__).resolve().parents
    if len(parents) > 3 and (parents[3] / 'scientific_agent').is_dir():
        sys.path.insert(0, str(parents[3]))
    from scientific_agent import report_exports

    out.mkdir(parents=True, exist_ok=True)
    records = []
    for name, markdown in fixtures():
        project = {"title": name, "requirements": {"question": name}}
        version = {"id": "acceptance-" + name, "created_at": "2026-11-05T00:00:00Z",
                   "report_markdown": markdown, "origin": "user_import", "finalized": False}
        reading = report_exports.reading_markdown(project, version)
        (out / (name + ".md")).write_text(reading, encoding="utf-8")
        for extension, renderer in (("docx", report_exports.docx), ("pdf", report_exports.pdf)):
            data = renderer(reading)
            path = out / (name + "." + extension)
            path.write_bytes(data)
            records.append({"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    (out / "exports.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"outputs": len(records), "directory": str(out)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Dedicated output folder for synthetic exports")
    args = parser.parse_args()
    print(json.dumps(generate(args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
