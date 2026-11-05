"""Fail closed unless all four native and four actual Office-rendered PDFs pass.

Requires PyMuPDF only for this acceptance harness, not the application runtime.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
import zipfile

import fitz

from generate_exports import FIXTURE_NAMES, LONG_URL, ROW_COUNT, TALL_REPETITIONS, TALL_SENTENCE


TOLERANCE = 1.5
W = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def compact(text):
    # Soft hyphens inserted by an Office line-break are formatting, not source
    # URL characters. Do not remove ordinary hyphens or other punctuation.
    return "".join(text.replace("\u00ad", "").split())


def in_page(rect, page):
    rect = fitz.Rect(rect)
    bounds = page.rect
    return (bounds.x0 - TOLERANCE <= rect.x0 <= rect.x1 <= bounds.x1 + TOLERANCE and
            bounds.y0 - TOLERANCE <= rect.y0 <= rect.y1 <= bounds.y1 + TOLERANCE)


def inspect_pdf(path, name, renderer, screenshots=None):
    record = {"name": f"{renderer}/{name}.pdf", "path": str(path), "errors": []}
    errors = record["errors"]
    if not path.is_file():
        errors.append("required_pdf_missing")
        return record
    record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        doc = fitz.open(path)
    except Exception as error:
        errors.append("pdf_open_failed:" + type(error).__name__)
        return record
    with doc:
        record["pages"] = len(doc)
        if not len(doc) or doc.is_encrypted:
            errors.append("pdf_empty_or_encrypted")
            return record
        pages = [page.get_text() for page in doc]
        joined = "".join(compact(text) for text in pages)
        required = {
            "overview": ["中文原生表格验收", "证据与大纲", "参考文献", "a|b", "`x|y`",
                         "<w:object>", "<script>literal</script>", "| code | example |"],
            "multipage": ["END-MULTIPAGE"],
            "tall-row": ["BEGIN-TALL", "END-TALL", "AFTER-TALL", "END-DOCUMENT"],
            "long-token": [LONG_URL, "不允许长网址将后续列推出页面。", "END-LONG-DOCUMENT"],
        }[name]
        missing = [marker for marker in required if compact(marker) not in joined]
        if missing:
            errors.append("required_text_missing")
        record["missing_markers"] = missing
        if name == "multipage":
            counts = Counter(re.findall(r"ROW-\d{3}", joined))
            expected = {f"ROW-{number:03d}" for number in range(1, ROW_COUNT + 1)}
            bad = {marker: counts[marker] for marker in sorted(expected) if counts[marker] != 1}
            extras = sorted(set(counts) - expected)
            row_pages = [index + 1 for index, text in enumerate(pages) if re.search(r"ROW-\d{3}", compact(text))]
            header_pages = [index + 1 for index, text in enumerate(pages)
                            if all(header in compact(text) for header in ("编号", "中文工程说明", "检查标记"))]
            record.update(row_counts=dict(sorted(counts.items())), incorrect_row_counts=bad,
                          unexpected_row_markers=extras, row_pages=row_pages, header_pages=header_pages)
            if bad or extras:
                errors.append("each_of_120_rows_must_appear_exactly_once")
            if not set(row_pages).issubset(header_pages):
                errors.append("table_header_missing_on_a_row_page")
            if len(row_pages) < 2:
                errors.append("multipage_table_did_not_span_pages")
        if name == "tall-row":
            # A page can split a sentence in the middle. Remove standalone
            # repeated header labels before rejoining page text, otherwise a
            # correct "...跨页" / "内容必须..." split would count as lost text.
            # Page-number/footer variants are not part of this cell's content.
            body_pages = [page.get_text(clip=fitz.Rect(0, 40, page.rect.width, page.rect.height - 40))
                          for page in doc]
            tall_text = "".join(compact("\n".join(
                line for line in text.splitlines()
                if line.strip() not in {"项目", "内容"} and not re.fullmatch(
                    r"\s*(?:Page\s*)?\d+(?:\s*(?:/|of)\s*\d+)?\s*", line, re.I)))
                for text in body_pages)
            count = tall_text.count(TALL_SENTENCE)
            record["complete_tall_sentences"] = count
            if count != TALL_REPETITIONS:
                errors.append("all_240_tall_cell_sentences_must_survive")
            for marker in ("BEGIN-TALL", "END-TALL", "AFTER-TALL", "END-DOCUMENT"):
                if joined.count(marker) != 1:
                    errors.append("tall_cell_marker_not_unique:" + marker)
            if len(doc) < 2:
                errors.append("tall_cell_did_not_span_pages")
        if name == "long-token" and joined.count(LONG_URL) != 1:
            errors.append("long_url_must_survive_exactly_once")

        outside, bad_links, internal_links = [], [], 0
        for page_index, page in enumerate(doc):
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        if not in_page(span["bbox"], page):
                            outside.append({"page": page_index + 1, "bbox": list(span["bbox"]),
                                            "text": span["text"][:80]})
            for link in page.get_links():
                if not in_page(link["from"], page):
                    bad_links.append({"page": page_index + 1, "reason": "link_rectangle_outside_page"})
                if link["kind"] == fitz.LINK_GOTO:
                    target = link.get("page", -1)
                    if not 0 <= target < len(doc):
                        bad_links.append({"page": page_index + 1, "reason": "invalid_internal_target_page"})
                        continue
                    point = link.get("to")
                    if point is not None and not in_page(fitz.Rect(point, point), doc[target]):
                        bad_links.append({"page": page_index + 1, "reason": "internal_target_outside_page"})
                    internal_links += 1
                elif link["kind"] != fitz.LINK_NONE:
                    # Synthetic source documents have only local citation
                    # hyperlinks; raw external URLs are deliberately plain text.
                    bad_links.append({"page": page_index + 1, "reason": "unexpected_nonlocal_link",
                                      "kind": link["kind"]})
        record.update(outside_page=outside, invalid_links=bad_links,
                      internal_links=internal_links, text_characters=sum(map(len, pages)))
        if outside:
            errors.append("text_outside_page")
        if bad_links:
            errors.append("invalid_or_nonlocal_link")
        if name == "overview" and internal_links < 2:
            errors.append("prose_and_table_citations_must_both_be_internal_links")
        path.with_suffix(".txt").write_text("\n".join(pages), encoding="utf-8")
        if screenshots is not None:
            output = screenshots / renderer
            output.mkdir(parents=True, exist_ok=True)
            for index in sorted({0, min(1, len(doc) - 1), len(doc) - 1}):
                doc[index].get_pixmap(matrix=fitz.Matrix(1.25, 1.25)).save(
                    output / f"{name}-page-{index + 1}.png")
    return record


def inspect(root, office, *, screenshots=False, office_version=None):
    errors, sources = [], []
    manifest_path = root / "exports.json"
    try:
        manifest = {row["name"]: row for row in json.loads(manifest_path.read_text(encoding="utf-8"))}
    except (OSError, ValueError, KeyError, TypeError):
        manifest = {}
        errors.append("required_generation_manifest_missing_or_invalid")
    for name in FIXTURE_NAMES:
        for extension in ("docx", "pdf"):
            path = root / f"{name}.{extension}"
            if not path.is_file():
                errors.append("required_generated_source_missing:" + path.name)
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if manifest.get(path.name, {}).get("sha256") != digest:
                errors.append("generated_source_hash_mismatch:" + path.name)
            sources.append({"name": path.name, "sha256": digest})
            if extension == "docx":
                try:
                    with zipfile.ZipFile(path) as archive:
                        document = ET.fromstring(archive.read("word/document.xml"))
                        tables = document.findall(".//w:tbl", W)
                        if len(tables) != 1 or not tables[0].findall("w:tr/w:tc", W):
                            errors.append("docx_must_contain_one_native_editable_table:" + path.name)
                except (OSError, KeyError, ValueError, zipfile.BadZipFile, ET.ParseError):
                    errors.append("docx_source_invalid:" + path.name)
    records = [inspect_pdf(folder / f"{name}.pdf", name, renderer,
                           root / "screenshots" if screenshots else None)
               for renderer, folder in (("native", root), ("office", office)) for name in FIXTURE_NAMES]
    return {"status": "passed" if not errors and not any(row["errors"] for row in records) else "failed",
            "office_version_reported_by_operator": office_version,
            "pdf_inspector": "PyMuPDF " + str(fitz.VersionBind), "required_pdf_count": 8,
            "source_files": sources, "errors": errors, "files": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Directory previously populated by generate_exports.py")
    parser.add_argument("--office-dir", type=Path, default=Path("office"),
                        help="Office PDF directory; relative to output unless absolute (default: office)")
    parser.add_argument("--office-version", help="Actual libreoffice --version output, recorded without inference")
    parser.add_argument("--screenshots", action="store_true", help="Save first, second and last PDF pages as PNG")
    args = parser.parse_args()
    root = args.output.resolve()
    office = args.office_dir if args.office_dir.is_absolute() else root / args.office_dir
    receipt = inspect(root, office, screenshots=args.screenshots, office_version=args.office_version)
    (root / "rendered-checks.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if receipt["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
