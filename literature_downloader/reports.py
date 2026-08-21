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


def _text(value: Any, fallback: str = "N/A") -> str:
    text = str(value or "").strip()
    return text or fallback


def _url(value: Any) -> str:
    text = str(value or "").strip()
    return f"<{text}>" if text.startswith(("http://", "https://")) else (text or "N/A")


def _metadata_lines(paper: dict[str, Any], *, include_abstract: bool = False) -> list[str]:
    providers = paper.get("providers") or []
    identifiers = paper.get("identifiers") or {}
    lines = [
        f"- 作者: {_text(paper.get('authors'))}",
        f"- 日期: {_text(paper.get('date'))}",
        f"- 来源数据库: {_text(paper.get('provider') or (providers[0] if providers else ''))}",
        f"- 其他来源: {_text(', '.join(str(item) for item in providers[1:]), 'N/A')}",
        f"- 期刊/来源: {_text(paper.get('venue'))}",
        f"- DOI: {_text(paper.get('doi'))}",
        f"- arXiv ID: {_text(paper.get('arxiv_id') or identifiers.get('arxiv'))}",
        f"- 元数据 URL: {_url(paper.get('url'))}",
        f"- 公开 PDF URL: {_url(paper.get('pdf_url'))}",
    ]
    if identifiers:
        identifier_text = ", ".join(
            f"{key}={value}" for key, value in identifiers.items() if str(value or "").strip()
        )
        if identifier_text:
            lines.append(f"- 其他标识符: {identifier_text}")
    if include_abstract and paper.get("abstract"):
        abstract = " ".join(str(paper["abstract"]).split())
        lines.append(f"- 摘要: {abstract[:800]}{'...' if len(abstract) > 800 else ''}")
    if paper.get("relevance_score") is not None:
        lines.append(f"- 规则相关性评分: {_text(paper.get('relevance_score'), '0')}")
    if paper.get("llm_relevance_score") is not None:
        lines.append(f"- LLM 相关性评分: {_text(paper.get('llm_relevance_score'), 'N/A')}")
    if paper.get("relevance_method"):
        lines.append(f"- 相关性判断方式: {_text(paper.get('relevance_method'))}")
    if paper.get("relevance_reason"):
        lines.append(f"- 相关性判断理由: {_text(paper.get('relevance_reason'))}")
    source_record = paper.get("source_record") or {}
    if isinstance(source_record, dict) and source_record:
        compact_source = json.dumps(source_record, ensure_ascii=False, default=str, separators=(",", ":"))
        lines.append(f"- 原始 API 字段摘要: `{compact_source[:1200]}`")
    return lines


def _attempt_lines(attempts: list[dict[str, Any]] | None) -> list[str]:
    attempts = attempts or []
    if not attempts:
        return ["- 下载尝试: 无可用下载 URL"]
    lines = ["- 下载尝试:"]
    for index, attempt in enumerate(attempts, 1):
        outcome = "成功" if attempt.get("ok") else "失败"
        source = _text(attempt.get("source"))
        url = _url(attempt.get("url"))
        elapsed = int(attempt.get("elapsed_ms") or 0)
        detail = f"；大小: {int(attempt.get('size') or 0)} bytes；耗时: {elapsed} ms"
        status_code = int(attempt.get("status_code") or 0)
        request_attempts = int(attempt.get("request_attempts") or 1)
        if status_code:
            detail += f"；HTTP: {status_code}"
        detail += f"；HTTP 请求次数: {request_attempts}"
        lines.append(f"  {index}. {source}：{outcome}；URL: {url}{detail}")
        final_url = str(attempt.get("final_url") or "")
        if final_url and final_url != str(attempt.get("url") or ""):
            lines.append(f"     最终重定向 URL: {_url(final_url)}")
        if attempt.get("error"):
            lines.append(f"     原因: {attempt['error']}")
    return lines


def _successful_attempt_url(row: dict[str, Any]) -> str:
    for attempt in row.get("attempts") or []:
        if attempt.get("ok"):
            return str(attempt.get("url") or "")
    return ""


