"""Tests for the 1.2 usability layer, templates, doctor, diff and integrity.

These tests need no display and no third-party packages: they exercise the pure
helpers that the CLI and the window share, plus the new CLI commands.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler import cli, templates  # noqa: E402
from ece_autocompiler.container import (  # noqa: E402
    ContainerWriter,
    Manifest,
    PayloadItem,
    diff_containers,
    sha256_file,
    validate_container,
    verify_container,
    verify_file,
    write_sidecar,
)
from ece_autocompiler.diagnostics.doctor import run_checks  # noqa: E402
from ece_autocompiler.build import timing  # noqa: E402
from ece_autocompiler.toolchain_versions import parse_version, toolchain_versions  # noqa: E402
from ece_autocompiler.usability import history_query  # noqa: E402
from ece_autocompiler.usability.guidance import (  # noqa: E402
    GuidanceContext,
    next_step,
)
from ece_autocompiler.usability.health import check_project_health  # noqa: E402
from ece_autocompiler.usability.plan import format_command, preview_plan, quote_arg  # noqa: E402
from ece_autocompiler.usability.summary import (  # noqa: E402
    build_summary,
    human_duration,
    human_size,
)
from ece_autocompiler.usability.wizard import WIZARD_STEPS, mark_seen, should_show  # noqa: E402


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _write_container(directory: Path, name: str, files: dict[str, bytes]) -> Path:
    sources = []
    for rel, data in files.items():
        source = directory / (name + "-" + rel.replace("/", "_"))
        source.write_bytes(data)
        sources.append(PayloadItem(path=rel, source=source))
    target = directory / f"{name}.ece"
    manifest = Manifest(project_name=name, adapter_id="python", payload_type="python")
    ContainerWriter().write(target, sources, manifest, overwrite=True)
    return target


class PlanPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def test_quote_arg_handles_spaces_and_quotes(self) -> None:
        self.assertEqual(quote_arg("plain"), "plain")
        self.assertEqual(quote_arg("two words"), '"two words"')
        self.assertEqual(quote_arg(""), '""')

    def test_format_command_is_copyable(self) -> None:
        line = format_command(["cmake", "-S", "my project", "-B", "build"])
        self.assertEqual(line, 'cmake -S "my project" -B build')

    def test_python_plan_is_ready_and_shows_commands(self) -> None:
        project = self.tmp / "pyapp"
        project.mkdir()
        (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
        preview = preview_plan(project)
        self.assertTrue(preview.ready)
        self.assertEqual(preview.adapter_id, "python")
        self.assertTrue(preview.steps)
        self.assertTrue(preview.steps[0].command_line)
        data = preview.to_dict()
        self.assertEqual(data["mode"], "ece")
        self.assertIn("steps", data)

    def test_missing_toolchain_is_a_blocker_not_a_fake_success(self) -> None:
        project = self.tmp / "rustapp"
        project.mkdir()
        (project / "Cargo.toml").write_text(
            '[package]\nname = "rustapp"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        (project / "src").mkdir()
        (project / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        preview = preview_plan(project)
        if preview.adapter_id:
            # Without cargo the plan must say so instead of pretending.
            self.assertFalse(preview.ready)
            self.assertTrue(preview.blockers)

    def test_exe_plan_uses_pyinstaller_commands(self) -> None:
        project = self.tmp / "exeapp"
        project.mkdir()
        (project / "main.py").write_text("print('hi')\n", encoding="utf-8")
        preview = preview_plan(project, mode="exe")
        self.assertEqual(preview.mode, "exe")
        joined = "\n".join(step.command_line for step in preview.steps)
        self.assertIn("pyinstaller", joined.lower())


class GuidanceTests(unittest.TestCase):
    def test_next_step_walks_the_first_run(self) -> None:
        first = next_step(GuidanceContext())
        self.assertTrue(first.title)
        second = next_step(GuidanceContext(project_selected=True, project_exists=True))
        self.assertTrue(second.title)
        missing = next_step(
            GuidanceContext(
                project_selected=True, project_exists=True, detected=True,
                missing_toolchains=["dotnet"],
            )
        )
        self.assertIn("dotnet", " ".join(missing.commands).lower() + missing.title.lower()
                      + missing.detail.lower())

    def test_next_step_after_success_suggests_output(self) -> None:
        step = next_step(
            GuidanceContext(
                project_selected=True, project_exists=True, detected=True,
                build_attempted=True, state="success",
            )
        )
        self.assertTrue(step.title)


class SummaryTests(unittest.TestCase):
    def test_human_units(self) -> None:
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(999), "999 B")
        self.assertEqual(human_size(1500), "1.5 KB")
        self.assertEqual(human_duration(0.5), "500 ms")
        self.assertEqual(human_duration(61), "1 min 1 s")

    def test_summary_is_copyable_and_states_what_did_not_happen(self) -> None:
        summary = build_summary(
            ok=True, project=Path("C:/tmp/app"), adapter_id="python", label="Python",
            output_path=Path("C:/tmp/app.ece"), output_size=2048, artifact_count=3,
            duration_seconds=2.5, sha256="a" * 64, verified="ok", step_count=2,
        )
        text = summary.text()
        self.assertIn("Build summary (OK)", text)
        self.assertIn("2.0 KB", text)
        self.assertIn("Installed nothing", text)
        self.assertIn("Packaged 3 file(s)", text)
        self.assertEqual(summary.as_dict()["sha256"], "a" * 64)

    def test_summary_redacts_secrets(self) -> None:
        summary = build_summary(ok=False, warnings=["token=supersecret failed"])
        self.assertNotIn("supersecret", summary.text())

    def test_summary_reports_the_estimate_and_the_real_time(self) -> None:
        summary = build_summary(
            ok=True, duration_seconds=12.0, estimated_seconds=10.0,
            estimate_note="Actual time 12.0s - 20% slower than estimated.",
        )
        text = summary.text()
        self.assertIn("Estimate: 10.0 s", text)
        self.assertIn("Actual time", text)
        data = summary.as_dict()
        self.assertEqual(data["estimated_seconds"], 10.0)
        self.assertEqual(data["estimate_s"], 10.0)
        self.assertEqual(data["elapsed_s"], 12.0)
        self.assertIsNone(data["eta_s"])


class TimingTests(unittest.TestCase):
    def test_no_history_is_stated_as_such(self) -> None:
        base = Path(tempfile.mkdtemp())
        estimate = timing.estimate_build(base, "python", "ece")
        self.assertFalse(estimate.available)
        self.assertIn("No successful timing history yet", estimate.message)

    def test_successful_samples_produce_a_range_and_a_note(self) -> None:
        base = Path(tempfile.mkdtemp())
        for seconds in (10, 12, 14):
            timing.record_sample(
                base, adapter_id="python", mode="ece", duration_seconds=seconds, ok=True
            )
        estimate = timing.estimate_build(base, "python", "ece")
        self.assertTrue(estimate.available)
        self.assertEqual(estimate.samples, 3)
        self.assertAlmostEqual(estimate.average or 0, 12.0)
        self.assertFalse(estimate.rough)  # exactly the minimum sample count
        self.assertIn("Estimated:", estimate.message)
        note = timing.compare_to_estimate(estimate, 18.0)
        self.assertIn("slower", note)

    def test_failed_samples_are_excluded_and_counted(self) -> None:
        base = Path(tempfile.mkdtemp())
        timing.record_sample(base, adapter_id="node", mode="ece", duration_seconds=5, ok=False)
        timing.record_sample(base, adapter_id="node", mode="ece", duration_seconds=20, ok=True)
        estimate = timing.estimate_build(base, "node", "ece")
        self.assertTrue(estimate.available)
        self.assertEqual(estimate.samples, 1)
        self.assertEqual(estimate.excluded, 1)
        self.assertTrue(estimate.rough)
        self.assertIn("excluded", estimate.message)

    def test_modes_and_adapters_do_not_mix(self) -> None:
        base = Path(tempfile.mkdtemp())
        timing.record_sample(base, adapter_id="python", mode="ece", duration_seconds=10, ok=True)
        self.assertFalse(timing.estimate_build(base, "python", "exe").available)
        self.assertFalse(timing.estimate_build(base, "node", "ece").available)

    def test_ticker_never_invents_an_eta_without_a_fraction(self) -> None:
        now = [0.0]
        ticker = timing.Ticker(clock=lambda: now[0])
        self.assertIsNone(ticker.eta(None))
        self.assertIsNone(ticker.eta(0))
        now[0] = 10.0
        self.assertIsNone(ticker.eta(0))
        # half the work in 10 s: about 10 s left.
        self.assertAlmostEqual(ticker.eta(0.5) or 0, 10.0)
        self.assertIn("left", ticker.summary(0.5))
        # No honest fraction at the very end: elapsed only, never a fake 0.
        self.assertIsNone(ticker.eta(1.0))
        self.assertIn("elapsed", ticker.summary(1.0))


class WizardTests(unittest.TestCase):
    def test_first_run_then_seen(self) -> None:
        self.assertTrue(should_show({}))
        self.assertFalse(should_show(mark_seen({})))
        self.assertTrue(WIZARD_STEPS)


class HealthTests(unittest.TestCase):
    def test_clean_project_has_no_blocking_issues(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        (tmp / "main.py").write_text("print('hi')\n", encoding="utf-8")
        report = check_project_health(tmp)
        self.assertTrue(report.ok)
        self.assertTrue(report.summary)

    def test_versions_parse_and_compare(self) -> None:
        self.assertEqual(parse_version("Python 3.12.0"), (3, 12, 0))
        rows = {row.tool_id: row for row in toolchain_versions(["python"])}
        self.assertIn("python", rows)
        self.assertTrue(rows["python"].found)
        self.assertTrue(rows["python"].satisfies)


class DoctorTests(unittest.TestCase):
    def test_report_has_a_shape_and_copyable_lines(self) -> None:
        report = run_checks(tool_ids=["python"])
        keys = {check.key for check in report.checks}
        self.assertIn("platform", keys)
        self.assertIn("python", keys)
        self.assertIn("tool:python", keys)
        data = report.as_dict()
        self.assertIn("checks", data)
        self.assertTrue(report.text())
        for check in report.checks:
            self.assertIn(check.status, ("ok", "warn", "error"))

    def test_doctor_json_cli(self) -> None:
        with mock.patch.dict(os.environ, {"ECE_AUTOCOMPILER_HOME": tempfile.mkdtemp()}):
            code, out, _err = run_cli("doctor", "--json", "--tools", "python")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("ok", data)
        self.assertIn("checks", data)


class HistoryQueryTests(unittest.TestCase):
    RECORDS = [
        {"timestamp": "2026-01-01T00:00:00Z", "project_path": "C:/a", "adapter_id": "python",
         "mode": "ece", "ok": True, "message": "Build and package succeeded"},
        {"timestamp": "2026-01-02T00:00:00Z", "project_path": "C:/b", "adapter_id": "node",
         "mode": "exe", "ok": False, "message": "Build failed"},
    ]

    def test_filter_by_text_result_and_adapter(self) -> None:
        self.assertEqual(len(history_query.filter_records(self.RECORDS)), 2)
        self.assertEqual(len(history_query.filter_records(self.RECORDS, query="node")), 1)
        self.assertEqual(len(history_query.filter_records(self.RECORDS, ok=False)), 1)
        self.assertEqual(len(history_query.filter_records(self.RECORDS, adapter_id="python")), 1)

    def test_exports_json_and_csv(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        json_path = history_query.export_json(self.RECORDS, tmp / "history.json")
        csv_path = history_query.export_csv(self.RECORDS, tmp / "history.csv")
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(len(loaded), 2)
        text = csv_path.read_text(encoding="utf-8")
        self.assertIn("timestamp", text)
        self.assertIn("C:/b", text)

    def test_replay_command_matches_the_mode(self) -> None:
        self.assertIn("build", history_query.replay_command(self.RECORDS[0]))
        self.assertIn("exe", history_query.replay_command(self.RECORDS[1]))

    def test_summary_counts_failures_honestly(self) -> None:
        stats = history_query.summarize(self.RECORDS)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["ok"], 1)
        self.assertEqual(stats["failed"], 1)


class ContainerDiffTests(unittest.TestCase):
    def test_identical_containers(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = _write_container(tmp, "old", {"a.txt": b"same"})
        result = diff_containers(old, old)
        self.assertTrue(result.identical)
        self.assertEqual(result.changes, [])

    def test_added_removed_and_modified_files(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = _write_container(tmp, "old", {"a.txt": b"one", "b.txt": b"keep"})
        new = _write_container(tmp, "new", {"a.txt": b"two", "c.txt": b"new"})
        result = diff_containers(old, new)
        self.assertEqual([c.path for c in result.added], ["c.txt"])
        self.assertEqual([c.path for c in result.removed], ["b.txt"])
        self.assertEqual([c.path for c in result.modified], ["a.txt"])
        self.assertIn("added", result.summary)
        self.assertTrue(result.as_dict()["added"])

    def test_diff_cli(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        old = _write_container(tmp, "old", {"a.txt": b"one"})
        new = _write_container(tmp, "new", {"a.txt": b"two"})
        code, out, _err = run_cli("diff", str(old), str(new), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(len(data["modified"]), 1)


class IntegrityTests(unittest.TestCase):
    def test_sidecar_round_trip_and_mismatch(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        target = tmp / "app.bin"
        target.write_bytes(b"hello")
        digest = sha256_file(target)
        write_sidecar(target, digest=digest)
        self.assertEqual((tmp / "app.bin.sha256").read_text(encoding="ascii").split()[0], digest)
        self.assertTrue(verify_file(target).ok)
        target.write_bytes(b"changed")
        self.assertFalse(verify_file(target).ok)

    def test_container_verification(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        container = _write_container(tmp, "app", {"main.py": b"print(1)\n"})
        write_sidecar(container)
        check = verify_container(container, deep=True)
        self.assertTrue(check.ok, check.message)
        self.assertTrue(validate_container(container, deep=True).ok)


class TemplateTests(unittest.TestCase):
    def test_creates_every_template(self) -> None:
        for template in templates.list_templates():
            with self.subTest(template=template.key):
                destination = Path(tempfile.mkdtemp()) / template.key
                written = templates.create_project(template.key, destination, name="demo")
                self.assertTrue(written)
                self.assertTrue((destination / template.manifest()).is_file()
                                or any(destination.iterdir()))

    def test_unknown_template_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            templates.create_project("no-such-template", Path(tempfile.mkdtemp()) / "x")

    def test_non_empty_destination_needs_force(self) -> None:
        destination = Path(tempfile.mkdtemp())
        (destination / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(Exception):
            templates.create_project("python-script", destination)
        templates.create_project("python-script", destination, force=True)
        self.assertTrue((destination / "main.py").is_file())


class NewCliCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp())
        self.env = mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": str(self.home), "ECE_AUTOCOMPILER_HOME": str(self.home / "app")},
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.project = self.home / "demo"
        self.project.mkdir()
        (self.project / "main.py").write_text("print('hi')\n", encoding="utf-8")

    def test_plan_json(self) -> None:
        code, out, _err = run_cli("plan", str(self.project), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["ready"])
        self.assertTrue(data["steps"])

    def test_build_plan_flag_is_a_dry_run(self) -> None:
        code, out, _err = run_cli("build", str(self.project), "--plan")
        self.assertEqual(code, 0)
        self.assertIn("Build plan for", out)
        # No output file was produced (the config's empty folder may exist).
        outputs = list((self.home / "ECE-Universal-AutoCompiler" / "output").glob("*.ece"))
        self.assertEqual(outputs, [])

    def test_build_records_timing_and_uses_it_next_time(self) -> None:
        from ece_autocompiler.adapters.base import BuildResult
        from ece_autocompiler.build.orchestrator import PackageResult

        output = self.home / "out.ece"
        output.write_bytes(b"fake container")

        def fake_build(duration):
            def side_effect(*_args, **kwargs):
                holder = kwargs.get("selected_adapter")
                if holder is not None:
                    holder[:] = ["python"]
                return (
                    BuildResult(ok=True, exit_code=0, duration_seconds=duration),
                    PackageResult(
                        ok=True, container_path=output, sha256="b" * 64,
                        file_count=4, size=1024,
                    ),
                )

            return side_effect

        with mock.patch(
            "ece_autocompiler.build.BuildOrchestrator.build_and_package",
            side_effect=fake_build(2.5),
        ):
            code, out, _err = run_cli(
                "build", str(self.project), "--json", "--no-integrity",
                "--overwrite", "-o", str(output),
            )
        self.assertEqual(code, 0)
        first = json.loads(out)
        self.assertTrue(first["ok"])
        self.assertIsNone(first["estimated_seconds"])

        timings = self.home / "ECE-Universal-AutoCompiler" / "work" / "timings.jsonl"
        self.assertTrue(timings.is_file())
        self.assertIn('"adapter_id":"python"', timings.read_text(encoding="utf-8"))

        with mock.patch(
            "ece_autocompiler.build.BuildOrchestrator.build_and_package",
            side_effect=fake_build(3.0),
        ):
            code, out, _err = run_cli(
                "build", str(self.project), "--json", "--no-integrity",
                "--overwrite", "-o", str(output),
            )
        self.assertEqual(code, 0)
        second = json.loads(out)
        self.assertEqual(second["estimated_seconds"], 2.5)
        self.assertIn("slower", second["estimate_note"])

        # Package-only runs are recorded under their own mode so they never
        # drag down estimates for real builds.
        with mock.patch(
            "ece_autocompiler.build.BuildOrchestrator.build_and_package",
            side_effect=fake_build(1.0),
        ):
            code, _out, _err = run_cli(
                "build", str(self.project), "--json", "--no-integrity",
                "--overwrite", "--package-only", "-o", str(output),
            )
        self.assertEqual(code, 0)
        last = json.loads(timings.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["mode"], "package")

    def test_init_and_list(self) -> None:
        code, out, _err = run_cli("init", "--list")
        self.assertEqual(code, 0)
        self.assertIn("python-script", out)
        destination = self.home / "newproject"
        code, out, _err = run_cli(
            "init", str(destination), "--template", "python-script", "--name", "demo"
        )
        self.assertEqual(code, 0)
        self.assertTrue((destination / "main.py").is_file())

    def test_history_json_and_clear_requires_yes(self) -> None:
        code, out, _err = run_cli("history", "--json")
        self.assertEqual(code, 0)
        self.assertIn("records", json.loads(out))
        code, out, _err = run_cli("history", "--clear")
        self.assertEqual(code, 1)
        self.assertIn("--yes", out)

    def test_analyze_json_has_health_and_next_step(self) -> None:
        code, out, _err = run_cli("analyze", str(self.project), "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("health", data)
        self.assertIn("next_step", data)
        self.assertIn("install", data)

    def test_validate_integrity(self) -> None:
        container = _write_container(self.home, "sample", {"main.py": b"print(1)\n"})
        code, out, _err = run_cli("validate", str(container), "--integrity", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertEqual(data["integrity"]["status"], "ok")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
