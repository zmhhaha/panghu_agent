"""No-model preview boundary tests; optional Linux test uses pinned official code."""
import http.client
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from ppt_service.worker import preview


class PreviewTests(unittest.TestCase):
    def setUp(self):
        identity = patch.object(preview.os, "getresuid", return_value=(10000, 10000, 10000), create=True)
        identity.start()
        self.addCleanup(identity.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.project = self.parent / "deck"
        self.project.mkdir()
        for name in ("design_spec.md", "spec_lock.md"):
            (self.project / name).write_text("fixture", encoding="utf-8")

    def supervisor(self):
        return preview.OfficialPreview(self.parent, lifetime=120)

    def health(self, process):
        return {"service": "live_preview", "status": "ok", "live": True,
                "project": str(self.project), "svg_output": str(self.project / "svg_output"),
                "pid": process.pid}

    def test_one_root_and_bounded_scan(self):
        self.assertEqual(preview.project_root(self.parent), self.project)
        for constant, limit in (("MAX_ENTRIES", 1), ("MAX_DEPTH", 0)):
            with patch.object(preview, constant, limit), self.assertRaises(ValueError):
                self.supervisor()
        second = self.parent / "second"
        second.mkdir()
        for name in ("design_spec.md", "spec_lock.md"):
            (second / name).write_text("fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.supervisor()

    def test_missing_or_parent_root_rejected(self):
        (self.project / "spec_lock.md").unlink()
        with self.assertRaises(ValueError):
            self.supervisor()
        for name in ("design_spec.md", "spec_lock.md"):
            (self.parent / name).write_text("fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.supervisor()
        (self.project / "spec_lock.md").write_text("fixture", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.supervisor()

    def revision_base(self):
        base = self.project / "revisions" / "round-08" / "base"
        base.mkdir(parents=True)
        for name in ("design_spec.md", "spec_lock.md"):
            (base / name).write_text("immutable base fixture", encoding="utf-8")
        return base

    def test_nested_revision_base_markers_select_current_direct_child(self):
        self.revision_base()
        parsed = self.parent / "parsed_inputs"
        parsed.mkdir()
        (parsed / "input.md").write_text("parsed fixture", encoding="utf-8")
        self.assertEqual(preview.project_root(self.parent), self.project)
        self.assertEqual(self.supervisor().project, self.project)
        for constant, limit in (("MAX_ENTRIES", 8), ("MAX_DEPTH", 3)):
            with patch.object(preview, constant, limit), self.assertRaises(ValueError):
                self.supervisor()

    def test_nested_only_markers_are_not_a_restored_root(self):
        self.revision_base()
        for name in ("design_spec.md", "spec_lock.md"):
            (self.project / name).unlink()
        with self.assertRaises(ValueError):
            self.supervisor()

    def test_nested_revision_archive_unsafe_entries_rejected(self):
        base = self.revision_base()
        original = Path.lstat
        for target, mode, attributes in (
                (base, preview.stat.S_IFLNK, 0),
                (base, preview.stat.S_IFDIR, 0x400),
                (base / "spec_lock.md", preview.stat.S_IFLNK, 0),
                (base / "spec_lock.md", preview.stat.S_IFREG, 0x400),
                (base / "spec_lock.md", preview.stat.S_IFIFO, 0)):
            def hostile(path, *args, **kwargs):
                if path == target:
                    return Mock(st_mode=mode, st_file_attributes=attributes, st_nlink=1)
                return original(path, *args, **kwargs)
            with self.subTest(target=target, mode=mode), patch.object(Path, "lstat", hostile):
                with self.assertRaises(ValueError):
                    self.supervisor()
        os.link(base / "spec_lock.md", base / "alias")
        with self.assertRaises(ValueError):
            self.supervisor()

    def test_links_and_path_escape_rejected(self):
        original = Path.lstat
        for target, mode, attributes in (
                (self.project, preview.stat.S_IFLNK, 0),
                (self.project, preview.stat.S_IFDIR, 0x400),
                (self.project / "spec_lock.md", preview.stat.S_IFREG, 0x400),
                (self.project / "spec_lock.md", preview.stat.S_IFIFO, 0),
                (self.parent, preview.stat.S_IFLNK, 0)):
            def hostile(path, *args, **kwargs):
                if path == target:
                    return Mock(st_mode=mode, st_file_attributes=attributes, st_nlink=1)
                return original(path, *args, **kwargs)
            with self.subTest(target=target, mode=mode), patch.object(Path, "lstat", hostile):
                with self.assertRaises(ValueError):
                    self.supervisor()
        os.link(self.project / "spec_lock.md", self.project / "alias")
        with self.assertRaises(ValueError):
            self.supervisor()

    def test_restored_locks_fail_without_signalling_any_pid(self):
        for relative in ("live_preview/lock.json", ".live_preview.lock"):
            lock = self.project / relative
            lock.parent.mkdir(exist_ok=True)
            lock.write_text(json.dumps({"pid": os.getpid(), "port": 8765}), encoding="utf-8")
            with patch.object(preview.os, "kill", create=True) as kill:
                with self.assertRaises(ValueError):
                    self.supervisor()
                kill.assert_not_called()
            lock.unlink()

    def test_root_or_retained_root_identity_rejected_before_bind_or_spawn(self):
        service = self.supervisor()
        for uids in ((0, 0, 0), (0, 10000, 0), (10000, 10000, 0), (10000, 20000, 10000)):
            with (self.subTest(uids=uids), patch.object(preview.os, "name", "posix"),
                  patch.object(preview.os, "getresuid", return_value=uids),
                  patch.object(preview.socket, "socket") as socket_factory,
                  patch.object(preview.subprocess, "Popen") as popen):
                with self.assertRaisesRegex(RuntimeError, "non-root attempt identity"):
                    service.start()
                socket_factory.assert_not_called()
                popen.assert_not_called()
                self.assertTrue(service.stopped)
                self.assertFalse(service.ready)

    def test_fixed_command_environment_and_ready_identity(self):
        service = self.supervisor()
        process = Mock(pid=12345)
        process.poll.return_value = None
        with (patch.object(preview.os, "name", "posix"),
              patch.object(preview.socket, "socket") as socket_factory,
              patch.object(preview.subprocess, "Popen", return_value=process) as popen,
              patch.object(service, "_request", return_value=self.health(process)),
              patch.dict(os.environ, {"PPT_MODEL_API_KEY": "secret", "API_SERVER_KEY": "secret",
                                      "HTTP_PROXY": "http://untrusted", "PPT_PREVIEW_URL": "https://untrusted"})):
            service.start()
        socket_factory.return_value.__enter__.return_value.bind.assert_called_once_with(("127.0.0.1", 8765))
        command = popen.call_args.args[0]
        self.assertEqual(command, [preview.PYTHON, preview.SERVER, str(self.project),
                                  "--live", "--no-browser", "--port", "8765", "--timeout", "120"])
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["env"], preview.renderer_environment())
        self.assertNotIn("secret", repr(popen.call_args))
        self.assertNotIn("untrusted", service.instructions())
        self.assertIn("--server-url http://127.0.0.1:8765", service.instructions())
        lines = service.instructions().splitlines()
        help_command = next(line.split('：', 1)[1] for line in lines if line.startswith('官方渲染帮助命令：'))
        render_command = next(line.split('：', 1)[1] for line in lines if line.startswith('本任务全页渲染命令：'))
        self.assertEqual(shlex.split(help_command), [preview.PYTHON, preview.VISUAL_REVIEW, '--help'])
        self.assertEqual(shlex.split(render_command), [preview.PYTHON, preview.VISUAL_REVIEW,
                                                     str(self.project), '--server-url', preview.ORIGIN])
        self.assertNotIn('--pages', shlex.split(render_command))
        self.assertTrue(service.ready)

    def test_render_context_shell_quotes_project_and_preserves_approval_stop_rule(self):
        self.project = self.project.rename(self.project.with_name("deck with spaces 'quote' $(marker)"))
        service = self.supervisor()
        with self.assertRaisesRegex(RuntimeError, 'readiness'):
            service.instructions()
        service.ready = True  # Only exercising serialization after the separate readiness test.
        context = service.instructions()
        command = next(line.split('：', 1)[1] for line in context.splitlines()
                       if line.startswith('本任务全页渲染命令：'))
        self.assertEqual(shlex.split(command), [preview.PYTHON, preview.VISUAL_REVIEW,
                                               str(self.project), '--server-url', preview.ORIGIN])
        self.assertIn('同一操作已被拒绝、超时或待授权时必须停止', context)
        self.assertIn('不得更换解释器或改写命令重试', context)

    def test_health_must_match_all_identity_fields(self):
        for field, value in (("project", "/outside"), ("pid", 9), ("pid", True),
                             ("service", "other"), ("svg_output", "/outside"),
                             ("live", False), ("status", "error")):
            service = self.supervisor()
            process = Mock(pid=12345)
            process.poll.return_value = None
            health = self.health(process)
            health[field] = value
            with (self.subTest(field=field, value=value),
                  patch.object(preview.os, "name", "posix"),
                  patch.object(preview.socket, "socket"),
                  patch.object(preview.subprocess, "Popen", return_value=process),
                  patch.object(service, "_request", return_value=health)):
                with self.assertRaises(RuntimeError):
                    service.start()
                self.assertFalse(service.ready)
                with self.assertRaises(RuntimeError):
                    service.instructions()

    def test_exited_launcher_and_interrupt_never_ready(self):
        for exited, interrupted in ((0, False), (None, True)):
            service = self.supervisor()
            process = Mock(pid=12345)
            process.poll.return_value = exited
            with (patch.object(preview.os, "name", "posix"),
                  patch.object(preview.socket, "socket"),
                  patch.object(preview.subprocess, "Popen", return_value=process),
                  patch.object(service, "_request") as request):
                with self.assertRaises(RuntimeError):
                    service.start(lambda: interrupted)
                request.assert_not_called()
                self.assertFalse(service.ready)

    def test_occupied_origin_does_not_spawn_or_attach(self):
        service = self.supervisor()
        with (patch.object(preview.os, "name", "posix"),
              patch.object(preview.socket, "socket") as socket_factory,
              patch.object(preview.subprocess, "Popen") as popen):
            socket_factory.return_value.__enter__.return_value.bind.side_effect = OSError("occupied")
            with self.assertRaises(OSError):
                service.start()
            popen.assert_not_called()
            service.stop()
            self.assertTrue(service.stopped)

    def test_http_rejects_redirect_and_oversize_without_environment_proxy(self):
        service = self.supervisor()
        for status, body in ((302, b"{}"), (200, b"x" * 65537), (200, b"[]")):
            connection = Mock()
            connection.getresponse.return_value.status = status
            connection.getresponse.return_value.read.return_value = body
            with patch.object(http.client, "HTTPConnection", return_value=connection) as factory:
                with self.assertRaises(ValueError):
                    service._request("/api/health")
                factory.assert_called_once_with("127.0.0.1", 8765, timeout=0.5)
                connection.close.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "Linux process-group signals required")
    def test_shutdown_waits_and_checks_group_after_official_http(self):
        service = self.supervisor()
        process = Mock(pid=12345)
        process.poll.return_value = None
        service.process, service.ready, service.stopped = process, True, False
        with (patch.object(service, "_request", return_value={}) as request,
              patch.object(preview.os, "killpg", side_effect=ProcessLookupError) as killpg):
            service.stop()
        request.assert_called_once_with("/api/shutdown", method="POST")
        process.wait.assert_any_call(timeout=3)
        killpg.assert_called_once_with(12345, signal.SIGTERM)
        self.assertTrue(service.stopped)

    @unittest.skipUnless(os.name == "posix", "Linux process-group signals required")
    def test_shutdown_timeout_forces_group_kill_and_does_not_fake_success(self):
        service = self.supervisor()
        process = Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("fixture", 1)
        service.process, service.ready, service.stopped = process, True, False
        with (patch.object(service, "_request", side_effect=OSError),
              patch.object(preview.os, "killpg") as killpg):
            with self.assertRaises(RuntimeError):
                service.stop(grace=0)
        self.assertEqual(killpg.call_args_list[0].args, (12345, signal.SIGTERM))
        self.assertEqual(killpg.call_args_list[1].args, (12345, signal.SIGKILL))
        self.assertFalse(service.stopped)


@unittest.skipUnless(os.name == "posix" and Path(preview.SERVER).is_file()
                     and Path(preview.PYTHON).is_file(),
                     "Pinned official Linux image is required")
class OfficialLifecycleTests(unittest.TestCase):
    def test_real_official_foreground_health_and_shutdown_without_rendering(self):
        # Only an empty private project is needed. No model, credential, renderer,
        # gateway, network service or existing attempt is touched.
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            project = parent / "deck"
            project.mkdir()
            for name in ("design_spec.md", "spec_lock.md"):
                (project / name).write_text("lifecycle fixture", encoding="utf-8")
            service = preview.OfficialPreview(parent, lifetime=60)
            try:
                service.start()
                self.assertTrue(service.ready)
                health = service._request("/api/health")
                self.assertEqual(health["pid"], service.process.pid)
                self.assertEqual(health["project"], str(project))
            finally:
                service.stop()
            self.assertTrue(service.stopped)
            self.assertIsNotNone(service.process.poll())
            with self.assertRaises(ProcessLookupError):
                os.killpg(service.process.pid, 0)
            with self.assertRaises(OSError):
                service._request("/api/health")


if __name__ == "__main__":
    unittest.main()