def format_search_report(result: dict[str, Any]) -> str:
    plan = result.get("search_plan") or {}
    llm = plan.get("llm") or {}
    lines = [
        "# 文献检索报告", "", f"**研究主题**: {result['topic']}",
        f"**查询变体**: {', '.join(result.get('query_variants', [])) or 'N/A'}",
        f"**检索结果总数**: {len(result.get('papers', []))} 篇",
        f"**本地库命中**: {result.get('local_hits', 0)} 篇",
        f"**待下载**: {len(result.get('need_download', []))} 篇",
        f"**检索轮数**: {len(result.get('search_rounds') or []) or 1}",
        f"**DOI 数量**: {sum(1 for paper in result.get('papers') or [] if str(paper.get('doi') or '').strip())}",
        "**说明**: 本报告仅完成文献检索；PDF 下载需在下载标签中输入任务 ID 单独触发。", "",
    ]
    provider_queries = result.get("provider_queries") or {}
    if provider_queries:
        lines.extend(["**各 Provider 实际查询**:", ""])
        for provider, queries in provider_queries.items():
            lines.append(f"- {provider}: {'；'.join(str(query) for query in queries) or '未执行'}")
        lines.append("")

    lines.extend([
        "## 文献检索专家 Agent", "",
        f"- 状态: {_text(llm.get('status'), 'rules')}",
        f"- 是否使用 LLM: {'是' if llm.get('used') else '否'}",
        f"- Provider: {_text(llm.get('provider'))}",
        f"- 模型: {_text(llm.get('model'))}",
        f"- 说明: {_text(llm.get('reason'))}",
        f"- 核心概念: {_text(', '.join(str(item) for item in plan.get('core_concepts') or []))}",
        f"- 同义词和扩展术语: {_text(', '.join(str(item) for item in plan.get('synonyms') or []))}",
        "- 纳入标准:",
    ])
    lines.extend(f"  - {item}" for item in plan.get("inclusion_criteria") or [])
    lines.append("- 排除标准:")
    lines.extend(f"  - {item}" for item in plan.get("exclusion_criteria") or [])
    scope_filtering = plan.get("scope_filtering") or {}
    lines.append(f"- scope_requirements strict filtering: {'yes' if scope_filtering.get('active') else 'no'}")
    lines.append(f"- scope note: {_text(scope_filtering.get('reason'))}")
    lines.append("- LLM-provided scope requirements:")
    for group in plan.get("scope_requirements") or []:
        if isinstance(group, dict):
            required = "required" if group.get("required", True) else "optional"
            terms = ", ".join(str(term) for term in group.get("terms") or [])
            lines.append(f"  - {group.get('name') or 'group'} ({required}): {terms}")
    lines.extend([
        "",
        "## 文献检索员", "",
        f"- 相关性重排: {_text((result.get('relevance') or {}).get('status'), 'rules')}",
        f"- LLM 判断文献数: {(result.get('relevance') or {}).get('judged', 0)}", "",
    ])
    round_rows = result.get("search_rounds") or []
    if round_rows:
        lines.extend(["## 各轮检索统计", "", "| 轮次 | 偏移量 | 命中 | 本地命中 | Provider 统计 |", "|---:|---:|---:|---:|---|"])
        for row in round_rows:
            counts = ", ".join(f"{key}={value}" for key, value in (row.get("provider_counts") or {}).items()) or "N/A"
            lines.append(
                f"| {row.get('round', '-')} | {row.get('offset', 0)} | {row.get('found', 0)} | "
                f"{row.get('local_hits', 0)} | {counts} |"
            )
        lines.append("")

    local_rows = result.get("local_results") or []
    lines.extend(["## 本地文献库命中", ""])
    if not local_rows:
        lines.append("本地文献库中未找到匹配文献。\n")
    else:
        for index, paper in enumerate(local_rows, 1):
            lines.extend([f"### [{index}] {_text(paper.get('title'))}"])
            lines.extend(_metadata_lines(paper, include_abstract=True))
            lines.extend([f"- 本地 PDF 状态: {_text(paper.get('pdf_status'))}", ""])

    for provider, rows in result.get("api_results", {}).items():
        lines.extend([f"## {provider} ({len(rows)} 篇)", ""])
        for index, paper in enumerate(rows, 1):
            lines.extend([
                f"### [{index}] {_text(paper.get('title'))}",
            ])
            lines.extend(_metadata_lines(paper, include_abstract=True))
            lines.append("")

    lines.extend(["## 合并去重后的检索结果", ""])
    for index, paper in enumerate(result.get("papers") or [], 1):
        lines.extend([f"### [{index}] {_text(paper.get('title'))}"])
        lines.extend(_metadata_lines(paper))
        lines.extend([f"- 下载状态: {_text(paper.get('pdf_status'))}", ""])

    if result.get("errors"):
        lines.extend(["## API 错误", ""])
        lines.extend(f"- {name}: {error}" for name, error in result["errors"].items())
    return "\n".join(lines)


