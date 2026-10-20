"""The advanced package generator: real packages, real verification.

These tests create real archives on disk and read them back, so a regression
in the writer, the manifest or the verification shows up here.
"""

from __future__ import annotations

import json
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.config import AppConfig  # noqa: E402
from ece_autocompiler.packaging import (  # noqa: E402
    AdvancedPackager,
    PackageMetadata,
    PackageRequest,
)
from ece_autocompiler.packaging.collection import (  # noqa: E402
    is_system_path,
    matches_any,
)
from ece_autocompiler.packaging.formats import (  # noqa: E402
    describe_formats,
    format_ids,
    get_format,
)
from ece_autocompiler.packaging.licenses import (  # noqa: E402
    classify_license,
    redistribution_warnings,
)
from ece_autocompiler.packaging.manifest import SCHEMA  # noqa: E402


class PackageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        (self.project / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (self.project / "data.json").write_text('{"a": 1}\n', encoding="utf-8")
        (self.project / "node_modules").mkdir()
        (self.project / "node_modules" / "junk.js").write_text("// junk\n", encoding="utf-8")
        (self.project / "__pycache__").mkdir()
        (self.project / "__pycache__" / "x.pyc").write_text("cached\n", encoding="utf-8")
        self.out = self.tmp / "out"
        self.work = self.tmp / "work"

    def config(self) -> AppConfig:
        cfg = AppConfig(output_dir=self.out, work_dir=self.work)
        cfg.ensure_directories()
        return cfg

    def request(self, **kwargs) -> PackageRequest:
        params = {
            "project": self.project,
            "output_dir": self.out,
            "fmt": "zip",
            "metadata": PackageMetadata(name="Demo App", version="1.2.3"),
            "overwrite": True,
        }
        params.update(kwargs)
        return PackageRequest(**params)


class ZipPackageTests(PackageTestCase):
    def test_zip_package_is_created_and_verified(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request())
        self.assertTrue(result.ok, msg=result.text())
        self.assertTrue(result.path.is_file())
        self.assertTrue(result.validated)
        self.assertTrue(result.sha256)
        self.assertEqual(result.entry_point, "app.py")

        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
            self.assertIn("app.py", names)
            self.assertIn("package-manifest.json", names)
            # Cache and dependency folders never belong in a package.
            self.assertFalse(any(name.startswith("__pycache__/") for name in names))
            self.assertFalse(any(name.startswith("node_modules/") for name in names))

    def test_manifest_lists_every_file_with_a_checksum(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request())
        self.assertTrue(result.ok, msg=result.text())
        with zipfile.ZipFile(result.path) as archive:
            manifest = json.loads(archive.read("package-manifest.json").decode("utf-8"))
        self.assertEqual(manifest["schema"], SCHEMA)
        self.assertEqual(manifest["application"]["name"], "Demo App")
        self.assertEqual(manifest["application"]["version"], "1.2.3")
        paths = {entry["path"] for entry in manifest["content"]["files"]}
        self.assertIn("app.py", paths)
        for entry in manifest["content"]["files"]:
            self.assertEqual(len(entry["sha256"]), 64)

    def test_tar_gz_package_is_created(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request(fmt="tar.gz"))
        self.assertTrue(result.ok, msg=result.text())
        with tarfile.open(result.path, "r:gz") as archive:
            names = archive.getnames()
        self.assertIn("app.py", names)
        self.assertIn("package-manifest.json", names)

    def test_folder_package_is_created(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request(fmt="folder"))
        self.assertTrue(result.ok, msg=result.text())
        self.assertTrue((result.path / "app.py").is_file())
        self.assertTrue((result.path / "package-manifest.json").is_file())

    def test_two_runs_produce_the_same_archive(self) -> None:
        packager = AdvancedPackager(config=self.config())
        first = packager.package(self.request())
        second = packager.package(self.request())
        self.assertTrue(first.ok and second.ok)
        self.assertEqual(
            first.sha256,
            second.sha256,
            "a reproducible package must be byte-identical between runs",
        )

    def test_include_pattern_restricts_the_package(self) -> None:
        result = AdvancedPackager(config=self.config()).package(
            self.request(include=["data.json"])
        )
        self.assertTrue(result.ok, msg=result.text())
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
        self.assertIn("data.json", names)
        self.assertNotIn("app.py", names)

    def test_exclude_pattern_removes_a_file(self) -> None:
        result = AdvancedPackager(config=self.config()).package(
            self.request(exclude=["data.json"])
        )
        self.assertTrue(result.ok, msg=result.text())
        with zipfile.ZipFile(result.path) as archive:
            names = archive.namelist()
        self.assertNotIn("data.json", names)
        self.assertIn("app.py", names)

    def test_missing_dependency_is_reported(self) -> None:
        (self.project / "requirements.txt").write_text(
            "definitely-not-installed-xyz==1.0\n", encoding="utf-8"
        )
        result = AdvancedPackager(config=self.config()).package(self.request())
        self.assertTrue(result.ok, msg=result.text())
        names = [issue.name for issue in result.dependencies]
        self.assertIn("definitely-not-installed-xyz", names)
        issue = next(i for i in result.dependencies if i.name == "definitely-not-installed-xyz")
        self.assertEqual(issue.kind, "missing")
        self.assertIn("pip install", issue.action)

    def test_missing_license_is_reported(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request())
        self.assertTrue(result.ok, msg=result.text())
        self.assertTrue(
            any("license" in warning.lower() for warning in result.warnings),
            msg=str(result.warnings),
        )

    def test_copyleft_license_needs_confirmation(self) -> None:
        (self.project / "LICENSE").write_text(
            "GNU GENERAL PUBLIC LICENSE Version 3", encoding="utf-8"
        )
        result = AdvancedPackager(config=self.config()).package(self.request())
        self.assertTrue(result.ok, msg=result.text())
        self.assertTrue(any("copyleft" in w.lower() for w in result.warnings))
        allowed = AdvancedPackager(config=self.config()).package(
            self.request(allow_copyleft=True)
        )
        self.assertTrue(allowed.ok)
        self.assertTrue(any("copyleft" in w.lower() for w in allowed.warnings))

    def test_unknown_format_fails_with_the_supported_list(self) -> None:
        result = AdvancedPackager(config=self.config()).package(self.request(fmt="rar"))
        self.assertFalse(result.ok)
        self.assertTrue(result.errors)
        self.assertIn("zip", " ".join(result.errors))

    def test_existing_package_is_not_overwritten_without_permission(self) -> None:
        packager = AdvancedPackager(config=self.config())
        first = packager.package(self.request(overwrite=True))
        self.assertTrue(first.ok, msg=first.text())
        second = packager.package(self.request(overwrite=False))
        self.assertFalse(second.ok)
        self.assertIn("already exists", second.message)

    def test_installer_without_its_tool_reports_what_is_missing(self) -> None:
        from ece_autocompiler.packaging.tools import tool_path

        if tool_path("inno-setup"):
            self.skipTest("Inno Setup is installed on this machine")
        result = AdvancedPackager(config=self.config()).package(self.request(fmt="inno"))
        self.assertFalse(result.ok)
        self.assertIn("not available", result.message)
        self.assertTrue(result.errors)
        self.assertFalse(result.path)

    def test_deb_format_without_dpkg_reports_what_is_missing(self) -> None:
        from ece_autocompiler.packaging.tools import tool_path

        if tool_path("dpkg-deb"):
            self.skipTest("dpkg-deb is installed on this machine")
        result = AdvancedPackager(config=self.config()).package(self.request(fmt="deb"))
        self.assertFalse(result.ok)
        self.assertIn("not available", result.message)


class FormatRegistryTests(unittest.TestCase):
    def test_every_format_is_described(self) -> None:
        described = describe_formats()
        self.assertEqual(len(described), len(format_ids()))
        for entry in described:
            self.assertIn("id", entry)
            self.assertIn("available", entry)
            self.assertIsInstance(entry["available"], bool)

    def test_unknown_format_returns_none(self) -> None:
        self.assertIsNone(get_format("nope"))


class CollectionHelperTests(unittest.TestCase):
    def test_pattern_matching(self) -> None:
        self.assertTrue(matches_any("build/app.exe", ["build/*"]))
        self.assertTrue(matches_any("src/main.py", ["*.py"]))
        self.assertFalse(matches_any("src/main.py", ["*.rs"]))
        self.assertFalse(matches_any("src/main.py", []))

    def test_system_paths_are_recognised(self) -> None:
        self.assertTrue(is_system_path(Path("C:/Windows/System32/kernel32.dll")))
        self.assertTrue(is_system_path(Path("/usr/lib/libc.so")))
        self.assertFalse(is_system_path(Path("C:/projects/app/dist/app.exe")))


class LicenseTests(unittest.TestCase):
    def test_license_classification(self) -> None:
        self.assertEqual(
            classify_license("Permission is hereby granted, free of charge"), "MIT"
        )
        self.assertEqual(
            classify_license("GNU GENERAL PUBLIC LICENSE Version 3"), "GPL"
        )
        self.assertEqual(classify_license("something else entirely"), "Unknown")

    def test_copyleft_warning_requires_approval(self) -> None:
        from ece_autocompiler.packaging.licenses import LicenseFinding

        findings = [LicenseFinding(path="LICENSE", spdx="GPL")]
        blocked = redistribution_warnings(findings, allow_copyleft=False)
        allowed = redistribution_warnings(findings, allow_copyleft=True)
        self.assertTrue(blocked)
        self.assertTrue(allowed)
        self.assertIn("allow-copyleft", blocked[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
