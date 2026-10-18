"""Toolchains that are present but half-working.

Real-world failure shapes: a dotnet runtime without an SDK, a tool that
answers but reports no version, a tool writing its version to stderr, and a
tool missing from PATH. Probes are read-only, so the tests drive them with
small ``.cmd`` fakes placed first on PATH and assert the honest reporting.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.detection import toolchains  # noqa: E402


@unittest.skipUnless(os.name == "nt", "fake tool probes use .cmd wrappers")
class FakeToolMixin:
    """Puts fake executables first on PATH for the duration of a test."""

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self.fake_dir = Path(tempfile.mkdtemp(prefix="ece_tools_"))
        self.addCleanup(self._restore)
        self._old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.fake_dir) + os.pathsep + self._old_path

    def _restore(self) -> None:
        os.environ["PATH"] = self._old_path
        toolchains.discover_all.cache_clear()
        import shutil

        shutil.rmtree(self.fake_dir, ignore_errors=True)

    def _fake(self, name: str, body: str) -> Path:
        script = self.fake_dir / f"{name}.cmd"
        script.write_text("@echo off\r\n" + body + "\r\n", encoding="utf-8")
        return script

    def _probe(self, tool_id: str):
        """Run one probe directly (no full discovery, no caching)."""
        spec = next(s for s in toolchains.SPECS if s.id == tool_id)
        return toolchains._discover_one(spec)


class DotnetPartialTests(FakeToolMixin, unittest.TestCase):
    def test_runtime_without_sdk_is_reported(self) -> None:
        # `dotnet --list-sdks` prints nothing when only the runtime exists.
        self._fake("dotnet", "exit /b 0")
        info = self._probe("dotnet")
        self.assertFalse(info.found)
        self.assertEqual(info.error, "Runtime present but no SDK found")
        self.assertIsNotNone(info.path)  # the binary itself was located

    def test_sdk_version_is_parsed_and_trimmed(self) -> None:
        self._fake("dotnet", "echo 8.0.100 [C:\\Program Files\\dotnet\\sdk]")
        info = self._probe("dotnet")
        self.assertTrue(info.found)
        self.assertEqual(info.version, "8.0.100")

    def test_missing_from_path_is_honest(self) -> None:
        # PATH already starts with an empty fake dir; remove any real dotnet.
        os.environ["PATH"] = str(self.fake_dir)
        info = self._probe("dotnet")
        self.assertFalse(info.found)
        self.assertEqual(info.error, "Not found on PATH")
        self.assertTrue(info.install_hint.startswith("https://"))


class VersionProbeTests(FakeToolMixin, unittest.TestCase):
    def test_tool_without_version_output_is_found_but_versionless(self) -> None:
        self._fake("node", "exit /b 0")
        info = self._probe("node")
        self.assertTrue(info.found)
        self.assertIsNone(info.version)
        self.assertIsNone(info.error)

    def test_version_on_stdout(self) -> None:
        self._fake("node", "echo v20.11.1")
        info = self._probe("node")
        self.assertTrue(info.found)
        self.assertEqual(info.version, "v20.11.1")

    def test_version_on_stderr_is_captured(self) -> None:
        # java-style tools print the version to stderr.
        self._fake("java", "echo openjdk 19.0.1 2022-09-20 1>&2")
        info = self._probe("java")
        self.assertTrue(info.found)
        self.assertEqual(info.version, "openjdk 19.0.1 2022-09-20")

    def test_failing_probe_still_reports_found(self) -> None:
        # The binary exists but the probe fails without output (rc is ignored
        # on purpose: banner-style tools like cl.exe exit non-zero too).
        self._fake("cargo", "exit /b 9")
        info = self._probe("cargo")
        self.assertTrue(info.found)
        self.assertIsNone(info.version)

    def test_to_dict_shape_for_partial_tool(self) -> None:
        self._fake("dotnet", "exit /b 0")
        payload = self._probe("dotnet").to_dict()
        for key in ("id", "name", "found", "path", "version", "error", "install_hint"):
            self.assertIn(key, payload)
        self.assertFalse(payload["found"])


class InstallHintTests(unittest.TestCase):
    def test_every_missing_tool_advertises_an_install_path(self) -> None:
        for tool in toolchains.SPECS:
            if tool.id in ("python", "git"):
                continue  # expected to exist on any dev machine
            self.assertTrue(
                tool.install_hint,
                f"tool {tool.id!r} has no install hint for actionable errors",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
