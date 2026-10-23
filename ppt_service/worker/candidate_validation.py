"""Static checks on a stopped writer's PRIVATE candidate snapshot.

Caller MUST stop the whole Pod/process tree, then make a private no-follow copy.
Path/lstat checks cannot prevent concurrent rename/write races. This module does
not copy/publish, execute Office, follow links, verify upstream gates, or prove
visual/factual quality. Run these bounded parsers in a restricted process.
Artifact paths are relative to project_root inside snapshot_root.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import io
from pathlib import Path
import posixpath
import re
import stat
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import zipfile
import zlib


class ValidationError(ValueError):
    """Invalid candidate or explicitly unsupported feature."""


@dataclass(frozen=True)
class Limits:
    max_files: int = 2048
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_zip_entries: int = 4096
    max_uncompressed_bytes: int = 256 * 1024 * 1024
    max_part_bytes: int = 32 * 1024 * 1024
    max_xml_bytes: int = 8 * 1024 * 1024
    max_xml_nodes: int = 100000
    max_xml_depth: int = 100
    max_compression_ratio: int = 1000
    max_slides: int = 200
    max_charts: int = 200
    max_cells: int = 100000


NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
RID = "{" + NS["r"] + "}id"
REL = NS["r"] + "/"
SHA = re.compile(r"[0-9a-f]{64}\Z")


def _fail(message):
    raise ValidationError(message)


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 1024
            or any(c in value for c in "\\:%?#")
            or any(ord(c) < 32 for c in value)
            or any(p in {"", ".", ".."} for p in value.split("/"))):
        _fail("Path must be relative POSIX without traversal, drive or URI")
    return value


def _no_links(path, *, directory=False):
    """Check all ancestors too; deliberately NOT a race-safe open."""
    absolute = Path(path).absolute()
    for component in (*reversed(absolute.parents), absolute):
        try:
            info = component.lstat()
        except OSError as exc:
            raise ValidationError("Snapshot path missing/unreadable") from exc
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            _fail("Symlink/reparse point in snapshot path")
        if component != absolute and not stat.S_ISDIR(info.st_mode):
            _fail("Non-directory snapshot path component")
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            _fail("Snapshot root must be a directory")
    elif not stat.S_ISREG(info.st_mode):
        _fail("Artifact must be a regular file")
    return absolute, info


def _digest(path, limits):
    path, info = _no_links(path)
    if info.st_size > limits.max_file_bytes:
        _fail("Artifact exceeds file size limit")
    digest, size = hashlib.sha256(), 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > limits.max_file_bytes:
                    _fail("Artifact exceeds file size limit")
                digest.update(chunk)
    except OSError as exc:
        raise ValidationError("Cannot read artifact") from exc
    if size != info.st_size:
        _fail("Snapshot changed; caller must guarantee quiescence")
    return size, digest.hexdigest()


def validate_manifest(snapshot_root, manifest, *, expected_charts=None, limits=Limits()):
    """Validate schema v1 declarations. Reports remain untrusted.

    Quality may contain nested {path, sha256?} references or lists of declared
    artifact paths. Only reference/hash integrity is checked. No upstream report
    is fabricated and no pass/fail claim is interpreted as a successful gate.
    expected_charts must come from the trusted caller, never from manifest data;
    without it, chart inspection is explicitly structural only.
    """
    root, _ = _no_links(snapshot_root, directory=True)
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        _fail("Expected manifest schema_version 1")
    states = {
        "plan_initial": {"awaiting_confirmation", "needs_input"},
        "plan_options": {"awaiting_confirmation", "needs_input"},
        "prepare": {"prepared", "needs_input"},
        "generate": {"candidate", "needs_input"},
        "revise": {"candidate", "needs_input"},
    }
    phase, status = manifest.get("phase"), manifest.get("status")
    if not isinstance(phase, str) or phase not in states or not isinstance(status, str) or status not in states[phase]:
        _fail("Invalid phase/status pair")
    if not isinstance(manifest.get("input_snapshot_sha256"), str) or not SHA.fullmatch(manifest["input_snapshot_sha256"]):
        _fail("Invalid input snapshot SHA256")
    project = root / _relative(manifest.get("project_root"))
    _no_links(project, directory=True)
    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list) or len(artifacts) > limits.max_files:
        _fail("Invalid artifact list/too many artifacts")
    indexed, folded, roles, total = {}, set(), set(), 0
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            _fail("Artifact must be an object")
        name = _relative(artifact.get("path"))
        if name.casefold() in folded:
            _fail("Duplicate artifact path")
        folded.add(name.casefold())
        size, digest = artifact.get("bytes"), artifact.get("sha256")
        if type(size) is not int or size < 0 or size > limits.max_file_bytes:
            _fail("Invalid artifact size")
        if not isinstance(digest, str) or not SHA.fullmatch(digest):
            _fail("Invalid artifact SHA256")
        if not isinstance(artifact.get("role"), str) or not artifact["role"]:
            _fail("Missing artifact role")
        if not isinstance(artifact.get("media_type"), str) or not artifact["media_type"]:
            _fail("Missing artifact media_type")
        total += size
        if total > limits.max_total_bytes:
            _fail("Candidate exceeds total size limit")
        actual_size, actual_hash = _digest(project / name, limits)
        if actual_size != size or actual_hash != digest:
            _fail("Artifact size/SHA256 mismatch: " + name)
        indexed[name] = artifact
        roles.add(artifact["role"])
    reports = []

    def reference(name):
        _relative(name)
        if name not in indexed:
            _fail("Reference is not a declared hashed artifact: " + name)
        return indexed[name]

    descriptive_quality = phase in {"plan_initial", "plan_options"}

    def quality_refs(value, depth=0):
        if depth > 32:
            _fail("Quality references too deeply nested")
        if isinstance(value, dict):
            if "path" in value:
                artifact = reference(value["path"])
                if "sha256" in value and value["sha256"] != artifact["sha256"]:
                    _fail("Quality report reference hash mismatch")
                reports.append(value["path"])
                return  # Extra upstream claims are deliberately not interpreted.
            for item in value.values():
                quality_refs(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                quality_refs(item, depth + 1)
        elif isinstance(value, str):
            # Planning has no final quality reports. Prose/status claims are
            # ignored, never promoted to evidence. Explicit {path: ...}
            # references above are checked even during planning.
            if descriptive_quality and value not in indexed:
                return
            reference(value)
            reports.append(value)
        elif value is not None and not descriptive_quality:
            _fail("Quality evidence must contain report references")

    quality_refs(manifest.get("quality", {}))
    slides = manifest.get("slides", [])
    if not isinstance(slides, list) or len(slides) > limits.max_slides:
        _fail("Invalid slides list")
    generated = phase in {"generate", "revise"} and status == "candidate"
    for ordinal, slide in enumerate(slides, 1):
        if not isinstance(slide, dict) or type(slide.get("ordinal")) is not int or slide["ordinal"] != ordinal:
            _fail("Slide ordinals must be sequential starting at one")
        for key in ("svg_path", "preview_path"):
            if slide.get(key):
                reference(slide[key])
        if generated and not slide.get("preview_path"):
            _fail("Generated slide lacks preview reference")
    inspections = []
    if generated:
        if "pptx" not in roles or not slides:
            _fail("Generated candidate needs PPTX and slide previews")
        for artifact in artifacts:
            if artifact["role"] == "pptx":
                # The candidate cannot supply its own acceptance oracle.
                result = inspect_pptx(project / artifact["path"], expected_charts, limits=limits)
                if result["slide_count"] != len(slides):
                    _fail("PPTX slide count differs from manifest")
                inspections.append(result)
    return {"phase": phase, "status": status, "artifact_count": len(indexed),
            "total_bytes": total, "quality_report_references": reports,
            "quality_reports_verified": False, "pptx": inspections, "published": False}


class _Budget:
    def __init__(self, limits):
        self.limits, self.entries, self.expanded = limits, 0, 0


class _Package:
    def __init__(self, data, budget, *, allow_ppt_printer_settings=False):
        self.budget, self.xmls, self.relationships = budget, {}, {}
        printer_parts = set()
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise ValidationError("Invalid/encrypted OOXML ZIP") from exc
        self.parts, folded = {}, set()
        for entry in self.zip.infolist():
            budget.entries += 1
            if budget.entries > budget.limits.max_zip_entries:
                _fail("ZIP entry count limit exceeded")
            # ZipInfo.filename normalizes backslashes on Windows and truncates
            # at NUL; inspect the original archive spelling before trusting it.
            name = entry.orig_filename
            mode = entry.external_attr >> 16
            if entry.flag_bits & 1:
                _fail("Encrypted ZIP member")
            if entry.is_dir():
                _relative(name[:-1])
                if stat.S_IFMT(mode) not in {0, stat.S_IFDIR}:
                    _fail("Non-directory ZIP directory member")
                continue
            _relative(name)
            if name.casefold() in folded:
                _fail("Duplicate ZIP member")
            folded.add(name.casefold())
            if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
                _fail("Non-regular ZIP member")
            if entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                _fail("Unsupported ZIP compression")
            budget.expanded += entry.file_size
            if entry.file_size > budget.limits.max_part_bytes or budget.expanded > budget.limits.max_uncompressed_bytes:
                _fail("ZIP expanded byte limit exceeded")
            if entry.file_size > max(1, entry.compress_size) * budget.limits.max_compression_ratio:
                _fail("ZIP compression ratio limit exceeded")
            lower = name.lower()
            printer = (allow_ppt_printer_settings and re.fullmatch(
                r"ppt/printerSettings/printerSettings[1-9][0-9]*\.bin", name))
            if (any(s in lower for s in ("vbaproject", "activex", "oleobject", "encryptedpackage", "encryptioninfo"))
                    or lower.endswith(".vba") or (lower.endswith(".bin") and not printer)):
                _fail("Macros, ActiveX, OLE and encrypted parts forbidden")
            if printer:
                printer_parts.add(name)
            self.parts[name] = entry
        if "[Content_Types].xml" not in self.parts:
            _fail("Missing OOXML content types")
        for name in self.parts:
            if name.endswith((".xml", ".rels")):
                for node in self.xml(name).iter():
                    local = node.tag.rsplit("}", 1)[-1].lower()
                    if local in {"oleobj", "oleobject", "control", "controls", "externalreference", "externalreferences"}:
                        _fail("Active content/external workbook references forbidden")
                    for value in node.attrib.values():
                        if any(x in value.lower() for x in ("macroenabled", "vbaproject", "activex", "oleobject")):
                            _fail("Active content type forbidden")
        for name in self.parts:
            if name.endswith(".rels"):
                self._parse_relationships(name)

        if printer_parts:
            self._check_printer_settings(printer_parts)

    def _content_type(self, name):
        types = self.xml("[Content_Types].xml")
        ct = "{http://schemas.openxmlformats.org/package/2006/content-types}"
        if types.tag != ct + "Types":
            _fail("Invalid compatibility content types root")
        defaults, overrides = {}, {}
        for node in types:
            if node.tag == ct + "Default":
                key, mapping = node.get("Extension", "").lower(), defaults
            elif node.tag == ct + "Override":
                key, mapping = node.get("PartName", ""), overrides
            else:
                _fail("Invalid compatibility content type declaration")
            if not key or key in mapping or not node.get("ContentType"):
                _fail("Invalid/duplicate compatibility content type declaration")
            mapping[key] = node.get("ContentType")
        return overrides.get("/" + name, defaults.get(name.rsplit(".", 1)[-1].lower()))

    def _check_printer_settings(self, parts):
        """One passive, implicitly referenced PresentationML printer part only.

        Printer data is implementation-defined (including Mac print records),
        not necessarily DEVMODE. Never execute it or treat it as another package.
        The exception is disabled for every embedded workbook.
        """
        if len(parts) != 1:
            _fail("Only one presentation printerSettings part supported")
        part = next(iter(parts))
        presentation = "ppt/presentation.xml"
        if (self._content_type(part) != "application/vnd.openxmlformats-officedocument.presentationml.printerSettings"
                or self._content_type(presentation) != "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"):
            _fail("Wrong printerSettings/presentation content type")
        office = [(kind, target) for kind, target in self.relationships.get("", {}).values()
                  if kind == REL + "officeDocument"]
        if (office != [(REL + "officeDocument", presentation)]
                or self.xml(presentation).tag != "{" + NS["p"] + "}presentation"):
            _fail("printerSettings requires the main presentation source")
        incoming = [(source, kind) for source, relationships in self.relationships.items()
                    for kind, target in relationships.values() if target == part]
        printer_rels = [(source, target) for source, relationships in self.relationships.items()
                        for kind, target in relationships.values() if kind == REL + "printerSettings"]
        if (incoming != [(presentation, REL + "printerSettings")]
                or printer_rels != [(presentation, part)] or part in self.relationships):
            _fail("printerSettings requires one implicit internal presentation reference and no outgoing relationships")
        data = self.read(part)  # Also enforce CRC and existing byte limits.
        if (not data or any(signature in data for signature in
                            (bytes.fromhex("d0cf11e0a1b11ae1"), b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))
                or data.startswith((b"MZ", b"\x7fELF"))):
            _fail("OLE, ZIP or executable data disguised as printerSettings")

    def read(self, name):
        if name not in self.parts:
            _fail("Missing package part: " + name)
        try:
            with self.zip.open(self.parts[name]) as stream:
                data = stream.read(self.budget.limits.max_part_bytes + 1)
        except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, EOFError, zlib.error) as exc:
            raise ValidationError("Cannot read ZIP member") from exc
        if len(data) != self.parts[name].file_size or len(data) > self.budget.limits.max_part_bytes:
            _fail("ZIP member size mismatch")
        return data

    def xml(self, name):
        if name in self.xmls:
            return self.xmls[name]
        if name not in self.parts or self.parts[name].file_size > self.budget.limits.max_xml_bytes:
            _fail("Missing/oversized XML part")
        try:
            text = self.read(name).decode("utf-8-sig")
            if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                _fail("DTD/entities and non-UTF8 XML forbidden")
            root = ET.fromstring(text)
        except (UnicodeError, ET.ParseError) as exc:
            raise ValidationError("Malformed/unsupported XML") from exc
        count, pending = 0, [(root, 1)]
        while pending:
            node, depth = pending.pop()
            count += 1
            if count > self.budget.limits.max_xml_nodes or depth > self.budget.limits.max_xml_depth:
                _fail("XML node/depth limit exceeded")
            pending.extend((child, depth + 1) for child in node)
        self.xmls[name] = root
        return root

    def _parse_relationships(self, name):
        if name == "_rels/.rels":
            source = ""
        else:
            folder, base = posixpath.split(name)
            if posixpath.basename(folder) != "_rels":
                _fail("Invalid relationship part path")
            source = posixpath.join(posixpath.dirname(folder), base[:-5])
            if source not in self.parts:
                _fail("Relationship source missing")
        root = self.xml(name)
        if root.tag != "{" + NS["pr"] + "}Relationships":
            _fail("Invalid relationships XML root")
        found = {}
        for node in root:
            if node.tag != "{" + NS["pr"] + "}Relationship":
                _fail("Invalid relationship element")
            rid, kind, target = node.get("Id"), node.get("Type", ""), node.get("Target", "")
            metadata = {
                NS["pr"] + "/metadata/core-properties": (
                    "docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                NS["pr"] + "/metadata/thumbnail": (
                    "docProps/thumbnail.jpeg", "image/jpeg"),
            }
            if (not rid or rid in found
                    or (not kind.startswith(REL) and kind not in metadata)):
                _fail("Invalid/duplicate relationship id/type")
            if kind in metadata and (source or any(k == kind for k, _ in found.values())):
                _fail("Metadata relationship must be unique and package-root sourced")
            if any(x in kind.lower() for x in ("oleobject", "vbaproject", "activex", "control", "externallink")):
                _fail("Active/external workbook relationship forbidden")
            mode = node.get("TargetMode", "Internal")
            if mode == "External":
                try:
                    url = urlsplit(target)
                except ValueError as exc:
                    raise ValidationError("Malformed external URL") from exc
                if (kind != REL + "hyperlink" or url.scheme != "https" or not url.hostname
                        or url.username or url.password or "\\" in target or any(ord(c) < 32 for c in target)):
                    _fail("Only ordinary HTTPS hyperlinks may be external")
                found[rid] = (kind, None)
                continue
            if mode != "Internal" or not target or any(c in target for c in "\\:%?#") or target.startswith("/") or any(ord(c) < 32 for c in target):
                _fail("Unsafe internal relationship target")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(source), target))
            _relative(resolved)
            if resolved not in self.parts:
                _fail("Relationship target missing")
            if kind in metadata:
                expected_path, expected_type = metadata[kind]
                if target != expected_path or self._content_type(resolved) != expected_type:
                    _fail("Wrong standard metadata target/content type")
                if kind.endswith("/core-properties"):
                    if self.xml(resolved).tag != "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}coreProperties":
                        _fail("Invalid core properties root")
                elif not self.read(resolved).startswith(b"\xff\xd8\xff"):
                    _fail("Invalid JPEG metadata thumbnail")
            found[rid] = (kind, resolved)
        self.relationships[source] = found

    def related(self, source, rid, kind):
        relationship = self.relationships.get(source, {}).get(rid)
        if not relationship or relationship[0] != REL + kind or relationship[1] is None:
            _fail("Missing/external/wrongly typed " + kind + " relationship")
        return relationship[1]


_RANGE = re.compile(r"(?P<sheet>'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_ .]*)!\$(?P<c1>[A-Z]{1,3})\$(?P<r1>[1-9][0-9]*)(?::\$(?P<c2>[A-Z]{1,3})\$(?P<r2>[1-9][0-9]*))?\Z")
_CELL = re.compile(r"([A-Z]{1,3})([1-9][0-9]*)\Z")


def _column(text):
    result = 0
    for c in text:
        result = result * 26 + ord(c) - 64
    if result > 16384:
        _fail("Worksheet column outside Excel limits")
    return result


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        _fail("Invalid chart numeric value")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValidationError("Invalid chart numeric value") from exc
    if not number.is_finite():
        _fail("Non-finite chart value")
    return number


class _Workbook:
    def __init__(self, package):
        self.package, self.sheets, self.cells = package, {}, {}
        if any("/embeddings/" in name or name.lower().endswith((".xlsx", ".xlsm", ".zip")) for name in package.parts):
            _fail("Nested workbook packages unsupported")
        if any(kind == REL + "package" for rels in package.relationships.values() for kind, _ in rels.values()):
            _fail("Workbook package relationships unsupported")
        office = [p for kind, p in package.relationships.get("", {}).values() if kind == REL + "officeDocument" and p]
        if len(office) != 1:
            _fail("Workbook requires one officeDocument relationship")
        workbook = office[0]
        root = package.xml(workbook)
        if root.tag != "{" + NS["s"] + "}workbook":
            _fail("Embedded package is not an XLSX workbook")
        for sheet in root.findall("s:sheets/s:sheet", NS):
            name = sheet.get("name")
            if not name or name in self.sheets:
                _fail("Invalid/duplicate worksheet name")
            self.sheets[name] = package.related(workbook, sheet.get(RID), "worksheet")
        strings = [p for kind, p in package.relationships.get(workbook, {}).values() if kind == REL + "sharedStrings" and p]
        if len(strings) > 1:
            _fail("Duplicate shared strings relationship")
        self.strings = []
        if strings:
            for item in package.xml(strings[0]).findall("s:si", NS):
                self.strings.append("".join(t.text or "" for t in item.findall(".//s:t", NS)))

    def _sheet(self, name):
        if name not in self.sheets:
            _fail("Chart formula refers to missing worksheet")
        if name in self.cells:
            return self.cells[name]
        data = {}
        for cell in self.package.xml(self.sheets[name]).findall("s:sheetData/s:row/s:c", NS):
            address = cell.get("r", "")
            match = _CELL.fullmatch(address)
            if not match or address in data or int(match[2]) > 1048576:
                _fail("Invalid/duplicate worksheet cell")
            _column(match[1])
            if cell.find("s:f", NS) is not None:
                _fail("Formula cells unsupported; cached values are not proof")
            kind, value = cell.get("t", "n"), cell.findtext("s:v", namespaces=NS)
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
            elif kind == "s":
                try:
                    index = int(value)
                    if index < 0:
                        raise ValueError
                    value = self.strings[index]
                except (ValueError, TypeError, IndexError) as exc:
                    raise ValidationError("Invalid shared string cell") from exc
            elif kind == "n":
                value = _number(value)
            elif kind != "str":
                _fail("Unsupported worksheet cell type")
            data[address] = value
            if len(data) > self.package.budget.limits.max_cells:
                _fail("Worksheet cell limit exceeded")
        self.cells[name] = data
        return data

    def values(self, formula, count):
        match = _RANGE.fullmatch(formula or "")
        if not match:
            _fail("Unsupported chart formula; need local absolute cell range")
        sheet = match["sheet"]
        if sheet.startswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
        c1, c2 = _column(match["c1"]), _column(match["c2"] or match["c1"])
        r1, r2 = int(match["r1"]), int(match["r2"] or match["r1"])
        if (r2 > 1048576 or r2 < r1 or c2 < c1 or (r1 != r2 and c1 != c2)
                or (r2 - r1 + 1) * (c2 - c1 + 1) != count
                or count > self.package.budget.limits.max_cells):
            _fail("Chart formula range/cache point count mismatch")
        cells, values = self._sheet(sheet), []
        for row in range(r1, r2 + 1):
            for col in range(c1, c2 + 1):
                letters, n = "", col
                while n:
                    n, rem = divmod(n - 1, 26)
                    letters = chr(65 + rem) + letters
                address = letters + str(row)
                if address not in cells:
                    _fail("Chart source cell missing")
                values.append(cells[address])
        return values


def _cache(reference, numeric):
    cache = reference.find("c:numCache" if numeric else "c:strCache", NS)
    if cache is None:
        _fail("Chart source requires a cache")
    try:
        count = int(cache.find("c:ptCount", NS).get("val"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError("Invalid chart cache count") from exc
    points = cache.findall("c:pt", NS)
    if count < 1 or count != len(points):
        _fail("Incomplete chart cache")
    result = {}
    for point in points:
        try:
            index = int(point.get("idx"))
        except (ValueError, TypeError) as exc:
            raise ValidationError("Invalid chart point index") from exc
        value = point.findtext("c:v", namespaces=NS)
        if index < 0 or index >= count or index in result or value is None:
            _fail("Invalid/duplicate chart point")
        result[index] = _number(value) if numeric else value
    return [result[i] for i in range(count)]


def _source(series, path, numeric, workbook, expected):
    holders = series.findall(path, NS)
    if len(holders) != 1 or len(holders[0]) != 1:
        _fail("Chart needs one explicit cached source reference")
    ref = holders[0][0]
    if len(ref.findall("c:f", NS)) != 1:
        _fail("Chart source needs exactly one formula")
    if ref.tag != "{" + NS["c"] + "}" + ("numRef" if numeric else "strRef"):
        _fail("Only cached worksheet references supported")
    values = _cache(ref, numeric)
    normalized = [_number(v) for v in expected] if numeric else expected
    if values != normalized:
        _fail("Chart cache differs from expected data")
    actual = workbook.values(ref.findtext("c:f", namespaces=NS), len(values))
    if numeric and any(not isinstance(v, Decimal) for v in actual):
        _fail("Chart numeric source is not a numeric cell")
    if actual != values:
        _fail("Embedded workbook differs from chart cache")


def _check_chart(root, workbook, expected):
    plot = root.find("c:chart/c:plotArea", NS)
    if plot is None:
        _fail("Missing chart plot area")
    types = [n for n in plot if n.tag.rsplit("}", 1)[-1].endswith("Chart")]
    if len(types) != 1:
        _fail("Combination/unknown chart types unsupported for expected data")
    chart = types[0]
    kind = chart.tag.rsplit("}", 1)[-1]
    if kind not in {"barChart", "lineChart", "pieChart"}:
        _fail("Unsupported expected chart type: " + kind)
    grouping = chart.find("c:grouping", NS)
    if kind == "barChart":
        direction = chart.find("c:barDir", NS)
        if direction is None or direction.get("val") != "col" or (grouping is not None and grouping.get("val") not in {"clustered", "standard"}):
            _fail("Only simple column barChart supported")
    if kind == "lineChart" and grouping is not None and grouping.get("val") != "standard":
        _fail("Only simple lineChart supported")
    if expected.get("type") is not None and expected["type"] != {"barChart": "column", "lineChart": "line", "pieChart": "pie"}[kind]:
        _fail("Chart type differs from expected type")
    series = chart.findall("c:ser", NS)
    if len(series) != len(expected["series"]) or (kind == "pieChart" and len(series) != 1):
        _fail("Chart series count mismatch")
    for actual, wanted in zip(series, expected["series"]):
        _source(actual, "c:tx", False, workbook, [wanted["name"]])
        _source(actual, "c:cat", False, workbook, expected["categories"])
        _source(actual, "c:val", True, workbook, wanted["values"])


def _expectations(expected_charts, limits):
    if expected_charts is None:
        return None
    if not isinstance(expected_charts, list) or len(expected_charts) > limits.max_charts:
        _fail("expected_charts must be a bounded list")
    for chart in expected_charts:
        if not isinstance(chart, dict) or type(chart.get("slide")) is not int or chart["slide"] < 1:
            _fail("Expected chart slide must be a one-based ordinal")
        categories, series = chart.get("categories"), chart.get("series")
        if not isinstance(categories, list) or not categories or len(categories) > limits.max_cells or not all(isinstance(v, str) for v in categories):
            _fail("Expected chart needs string categories")
        if not isinstance(series, list) or not series or len(series) > limits.max_charts:
            _fail("Expected chart needs series")
        if chart.get("type") not in {None, "column", "line", "pie"}:
            _fail("Unsupported expected chart type")
        for item in series:
            if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                    or not isinstance(item.get("values"), list) or len(item["values"]) != len(categories)):
                _fail("Expected series needs name and one value per category")
            for value in item["values"]:
                if not isinstance(value, (int, float, Decimal)) or isinstance(value, bool):
                    _fail("Expected series values must be numbers")
                _number(value)
    return expected_charts


def inspect_pptx(path, expected_charts=None, *, limits=Limits()):
    """Check OOXML/chart chain and optionally cached + XLSX source data.

    Expected: [{slide: int, categories: [str], series: [{name, values}]}].
    Optional type: column/line/pie. Ordinals come from sldIdLst relationships;
    charts within each slide and series use XML order. Nonempty expectations
    must cover all referenced charts. None/[] performs structure checks only.
    Simple charts and local absolute single-row/column ranges are supported.
    Formula cells, named ranges and other chart types are refused for data checks.
    """
    expectations = _expectations(expected_charts, limits)
    path, info = _no_links(path)
    if info.st_size > limits.max_file_bytes:
        _fail("PPTX exceeds file size limit")
    try:
        with path.open("rb") as stream:
            data = stream.read(limits.max_file_bytes + 1)
    except OSError as exc:
        raise ValidationError("Cannot read PPTX") from exc
    if len(data) != info.st_size or len(data) > limits.max_file_bytes:
        _fail("PPTX size changed/exceeds limit")
    budget = _Budget(limits)
    package = _Package(data, budget, allow_ppt_printer_settings=True)
    office = [p for kind, p in package.relationships.get("", {}).values() if kind == REL + "officeDocument" and p]
    if len(office) != 1:
        _fail("PPTX requires one officeDocument relationship")
    presentation = office[0]
    root = package.xml(presentation)
    if root.tag != "{" + NS["p"] + "}presentation":
        _fail("Package is not a presentation")
    slide_ids = root.findall("p:sldIdLst/p:sldId", NS)
    if not slide_ids or len(slide_ids) > limits.max_slides:
        _fail("Invalid presentation slide count")
    embedded = {}
    for relationships in package.relationships.values():
        for kind, target in relationships.values():
            if kind == REL + "package" and (not target or not target.lower().endswith(".xlsx")):
                _fail("Only embedded XLSX package relationships supported")
    for part in package.parts:
        if "/embeddings/" in part or part.lower().endswith(".xlsx"):
            if not part.lower().endswith(".xlsx"):
                _fail("Only XLSX embedded packages supported")
            embedded[part] = _Workbook(_Package(package.read(part), budget))
    seen_slides, found = set(), []
    for ordinal, slide_id in enumerate(slide_ids, 1):
        slide = package.related(presentation, slide_id.get(RID), "slide")
        if slide in seen_slides:
            _fail("Duplicate slide relationship in presentation order")
        seen_slides.add(slide)
        slide_root = package.xml(slide)
        if slide_root.tag != "{" + NS["p"] + "}sld":
            _fail("Slide relationship points to non-slide part")
        for node in slide_root.findall(".//c:chart", NS):
            if len(found) >= limits.max_charts:
                _fail("Chart count limit exceeded")
            part = package.related(slide, node.get(RID), "chart")
            chart_root = package.xml(part)
            if chart_root.tag != "{" + NS["c"] + "}chartSpace":
                _fail("Chart relationship points to non-chart part")
            external = chart_root.findall("c:externalData", NS)
            if len(external) != 1:
                _fail("Native chart must reference one embedded workbook")
            workbook_part = package.related(part, external[0].get(RID), "package")
            if workbook_part not in embedded:
                if not workbook_part.lower().endswith(".xlsx"):
                    _fail("Chart data must be embedded XLSX")
                embedded[workbook_part] = _Workbook(_Package(package.read(workbook_part), budget))
            found.append((ordinal, part, chart_root, embedded[workbook_part]))
    if expectations:
        if len(expectations) != len(found):
            _fail("Expected/native chart count mismatch")
        ordered = sorted(expectations, key=lambda item: item["slide"])
        for wanted, (ordinal, part, chart_root, workbook) in zip(ordered, found):
            if wanted["slide"] != ordinal:
                _fail("Native chart not on expected slide ordinal")
            _check_chart(chart_root, workbook, wanted)
    return {"slide_count": len(slide_ids), "chart_count": len(found),
            "charts": [{"slide": ordinal, "part": part} for ordinal, part, _, _ in found],
            "chart_data_verified": bool(expectations), "structural_only": not bool(expectations),
            "quality_gates_verified": False, "visual_acceptance": False}
