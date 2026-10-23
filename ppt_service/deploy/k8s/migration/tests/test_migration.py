"""Destructive/rejection probes use only fresh TemporaryDirectory fixtures."""
from __future__ import annotations

import hashlib
import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import snapshot
import db_inventory


class IndexDefinitionComparisonTests(unittest.TestCase):
    # Literal observations from retry-04's raw PostgreSQL captures.
    source_ddl = (
        "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
        "WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'starting'::character varying, "
        "'running'::character varying, 'collecting'::character varying, 'validating'::character varying, "
        "'cancelling'::character varying])::text[]))"
    )
    restored_ddl = (
        "CREATE UNIQUE INDEX uq_project_active_job ON public.jobs USING btree (project_id) "
        "WHERE ((status)::text = ANY (ARRAY[('queued'::character varying)::text, ('starting'::character varying)::text, "
        "('running'::character varying)::text, ('collecting'::character varying)::text, "
        "('validating'::character varying)::text, ('cancelling'::character varying)::text]))"
    )

    def report(self, ddl):
        return {"schema": db_inventory.SCHEMA, "database_schema": "public",
                "definitions": [{"tablename": "jobs", "indexname": "uq_project_active_job", "indexdef": ddl}],
                "tables": {"jobs": {"rows": 1, "sha256": "a" * 64}},
                "quiescence": {"nonterminal_jobs": 0}}

    def test_observed_complete_cast_variants_compare_without_changing_raw_captures(self):
        expected, observed = self.report(self.source_ddl), self.report(self.restored_ddl)
        original = copy.deepcopy((expected, observed))
        self.assertNotEqual(expected, observed)
        self.assertEqual(db_inventory.compare_manifests(expected, observed), ["ppt-jobs-active-index-cast-v1"])
        self.assertEqual(db_inventory.compare_manifests(observed, expected), ["ppt-jobs-active-index-cast-v1"])
        self.assertEqual(db_inventory.compare_manifests(expected, expected), [])
        self.assertEqual((expected, observed), original)

    def test_predicate_uniqueness_identity_type_or_other_metadata_changes_remain_rejected(self):
        expected = self.report(self.source_ddl)
        changed_ddls = [
            self.restored_ddl.replace("CREATE UNIQUE INDEX", "CREATE INDEX"),
            self.restored_ddl.replace("'cancelling'::character varying", "'cancelled'::character varying"),
            self.restored_ddl.replace("'queued'::character varying", "'queued'::text"),
            self.restored_ddl.replace("'starting'", "'temporary'").replace("'running'", "'starting'").replace("'temporary'", "'running'"),
            self.restored_ddl + " AND (project_id IS NOT NULL)",
            self.restored_ddl.replace("ON public.jobs", "ON public.other_jobs"),
            self.restored_ddl.replace("(project_id)", "(issuer)"),
            self.restored_ddl.replace("uq_project_active_job", "another_index"),
        ]
        candidates = [self.report(ddl) for ddl in changed_ddls]
        for field, value in (("tablename", "other_jobs"), ("indexname", "another_index"), ("unexpected", True)):
            candidate = self.report(self.restored_ddl)
            candidate["definitions"][0][field] = value
            candidates.append(candidate)
        changed_rows = self.report(self.restored_ddl)
        changed_rows["tables"]["jobs"]["sha256"] = "b" * 64
        candidates.append(changed_rows)
        changed_schema = self.report(self.restored_ddl)
        changed_schema["database_schema"] = "other_schema"
        candidates.append(changed_schema)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                with self.assertRaises(snapshot.Refused):
                    db_inventory.compare_manifests(expected, candidate)


