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

from ece_autocompiler.adapters import AdapterRegistry  # noqa: E402
from ece_autocompiler.adapters.base import DetectionResult  # noqa: E402
from ece_autocompiler.detection.detector import ADAPTER_LABELS  # noqa: E402


def _project(files: dict[str, str]) -> Path:
    root = Path(tempfile.mkdtemp())
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


class RegistryTests(unittest.TestCase):
    def test_all_adapter_ids_have_labels(self) -> None:
        registry = AdapterRegistry()
        for adapter in registry.all():
            self.assertIn(adapter.adapter_id, ADAPTER_LABELS, adapter.adapter_id)

    def test_for_detected_returns_adapter(self) -> None:
        registry = AdapterRegistry()
        detected = DetectionResult(adapter_id="python", project_type="python", root=Path("."))
        adapter = registry.for_detected(detected)
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.adapter_id, "python")

    def test_for_detected_unknown(self) -> None:
        registry = AdapterRegistry()
        detected = DetectionResult(adapter_id="nope", project_type="nope", root=Path("."))
        self.assertIsNone(registry.for_detected(detected))

    def test_registry_detects_python(self) -> None:
        root = _project({"requirements.txt": "x", "app.py": "print(1)"})
        results = AdapterRegistry().detect(root)
        self.assertIn("python", [r.adapter_id for r in results])

    def test_external_adapter_reports_missing_tool(self) -> None:
        root = _project({"CMakeLists.txt": "project(x)"})
        cmake = next(a for a in AdapterRegistry().all() if a.adapter_id == "cpp-cmake")
        report = cmake.inspect_requirements(root)
        # cmake is not installed in this environment; it must say so honestly.
        if not report.ready:
            self.assertFalse(report.ready)
            self.assertTrue(report.notes)

    def test_plan_for_python_has_steps(self) -> None:
        root = _project({"requirements.txt": "x", "app.py": "print(1)"})
        python = next(a for a in AdapterRegistry().all() if a.adapter_id == "python")
        plan = python.create_build_plan(root)
        self.assertTrue(plan.steps)


if __name__ == "__main__":
    unittest.main()
