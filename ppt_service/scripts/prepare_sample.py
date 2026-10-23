"""Build public-source acceptance inputs from the exact pinned vendor archive.

No network, model request or Office generation. The example numbers are quoted
from upstream documentation and explicitly NOT represented as business data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tarfile

COMMIT = "c350bc31e6d43b7bcf5234183d650ba8f9f4c9d7"
ARCHIVE_SHA256 = "8dc15aa98b9906ee0aba38c2b8b1496b1c3a4db05694c22de023131e81864f98"
FILES = [
    "README.md",
    "skills/ppt-master/references/native-data-interface.md",
    "skills/ppt-master/workflows/stages/resume-execute.md",
    "skills/ppt-master/references/artifact-ownership.md",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.archive.is_symlink() or not args.archive.is_file():
        raise ValueError("Expected a regular pinned vendor archive")
    with args.archive.open("rb") as archive_file:
        if hashlib.file_digest(archive_file, "sha256").hexdigest() != ARCHIVE_SHA256:
            raise ValueError("Pinned vendor archive SHA-256 mismatch")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    assets = []
    with tarfile.open(args.archive, "r:") as archive:
        for index, name in enumerate(FILES, 1):
            info = archive.getmember(name)
            if not info.isfile() or info.size > 4_194_304:
                raise ValueError("Unexpected source member")
            stream = archive.extractfile(info)
            assert stream is not None
            raw = stream.read()
            filename = f"source-{index:02d}-{Path(name).name}"
            (output / filename).write_bytes(raw)
            assets.append({"asset_id": f"public-doc-{index}", "path": f"/work/inputs/{filename}", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "source_url": f"https://github.com/hugohe3/ppt-master/blob/{COMMIT}/{name}", "source_kind": "public_upstream_documentation"})
    from PIL import Image, ImageDraw, ImageFont
    diagram = Image.new("RGB", (1200, 300), "#F1F5F9")
    draw = ImageDraw.Draw(diagram)
    font = ImageFont.load_default(size=24)
    for index, label in enumerate(("Read & clarify", "Confirm outline", "Spec & lock", "Editable draft")):
        x = 24 + index * 296
        draw.rounded_rectangle((x, 72, x + 264, 228), radius=20, fill="#123D59")
        draw.text((x + 22, 100), f"{index + 1:02d}", fill="#61D8B1", font=font)
        draw.text((x + 22, 158), label, fill="white", font=font)
        if index < 3:
            draw.polygon(((x + 270, 144), (x + 286, 156), (x + 270, 168)), fill="#123D59")
    filename = "workflow-illustration.png"
    diagram.save(output / filename)
    raw = (output / filename).read_bytes()
    assets.append({"asset_id": "service-workflow-illustration", "path": f"/work/inputs/{filename}", "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "source_kind": "service_generated_illustration", "description": "由服务根据公开流程绘制的示意图，仅用于图片嵌入验收，不是外部事实或统计证据。"})
    snapshot = hashlib.sha256(json.dumps(assets, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    expected = {"slide": 4, "categories": ["Q1", "Q2", "Q3"], "series": [{"name": "Cloud", "values": [12, 15, 19]}, {"name": "Services", "values": [8, 9, 11]}]}
    task = {"job_id": "acceptance-public-sample", "attempt": 1, "phase": "plan_initial", "session_id": "ppt-public-sample-v1", "input_snapshot_sha256": snapshot, "requirements": {"title": "从资料到可编辑 PPT：公开文档验收样稿", "language": "zh-CN", "pages": 6, "audience": "服务维护者和受邀试用者", "goal": "解释资料忠实、确认流程、原生图表及恢复边界；用于技术验收，不是商业演示", "external_model_acknowledged": True, "template_preference": "free_design", "notes": False, "animations": False, "native_charts": True, "visual_review": True, "layout_acceptance": "至少一页含较长中文段落，至少一页嵌入提供的workflow-illustration.png并标为流程示意。", "chart_requirement": "第4页必须使用public-doc-2的Q1/Q2/Q3、Cloud [12,15,19]、Services [8,9,11]，原生分组柱状图，页上标明文档示例数据、单位未提供。不得称其为任何真实公司的营收或补单位。", "missing_data_test": "源图表示例无业务主体/币种/计量单位；请显式列缺项，保留无单位示例，不推断经营结论。", "final_acceptance": "需要PowerPoint人工打开并编辑图表数据；SVG预览仅作为辅助。"}, "assets": assets}
    (output / "source-manifest.json").write_text(json.dumps({"schema_version": 1, "input_snapshot_sha256": snapshot, "assets": assets}, ensure_ascii=False, indent=2), encoding="utf-8")
    # Keep task and acceptance oracle OUTSIDE the worker's read-only input mount.
    (output.parent / "sample-task.initial.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    (output.parent / "sample-expected-charts.json").write_text(json.dumps([expected], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"assets": len(assets), "input_snapshot_sha256": snapshot, "output": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