def format_doi_list(result: dict[str, Any]) -> str:
    """Create a compact, traceable identifier list for downstream retrieval."""
    papers = result.get("papers") or []
    doi_count = sum(1 for paper in papers if str(paper.get("doi") or "").strip())
    lines = [
        "# DOI 列表",
        "",
        f"研究主题：{_text(result.get('topic'))}",
        f"检索结果：{len(papers)} 篇；包含 DOI：{doi_count} 篇",
        "",
        "| 序号 | DOI | 标题 | 来源 | 其他标识 |",
        "|---:|---|---|---|---|",
    ]
    for index, paper in enumerate(papers, 1):
        doi = str(paper.get("doi") or "").strip() or "N/A"
        identifiers = paper.get("identifiers") or {}
        other = str(paper.get("arxiv_id") or identifiers.get("arxiv") or paper.get("url") or "N/A")
        title = " ".join(str(paper.get("title") or "N/A").split()).replace("|", "\\|")
        source = _text(paper.get("provider"), "N/A").replace("|", "\\|")
        lines.append(f"| {index} | `{doi}` | {title} | {source} | {other} |")
    if not papers:
        lines.append("| - | N/A | 未找到符合条件的文献 | - | - |")
    lines.extend([
        "",
        "此列表同时保留无 DOI 文献的 arXiv ID 或元数据 URL，便于后续下载和人工追溯。",
    ])
    return "\n".join(lines)


def format_need_to_download(papers: list[dict[str, Any]]) -> str:
    lines = [
        "# 待下载文献清单",
        "",
        f"共 {len(papers)} 篇待下载",
        "",
        "收集专家必须依据每篇文献的元数据 URL、公开 PDF URL、DOI 或 arXiv ID 下载；服务器本地路径仅用于保存下载结果和后续校验。",
        "",
    ]
    for index, paper in enumerate(papers, 1):
        lines.append(f"## [{index}] {_text(paper.get('title'))}")
        lines.extend(_metadata_lines(paper))
        lines.append("- 推荐下载顺序: arXiv -> 元数据公开 PDF/落地页 -> Unpaywall/OpenAlex OA -> DOI 落地页 PDF 发现 -> Semantic Scholar/PMC")
        lines.append("")
    return "\n".join(lines)


def format_collection_report(results: list[dict[str, Any]], round_num: int) -> str:
    success = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]
    total_bytes = sum(int(row.get("size") or 0) for row in success)
    lines = [
        "# 文献收集报告", "",
        "**收集方式**: 单次下载与校验", "",
        f"尝试收集: {len(results)} 篇", f"成功下载: {len(success)} 篇", f"下载失败: {len(failed)} 篇",
        f"总下载量: {total_bytes / 1024 / 1024:.2f} MB", "",
    ]
    if success:
        lines.extend(["## 成功下载", ""])
        for row in success:
            lines.append(f"### {_text(row.get('title'))}")
            lines.extend(_metadata_lines(row))
            lines.extend([
                f"- 实际下载来源: {_text(row.get('source'))}",
                f"- 实际下载 URL: {_url(_successful_attempt_url(row))}",
                f"- 本地存储路径: {_text(row.get('path'))}",
                f"- 大小: {int(row.get('size') or 0) / 1024:.1f} KB",
            ])
            lines.extend(_attempt_lines(row.get("attempts")))
            lines.append("")
    if failed:
        lines.extend(["## 下载失败", ""])
        for row in failed:
            lines.append(f"### {_text(row.get('title'))}")
            lines.extend(_metadata_lines(row))
            lines.extend([f"- 最终原因: {_text(row.get('error'), 'unknown')}"])
            lines.extend(_attempt_lines(row.get("attempts")))
            lines.append("")
    return "\n".join(lines)


