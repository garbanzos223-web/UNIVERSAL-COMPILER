"""The fusion engine: workspace, module graph, bridges, orchestrator, packaging.

Everything here runs against fixture projects created in temporary
directories. Toolchains that are not installed on the test host (CMake,
Cargo, Unreal Engine, ...) are asserted only for *command construction* and
*honest failure reporting* - never as proof of a real toolchain build.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
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

from ece_autocompiler.fusion import FusionWorkspace, WorkspaceError  # noqa: E402
from ece_autocompiler.fusion.bridges import plan_all  # noqa: E402
from ece_autocompiler.fusion.compatibility import binary_architecture  # noqa: E402
from ece_autocompiler.fusion.incremental import CacheState  # noqa: E402
from ece_autocompiler.fusion.modules import build_graph  # noqa: E402
from ece_autocompiler.fusion.orchestrator import FusionOrchestrator  # noqa: E402
from ece_autocompiler.fusion.unreal import plan_unreal_module  # noqa: E402
from ece_autocompiler.fusion.workspace import sha256_file  # noqa: E402


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="ece-fusion-test-"))
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)

    # -- fixture builders ------------------------------------------------

    def make_ws(self, name: str = "app") -> FusionWorkspace:
        ws = FusionWorkspace(self.base / f"{name}-ws")
        ws.create(name=name)
        return ws


# ---------------------------------------------------------------------------
# workspace
# ---------------------------------------------------------------------------


class WorkspaceTests(TempCase):
    def test_structure_is_preserved_and_never_flattened(self) -> None:
        src = self.base / "proj" / "src" / "deep"
        (src / "nested").mkdir(parents=True)
        (src / "nested" / "main.py").write_text("print(1)\n", encoding="utf-8")
        (self.base / "proj" / "README.md").write_text("readme\n", encoding="utf-8")
        ws = self.make_ws("flat")
        inp = ws.add(self.base / "proj")
        copied = ws.input_location(inp)
        self.assertTrue((copied / "src" / "deep" / "nested" / "main.py").exists())
        self.assertTrue((copied / "README.md").exists())
        self.assertTrue(inp.detected_type)  # the input records its real type

    def test_individual_files_and_multiple_at_once(self) -> None:
        a = self.base / "a.py"; a.write_text("x = 1\n", encoding="utf-8")
        b = self.base / "b.py"; b.write_text("y = 2\n", encoding="utf-8")
        ws = self.make_ws("files")
        for path in (a, b):
            ws.add(path)
        self.assertEqual(len(ws.inputs), 2)
        self.assertTrue((ws.root / "inputs" / "a" / "a.py").exists())

    def test_relpath_collisions_are_detected_identical_and_conflicting(self) -> None:
        src = self.base / "dup"
        src.mkdir()
        (src / "same.txt").write_text("abc\n", encoding="utf-8")
        other = self.base / "dup2"
        other.mkdir()
        (other / "same.txt").write_text("abc\n", encoding="utf-8")
        ws = self.make_ws("dup")
        inp1 = ws.add(src)
        inp2 = ws.add(other)
        self.assertEqual(sha256_file(src / "same.txt"), sha256_file(other / "same.txt"))
        conflicts = ws.conflicts()
        self.assertTrue(
            any(c["level"] == "warning" and "'same.txt'" in c["message"] for c in conflicts),
            conflicts,
        )
        # Now conflicting content at the same relative path must be flagged.
        (other / "same.txt").write_text("changed\n", encoding="utf-8")
        ws.inputs.remove(inp2)
        inp2 = ws.add(other)
        conflicts = ws.conflicts()
        self.assertTrue(
            any(c["level"] == "conflict" and "'same.txt'" in c["message"] for c in conflicts),
            conflicts,
        )

    def test_colliding_input_gets_its_own_folder_and_is_reported(self) -> None:
        """Nothing is ever silently overwritten: a colliding input goes to a
        suffixed folder ('seeded-2') and the path collision is reported."""
        ws = self.make_ws("overwrite")
        src = self.base / "seeded"
        src.mkdir()
        (src / "same.txt").write_text("original\n", encoding="utf-8")
        ws.add(src)
        (src / "same.txt").write_text("changed\n", encoding="utf-8")
        inp = ws.add(src)
        self.assertEqual(inp.workspace_path, "inputs/seeded-2")
        self.assertNotEqual(
            sha256_file(ws.root / "inputs" / "seeded" / "same.txt"),
            sha256_file(ws.root / "inputs" / "seeded-2" / "same.txt"),
        )
        # The collision is explicit: same relative path, different content.
        conflicts = ws.conflicts()
        self.assertTrue(
            any(c["level"] == "conflict" and "'same.txt'" in c["message"] for c in conflicts),
            conflicts,
        )

    def test_renamed_copy_of_the_same_file_is_reported(self) -> None:
        # Different rel paths, identical content → informational duplicate note.
        a = self.base / "one"
        a.mkdir()
        (a / "lib.py").write_text("x = 1\n", encoding="utf-8")
        b = self.base / "two"
        b.mkdir()
        (b / "copy.py").write_text("x = 1\n", encoding="utf-8")
        ws = self.make_ws("samecontent")
        ws.add(a); ws.add(b)
        conflicts = ws.conflicts()
        self.assertTrue(any(c["level"] == "info" for c in conflicts), conflicts)

    def test_replace_updates_input_and_keeps_id(self) -> None:
        src = self.base / "r1"; src.mkdir()
        (src / "main.py").write_text("print(1)\n", encoding="utf-8")
        ws = self.make_ws("replace")
        inp = ws.add(src)
        new = self.base / "r2"; new.mkdir()
        (new / "main.py").write_text("print(2)\n", encoding="utf-8")
        (new / "extra.py").write_text("print(3)\n", encoding="utf-8")
        replaced = ws.replace(inp.input_id, new)
        self.assertEqual(replaced.file_count, 2)
        self.assertEqual(replaced.input_id, inp.input_id)

    def test_remove_deletes_copied_files(self) -> None:
        src = self.base / "gone"
        src.mkdir()
        (src / "main.py").write_text("print(1)\n", encoding="utf-8")
        ws = self.make_ws("remove")
        inp = ws.add(src)
        workspace_path = ws.root / inp.workspace_path
        ws.remove(inp.input_id)
        self.assertFalse(workspace_path.exists())
        self.assertEqual([i for i in ws.inputs if i.input_id == inp.input_id], [])

    def test_linked_input_is_referenced_in_place(self) -> None:
        src = self.base / "linked"
        src.mkdir()
        (src / "lib.py").write_text("v = 5\n", encoding="utf-8")
        ws = self.make_ws("link")
        inp = ws.add(src, copy=False)
        self.assertTrue(inp.linked)
        self.assertEqual(inp.workspace_path, str(src))

    def test_workspace_inside_own_inputs_is_rejected(self) -> None:
        ws = self.make_ws("selfish")
        with self.assertRaises(WorkspaceError):
            ws.add(ws.root)

    def test_cannot_load_a_non_workspace_directory(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        with self.assertRaises(WorkspaceError):
            FusionWorkspace.load(plain)

    def test_missing_input_reported(self) -> None:
        src = self.base / "vanish"
        src.mkdir()
        (src / "x.py").write_text("v = 1\n", encoding="utf-8")
        ws = self.make_ws("vanish")
        inp = ws.add(src)
        copied = ws.root / inp.workspace_path
        shutil.rmtree(copied)
        ws2 = FusionWorkspace.load(ws.root)
        problems = ws2.verify()
        self.assertTrue(problems and inp.input_id in problems[0])


# ---------------------------------------------------------------------------
# module graph
# ---------------------------------------------------------------------------


class GraphTests(TempCase):
    def test_python_import_creates_dependency_and_order(self) -> None:
        app = self.base / "app_pkg"
        app.mkdir()
        (app / "main.py").write_text(
            "import support\n\nif __name__ == '__main__':\n    print(support.v)\n",
            encoding="utf-8",
        )
        lib = self.base / "support"
        lib.mkdir()
        (lib / "__init__.py").write_text("v = 9\n", encoding="utf-8")
        ws = self.make_ws("py")
        ws.add(app); ws.add(lib)
        graph = build_graph(ws)
        self.assertEqual(graph.entry_point, "app_pkg")
        self.assertEqual(graph.module("app_pkg").dependencies, ["support"])
        reason = graph.module("app_pkg").dependency_reasons["support"]
        self.assertIn("python import 'support'", reason)
        self.assertEqual(graph.order, ["support", "app_pkg"])

    def test_stdlib_imports_never_become_build_dependencies(self) -> None:
        lib = self.base / "stdlibmod"
        lib.mkdir()
        (lib / "__init__.py").write_text("import os, json\n", encoding="utf-8")
        ws = self.make_ws("stdlib")
        ws.add(lib)
        graph = build_graph(ws)
        # A manifest-less folder may be organised as a python module by source
        # hints, but stdlib imports must not create dependencies.
        for module in graph.modules:
            self.assertEqual(module.dependencies, [])

    def test_duplicate_module_names_get_unique_ids(self) -> None:
        for folder in ("sol", "sol2"):
            d = self.base / folder
            d.mkdir()
            (d / "pyproject.toml").write_text(
                '[project]\nname = "sol"\n', encoding="utf-8"
            )
        ws = self.make_ws("dupe")
        ws.add(self.base / "sol")
        ws.add(self.base / "sol2")
        graph = build_graph(ws)
        ids = {m.module_id for m in graph.modules}
        self.assertEqual(len(ids), 2)

    def test_cargo_path_dependency_is_detected(self) -> None:
        libA = self.base / "crate-a"
        libA.mkdir()
        (libA / "Cargo.toml").write_text(
            '[package]\nname = "crate-a"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        libB = self.base / "crate-b"
        libB.mkdir()
        (libB / "Cargo.toml").write_text(
            '[package]\nname = "crate-b"\nversion = "0.1.0"\nedition = "2021"\n'
            '\n[dependencies]\ncrate-a = { path = "../crate-a" }\n',
            encoding="utf-8",
        )
        (libB / "src").mkdir()
        (libB / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        ws = self.make_ws("rust")
        ws.add(libA); ws.add(libB)
        graph = build_graph(ws)
        consumer = graph.module("crate-b")
        self.assertIn("crate-a", consumer.dependencies)
        self.assertIn("cargo path dependency", consumer.dependency_reasons["crate-a"])
        self.assertEqual(graph.order, ["crate-a", "crate-b"])

    def test_dependency_cycle_is_detected_and_explained(self) -> None:
        # Folder names match the imports so module ids are 'a' and 'b'.
        a = self.base / "a"
        a.mkdir()
        (a / "__init__.py").write_text("import b\nimport os\n", encoding="utf-8")
        b = self.base / "b"
        b.mkdir()
        (b / "__init__.py").write_text("import a\n", encoding="utf-8")
        ws = self.make_ws("cycle")
        ws.add(a); ws.add(b)
        graph = build_graph(ws)
        self.assertEqual(len(graph.cycles), 1, graph.cycles)
        self.assertIn("Break the cycle", graph.conflicts[0])
        self.assertEqual(sorted(graph.order), ["a", "b"])

    def test_multiple_entry_points_reported_not_silently_chosen(self) -> None:
        app = self.base / "one"
        app.mkdir()
        (app / "main.py").write_text("if __name__ == '__main__':\n    print('one')\n", encoding="utf-8")
        other = self.base / "two"
        other.mkdir()
        (other / "main.py").write_text("if __name__ == '__main__':\n    print('two')\n", encoding="utf-8")
        ws = self.make_ws("twoentries")
        ws.add(app); ws.add(other)
        graph = build_graph(ws)
        self.assertEqual(graph.entry_point, "")
        self.assertTrue(any("Multiple entry-point candidates" in c for c in graph.conflicts))
        ws.config.entry_point = "one"
        graph = build_graph(ws)
        self.assertEqual(graph.entry_point, "one")

    def test_node_file_dependency(self) -> None:
        pkg = self.base / "webapp"
        pkg.mkdir()
        (pkg / "package.json").write_text(
            '{"name": "webapp", "dependencies": {"core": "file:../core"}}',
            encoding="utf-8",
        )
        core = self.base / "core"
        core.mkdir()
        (core / "package.json").write_text('{"name": "core"}', encoding="utf-8")
        ws = self.make_ws("node")
        ws.add(pkg); ws.add(core)
        graph = build_graph(ws)
        self.assertIn("core", graph.module("webapp").dependencies)

    def test_dotnet_project_reference(self) -> None:
        host = self.base / "shell"
        host.mkdir()
        (host / "Shell.csproj").write_text(
            '<Project><ItemGroup>'
            '<ProjectReference Include="..\\core\\Core.csproj" />'
            "</ItemGroup></Project>",
            encoding="utf-8",
        )
        core = self.base / "core"
        core.mkdir()
        (core / "Core.csproj").write_text("<Project></Project>", encoding="utf-8")
        ws = self.make_ws("net")
        ws.add(host); ws.add(core)
        graph = build_graph(ws)
        self.assertIn("core", graph.module("shell").dependencies)

    def test_asset_directory_becomes_assets_module(self) -> None:
        music = self.base / "audio"
        music.mkdir()
        (music / "ambient.wav").write_bytes(b"RIFF")
        (music / "loop.wav").write_bytes(b"RIFF")
        ws = self.make_ws("assets")
        inp = ws.add(music)
        graph = build_graph(ws)
        assets = [m for m in graph.modules if m.language == "assets"]
        self.assertEqual(len(assets), 1)
        module = assets[0]
        self.assertEqual(inp.kind, "asset")
        self.assertIn("audio", " ".join(module.notes))

    def test_unreal_project_declares_engine_runtime_and_modules(self) -> None:
        game = self.base / "uegame"
        game.mkdir()
        (game / "CoolGame.uproject").write_text(
            '{"EngineAssociation": "5.3", "Modules": [{"Name": "CoolGame", "Type": "Runtime"}],'
            ' "Plugins": [{"Name": "Water", "Enabled": true}]}',
            encoding="utf-8",
        )
        ws = self.make_ws("unreal")
        inp = ws.add(game)
        self.assertEqual(inp.detected_type, "unreal")
        graph = build_graph(ws)
        module = graph.modules[0]
        self.assertEqual(module.language, "unreal")
        self.assertIn("unreal-engine:5.3", module.required_runtime)
        self.assertIn("unreal-plugin:Water", module.required_runtime)


# ---------------------------------------------------------------------------
# bridges
# ---------------------------------------------------------------------------


class BridgeTests(TempCase):
    def test_cpp_shared_library_consumable_by_python(self) -> None:
        py = self.base / "pyapp"
        py.mkdir()
        (py / "main.py").write_text("import nativelib\n", encoding="utf-8")
        cpp = self.base / "nativelib"
        cpp.mkdir()
        (cpp / "CMakeLists.txt").write_text(
            "project(nativelib)\nadd_library(nativelib SHARED a.c)\n", encoding="utf-8"
        )
        ws = self.make_ws("bridge")
        ws.add(py); ws.add(cpp)
        graph = build_graph(ws)
        bridges = plan_all(graph, ws)
        plan = bridges.get(("pyapp", "nativelib"))
        self.assertIsNotNone(plan)
        self.assertEqual(plan.method, "shared-library")
        self.assertTrue(plan.feasible)

    def test_missing_interface_explained_not_fabricated(self) -> None:
        py = self.base / "pyapp2"
        py.mkdir()
        (py / "main.py").write_text("import somepkg\n", encoding="utf-8")
        other = self.base / "unrelatedcpp"
        other.mkdir()
        (other / "code.cpp").write_text("int f(){return 0;}\n", encoding="utf-8")
        (other / "makefile").write_text("all:\n\tgcc code.cpp\n", encoding="utf-8")
        ws = self.make_ws("nobridge")
        ws.add(py); ws.add(other)
        graph = build_graph(ws)
        bridges = plan_all(graph, ws)
        # No dependency pair exists, so no bridge is fabricated.
        self.assertEqual(list(bridges.keys()), [])
        # The import is recorded as a runtime requirement, not a fake dep.
        if graph.modules:
            py_mod = graph.module("pyapp2") or graph.modules[0]
            self.assertIn("python-package:somepkg", py_mod.required_runtime)


# ---------------------------------------------------------------------------
# compatibility
# ---------------------------------------------------------------------------


class CompatibilityTests(TempCase):
    def _pe(self, machine: int) -> bytes:
        dos = b"MZ" + b"\x00" * 0x3A + struct.pack("<I", 0x40)
        pe = b"PE\x00\x00" + struct.pack("<H", machine) + b"\x00\x00"
        return dos + pe

    def test_pe_header_architecture_detection(self) -> None:
        p = self.base / "x64.exe"
        p.write_bytes(self._pe(0x8664))
        self.assertEqual(binary_architecture(p), "x64")
        q = self.base / "x86.dll"
        q.write_bytes(self._pe(0x014C))
        self.assertEqual(binary_architecture(q), "x86")

    def test_python_extension_arch_mismatch_is_reported(self) -> None:
        from ece_autocompiler.fusion.compatibility import CompatibilityReport, check_python_extension_arch

        p = self.base / "win32ext.pyd"
        p.write_bytes(self._pe(0x014C))  # 32-bit vs the 64-bit test interpreter
        report = CompatibilityReport()
        check_python_extension_arch(p, report)
        self.assertTrue(any(c["level"] == "error" for c in report.checks))


# ---------------------------------------------------------------------------
# orchestrator (real builds only: the Python toolchain exists on the host).
# ---------------------------------------------------------------------------


class OrchestratorTests(TempCase):
    def _two_module_workspace(self) -> FusionWorkspace:
        lib = self.base / "support"
        lib.mkdir()
        (lib / "__init__.py").write_text("v = 21\n", encoding="utf-8")
        (lib / "pyproject.toml").write_text('[project]\nname = "support"\n', encoding="utf-8")
        app = self.base / "runner"
        app.mkdir()
        (app / "main.py").write_text(
            "import support\n\n"
            "if __name__ == '__main__':\n    print('answer', support.v)\n",
            encoding="utf-8",
        )
        ws = self.make_ws("orbit")
        ws.add(lib); ws.add(app)
        ws.config.entry_point = "runner"
        return ws

    def test_end_to_end_python_fusion_builds_ece_package(self) -> None:
        ws = self._two_module_workspace()
        result = FusionOrchestrator(ws).run(package_format="ece")
        self.assertTrue(result.ok, result.messages)
        package = Path(result.package_path)
        self.assertTrue(package.exists())
        self.assertEqual(package.suffix, ".ece")
        # container must carry the fusion graph in its manifest
        from ece_autocompiler.container import ContainerReader

        reader = ContainerReader(package)
        reader.open()
        try:
            manifest = reader.manifest
            self.assertIn("fusion", manifest.payload_type)
            self.assertIn("fusion_modules", manifest.unknown_fields)
            self.assertIn("fusion_graph", manifest.unknown_fields)
            self.assertEqual([e["module"] for e in manifest.entry_points], ["runner"])
            self.assertFalse(reader.verify_all())
        finally:
            reader.close()

    def test_assembled_application_actually_runs(self) -> None:
        ws = self._two_module_workspace()
        result = FusionOrchestrator(ws).run(package_format="folder")
        self.assertTrue(result.ok, result.messages)
        launcher = Path(result.assembly_dir) / "run_application.py"
        self.assertTrue(launcher.exists())
        proc = subprocess.run(
            [sys.executable, str(launcher)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        self.assertIn("answer 21", proc.stdout, proc.stdout)
        self.assertEqual(proc.returncode, 0)

    def test_missing_toolchain_fails_honestly_without_fake_success(self) -> None:
        lib = self.base / "native"
        lib.mkdir()
        (lib / "CMakeLists.txt").write_text(
            "project(nat)\nadd_library(nat SHARED a.c)\n", encoding="utf-8"
        )
        ws = self.make_ws("nocmake")
        ws.add(lib)
        result = FusionOrchestrator(ws).run(package_format="folder")
        task = next(t for t in result.report["tasks"] if t["module_id"] == "native")
        self.assertEqual(task["status"], "failed")
        self.assertIn("Toolchain missing", task["error"])
        self.assertIn("NOT built", task["error"])
        self.assertFalse(result.ok)

    def test_incremental_cache_hits_and_invalidates(self) -> None:
        ws = self._two_module_workspace()
        orchestrator = FusionOrchestrator(ws)
        first = orchestrator.run(package_format="folder")
        self.assertTrue(first.ok)
        graph_ids = {m.module_id: m for m in first.graph.modules}
        self.assertIn("support", graph_ids)
        # Second run: unchanged inputs must be served from the cache.
        second = FusionOrchestrator(ws).run(package_format="folder")
        cached_tasks = [t for t in second.report["tasks"] if t.get("cached")]
        self.assertTrue(cached_tasks, "second identical build should hit the cache")
        # Touch a source file: its module must rebuild, its dependents too.
        version_file = None
        for inp in ws.inputs:
            loc = ws.input_location(inp)
            if (loc / "__init__.py").exists():
                version_file = loc / "__init__.py"
        version_file.write_text("v = 22\n", encoding="utf-8")
        third = FusionOrchestrator(ws).run(package_format="folder")
        third_by_id = {t["module_id"]: t for t in third.report["tasks"]}
        self.assertFalse(third_by_id["support"].get("cached"))
        self.assertFalse(third_by_id["runner"].get("cached"))

    def test_module_tests_run_and_are_recorded(self) -> None:
        ws = self._two_module_workspace()
        ws.config.test_commands = {"runner": f"{sys.executable} -c \"print('t-ok')\""}
        orchestrator = FusionOrchestrator(ws)
        result = orchestrator.run(package_format="folder")
        self.assertTrue(result.ok)
        recorded = result.report["module_tests"]
        self.assertIn("runner", recorded)
        self.assertTrue(recorded["runner"]["ok"])
        self.assertIn("t-ok", recorded["runner"]["summary"])

    def test_asset_modules_are_staged_when_no_unreal(self) -> None:
        music = self.base / "sfx"
        music.mkdir()
        (music / "boom.wav").write_bytes(b"RIFF")
        app = self.base / "tiny"
        app.mkdir()
        (app / "main.py").write_text("if __name__ == '__main__':\n    print('ok')\n", encoding="utf-8")
        ws = self.make_ws("withassets")
        ws.add(app); ws.add(music)
        ws.config.entry_point = "tiny"
        result = FusionOrchestrator(ws).run(package_format="folder")
        self.assertTrue(result.ok, result.messages)
        staged = Path(result.assembly_dir) / "assets" / "sfx" / "boom.wav"
        self.assertTrue(staged.exists(), result.report["assembly"])

    def test_report_records_the_full_pipeline(self) -> None:
        ws = self._two_module_workspace()
        result = FusionOrchestrator(ws).run(package_format="zip")
        report = result.report
        for key in ("tasks", "graph", "bridges", "compatibility", "assembly", "assets", "duration_seconds"):
            self.assertIn(key, report)
        zip_path = Path(result.package_path)
        self.assertTrue(zip_path.exists())

    def test_cancelled_build_reports_cancellation(self) -> None:
        import threading

        ws = self._two_module_workspace()
        cancel = threading.Event()
        cancel.set()
        result = FusionOrchestrator(ws, cancel=cancel).run(package_format="folder")
        self.assertIn("Build cancelled by user.", " ".join(result.messages))


# ---------------------------------------------------------------------------
# Unreal Engine honest reporting and command construction
# ---------------------------------------------------------------------------


class UnrealTests(TempCase):
    def test_engine_missing_reported_honestly(self) -> None:
        game = self.base / "proje5x"
        game.mkdir()
        (game / "MyGame.uproject").write_text('{"EngineAssociation": "5.3"}', encoding="utf-8")
        from ece_autocompiler.fusion.models import Module as FusionModule

        plan = plan_unreal_module(
            FusionModule(module_id="g", language="unreal"), game, package_unreal=True
        )
        self.assertFalse(plan.ready)
        self.assertTrue(any("not installed" in m for m in plan.missing))

    def test_commands_use_unreal_buildtool_and_runuat(self) -> None:
        engine = self.base / "UE_5.4"
        (engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool").mkdir(parents=True)
        (engine / "Engine" / "Build" / "BatchFiles").mkdir(parents=True)
        (engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe").write_bytes(b"MZ")
        (engine / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat").write_text("@echo off\n")
        game = self.base / "gproj5x"
        game.mkdir()
        (game / "MyGame.uproject").write_text('{"EngineAssociation": "5.4"}', encoding="utf-8")
        from ece_autocompiler.fusion.models import Module as FusionModule

        plan = plan_unreal_module(
            FusionModule(module_id="g", language="unreal"), game,
            package_unreal=True, engine_root_hint=engine,
        )
        self.assertTrue(plan.ready, plan.missing)
        self.assertTrue(plan.build_cmd[0].endswith("UnrealBuildTool.exe"))
        self.assertIn("MyGameWin64", plan.build_cmd[1])
        self.assertIn("BuildCookRun", plan.package_cmd)
        self.assertIn("-cook", plan.package_cmd)

    def test_asset_scripts_marked_build_time_only(self) -> None:
        engine = self.base / "UE_5.4b"
        (engine / "Engine" / "Binaries" / "Win64").mkdir(parents=True)
        (engine / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe").write_bytes(b"MZ")
        (engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool").mkdir(parents=True)
        (engine / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe").write_bytes(b"MZ")
        game = self.base / "ascr"
        game.mkdir()
        (game / "MyGame.uproject").write_text('{"EngineAssociation": "5.4"}', encoding="utf-8")
        (game / "Scripts").mkdir()
        (game / "Scripts" / "process.py").write_text("# asset processing\n", encoding="utf-8")
        from ece_autocompiler.fusion.models import Module as FusionModule

        plan = plan_unreal_module(
            FusionModule(module_id="g", language="unreal"), game, engine_root_hint=engine,
        )
        self.assertTrue(plan.asset_script_cmd)
        self.assertIn("UnrealEditor-Cmd.exe", plan.asset_script_cmd[0])


# ---------------------------------------------------------------------------
# incremental cache unit behaviour
# ---------------------------------------------------------------------------


class CacheStateTests(TempCase):
    def test_cache_invalidates_when_output_file_disappears(self) -> None:
        build = self.base / "built.dll"
        build.write_bytes(b"output")
        state = CacheState.load(self.base)
        state.record("m", key="k1", outputs=[str(build)])
        from ece_autocompiler.fusion.incremental import find_cache_hit

        self.assertTrue(find_cache_hit("m", state, "k1"))
        build.unlink()
        self.assertFalse(find_cache_hit("m", state, "k1"))

    def test_cache_survives_reload(self) -> None:
        a = self.base / "a.py"
        a.write_text("x = 1\n", encoding="utf-8")
        state = CacheState.load(self.base)
        state.record("m", key="k", outputs=[str(a)])
        state.save()
        reloaded = CacheState.load(self.base)
        from ece_autocompiler.fusion.incremental import find_cache_hit

        self.assertTrue(find_cache_hit("m", reloaded, "k"))


class CliSurfaceTests(TempCase):
    """The workspace/fuse CLI commands, driven through cli_main (real argv)."""

    def test_workspace_create_add_list_config_and_fuse_plan(self) -> None:
        from ece_autocompiler.cli import main as cli_main

        lib = self.base / "supportlib"
        lib.mkdir()
        (lib / "__init__.py").write_text("v = 3\n", encoding="utf-8")
        (lib / "pyproject.toml").write_text('[project]\nname = "supportlib"\n', encoding="utf-8")
        app = self.base / "frontapp"
        app.mkdir()
        (app / "main.py").write_text(
            "import supportlib\nif __name__ == '__main__':\n    print('v', supportlib.v)\n",
            encoding="utf-8",
        )
        ws_dir = self.base / "cli-ws"

        self.assertEqual(cli_main(["workspace", "create", str(ws_dir), "--name", "clisuite"]), 0)
        self.assertEqual(cli_main(["workspace", "add", str(ws_dir), str(lib), str(app)]), 0)
        self.assertEqual(
            cli_main(["workspace", "config", str(ws_dir), "--entry-point", "frontapp"]), 0
        )
        # list over a reloaded workspace shows both inputs
        self.assertEqual(cli_main(["workspace", "list", str(ws_dir)]), 0)
        loaded = FusionWorkspace.load(ws_dir)
        self.assertEqual(loaded.config.entry_point, "frontapp")
        self.assertEqual(len(loaded.inputs), 2)
        # plan must surface the dependency and not crash
        self.assertEqual(cli_main(["fuse", "plan", str(ws_dir)]), 0)
        # unknown subcommand is an argparse usage error (SystemExit)
        with self.assertRaises(SystemExit):
            cli_main(["workspace", "frobnicate", str(ws_dir)])

    def test_fuse_validate_reports_toolchain_situation(self) -> None:
        from ece_autocompiler.cli import main as cli_main

        lib = self.base / "nativelib"
        lib.mkdir()
        (lib / "CMakeLists.txt").write_text("project(nl)\nadd_library(nl SHARED a.c)\n", encoding="utf-8")
        ws_dir = self.base / "val-ws"
        self.assertEqual(cli_main(["workspace", "create", str(ws_dir)]), 0)
        cli_main(["workspace", "add", str(ws_dir), str(lib)])
        self.assertEqual(cli_main(["fuse", "validate", str(ws_dir)]), 0)


# ---------------------------------------------------------------------------
# ece package hardening from the fusion side
# ---------------------------------------------------------------------------


class LaunchTests(TempCase):
    def test_launch_extracts_and_runs_python_entry(self) -> None:
        """A fused Python application can be launched from its .ece package.

        This runs a real extraction plus a real launcher; 'fuse launch' is the
        create-inspect-validate-extract-launch story of §8 end to end.
        """
        from ece_autocompiler.cli import main as cli_main

        ws = self._orbit_ws()
        result = FusionOrchestrator(ws).run(package_format="ece")
        self.assertTrue(result.ok, result.messages)
        package = result.package_path
        into = self.base / "launch-target"
        code = cli_main(["fuse", "launch", package, "--into", str(into)])
        self.assertEqual(code, 0)
        launcher = into / "run_application.py"
        self.assertTrue(launcher.exists())

    def _orbit_ws(self) -> FusionWorkspace:
        lib = self.base / "support"
        lib.mkdir()
        (lib / "__init__.py").write_text("v = 21\n", encoding="utf-8")
        (lib / "pyproject.toml").write_text('[project]\nname = "support"\n', encoding="utf-8")
        app = self.base / "runner"
        app.mkdir()
        (app / "main.py").write_text(
            "import support\n\nif __name__ == '__main__':\n    print('answer', support.v)\n",
            encoding="utf-8",
        )
        ws = self.make_ws("orbit-launch")
        ws.add(lib); ws.add(app)
        ws.config.entry_point = "runner"
        return ws


class ManifestIntegrityTests(TempCase):
    def test_fusion_manifest_survives_a_rewrite(self) -> None:
        from ece_autocompiler.container import ContainerWriter, Manifest, PayloadItem

        manifest = Manifest(project_name="m", adapter_id="fusion", payload_type="fusion-application (ece)")
        manifest.unknown_fields["fusion_modules"] = [{"module_id": "x"}]
        writer = ContainerWriter()
        dest = self.base / "m.ece"
        writer.write(dest, [PayloadItem(path="f.txt", data=b"data")], manifest)
        from ece_autocompiler.container import ContainerReader

        reader = ContainerReader(dest)
        reader.open()
        try:
            self.assertEqual(reader.manifest.unknown_fields.get("fusion_modules"), [{"module_id": "x"}])
        finally:
            reader.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
