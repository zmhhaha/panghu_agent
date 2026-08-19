"""EvidenceGate-new compatible Markdown report formatters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _header(title: str, report_type: str) -> str:
    quoted_title = json.dumps(title, ensure_ascii=False)
    return "---\n" + f"title: {quoted_title}\n" + f"type: {report_type}\n" + f"generated_at: {datetime.now(timezone.utc).isoformat()}\n" + "---\n\n"


def save_report(content: str, topic: str, report_type: str, output_dir: str | Path, filename: str | None = None) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if not filename:
        filename = f"{report_type}.md"
    path = directory / filename
    path.write_text(_header(topic, report_type) + content, encoding="utf-8")
    return path


def format_search_report(result: dict[str, Any]) -> str:
    lines = [
        "# 文献检索报告", "", f"**研究主题**: {result['topic']}",
        f"**查询变体**: {', '.join(result.get('query_variants', []))}",
        f"**本地库命中**: {result.get('local_hits', 0)} 篇",
        f"**待下载**: {len(result.get('need_download', []))} 篇", "",
    ]
    for provider, rows in result.get("api_results", {}).items():
        lines.extend([f"## {provider} ({len(rows)} 篇)", ""])
        for index, paper in enumerate(rows, 1):
            lines.extend([
                f"### [{index}] {paper.get('title', '')}",
                f"- 作者: {paper.get('authors') or 'N/A'}",
                f"- 日期: {paper.get('date') or 'N/A'}",
                f"- DOI: {paper.get('doi') or 'N/A'}",
                f"- URL: {paper.get('url') or 'N/A'}", "",
            ])
    if result.get("errors"):
        lines.extend(["## API 错误", ""])
        lines.extend(f"- {name}: {error}" for name, error in result["errors"].items())
    return "\n".join(lines)


def format_need_to_download(papers: list[dict[str, Any]]) -> str:
    lines = ["# 待下载文献清单", "", f"共 {len(papers)} 篇待下载", ""]
    for index, paper in enumerate(papers, 1):
        lines.extend([
            f"## [{index}] {paper.get('title', '')}",
            f"- 作者: {paper.get('authors') or 'N/A'}",
            f"- 来源: {paper.get('provider') or 'N/A'}",
            f"- DOI: {paper.get('doi') or 'N/A'}",
            f"- arXiv: {paper.get('arxiv_id') or 'N/A'}",
            f"- URL: {paper.get('url') or 'N/A'}", "",
        ])
    return "\n".join(lines)


def format_collection_report(results: list[dict[str, Any]], round_num: int) -> str:
    success = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]
    total_bytes = sum(int(row.get("size") or 0) for row in success)
    lines = [
        f"# 第 {round_num} 轮文献收集报告", "",
        f"尝试收集: {len(results)} 篇", f"成功下载: {len(success)} 篇", f"下载失败: {len(failed)} 篇",
        f"总下载量: {total_bytes / 1024 / 1024:.2f} MB", "",
    ]
    if success:
        lines.extend(["## 成功下载", ""])
        for row in success:
            lines.extend([
                f"### {row.get('title', '')}",
                f"- 来源: {row.get('source') or 'N/A'}",
                f"- 路径: {row.get('path') or 'N/A'}",
                f"- 大小: {int(row.get('size') or 0) / 1024:.1f} KB", "",
            ])
    if failed:
        lines.extend(["## 下载失败", ""])
        for row in failed:
            lines.extend([f"### {row.get('title', '')}", f"- 原因: {row.get('error') or 'unknown'}", ""])
    return "\n".join(lines)


def format_verification_report(results: list[dict[str, Any]], round_num: int) -> str:
    passed = [row for row in results if row.get("verdict") == "pass"]
    failed = [row for row in results if row.get("verdict") == "fail"]
    uncertain = [row for row in results if row.get("verdict") == "uncertain"]
    lines = [
        f"# 第 {round_num} 轮文献校验报告", "",
        f"检查总数: {len(results)}", f"通过: {len(passed)}", f"未通过: {len(failed)}", f"存疑: {len(uncertain)}", "",
    ]
    for heading, rows in (("通过的文献", passed), ("未通过的文献", failed), ("存疑的文献", uncertain)):
        if not rows:
            continue
        lines.extend([f"## {heading}", ""])
        for row in rows:
            lines.extend([
                f"### {row.get('title', '')}",
                f"- 结论: {row.get('verdict')}",
                f"- 文件: {row.get('path') or 'N/A'}",
                f"- 大小: {int(row.get('size') or 0) / 1024:.1f} KB",
                f"- 文本字符数: {row.get('text_chars', 0)}",
                f"- 备注: {row.get('reason') or row.get('notes') or 'N/A'}", "",
            ])
    return "\n".join(lines)


def format_final_report(topic: str, rounds: list[dict[str, Any]], verified: list[dict[str, Any]], pending: list[dict[str, Any]]) -> str:
    lines = [
        "# 文献下载最终报告", "", f"**研究主题**: {topic}", f"**收集轮数**: {len(rounds)}",
        f"**最终通过校验**: {len(verified)} 篇", f"**仍待处理**: {len(pending)} 篇", "",
        "## 通过校验的文献", "",
    ]
    for index, paper in enumerate(verified, 1):
        lines.extend([
            f"### [{index}] {paper.get('title', '')}",
            f"- 作者: {paper.get('authors') or 'N/A'}",
            f"- 来源: {paper.get('provider') or 'N/A'}",
            f"- PDF: {paper.get('pdf_path') or 'N/A'}", "",
        ])
    if pending:
        lines.extend(["## 未完成文献", ""])
        lines.extend(f"- {paper.get('title', '')}: {paper.get('pdf_status', 'pending')}" for paper in pending)
    return "\n".join(lines)