def format_verification_report(results: list[dict[str, Any]], round_num: int) -> str:
    passed = [row for row in results if row.get("verdict") == "pass"]
    failed = [row for row in results if row.get("verdict") == "fail"]
    uncertain = [row for row in results if row.get("verdict") == "uncertain"]
    lines = [
        "# 文献校验报告", "",
        "**校验方式**: 对本次下载结果统一校验", "",
        f"检查总数: {len(results)}", f"通过: {len(passed)}", f"未通过: {len(failed)}", f"存疑: {len(uncertain)}", "",
    ]
    for heading, rows in (("通过的文献", passed), ("未通过的文献", failed), ("存疑的文献", uncertain)):
        if not rows:
            continue
        lines.extend([f"## {heading}", ""])
        for row in rows:
            lines.append(f"### {_text(row.get('title'))}")
            lines.extend(_metadata_lines(row))
            lines.extend([
                f"- 下载来源: {_text(row.get('download_source'))}",
                f"- 下载 URL: {_url(row.get('download_url'))}",
                f"- 校验结论: {_text(row.get('verdict'))}",
                f"- 本地文件: {_text(row.get('path'))}",
                f"- 大小: {int(row.get('size') or 0) / 1024:.1f} KB",
                f"- 文本字符数: {row.get('text_chars', 0)}",
                f"- 备注: {_text(row.get('reason') or row.get('notes'))}",
                "",
            ])
    return "\n".join(lines)