@unittest.skipUnless(sys.platform.startswith("linux"), "production no-follow migration is Linux-only")
class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-p1-migration-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "old-data"
        self.source.mkdir(mode=0o700)
        for directory in ("projects/project/assets/asset", "projects/project/checkpoints/base", "material-locks", "uid-ledger", "empty"):
            (self.source / directory).mkdir(parents=True, exist_ok=True)
        for relative, body in {
            "projects/project/assets/asset/original": b"original material\n",
            "projects/project/checkpoints/base/manifest.json": b'{"checkpoint":"synthetic"}',
            "material-locks/asset": b"",
            "uid-ledger/state.json": b'{"next_uid":21000}',
            "root-control-state.json": b'{"disabled":true}',
        }.items():
            target = self.source / relative
            target.write_bytes(body)
            target.chmod(0o640)
            os.utime(target, ns=(1700000000123456789, 1700000000123456789))
        self.manifest = {"schema": snapshot.SCHEMA, "entries": snapshot.inventory(str(self.source))}

    def test_complete_tree_copied_atomically_with_metadata_and_old_data_unchanged(self):
        destination = self.base / "new-data"
        result = snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
        self.assertTrue(result["verified"])
        self.assertEqual(snapshot.inventory(str(self.source)), self.manifest["entries"])
        self.assertEqual(snapshot.inventory(str(destination)), self.manifest["entries"])
        self.assertIn("root-control-state.json", self.manifest["entries"])
        self.assertIn("uid-ledger/state.json", self.manifest["entries"])
        self.assertTrue((destination / "empty").is_dir())
        self.assertFalse(list(self.base.glob(".ppt-migration-incomplete-*")))

    def test_existing_empty_destination_is_never_replaced(self):
        destination = self.base / "new-data"
        destination.mkdir()
        inode = destination.stat().st_ino
        with self.assertRaises(snapshot.Refused):
            snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
        self.assertEqual(destination.stat().st_ino, inode)
        self.assertEqual(snapshot.inventory(str(self.source)), self.manifest["entries"])

    def test_existing_nonempty_destination_preserved(self):
        destination = self.base / "new-data"
        destination.mkdir()
        (destination / "keep").write_bytes(b"do not overwrite")
        with self.assertRaises(snapshot.Refused):
            snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
        self.assertEqual((destination / "keep").read_bytes(), b"do not overwrite")

    def test_destination_created_at_publish_race_is_never_replaced(self):
        destination = self.base / "raced-destination"
        original_rename = snapshot.rename_fresh

        def create_then_rename(parent, before, after):
            destination.mkdir()
            (destination / "keep").write_bytes(b"another operation owns this directory")
            return original_rename(parent, before, after)

        with patch.object(snapshot, "rename_fresh", create_then_rename):
            with self.assertRaises(FileExistsError):
                snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
        self.assertEqual((destination / "keep").read_bytes(), b"another operation owns this directory")
        self.assertEqual(snapshot.inventory(str(self.source)), self.manifest["entries"])

    def test_destination_inside_source_refused_before_writes(self):
        with self.assertRaises(snapshot.Refused):
            snapshot.copy_tree(str(self.source), str(self.source / "nested"), self.manifest, "preserve")
        self.assertEqual(snapshot.inventory(str(self.source)), self.manifest["entries"])

    def test_symlink_file_directory_and_root_ancestor_refused(self):
        target = self.base / "outside"
        target.write_bytes(b"private")
        link = self.source / "link"
        link.symlink_to(target)
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))
        link.unlink()
        link.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))
        alias = self.base / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            snapshot.inventory(str(alias))
        self.assertEqual(target.read_bytes(), b"private")

    def test_hardlink_and_fifo_refused_without_following_or_hanging(self):
        ordinary = self.source / "root-control-state.json"
        linked = self.base / "outside-hardlink"
        os.link(ordinary, linked)
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))
        linked.unlink()
        fifo = self.source / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))

    def test_manifest_digest_and_unsafe_parent_path_rejected(self):
        path = str(self.base / "manifest.json")
        digest = snapshot.atomic_json(path, self.manifest)
        self.assertEqual(snapshot.read_json(path, digest), self.manifest)
        with open(path, "ab") as stream:
            stream.write(b" ")
        with self.assertRaises(snapshot.Refused):
            snapshot.read_json(path, digest)
        poisoned = json.loads(json.dumps(self.manifest))
        poisoned["entries"]["../escape"] = poisoned["entries"]["root-control-state.json"]
        with self.assertRaises(snapshot.Refused):
            snapshot.copy_tree(str(self.source), str(self.base / "new-data"), poisoned, "preserve")
        self.assertFalse((self.base / "new-data").exists())

    def test_atomic_manifest_never_overwrites_old_evidence(self):
        path = str(self.base / "manifest.json")
        original = snapshot.atomic_json(path, self.manifest)
        with self.assertRaises(FileExistsError):
            snapshot.atomic_json(path, {"replacement": True})
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), original)

    def test_changed_bytes_same_length_and_mtime_detected(self):
        target = self.source / "root-control-state.json"
        previous = target.stat()
        target.write_bytes(b"X" * previous.st_size)
        os.utime(target, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        with self.assertRaises(snapshot.Refused):
            snapshot.copy_tree(str(self.source), str(self.base / "new-data"), self.manifest, "preserve")
        self.assertFalse((self.base / "new-data").exists())

    def test_source_drift_during_copy_never_publishes_and_keeps_unpublished_tree(self):
        original_scan = snapshot.file_scan
        changed = False

        def drift(parent, name, device, consume=None):
            nonlocal changed
            result = original_scan(parent, name, device, consume)
            if consume is not None and not changed:
                changed = True
                (self.source / "appeared-during-copy").write_bytes(b"writer was not stopped")
            return result

        destination = self.base / "new-data"
        with patch.object(snapshot, "file_scan", drift):
            with self.assertRaises(snapshot.Refused):
                snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
        self.assertFalse(destination.exists())
        self.assertEqual(len(list(self.base.glob(".ppt-migration-incomplete-*"))), 1)
        self.assertTrue((self.source / "appeared-during-copy").exists())

    def test_corrupt_copy_or_mode_or_mtime_detected(self):
        for change in ("content", "mode", "mtime"):
            destination = self.base / ("new-" + change)
            snapshot.copy_tree(str(self.source), str(destination), self.manifest, "preserve")
            file = destination / "root-control-state.json"
            if change == "content":
                file.write_bytes(b"corrupt")
            elif change == "mode":
                file.chmod(0o600)
            else:
                os.utime(file, ns=(1, 1))
            with self.assertRaises(snapshot.Refused):
                snapshot.verify(str(destination), self.manifest, "preserve")
        self.assertEqual(snapshot.inventory(str(self.source)), self.manifest["entries"])

    def test_explicit_owner_mapping_is_reported_and_validated(self):
        destination = self.base / "mapped"
        result = snapshot.copy_tree(str(self.source), str(destination), self.manifest, "map", os.geteuid(), os.getegid())
        self.assertEqual(result["ownership"], "map")
        self.assertTrue(snapshot.verify(str(destination), self.manifest, "map", os.geteuid(), os.getegid())["verified"])
        with self.assertRaises(snapshot.Refused):
            snapshot.expected_entries(self.manifest["entries"], "map")
        with self.assertRaises(snapshot.Refused):
            snapshot.expected_entries(self.manifest["entries"], "preserve", 10000, 10000)

    @unittest.skipUnless(hasattr(os, "geteuid") and os.geteuid() == 0, "real owner change requires isolated root")
    def test_real_uid_change_is_not_misreported_as_preserved_ownership(self):
        destination = self.base / "mapped-owner"
        snapshot.copy_tree(str(self.source), str(destination), self.manifest, "map", 10000, 10000)
        self.assertEqual((destination / "root-control-state.json").stat().st_uid, 10000)
        with self.assertRaises(snapshot.Refused):
            snapshot.verify(str(destination), self.manifest, "preserve")
        self.assertTrue(snapshot.verify(str(destination), self.manifest, "map", 10000, 10000)["verified"])

    def test_special_bits_and_xattrs_refused(self):
        target = self.source / "root-control-state.json"
        target.chmod(0o4640)
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))
        target.chmod(0o640)
        try:
            os.setxattr(target, "user.ppt-migration-test", b"do not silently lose")
        except OSError:
            self.skipTest("temporary filesystem has no user xattrs")
        with self.assertRaises(snapshot.Refused):
            snapshot.inventory(str(self.source))


