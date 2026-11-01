"""Render the exact delivered PPTX with fixed, image-owned Office tools.

This runs only inside the network-isolated independent validator. It never
executes project programs, rewrites the deck, accepts a human review or grants
publication. Its fresh PNGs describe LibreOffice's rendering of the final PPTX,
separately from SVG previews and actual desktop PowerPoint acceptance.
"""
from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import posixpath
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET
import zipfile

from .execution import ControlError
from .render_validation import fingerprint, hash_file, stop_group
from .staging import atomic_json, copy_file, open_regular

SCHEMA = "ppt-service.final-pptx-render.v1"
FINAL_PPTX_GATE = "independent_final_pptx_rendering"
LIBREOFFICE = "/usr/bin/libreoffice"
PDFINFO = "/usr/bin/pdfinfo"
PDFTOPPM = "/usr/bin/pdftoppm"
FONTLIST = "/usr/bin/fc-list"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def _require(value, code):
    if not value:
        raise ControlError(code)


def _small_text(path, limit=1024 * 1024):
    with open_regular(path.parent, path.name, limit) as stream:
        return stream.read(limit + 1).decode("utf-8", errors="replace")


def _fresh(path):
    _require(not path.exists() and not path.is_symlink(), "final_pptx_output_not_fresh")
    for parent in path.parents:
        _require(not parent.is_symlink() and not parent.is_junction(), "final_pptx_output_linked")
    path.mkdir(parents=True)


def _tool_identity(path):
    """Hash the resolved fixed image tool, never a project-selected executable."""
    target = Path(path).resolve(strict=True)
    _require(target.is_file() and os.access(target, os.X_OK), "final_pptx_renderer_unavailable")
    # Distribution executables can have multiple hardlinks; these are trusted
    # immutable image files, not untrusted copied candidate files.
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return {"path": path, "resolved_path": str(target), "sha256": digest.hexdigest()}