def format_final_report(
    topic: str,
    rounds: list[dict[str, Any]],
    verified: list[dict[str, Any]],
    pending: list[dict[str, Any]],
    *,
    search: dict[str, Any] | None = None,
) -> str:
    lines = [
        "# 文献下载最终报告", "", f"**研究主题**: {topic}", "**收集阶段**: 单次下载与校验",
        f"**最终通过校验**: {len(verified)} 篇", f"**仍待处理**: {len(pending)} 篇", "",
    ]

    if search:
        lines.extend([
            "## 一、文献检索专家 Agent 与文献检索员报告", "",
            f"- 检索结果总数: {search.get('total', len(search.get('papers') or []))} 篇",
            f"- 本地库命中: {search.get('local_hits', 0)} 篇",
            f"- 待下载: {search.get('need_download', 0)} 篇",
            f"- 检索来源统计: {_text(', '.join(f'{key}={value}' for key, value in (search.get('by_provider') or {}).items()))}",
            f"- 检索专家状态: {_text((search.get('search_plan') or {}).get('llm', {}).get('status'), 'rules') if isinstance(search.get('search_plan'), dict) else 'rules'}",
            f"- 检索专家说明: {_text((search.get('search_plan') or {}).get('llm', {}).get('reason')) if isinstance(search.get('search_plan'), dict) else 'N/A'}",
            f"- 相关性重排状态: {_text((search.get('relevance') or {}).get('status'), 'rules')}",
            "",
            "### 去重后的文献及来源",
            "",
        ])
        for index, paper in enumerate(search.get("papers") or [], 1):
            lines.append(f"#### [{index}] {_text(paper.get('title'))}")
            lines.extend(_metadata_lines(paper))
            lines.append("")

        # Local-library hits are shared search resources, but their records
        # belong in every task's report just like API results. Older tasks did
        # not persist this list, so retain a useful fallback for them.
        local_rows = list(search.get("local_results") or [])
        if not local_rows:
            local_rows = [
                paper for paper in search.get("papers") or []
                if paper.get("provider") == "LocalLibrary"
                or "LocalLibrary" in (paper.get("providers") or [])
            ]
        lines.extend(["### 本地库命中文献", ""])
        if local_rows:
            for index, paper in enumerate(local_rows, 1):
                lines.append(f"#### [{index}] {_text(paper.get('title'))}")
                lines.extend(_metadata_lines(paper, include_abstract=True))
                lines.extend([
                    f"- 当前 PDF 状态: {_text(paper.get('pdf_status'), 'unknown')}",
                    f"- 当前任务校验状态: {_text(paper.get('verification_status'), '未校验')}",
                    f"- 本地 PDF 路径: {_text(paper.get('pdf_path'), 'N/A')}",
                    f"- 本轮处理: {'已有已校验 PDF，本轮未重新下载' if paper.get('pdf_status') == 'verified' else '未确认本地 PDF 可用性'}",
                    "",
                ])
        elif search.get("local_hits"):
            lines.extend([
                f"本地库命中 {search.get('local_hits')} 篇，但该任务创建于明细保存功能之前，旧快照未保留逐篇元数据。",
                "",
            ])
        else:
            lines.extend(["本次未命中本地文献库。", ""])

    lines.extend(["## 二、文献收集专家报告", ""])
    for round_data in rounds:
        lines.extend([
            "### 本次收集",
            f"- 尝试收集: {round_data.get('attempted', 0)} 篇",
            f"- 成功下载: {round_data.get('downloaded', 0)} 篇",
            f"- 下载失败: {round_data.get('download_failed', 0)} 篇",
            "",
        ])
        for row in round_data.get("papers") or []:
            lines.append(f"#### {_text(row.get('title'))}")
            lines.extend(_metadata_lines(row))
            lines.extend([
                f"- 下载结论: {'成功' if row.get('ok') else '失败'}",
                f"- 实际下载来源: {_text(row.get('source'))}",
                f"- 实际下载 URL: {_url(_successful_attempt_url(row))}",
                f"- 本地存储路径: {_text(row.get('path'))}",
            ])
            if not row.get("ok"):
                lines.append(f"- 最终原因: {_text(row.get('error'), 'unknown')}")
            lines.extend(_attempt_lines(row.get("attempts")))
            lines.append("")

    lines.extend(["## 三、文献检察人员报告", ""])
    for round_data in rounds:
        verification_rows = round_data.get("verification") or []
        if not verification_rows:
            continue
        lines.append("### 本次校验")
        for row in verification_rows:
            lines.append(f"#### {_text(row.get('title'))}")
            lines.extend(_metadata_lines(row))
            lines.extend([
                f"- 下载来源: {_text(row.get('download_source'))}",
                f"- 下载 URL: {_url(row.get('download_url'))}",
                f"- 校验结论: {_text(row.get('verdict'))}",
                f"- 本地文件: {_text(row.get('path'))}",
                f"- 文件大小: {int(row.get('size') or 0) / 1024:.1f} KB",
                f"- 文本字符数: {row.get('text_chars', 0)}",
                f"- 备注: {_text(row.get('reason') or row.get('notes'))}",
                "",
            ])

    lines.extend(["## 四、最终通过校验的文献", ""])
    for index, paper in enumerate(verified, 1):
        lines.append(f"### [{index}] {_text(paper.get('title'))}")
        lines.extend(_metadata_lines(paper))
        lines.extend([
            f"- 校验结论: {_text(paper.get('verification_status'))}",
            f"- 本地存储路径: {_text(paper.get('pdf_path'))}",
            "",
        ])
    if pending:
        lines.extend(["## 五、未完成文献", ""])
        for paper in pending:
            lines.append(f"### {_text(paper.get('title'))}")
            lines.extend(_metadata_lines(paper))
            lines.extend([f"- 处理状态: {_text(paper.get('pdf_status'), 'pending')}", ""])
    return "\n".join(lines)
