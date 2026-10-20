"""The release checker has to be honest about what it verified.

`tools/release_check.py` guards the release: it runs the console build, watches
the desktop build stay alive, and reports sizes and hashes. These tests pin its
judgement - a good run passes, a crashed run fails, a missing artifact fails
with what to do - so the guard itself cannot rot into a rubber stamp.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _add_paths() -> None:
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "src", root / "tools"):
        if candidate.is_dir() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


_add_paths()

import release_check  # noqa: E402
from ece_autocompiler.version import APP_NAME, APP_VERSION  # noqa: E402


class _FakeProcess:
    """Minimal Popen stand-in: stays alive for `alive_polls` polls."""

    def __init__(self, *, alive_polls: int | None = None, exit_code: int = 3) -> None:
        self.returncode = None
        self.terminated = False
        self._alive_polls = alive_polls
        self._exit_code = exit_code

    def poll(self):
        if self._alive_polls is None:  # never exits on its own
            return None
        if self._alive_polls > 0:
            self._alive_polls -= 1
            return None
        self.returncode = self._exit_code
        return self.returncode

    def terminate(self) -> None:  # pragma: no cover - exercised via launch_probe
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None) -> int:  # pragma: no cover
        return self.returncode or 0


class HashingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_rel_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_sha256_matches_hashlib(self) -> None:
        blob = b"contenido de prueba \xf0\x9f\x9a\x80" * 1000
        target = self.tmp / "file.bin"
        target.write_bytes(blob)
        self.assertEqual(release_check.sha256_file(target), hashlib.sha256(blob).hexdigest())

    def test_human_size_is_readable(self) -> None:
        self.assertEqual(release_check.human_size(0), "0 B")
        self.assertEqual(release_check.human_size(1023), "1023 B")
        self.assertEqual(release_check.human_size(1024), "1.00 KB")
        self.assertEqual(release_check.human_size(5 * 1024 * 1024), "5.00 MB")


class VersionJudgementTests(unittest.TestCase):
    def test_accepts_this_product_and_version(self) -> None:
        ok, detail = release_check.check_version_output(f"{APP_NAME} {APP_VERSION}\n")
        self.assertTrue(ok)
        self.assertIn(APP_VERSION, detail)

    def test_rejects_output_without_the_product_name(self) -> None:
        ok, detail = release_check.check_version_output(f"algo {APP_VERSION}\n")
        self.assertFalse(ok)
        self.assertIn(APP_NAME, detail)

    def test_rejects_output_with_the_wrong_version(self) -> None:
        ok, detail = release_check.check_version_output(f"{APP_NAME} 9.9.9\n")
        self.assertFalse(ok)
        self.assertIn(APP_VERSION, detail)

    def test_verify_cli_exe_reads_stdout(self) -> None:
        def runner(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=f"{APP_NAME} {APP_VERSION}\n", stderr="")

        ok, _ = release_check.verify_cli_exe(Path("x.exe"), runner=runner)
        self.assertTrue(ok)

    def test_verify_cli_exe_rejects_nonzero_exit(self) -> None:
        def runner(*_args, **_kwargs):
            return SimpleNamespace(returncode=1, stdout=f"{APP_NAME} {APP_VERSION}", stderr="")

        ok, detail = release_check.verify_cli_exe(Path("x.exe"), runner=runner)
        self.assertFalse(ok)
        self.assertIn("exited", detail)

    def test_verify_cli_exe_reports_an_executable_that_cannot_run(self) -> None:
        def runner(*_args, **_kwargs):
            raise OSError("no es un ejecutable valido")

        ok, detail = release_check.verify_cli_exe(Path("x.exe"), runner=runner)
        self.assertFalse(ok)
        self.assertIn("could not be run", detail)


class LaunchProbeTests(unittest.TestCase):
    def test_alive_window_passes_and_is_cleaned_up(self) -> None:
        proc = _FakeProcess(alive_polls=None)
        ok, detail = release_check.launch_probe(
            Path("gui.exe"), popen=lambda _argv: proc, wait_seconds=0.1
        )
        self.assertTrue(ok)
        self.assertIn("still running", detail)
        self.assertTrue(proc.terminated, "the probe must close what it started")

    def test_early_crash_fails(self) -> None:
        proc = _FakeProcess(alive_polls=0, exit_code=7)
        ok, detail = release_check.launch_probe(
            Path("gui.exe"), popen=lambda _argv: proc, wait_seconds=0.5
        )
        self.assertFalse(ok)
        self.assertIn("7", detail)

    def test_unstartable_binary_fails(self) -> None:
        def popen(_argv):
            raise OSError("denied")

        ok, detail = release_check.launch_probe(Path("gui.exe"), popen=popen)
        self.assertFalse(ok)
        self.assertIn("could not be started", detail)


class FunctionalProbeTests(unittest.TestCase):
    """The release check must prove the product works, not only that it starts."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_probe_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _runner(self, *, fail_on: str = ""):
        import zipfile

        def runner(argv, **_kwargs):
            command = " ".join(argv)
            if fail_on and fail_on in command:
                return SimpleNamespace(returncode=1, stdout="", stderr="boom")
            if " build " in f" {command} ":
                container = Path(argv[argv.index("-o") + 1])
                container.write_bytes(b"container")
            elif " package " in f" {command} ":
                out_dir = Path(argv[argv.index("--output-dir") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(out_dir / "demo-1.0.0.zip", "w") as archive:
                    archive.writestr("app.py", "print(1)")
                    archive.writestr("package-manifest.json", "{}")
            return SimpleNamespace(returncode=0, stdout="ok", stderr="")

        return runner

    def test_a_working_cli_passes_the_probe(self) -> None:
        ok, lines = release_check.functional_probe(
            Path("cli.exe"), work=self.tmp, runner=self._runner()
        )
        self.assertTrue(ok, msg=str(lines))
        self.assertTrue(any("manifest" in line for line in lines))

    def test_a_failing_build_fails_the_probe(self) -> None:
        ok, lines = release_check.functional_probe(
            Path("cli.exe"), work=self.tmp, runner=self._runner(fail_on=" build ")
        )
        self.assertFalse(ok)
        self.assertTrue(lines)

    def test_a_failing_package_fails_the_probe(self) -> None:
        ok, lines = release_check.functional_probe(
            Path("cli.exe"), work=self.tmp, runner=self._runner(fail_on=" package ")
        )
        self.assertFalse(ok)
        self.assertTrue(lines)

    def test_the_probe_does_not_touch_the_real_application_data(self) -> None:
        seen: dict = {}

        def runner(argv, **kwargs):
            seen.update(kwargs.get("env") or {})
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        release_check.functional_probe(Path("cli.exe"), work=self.tmp, runner=runner)
        self.assertIn("ECE_AUTOCOMPILER_HOME", seen)
        self.assertIn(str(self.tmp), seen["ECE_AUTOCOMPILER_HOME"])


class ChecklistTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="ece_reldist_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tmp.joinpath(release_check.GUI_EXE_NAME).write_bytes(b"GUI" * 10)
        self.tmp.joinpath(release_check.CLI_EXE_NAME).write_bytes(b"CLI" * 10)

    def _good_runner(self):
        def runner(*_args, **_kwargs):
            return SimpleNamespace(returncode=0, stdout=f"{APP_NAME} {APP_VERSION}\n", stderr="")

        return runner

    def test_missing_artifacts_fail_with_guidance(self) -> None:
        empty = self.tmp / "nada"
        empty.mkdir()
        artifacts = release_check.check_artifacts(empty)
        self.assertTrue(all(not a.passed for a in artifacts))
        detail = artifacts[0].checks[0][2]
        self.assertIn("build_exe.ps1", detail)

    def test_both_artifacts_pass_when_they_behave(self) -> None:
        artifacts = release_check.check_artifacts(
            self.tmp,
            cli_runner=self._good_runner(),
            gui_popen=lambda _argv: _FakeProcess(alive_polls=None),
            wait_seconds=0.1,
        )
        self.assertTrue(all(a.passed for a in artifacts), [a.as_dict() for a in artifacts])
        gui = artifacts[0]
        self.assertGreater(gui.size, 0)
        self.assertEqual(len(gui.sha256), 64)

    def test_skip_launch_still_checks_the_console_build(self) -> None:
        artifacts = release_check.check_artifacts(
            self.tmp, skip_launch=True, cli_runner=self._good_runner()
        )
        gui, cli = artifacts
        self.assertTrue(gui.passed)
        self.assertIn("skipped", gui.checks[-1][2])
        self.assertTrue(cli.passed)

    def test_main_writes_json_and_fails_loudly_on_an_empty_dist(self) -> None:
        empty = self.tmp / "vacio"
        empty.mkdir()
        out = io.StringIO()
        with redirect_stdout(out):
            code = release_check.main(["--dist", str(empty)])
        self.assertEqual(code, 1)
        text = out.getvalue()
        self.assertIn("What to do:", text)
        self.assertIn("build_exe.ps1", text)
        payload = json.loads((empty / "release.json").read_text(encoding="utf-8"))
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["version"], APP_VERSION)

    def test_main_passes_and_records_hashes(self) -> None:
        out = io.StringIO()
        with mock.patch.object(
            release_check, "verify_cli_exe", return_value=(True, "ok")
        ), mock.patch.object(
            release_check, "launch_probe", return_value=(True, "ok")
        ), redirect_stdout(out):
            code = release_check.main(["--dist", str(self.tmp)])
        self.assertEqual(code, 0)
        self.assertIn("All checks passed", out.getvalue())
        payload = json.loads((self.tmp / "release.json").read_text(encoding="utf-8"))
        self.assertTrue(payload["ok"])
        for artifact in payload["artifacts"]:
            self.assertEqual(len(artifact["sha256"]), 64)
            self.assertTrue(artifact["passed"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
