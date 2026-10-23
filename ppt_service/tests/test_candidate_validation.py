"""Minimal hand-built OOXML fixtures; no Office/model/generation dependencies."""
import copy
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from xml.sax.saxutils import escape

from ppt_service.worker.candidate_validation import (
    Limits, NS, REL, ValidationError, inspect_pptx, validate_manifest,
)


CT = '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>'
EXPECTED = [{"slide": 1, "categories": ["甲", "乙"], "series": [{"name": "收入", "values": [10, 20]}]}]


def zipped(parts):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in parts.items():
            info = zipfile.ZipInfo(name)
            info.filename = name  # Preserve hostile backslashes on Windows.
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return stream.getvalue()


def rels(items):
    body = ""
    for rid, kind, target, external in items:
        mode = ' TargetMode="External"' if external else ""
        body += f'<Relationship Id="{rid}" Type="{REL}{kind}" Target="{escape(target)}"{mode}/>'
    return f'<Relationships xmlns="{NS["pr"]}">{body}</Relationships>'


def workbook(*, name="收入", categories=("甲", "乙"), values=(10, 20), shared=False, formula=False):
    def text_cell(address, value):
        if shared:
            index = [name, *categories].index(value)
            return f'<c r="{address}" t="s"><v>{index}</v></c>'
        return f'<c r="{address}" t="inlineStr"><is><t>{escape(value)}</t></is></c>'
    header = text_cell("B1", name)
    rows = f'<row r="1">{header}</row>'
    for row, (category, value) in enumerate(zip(categories, values), 2):
        f = '<f>5+5</f>' if formula and row == 2 else ""
        rows += f'<row r="{row}">{text_cell("A" + str(row), category)}<c r="B{row}">{f}<v>{value}</v></c></row>'
    relationships = [("sheet", "worksheet", "worksheets/odd.xml", False)]
    parts = {
        "[Content_Types].xml": CT,
        "_rels/.rels": rels([("office", "officeDocument", "xl/workbook.xml", False)]),
        "xl/workbook.xml": f'<workbook xmlns="{NS["s"]}" xmlns:r="{NS["r"]}"><sheets><sheet name="Data" sheetId="1" r:id="sheet"/></sheets></workbook>',
        "xl/worksheets/odd.xml": f'<worksheet xmlns="{NS["s"]}"><sheetData>{rows}</sheetData></worksheet>',
    }
    if shared:
        relationships.append(("strings", "sharedStrings", "sharedStrings.xml", False))
        strings = "".join('<si><t>' + escape(value) + '</t></si>' for value in [name, *categories])
        parts["xl/sharedStrings.xml"] = f'<sst xmlns="{NS["s"]}">{strings}</sst>'
    parts["xl/_rels/workbook.xml.rels"] = rels(relationships)
    return zipped(parts)


def chart(*, name="收入", categories=("甲", "乙"), values=(10, 20), kind="barChart", range_value="Data!$B$2:$B$3"):
    def source(tag, formula, values, numeric=False):
        ref, cache = ("numRef", "numCache") if numeric else ("strRef", "strCache")
        points = "".join(f'<c:pt idx="{i}"><c:v>{escape(str(value))}</c:v></c:pt>' for i, value in enumerate(values))
        return f'<c:{tag}><c:{ref}><c:f>{escape(formula)}</c:f><c:{cache}><c:ptCount val="{len(values)}"/>{points}</c:{cache}></c:{ref}></c:{tag}>'
    series = source("tx", "Data!$B$1", [name]) + source("cat", "Data!$A$2:$A$3", categories) + source("val", range_value, values, True)
    options = '<c:barDir val="col"/><c:grouping val="clustered"/>' if kind == "barChart" else ""
    return f'<c:chartSpace xmlns:c="{NS["c"]}" xmlns:r="{NS["r"]}"><c:chart><c:plotArea><c:{kind}>{options}<c:ser><c:idx val="0"/><c:order val="0"/>{series}</c:ser></c:{kind}></c:plotArea></c:chart><c:externalData r:id="data"/></c:chartSpace>'


