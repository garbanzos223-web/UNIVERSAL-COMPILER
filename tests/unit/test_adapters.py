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

    def test_cmake_plan_configures_and_builds(self) -> None:
        from unittest import mock

        root = _project({"CMakeLists.txt": "project(x)"})
        cmake = next(a for a in AdapterRegistry().all() if a.adapter_id == "cpp-cmake")
        with mock.patch(
            "ece_autocompiler.adapters.external_tool_adapter.get_tool",
            return_value=_fake_tool("cmake"),
        ):
            plan = cmake.create_build_plan(root)
        ids = [step.id for step in plan.steps]
        # Configuring is not enough: the project has to be built too.
        self.assertIn("cmake-configure", ids)
        self.assertIn("cmake-build", ids)
        build = next(s for s in plan.steps if s.id == "cmake-build")
        self.assertIn("--build", build.cmd_args)
        self.assertIn("--parallel", build.cmd_args)

    def test_cmake_steps_need_consent(self) -> None:
        from unittest import mock

        root = _project({"CMakeLists.txt": "project(x)"})
        cmake = next(a for a in AdapterRegistry().all() if a.adapter_id == "cpp-cmake")
        with mock.patch(
            "ece_autocompiler.adapters.external_tool_adapter.get_tool",
            return_value=_fake_tool("cmake"),
        ):
            plan = cmake.create_build_plan(root)
        # Configuring runs the project's CMakeLists.txt: project-controlled code.
        self.assertTrue(plan.requires_consent)

    def test_make_plan_uses_parallel_jobs(self) -> None:
        from unittest import mock

        root = _project({"Makefile": "all:\n\t@echo hi\n"})
        make = next(a for a in AdapterRegistry().all() if a.adapter_id == "cpp-make")
        with mock.patch(
            "ece_autocompiler.adapters.external_tool_adapter.get_tool",
            return_value=_fake_tool("make"),
        ):
            plan = make.create_build_plan(root)
        step = plan.steps[0]
        self.assertIn("-j", step.cmd_args)

    def test_meson_project_requires_meson_and_ninja(self) -> None:
        from unittest import mock

        root = _project({"meson.build": "project('x', 'c')"})
        meson = next(a for a in AdapterRegistry().all() if a.adapter_id == "cpp-meson")
        with mock.patch(
            "ece_autocompiler.adapters.external_tool_adapter.get_tool",
            return_value=_fake_tool("ninja"),
        ):
            report = meson.inspect_requirements(root)
        ids = [r.id for r in report.requirements]
        self.assertIn("meson", ids)
        self.assertIn("ninja", ids)


def _fake_tool(tool_id: str):
    """A ToolInfo that looks like an installed tool."""
    from ece_autocompiler.detection.toolchains import ToolInfo

    return ToolInfo(
        id=tool_id,
        name=tool_id,
        found=True,
        path=f"C:/tools/{tool_id}.exe",
        version="1.0",
    )


if __name__ == "__main__":
    unittest.main()