def _deck_contract(pptx, expected_pages):
    """Measure native parts without treating them as desktop edit evidence."""
    from ppt_service.worker.candidate_validation import inspect_pptx
    native = inspect_pptx(pptx)
    _require(native["slide_count"] == expected_pages, "final_pptx_page_count_mismatch")
    with zipfile.ZipFile(pptx) as package:
        presentation = ET.fromstring(package.read("ppt/presentation.xml"))
        relationships = ET.fromstring(package.read("ppt/_rels/presentation.xml.rels"))
        targets = {node.get("Id"): posixpath.normpath("ppt/" + node.get("Target", ""))
                   for node in relationships
                   if node.get("Type") == "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
                   and node.get("TargetMode", "Internal") == "Internal"}
        ordered_slides = [targets.get(node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
                          for node in presentation.findall(_P + "sldIdLst/" + _P + "sldId")]
        actual_slides = {name for name in package.namelist()
                         if re.fullmatch(r"ppt/slides/[^/]+\.xml", name)}
        _require(len(ordered_slides) == expected_pages and set(ordered_slides) == actual_slides,
                 "final_pptx_page_count_mismatch")
        size = presentation.find(_P + "sldSz")
        try:
            cx, cy = int(size.get("cx")), int(size.get("cy"))
        except (AttributeError, TypeError, ValueError):
            raise ControlError("final_pptx_canvas_invalid") from None
        _require(0 < cx <= 100_000_000 and 0 < cy <= 100_000_000,
                 "final_pptx_canvas_invalid")
        width, height = ((1280, round(1280 * cy / cx)) if cx >= cy else
                         (round(1280 * cx / cy), 1280))
        _require(min(width, height) >= 64, "final_pptx_canvas_invalid")
        counts = {"native_shapes": 0, "text_shapes": 0, "picture_nodes": 0,
                  "semantic_connectors": 0, "connected_connectors": 0}
        for name in ordered_slides:
            slide = ET.fromstring(package.read(name))
            shapes = list(slide.iter(_P + "sp"))
            counts["native_shapes"] += len(shapes)
            counts["text_shapes"] += sum(any((n.text or "").strip() for n in shape.iter(_A + "t"))
                                          for shape in shapes)
            counts["picture_nodes"] += len(list(slide.iter(_P + "pic")))
            connectors = list(slide.iter(_P + "cxnSp"))
            counts["semantic_connectors"] += len(connectors)
            counts["connected_connectors"] += sum(
                connector.find(".//" + _A + "stCxn") is not None
                and connector.find(".//" + _A + "endCxn") is not None
                for connector in connectors)
    return {"slide_count": expected_pages, "width_emu": cx, "height_emu": cy,
            "width": width, "height": height, "notes_count": native["notes_count"],
            "chart_count": native["chart_count"], **counts}


class OfficeCommands:
    """Bounded fixed-tool commands with a new profile and no inherited secrets."""

    def __init__(self, runtime):
        self.runtime = runtime

    def run(self, args, stdout, stderr, *, timeout=120):
        environment = {
            "PATH": "/usr/bin:/bin", "HOME": str(self.runtime / "home"),
            "TMPDIR": str(self.runtime / "tmp"), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
            "SAL_USE_VCLPLUGIN": "svp", "SAL_DISABLE_OPENCL": "1",
        }
        with open(stdout, "xb") as out, open(stderr, "xb") as err:
            process = subprocess.Popen(args, cwd=self.runtime, env=environment,
                stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            failure = None
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                failure = "final_pptx_render_timeout"
            finally:
                stopped = stop_group(process)
            _require(stopped, "final_pptx_renderer_cleanup_failed")
            if failure:
                raise ControlError(failure)
            return code


def verify_final_pptx(pptx, expected_pptx, request, output, *, commands_factory=None):
    """Return retained audit evidence for exact PPTX bytes, with no authority.

    ``request`` is controller-created and must carry nonce, source-manifest hash,
    input-snapshot hash and page count. The external caller separately proves
    the validator Pod stopped and freezes this directory before consuming it.
    No JSON receipt supplied by an Actor can stand in for that transport proof.
    """
    pptx, output = Path(pptx).absolute(), Path(output).absolute()
    try:
        nonce = request.get("validation_nonce")
        _require(isinstance(nonce, str) and str(uuid.UUID(nonce)) == nonce,
                 "final_pptx_binding_invalid")
    except (ValueError, AttributeError):
        raise ControlError("final_pptx_binding_invalid") from None
    _require(all(isinstance(request.get(key), str) and _SHA.fullmatch(request[key])
                 for key in ("source_manifest_sha256", "input_snapshot_sha256"))
             and type(request.get("pages")) is int and 3 <= request["pages"] <= 20,
             "final_pptx_binding_invalid")
    _require(isinstance(expected_pptx, dict) and isinstance(expected_pptx.get("sha256"), str)
             and _SHA.fullmatch(expected_pptx["sha256"])
             and type(expected_pptx.get("bytes")) is int and 0 < expected_pptx["bytes"] <= 64 * 1024**2,
             "final_pptx_binding_invalid")
    before = hash_file(pptx.parent, pptx.name)
    _require(all(before[key] == expected_pptx[key] for key in ("bytes", "sha256")),
             "final_pptx_source_mismatch")
    _require(not output.is_relative_to(pptx.parent), "final_pptx_output_not_fresh")
    _fresh(output)
    deck = _deck_contract(pptx, request["pages"])
    runtime = output / "runtime"
    for name in ("home", "tmp", "profile/user"):
        (runtime / name).mkdir(parents=True)
    # Never execute macros or update document links while loading an untrusted
    # deck. The surrounding validator also has no network or model/DB secrets.
    (runtime / "profile/user/registrymodifications.xcu").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item>'
        '<item oor:path="/org.openoffice.Office.Common/Load">'
        '<prop oor:name="UpdateLinkMode" oor:op="fuse"><value>0</value></prop></item>'
        '</oor:items>', encoding="utf-8")
    commands = (commands_factory or OfficeCommands)(runtime)
    tools = {}
    try:
        for key, path, flags in (
            ("libreoffice", LIBREOFFICE, ["--version"]),
            ("pdfinfo", PDFINFO, ["-v"]),
            ("pdftoppm", PDFTOPPM, ["-v"]),
            ("fontconfig", FONTLIST, ["--version"]),
        ):
            tools[key] = _tool_identity(path)
            stdout, stderr = output / (key + "-version.txt"), output / (key + "-version.stderr.txt")
            _require(commands.run([path, *flags], stdout, stderr, timeout=15) == 0,
                     "final_pptx_renderer_unavailable")
            version = (_small_text(stdout, 65536) + "\n" + _small_text(stderr, 65536)).strip()
            _require(bool(version), "final_pptx_renderer_unavailable")
            tools[key]["version"] = version[:2000]
        tools["soffice_binary"] = _tool_identity("/usr/lib/libreoffice/program/soffice.bin")
    except (FileNotFoundError, PermissionError):
        raise ControlError("final_pptx_renderer_unavailable") from None
    fonts = output / "font-inventory.txt"
    _require(commands.run([FONTLIST, "--format", "%{family}\t%{style}\t%{file}\n"],
                          fonts, output / "font-inventory.stderr.txt", timeout=30) == 0
             and bool(_small_text(fonts).strip()), "final_pptx_font_inventory_failed")
    source = output / "input/presentation.pptx"
    copy_file(pptx.parent, pptx.name, source, expected_hash=before["sha256"])
    pdf_dir, raw_pngs, pages_dir = output / "pdf", output / "raster", output / "pages"
    for folder in (pdf_dir, raw_pngs, pages_dir):
        folder.mkdir()
    export_args = [LIBREOFFICE, "-env:UserInstallation=" + (runtime / "profile").as_uri(),
        "--headless", "--nologo", "--nodefault", "--norestore", "--nofirststartwizard",
        "--convert-to", "pdf:impress_pdf_Export", "--outdir", str(pdf_dir), str(source)]
    _require(commands.run(export_args, output / "export.stdout.txt", output / "export.stderr.txt", timeout=150) == 0,
             "final_pptx_export_failed")
    _require({p.name for p in pdf_dir.iterdir()} == {"presentation.pdf"}, "final_pptx_pdf_missing")
    pdf = pdf_dir / "presentation.pdf"
    pdf_row = hash_file(pdf_dir, pdf.name, 128 * 1024**2)
    with open_regular(pdf_dir, pdf.name, 128 * 1024**2) as stream:
        _require(stream.read(5) == b"%PDF-", "final_pptx_pdf_invalid")
    info_path = output / "pdfinfo.txt"
    _require(commands.run([PDFINFO, "-f", "1", "-l", str(request["pages"]), "-box", str(pdf)],
                          info_path, output / "pdfinfo.stderr.txt", timeout=30) == 0,
             "final_pptx_pdf_invalid")
    information = _small_text(info_path, 65536)
    pages = re.findall(r"^Pages:\s*([0-9]+)\s*$", information, re.M)
    _require(len(pages) == 1 and int(pages[0]) == request["pages"], "final_pptx_page_count_mismatch")
    sizes = re.findall(r"^Page\s+([0-9]+) size:\s*([0-9.]+) x ([0-9.]+) pts(?:\s.*)?$", information, re.M)
    rotations = re.findall(r"^Page\s+([0-9]+) rot:\s*([0-9]+)\s*$", information, re.M)
    _require([int(row[0]) for row in sizes] == list(range(1, request["pages"] + 1))
             and [(int(ordinal), int(angle)) for ordinal, angle in rotations]
                 == [(ordinal, 0) for ordinal in range(1, request["pages"] + 1)],
             "final_pptx_canvas_invalid")
    for _, raw_width, raw_height in sizes:
        try:
            page_width, page_height = float(raw_width), float(raw_height)
        except ValueError:
            raise ControlError("final_pptx_canvas_invalid") from None
        _require(all(math.isfinite(v) and 0 < v < 10000 for v in (page_width, page_height))
                 and abs(page_width / page_height - deck["width_emu"] / deck["height_emu"]) < 0.005,
                 "final_pptx_canvas_invalid")
    _require(commands.run([PDFTOPPM, "-png", "-scale-to-x", str(deck["width"]),
        "-scale-to-y", str(deck["height"]), str(pdf), str(raw_pngs / "slide")],
        output / "raster.stdout.txt", output / "raster.stderr.txt", timeout=150) == 0,
        "final_pptx_raster_failed")
    raster = {}
    for path in raw_pngs.iterdir():
        match = re.fullmatch(r"slide-([0-9]+)\.png", path.name)
        _require(match is not None and int(match[1]) not in raster, "final_pptx_raster_roster_invalid")
        raster[int(match[1])] = path
    _require(set(raster) == set(range(1, request["pages"] + 1)), "final_pptx_raster_roster_invalid")
    from PIL import Image
    result_pages = []
    for ordinal in range(1, request["pages"] + 1):
        path = raster[ordinal]
        row = hash_file(raw_pngs, path.name, 8 * 1024**2)
        with open_regular(raw_pngs, path.name, 8 * 1024**2) as stream, Image.open(stream) as image:
            _require(image.format == "PNG" and image.size == (deck["width"], deck["height"]),
                     "final_pptx_raster_canvas_invalid")
            image.verify()
        target = f"pages/{ordinal}.png"
        copy_file(raw_pngs, path.name, output / target, expected_hash=row["sha256"])
        result_pages.append({"ordinal": ordinal, "path": target, "sha256": row["sha256"],
                             "bytes": row["bytes"], "width": deck["width"], "height": deck["height"]})
    _require(hash_file(pptx.parent, pptx.name) == before
             and hash_file(source.parent, source.name)["sha256"] == before["sha256"]
             and hash_file(pdf_dir, pdf.name, 128 * 1024**2) == pdf_row, "final_pptx_source_changed")
    payload = {
        "schema": SCHEMA, "status": "passed", "publication_authorized": False,
        "full_exporter_postflight_verified": False, "human_visual_acceptance": False,
        "powerpoint_acceptance": "pending", "controller_nonce": nonce,
        "source_manifest_sha256": request["source_manifest_sha256"],
        "input_snapshot_sha256": request["input_snapshot_sha256"],
        "pptx": {"sha256": before["sha256"], "bytes": before["bytes"]},
        "renderer": {"name": "LibreOffice Impress", "version": tools["libreoffice"]["version"],
            "tools": tools, "font_inventory": hash_file(output, fonts.name),
            "font_inventory_sha256": hash_file(output, fonts.name)["sha256"],
            "runtime_image": os.environ.get("PPT_RUNTIME_WORKER_IMAGE"),
            "pipeline": "PPTX → LibreOffice PDF → Poppler PNG", "isolated_profile": True},
        "deck": deck, "pdf": {**pdf_row, "path": "pdf/presentation.pdf"},
        "pages": result_pages, "page_roster_sha256": fingerprint(result_pages),
        "export_exit_code": 0, "raster_exit_code": 0, "command_groups_stopped": True,
        "completed_programmatic_gates": [FINAL_PPTX_GATE],
        "source_unchanged": True, "validator_stop_proof_requires_controller": True,
    }
    atomic_json(output / "final-pptx-evidence.json", payload)
    return payload