def presentation(*, cache=None, book=None, external=False, hyperlink=None, two_slides=False):
    slide = f'<p:sld xmlns:p="{NS["p"]}" xmlns:c="{NS["c"]}" xmlns:r="{NS["r"]}"><p:cSld><p:spTree><c:chart r:id="native"/></p:spTree></p:cSld></p:sld>'
    slide_relationships = [("native", "chart", "../charts/strange-name.xml", False)]
    if hyperlink:
        slide_relationships.append(("link", "hyperlink", hyperlink, True))
    ids = '<p:sldId id="299" r:id="first"/>'
    presrels = [("first", "slide", "slides/slide93.xml", False)]
    parts = {
        "[Content_Types].xml": CT,
        "_rels/.rels": rels([("office", "officeDocument", "ppt/presentation.xml", False)]),
        "ppt/slides/slide93.xml": slide,
        "ppt/slides/_rels/slide93.xml.rels": rels(slide_relationships),
        "ppt/charts/strange-name.xml": cache or chart(),
        "ppt/charts/_rels/strange-name.xml.rels": rels([("data", "package", "https://evil.test/data.xlsx" if external else "../embeddings/data.xlsx", external)]),
        "ppt/embeddings/data.xlsx": book or workbook(),
    }
    if two_slides:
        # Filename slide1 is SECOND; ordinal comes from the sldIdLst.
        ids += '<p:sldId id="256" r:id="second"/>'
        presrels.append(("second", "slide", "slides/slide1.xml", False))
        parts["ppt/slides/slide1.xml"] = f'<p:sld xmlns:p="{NS["p"]}"><p:cSld/></p:sld>'
    parts["ppt/presentation.xml"] = f'<p:presentation xmlns:p="{NS["p"]}" xmlns:r="{NS["r"]}"><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>'
    parts["ppt/_rels/presentation.xml.rels"] = rels(presrels)
    return parts


PRINTER = "ppt/printerSettings/printerSettings1.bin"
PRINTER_CT = "application/vnd.openxmlformats-officedocument.presentationml.printerSettings"
PRESENTATION_CT = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
CORE_REL = NS["pr"] + "/metadata/core-properties"
THUMB_REL = NS["pr"] + "/metadata/thumbnail"


def with_printer(parts=None, *, override=False):
    parts = presentation() if parts is None else parts
    declaration = (f'<Override PartName="/{PRINTER}" ContentType="{PRINTER_CT}"/>' if override else
                   f'<Default Extension="bin" ContentType="{PRINTER_CT}"/>')
    declaration += f'<Override PartName="/ppt/presentation.xml" ContentType="{PRESENTATION_CT}"/>'
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace("</Types>", declaration + "</Types>")
    # Opaque platform-specific printer records need not be Windows DEVMODE.
    parts[PRINTER] = b"\x02\x00\x00\x00\x01\x00\x00\x00passive printer record"
    key = "ppt/_rels/presentation.xml.rels"
    parts[key] = parts[key].replace("</Relationships>",
        f'<Relationship Id="printer" Type="{REL}printerSettings" Target="printerSettings/printerSettings1.bin"/></Relationships>')
    return parts


def with_metadata():
    parts = presentation()
    parts["[Content_Types].xml"] = CT.replace("</Types>",
        '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>')
    parts["docProps/core.xml"] = '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>'
    parts["docProps/thumbnail.jpeg"] = b"\xff\xd8\xff\xd9"
    parts["_rels/.rels"] = parts["_rels/.rels"].replace("</Relationships>",
        f'<Relationship Id="core" Type="{CORE_REL}" Target="docProps/core.xml"/>'
        f'<Relationship Id="thumb" Type="{THUMB_REL}" Target="docProps/thumbnail.jpeg"/></Relationships>')
    return parts


class ChartValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "candidate.pptx"

    def inspect(self, parts=None, expected=EXPECTED, **kwargs):
        self.path.write_bytes(zipped(parts if parts is not None else presentation()))
        return inspect_pptx(self.path, expected, **kwargs)

    def test_column_cache_and_workbook_match(self):
        result = self.inspect()
        self.assertEqual(result["chart_count"], 1)
        self.assertTrue(result["chart_data_verified"])
        self.assertFalse(result["quality_gates_verified"])
        self.assertFalse(result["visual_acceptance"])

    def test_shared_string_workbook(self):
        self.assertTrue(self.inspect(presentation(book=workbook(shared=True)))["chart_data_verified"])

    def test_slide_ordinal_is_presentation_order(self):
        result = self.inspect(presentation(two_slides=True))
        self.assertEqual(result["slide_count"], 2)
        self.assertEqual(result["charts"][0]["slide"], 1)
        expected = copy.deepcopy(EXPECTED)
        expected[0]["slide"] = 2
        with self.assertRaisesRegex(ValidationError, "ordinal"):
            self.inspect(presentation(two_slides=True), expected)

    def test_line_and_pie(self):
        for kind in ("lineChart", "pieChart"):
            with self.subTest(kind=kind):
                self.assertTrue(self.inspect(presentation(cache=chart(kind=kind)))["chart_data_verified"])

    def test_cache_name_categories_and_values_mismatch(self):
        for changed in ({"name": "利润"}, {"categories": ("甲", "丙")}, {"values": (10, 21)}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValidationError, "cache differs"):
                self.inspect(presentation(cache=chart(**changed)))

    def test_workbook_name_categories_and_values_mismatch(self):
        for changed in ({"name": "利润"}, {"categories": ("甲", "丙")}, {"values": (10, 21)}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValidationError, "workbook differs"):
                self.inspect(presentation(book=workbook(**changed)))

    def test_formula_range_points_to_wrong_cells(self):
        with self.assertRaisesRegex(ValidationError, "source cell missing"):
            self.inspect(presentation(cache=chart(range_value="Data!$C$2:$C$3")))

    def test_bad_formula_and_calculated_cell_refused(self):
        for formula in ("[other.xlsx]Data!$B$2:$B$3", "Data!$B$2:$C$3", "SUM(Data!$B$2:$B$3)", "Data!$B$2:$B$4"):
            with self.subTest(formula=formula), self.assertRaises(ValidationError):
                self.inspect(presentation(cache=chart(range_value=formula)))
        with self.assertRaisesRegex(ValidationError, "Formula cells"):
            self.inspect(presentation(book=workbook(formula=True)))

    def test_external_data_forbidden_even_in_structural_mode(self):
        for expected in (None, [], EXPECTED):
            with self.subTest(expected=expected), self.assertRaisesRegex(ValidationError, "HTTPS hyperlinks"):
                self.inspect(presentation(external=True), expected)

    def test_https_hyperlink_allowed_without_fetching(self):
        self.assertTrue(self.inspect(presentation(hyperlink="https://example.com/data?q=1"))["chart_data_verified"])
        with self.assertRaises(ValidationError):
            self.inspect(presentation(hyperlink="file:///private"))

    def test_no_expectations_is_explicitly_structural_only(self):
        for expected in (None, []):
            result = self.inspect(presentation(cache=chart(values=(0, 0))), expected)
            self.assertTrue(result["structural_only"])
            self.assertFalse(result["chart_data_verified"])

    def test_unsupported_expected_chart_refused(self):
        for kind in ("scatterChart", "bar3DChart"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, "Unsupported expected chart type"):
                self.inspect(presentation(cache=chart(kind=kind)))
        wanted = copy.deepcopy(EXPECTED)
        wanted[0]["type"] = "scatter"
        with self.assertRaises(ValidationError):
            self.inspect(expected=wanted)

    def test_missing_chart_relationship_and_fake_orphan_chart(self):
        parts = presentation()
        parts["ppt/slides/_rels/slide93.xml.rels"] = rels([])
        with self.assertRaisesRegex(ValidationError, "chart relationship"):
            self.inspect(parts)
        parts = presentation()
        parts["ppt/slides/slide93.xml"] = f'<p:sld xmlns:p="{NS["p"]}"><p:cSld/></p:sld>'
        with self.assertRaisesRegex(ValidationError, "chart count mismatch"):
            self.inspect(parts)

    def test_zip_traversal_symlink_and_relationship_escape(self):
        for name in ("../escape.xml", "/escape.xml", "C:/escape.xml", "ppt\\evil.xml"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                parts = presentation()
                parts[name] = "<x/>"
                self.inspect(parts)
        parts = presentation()
        parts["ppt/charts/_rels/strange-name.xml.rels"] = rels([("data", "package", "../../../escape.xlsx", False)])
        with self.assertRaises(ValidationError):
            self.inspect(parts)
        self.path.write_bytes(zipped(presentation()))
        with zipfile.ZipFile(self.path, "a") as archive:
            info = zipfile.ZipInfo("link")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
        with self.assertRaisesRegex(ValidationError, "Non-regular"):
            inspect_pptx(self.path)

    def test_dtd_macro_activex_and_ole_forbidden(self):
        for filename, data in (("ppt/vbaProject.bin", b"x"), ("ppt/activeX/a.xml", "<x/>"), ("ppt/embeddings/oleObject1.bin", b"x"), ("extra.xml", '<!DOCTYPE x [<!ENTITY a "x">]><x>&a;</x>')):
            parts = presentation()
            parts[filename] = data
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                self.inspect(parts, None)
        parts = presentation()
        parts["[Content_Types].xml"] = CT.replace("application/xml", "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml")
        with self.assertRaises(ValidationError):
            self.inspect(parts, None)

    def test_presentation_printer_settings_default_and_override(self):
        for override in (False, True):
            with self.subTest(override=override):
                self.assertTrue(self.inspect(with_printer(override=override))["chart_data_verified"])
        self.assertTrue(self.inspect(with_printer(with_metadata()))["chart_data_verified"])

    def test_printer_path_is_exact_and_other_binary_parts_still_refused(self):
        for name in ("ppt/printerSettings/arbitrary.bin", "ppt/printerSettings/printerSettings0.bin",
                     "ppt/printerSettings/printerSettings1.BIN", "ppt/printerSettings/sub/printerSettings1.bin",
                     "xl/printerSettings/printerSettings1.bin", "ppt/embeddings/printerSettings1.bin",
                     "extra.bin", "ppt/vbaProject.bin", "ppt/activeX/a.bin", "ppt/oleObject1.bin"):
            parts = with_printer()
            parts[name] = b"passive"
            with self.subTest(name=name), self.assertRaises(ValidationError):
                self.inspect(parts, None)

    def test_printer_content_types_are_required_unambiguous_and_exact(self):
        for mutation in (
            lambda ct: ct.replace(PRINTER_CT, "application/octet-stream"),
            lambda ct: ct.replace(PRINTER_CT, "application/vnd.openxmlformats-officedocument.oleObject"),
            lambda ct: ct.replace(PRESENTATION_CT, "application/xml"),
            lambda ct: ct.replace(f'<Default Extension="bin" ContentType="{PRINTER_CT}"/>', ""),
            lambda ct: ct.replace("</Types>", '<Override PartName="/' + PRINTER + '" ContentType="application/octet-stream"/></Types>'),
            lambda ct: ct.replace("</Types>", '<Default Extension="BIN" ContentType="' + PRINTER_CT + '"/></Types>'),
            lambda ct: ct.replace("</Types>", '<Override PartName="/ppt/presentation.xml" ContentType="' + PRESENTATION_CT + '"/></Types>'),
            lambda ct: ct.replace('xmlns="http://schemas.openxmlformats.org/package/2006/content-types"', 'xmlns="urn:fake"'),
        ):
            parts = with_printer()
            parts["[Content_Types].xml"] = mutation(parts["[Content_Types].xml"])
            with self.subTest(ct=parts["[Content_Types].xml"]), self.assertRaises(ValidationError):
                self.inspect(parts, None)

    def test_printer_requires_unique_main_presentation_reference(self):
        relation = f'<Relationship Id="printer" Type="{REL}printerSettings" Target="printerSettings/printerSettings1.bin"/>'
        for change in ("orphan", "wrong-kind", "wrong-source", "duplicate", "other-incoming",
                       "outgoing", "two-parts", "wrong-office", "fake-presentation", "external"):
            parts = with_printer()
            key = "ppt/_rels/presentation.xml.rels"
            if change == "orphan":
                parts[key] = parts[key].replace(relation, "")
            elif change == "wrong-kind":
                parts[key] = parts[key].replace(REL + "printerSettings", REL + "image")
            elif change == "wrong-source":
                parts[key] = parts[key].replace(relation, "")
                other = "ppt/slides/_rels/slide93.xml.rels"
                parts[other] = parts[other].replace("</Relationships>", relation.replace('Target="printerSettings/', 'Target="../printerSettings/') + "</Relationships>")
            elif change in {"duplicate", "other-incoming"}:
                extra = relation.replace('Id="printer"', 'Id="printer2"')
                if change == "other-incoming":
                    extra = extra.replace(REL + "printerSettings", REL + "image")
                parts[key] = parts[key].replace("</Relationships>", extra + "</Relationships>")
            elif change == "outgoing":
                parts["ppt/printerSettings/_rels/printerSettings1.bin.rels"] = rels([])
            elif change == "two-parts":
                parts["ppt/printerSettings/printerSettings2.bin"] = b"passive"
            elif change == "wrong-office":
                parts["_rels/.rels"] = rels([("office", "officeDocument", "ppt/slides/slide93.xml", False)])
            elif change == "fake-presentation":
                parts["ppt/presentation.xml"] = "<fake/>"
            else:
                parts[key] = parts[key].replace(relation, relation.replace('Target="printerSettings/printerSettings1.bin"', 'Target="https://evil.test/printer.bin" TargetMode="External"'))
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.inspect(parts, None)

    def test_printer_rejects_ole_zip_and_executable_disguises(self):
        for payload in (b"", bytes.fromhex("d0cf11e0a1b11ae1") + b"OLE",
                        b"prefix" + bytes.fromhex("d0cf11e0a1b11ae1"), zipped({"evil": b"x"}),
                        b"prefixPK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"MZpayload", b"\x7fELFpayload"):
            parts = with_printer()
            parts[PRINTER] = payload
            with self.subTest(payload=payload[:20]), self.assertRaises(ValidationError):
                self.inspect(parts, None)

    def test_embedded_xlsx_does_not_inherit_printer_exception(self):
        with zipfile.ZipFile(io.BytesIO(workbook())) as archive:
            base = {name: archive.read(name) for name in archive.namelist()}
        for name in (PRINTER, "xl/printerSettings/printerSettings1.bin", "arbitrary.bin"):
            parts = dict(base)
            parts[name] = b"passive"
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, "Macros, ActiveX, OLE"):
                self.inspect(presentation(book=zipped(parts)), None)

    def test_standard_root_metadata(self):
        self.assertTrue(self.inspect(with_metadata())["chart_data_verified"])

    def test_metadata_wrong_type_source_target_content_and_external_refused(self):
        for change in ("unknown-type", "type-suffix", "wrong-source", "duplicate", "external",
                       "escape", "target", "missing", "core-type", "thumbnail-type", "core-root", "thumbnail-bytes"):
            parts = with_metadata()
            key = "_rels/.rels"
            if change == "unknown-type":
                parts[key] = parts[key].replace(CORE_REL, NS["pr"] + "/metadata/unknown")
            elif change == "type-suffix":
                parts[key] = parts[key].replace(CORE_REL, CORE_REL + "/suffix")
            elif change == "wrong-source":
                parts["ppt/_rels/presentation.xml.rels"] = parts["ppt/_rels/presentation.xml.rels"].replace("</Relationships>",
                    f'<Relationship Id="bad" Type="{CORE_REL}" Target="../docProps/core.xml"/></Relationships>')
            elif change == "duplicate":
                parts[key] = parts[key].replace("</Relationships>", f'<Relationship Id="other" Type="{CORE_REL}" Target="docProps/core.xml"/></Relationships>')
            elif change == "external":
                parts[key] = parts[key].replace('Target="docProps/core.xml"', 'Target="https://evil.test/core.xml" TargetMode="External"')
            elif change in {"escape", "target"}:
                target = "../docProps/core.xml" if change == "escape" else "ppt/presentation.xml"
                parts[key] = parts[key].replace('Target="docProps/core.xml"', f'Target="{target}"')
            elif change == "missing":
                del parts["docProps/core.xml"]
            elif change == "core-type":
                parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace("application/vnd.openxmlformats-package.core-properties+xml", "application/xml")
            elif change == "thumbnail-type":
                parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace("image/jpeg", "image/png")
            elif change == "core-root":
                parts["docProps/core.xml"] = "<fake/>"
            else:
                parts["docProps/thumbnail.jpeg"] = bytes.fromhex("d0cf11e0a1b11ae1")
            with self.subTest(change=change), self.assertRaises(ValidationError):
                self.inspect(parts, None)

    def test_encrypted_zip_and_office_container_rejected(self):
        data = bytearray(zipped(presentation()))
        central = data.index(b"PK\x01\x02")
        data[central + 8] |= 1  # General-purpose encryption bit in directory.
        self.path.write_bytes(data)
        with self.assertRaisesRegex(ValidationError, "Encrypted ZIP"):
            inspect_pptx(self.path)
        self.path.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"EncryptedPackage")
        with self.assertRaisesRegex(ValidationError, "Invalid/encrypted"):
            inspect_pptx(self.path)

    def test_resource_limits(self):
        for limits in (replace(Limits(), max_zip_entries=2), replace(Limits(), max_uncompressed_bytes=100), replace(Limits(), max_xml_bytes=40), replace(Limits(), max_xml_nodes=2), replace(Limits(), max_file_bytes=10)):
            with self.subTest(limits=limits), self.assertRaises(ValidationError):
                self.inspect(limits=limits)

    def test_nested_xlsx_external_reference_forbidden(self):
        nested = {
            "[Content_Types].xml": CT,
            "_rels/.rels": rels([("external", "officeDocument", "https://evil.test/wb.xml", True)]),
        }
        with self.assertRaises(ValidationError):
            self.inspect(presentation(book=zipped(nested)), None)


class ManifestValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "project").mkdir()
        self.manifest = {
            "schema_version": 1, "phase": "plan_initial", "status": "awaiting_confirmation",
            "project_root": "project", "input_snapshot_sha256": "a" * 64,
            "plan": {}, "questions": [], "artifacts": [], "slides": [], "quality": {},
        }

    def artifact(self, name="report.json", data=b'{"passed": true}', role="report"):
        (self.root / "project" / name).write_bytes(data)
        item = {"path": name, "role": role, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "media_type": "application/octet-stream"}
        self.manifest["artifacts"].append(item)
        return item

    def validate(self, **kwargs):
        return validate_manifest(self.root, self.manifest, **kwargs)

    def test_planning_does_not_need_fake_reports_or_pptx(self):
        result = self.validate()
        self.assertEqual(result["artifact_count"], 0)
        self.assertFalse(result["published"])
        self.assertFalse(result["quality_reports_verified"])
        self.manifest.update(phase="prepare", status="prepared")
        self.validate()

    def test_planning_quality_descriptions_are_not_evidence(self):
        self.manifest["quality"] = {
            "attribution_guard": {"exit_code": 0, "status": "passed"},
            "visual_review": "not_run_no_slides", "publication_authorized": False,
        }
        result = self.validate()
        self.assertEqual(result["quality_report_references"], [])
        self.assertFalse(result["quality_reports_verified"])
        self.manifest["quality"]["report"] = {"path": "missing.json"}
        with self.assertRaisesRegex(ValidationError, "declared hashed artifact"):
            self.validate()
        del self.manifest["quality"]["report"]
        self.manifest.update(phase="generate", status="candidate")
        with self.assertRaises(ValidationError):
            self.validate()

    def test_report_reference_hash_only(self):
        item = self.artifact()
        self.manifest["quality"] = {"final": {"path": item["path"], "sha256": item["sha256"], "passed": True}}
        result = self.validate()
        self.assertEqual(result["quality_report_references"], ["report.json"])
        self.assertFalse(result["quality_reports_verified"])
        self.manifest["quality"]["final"]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValidationError, "reference hash"):
            self.validate()

    def test_path_rules(self):
        item = self.artifact()
        for name in ("../x", "/tmp/x", "a/../x", "a\\x", "C:/x", "https://x", "a//x", "./x", "a%2fx"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                item["path"] = name
                self.validate()
        item["path"] = "report.json"
        self.manifest["project_root"] = "../project"
        with self.assertRaises(ValidationError):
            self.validate()

    def test_hash_size_and_total_limits(self):
        item = self.artifact()
        item["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValidationError, "SHA256 mismatch"):
            self.validate()
        item["sha256"] = hashlib.sha256(b'{"passed": true}').hexdigest()
        item["bytes"] += 1
        with self.assertRaises(ValidationError):
            self.validate()
        item["bytes"] -= 1
        with self.assertRaisesRegex(ValidationError, "total size"):
            self.validate(limits=replace(Limits(), max_total_bytes=1))

    def test_symlink_file_and_directory_components(self):
        self.artifact()
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "report.json").write_bytes(b'{"passed": true}')
        link = self.root / "project" / "alias.json"
        try:
            link.symlink_to(outside / "report.json")
        except (OSError, NotImplementedError):
            # Windows without symlink privilege: prove lstat rejection with a
            # targeted mocked mode; actual directory-component check below also
            # uses a real symlink whenever the platform supports it.
            original = Path.lstat
            def mocked(path, *args, **kwargs):
                if path == self.root / "project" / "report.json":
                    return type("Info", (), {"st_mode": stat.S_IFLNK})()
                return original(path, *args, **kwargs)
            with patch.object(Path, "lstat", mocked), self.assertRaisesRegex(ValidationError, "Symlink"):
                self.validate()
            return
        self.manifest["artifacts"][0]["path"] = "alias.json"
        with self.assertRaisesRegex(ValidationError, "Symlink"):
            self.validate()
        directory = self.root / "project" / "linked"
        directory.symlink_to(outside, target_is_directory=True)
        self.manifest["artifacts"][0]["path"] = "linked/report.json"
        with self.assertRaisesRegex(ValidationError, "Symlink"):
            self.validate()
        self.manifest["project_root"] = "project/linked"
        self.manifest["artifacts"][0]["path"] = "report.json"
        with self.assertRaisesRegex(ValidationError, "Symlink"):
            self.validate()

    def test_directory_is_not_artifact(self):
        self.artifact()
        self.manifest["artifacts"][0]["path"] = "folder"
        (self.root / "project" / "folder").mkdir()
        with self.assertRaisesRegex(ValidationError, "regular file"):
            self.validate()

    def test_generated_candidate_checks_pptx_and_slide_count(self):
        self.artifact("deck.pptx", zipped(presentation()), "pptx")
        self.artifact("preview.png", b"private preview", "preview")
        self.manifest.update(phase="generate", status="candidate", expected_charts=EXPECTED,
                             slides=[{"ordinal": 1, "preview_path": "preview.png"}])
        result = self.validate()
        self.assertFalse(result["pptx"][0]["chart_data_verified"])
        result = self.validate(expected_charts=EXPECTED)
        self.assertTrue(result["pptx"][0]["chart_data_verified"])
        wrong = copy.deepcopy(EXPECTED)
        wrong[0]["series"][0]["values"][0] += 1
        with self.assertRaises(ValidationError):
            self.validate(expected_charts=wrong)
        self.manifest["slides"].append({"ordinal": 2, "preview_path": "preview.png"})
        with self.assertRaisesRegex(ValidationError, "slide count"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