@unittest.skipUnless(sys.platform.startswith("linux"), "credential-file contract is Linux-only")
class DatabaseWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ppt-p1-db-wrapper-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.service_file = self.base / "service.conf"
        self.passfile = self.base / "pgpass"
        self.service_file.write_text("[fixture]\nhost=/tmp\ndbname=not-a-real-database\n", encoding="utf-8")
        self.passfile.write_text("synthetic-never-used\n", encoding="utf-8")
        self.service_file.chmod(0o600)
        self.passfile.chmod(0o600)

    def fake(self, messages, exitcode=0):
        script = self.base / "fake-psql"
        script.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                          "assert 'PGPASSWORD' not in os.environ\nassert 'DATABASE_URL' not in os.environ\n"
                          "assert '-X' in sys.argv and '--no-password' in sys.argv\n"
                          "assert os.environ['PGPASSFILE'].endswith('/pgpass')\n"
                          + "\n".join("print(" + repr(json.dumps(message)) + ")" for message in messages)
                          + "\nsys.exit(" + str(exitcode) + ")\n", encoding="utf-8")
        script.chmod(0o700)
        return str(script)

    def messages(self):
        state = {"nonterminal_jobs": 0, "unproven_attached_jobs": 0, "active_projects": 0,
                 "slots": 1, "idle_slot": 1, "parser_writers_or_leases": 0}
        return ([{"kind": "tables", "value": list(db_inventory.TABLES)}]
                + [{"kind": "table", "table": table} for table in db_inventory.TABLES]
                + [{"kind": "quiescence", "value": state}, {"kind": "done"}])

    def run_fingerprint(self, messages, exitcode=0):
        with patch.dict(os.environ, {"PGPASSWORD": "must-not-leak", "DATABASE_URL": "must-not-leak"}):
            return db_inventory.fingerprint("public", "fixture", str(self.service_file), str(self.passfile),
                                            self.fake(messages, exitcode))

    def test_complete_readonly_output_is_fingerprinted_without_inherited_credentials(self):
        result = self.run_fingerprint(self.messages())
        self.assertEqual(len(result["tables"]), 12)
        self.assertTrue(all(value["rows"] == 0 for value in result["tables"].values()))
        sql = db_inventory.query("public")
        self.assertIn("REPEATABLE READ READ ONLY", sql)
        self.assertIn("parser_writers_or_leases", sql)
        self.assertNotIn("DATABASE_URL", sql)

    def test_missing_table_partial_stream_or_nonzero_psql_are_rejected(self):
        bad = self.messages()
        bad[0] = {"kind": "tables", "value": list(db_inventory.TABLES[:-1])}
        for messages, code in ((bad, 0), (self.messages()[:-1], 0), (self.messages(), 3)):
            with self.assertRaises(snapshot.Refused):
                self.run_fingerprint(messages, code)

    def test_active_job_slot_or_parser_lease_rejected(self):
        for key in ("nonterminal_jobs", "unproven_attached_jobs", "active_projects", "parser_writers_or_leases"):
            messages = self.messages()
            messages[-2]["value"][key] = 1
            with self.assertRaises(snapshot.Refused):
                self.run_fingerprint(messages)
        messages = self.messages()
        messages[-2]["value"]["idle_slot"] = 0
        with self.assertRaises(snapshot.Refused):
            self.run_fingerprint(messages)

    def test_public_credential_permissions_symlink_and_sql_identifier_rejected(self):
        self.passfile.chmod(0o644)
        with self.assertRaises(snapshot.Refused):
            db_inventory.credential_file(str(self.passfile))
        self.passfile.chmod(0o600)
        alias = self.base / "alias"
        alias.symlink_to(self.passfile)
        with self.assertRaises(OSError):
            db_inventory.credential_file(str(alias))
        with self.assertRaises(snapshot.Refused):
            db_inventory.query('public"; DROP DATABASE postgres;--')


if __name__ == "__main__":
    unittest.main(verbosity=2)
