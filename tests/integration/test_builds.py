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


if __name__ == "__main__":
    unittest.main()
