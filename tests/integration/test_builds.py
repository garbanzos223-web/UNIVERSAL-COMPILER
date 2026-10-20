import shutil
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

from ece_autocompiler.build import BuildOrchestrator  # noqa: E402
from ece_autocompiler.build.exe_exporter import compile_exe, pyinstaller_available  # noqa: E402
from ece_autocompiler.container import validate_container  # noqa: E402
from ece_autocompiler.errors import ConfigurationError  # noqa: E402
from ece_autocompiler.extraction import Extractor  # noqa: E402


class PythonBuildIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "pyproj"
        self.project.mkdir()
        (self.project / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.project / "requirements.txt").write_text("# none\n", encoding="utf-8")
        (self.project / "pkg").mkdir()
        (self.project / "pkg" / "util.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.out = self.tmp / "pyproj.ece"

    def test_build_package_validate_extract(self) -> None:
        result, package = BuildOrchestrator().build_and_package(
            self.project, self.out, consent_granted=True, overwrite=True
        )
        self.assertTrue(result.ok, msg=str(result.errors))
        self.assertTrue(package.ok, msg=package.message)
        self.assertGreater(result.duration_seconds, 0.0)

        report = validate_container(package.container_path, deep=True)
        self.assertTrue(report.ok, msg=str(report.issues))

        dest = self.tmp / "extracted"
        Extractor().extract(package.container_path, dest, overwrite=True)
        self.assertTrue((dest / "app.py").exists())
        self.assertTrue((dest / "pkg" / "util.py").exists())

    def test_no_supported_project_raises(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(ConfigurationError):
            BuildOrchestrator().build_and_package(empty, self.tmp / "x.ece")

    def test_prefer_adapter(self) -> None:
        result, package = BuildOrchestrator().build_and_package(
            self.project, self.tmp / "p2.ece", consent_granted=True, overwrite=True, prefer_adapter="python"
        )
        self.assertTrue(result.ok)
        self.assertTrue(package.ok)


@unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
class NodeBuildIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import json

        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "nodeproj"
        self.project.mkdir()
        (self.project / "package.json").write_text(
            json.dumps({"name": "demo", "version": "1.0.0", "scripts": {"build": "node build.js"}}),
            encoding="utf-8",
        )
        (self.project / "build.js").write_text('console.log("building");\n', encoding="utf-8")
        (self.project / "index.js").write_text("module.exports = 42;\n", encoding="utf-8")
        self.out = self.tmp / "nodeproj.ece"

    def test_node_build_with_consent(self) -> None:
        result, package = BuildOrchestrator().build_and_package(
            self.project,
            self.out,
            config_overrides={"skip_npm_install": True},
            consent_granted=True,
            overwrite=True,
        )
        self.assertTrue(result.ok, msg=str(result.errors))
        self.assertTrue(package.ok, msg=package.message)
        self.assertTrue(validate_container(package.container_path, deep=True).ok)

    def test_node_build_without_consent_refused(self) -> None:
        with self.assertRaises(ConfigurationError):
            BuildOrchestrator().build_and_package(
                self.project,
                self.tmp / "n2.ece",
                config_overrides={"skip_npm_install": True},
                consent_granted=False,
                overwrite=True,
            )


@unittest.skipUnless(sys.platform == "win32", "Executable export targets Windows x64")
class ExeExportIntegrationTests(unittest.TestCase):
    """Real PyInstaller builds of a throwaway Python project, both layouts."""

    @classmethod
    def setUpClass(cls) -> None:
        available, info = pyinstaller_available()
        if not available:
            raise unittest.SkipTest(f"PyInstaller is not installed: {info}")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.project = self.tmp / "exe_proj"
        self.project.mkdir()
        (self.project / "main.py").write_text(
            'def main():\n    print("hello from an ece-built exe")\n\n'
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )

    def _fail_text(self, result) -> str:
        return result.message + "\n" + "\n".join(result.errors)

    def test_onefile_exe(self) -> None:
        out = self.tmp / "dist" / "app_onefile.exe"
        result = compile_exe(self.project, output=out, onefile=True, timeout=900)
        self.assertTrue(result.ok, msg=self._fail_text(result))
        self.assertTrue(out.is_file())
        # A onefile bundle is self-contained: one big file, no helper DLLs.
        helpers = [
            p for p in out.parent.iterdir()
            if p != out and p.suffix.lower() in (".dll", ".pyd")
        ]
        self.assertEqual(helpers, [], "onefile mode must not leave DLLs behind")
        self.assertGreater(out.stat().st_size, 1_000_000)
        self.assertEqual(result.language, "python")
        self.assertEqual(result.adapter_id, "python")

    def test_onedir_exe_keeps_bundle_folder(self) -> None:
        out = self.tmp / "dist" / "app_onedir.exe"
        result = compile_exe(self.project, output=out, onefile=False, timeout=900)
        self.assertTrue(result.ok, msg=self._fail_text(result))
        # Folder mode: dist\app_onedir\app_onedir.exe (exe + runtime together).
        bundle = out.parent / out.stem
        self.assertTrue(bundle.is_dir(), "onedir mode must keep the bundle folder")
        exe = bundle / f"{out.stem}.exe"
        self.assertEqual(result.exe_path, exe)
        self.assertTrue(exe.is_file(), "the executable must be inside the bundle folder")
        siblings = [p for p in bundle.rglob("*") if p.is_file() and p != exe]
        self.assertTrue(
            any(p.suffix.lower() in (".dll", ".pyd") for p in siblings),
            "the bundle folder must contain the runtime next to the .exe",
        )
        self.assertIn("Folder mode", " ".join(result.notes))


if __name__ == "__main__":
    unittest.main()
