"""The optimizer: profiles, plan changes, measurement and honest reporting.

These tests check that the flags really reach the build plan, that conflicts
are reported, and that no performance claim is made without a measurement.
"""

from __future__ import annotations

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

from ece_autocompiler.adapters.base import BuildPlan, BuildPlanStep  # noqa: E402
from ece_autocompiler.optimize.measure import (  # noqa: E402
    Measurement,
    compare,
    record_optimization,
)
from ece_autocompiler.optimize.plan import (  # noqa: E402
    OptimizationRequest,
    apply_optimization,
    build_optimization_plan,
)
from ece_autocompiler.optimize.profiles import (  # noqa: E402
    PROFILES,
    describe_profiles,
    profile_keys,
)
from ece_autocompiler.optimize.recommend import recommendations  # noqa: E402


def _plan(steps: list[BuildPlanStep]) -> BuildPlan:
    return BuildPlan(id="test", adapter_id="test", workdir=Path("."), steps=steps)


class ProfileTests(unittest.TestCase):
    def test_every_documented_profile_exists(self) -> None:
        for key in ("debug", "release", "performance", "size", "balanced", "custom"):
            self.assertIn(key, PROFILES)
        self.assertIn("none", profile_keys())

    def test_profiles_are_described(self) -> None:
        described = describe_profiles()
        self.assertTrue(described)
        for entry in described:
            self.assertIn("key", entry)
            self.assertIn("description", entry)

    def test_release_profile_sets_optimization_flags(self) -> None:
        plan = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("release"))
        self.assertTrue(plan.active)
        self.assertEqual(plan.build_type, "Release")
        self.assertIn("-O2", plan.flags)
        self.assertIn("-DNDEBUG", plan.flags)

    def test_debug_profile_keeps_debug_information(self) -> None:
        plan = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("debug"))
        self.assertEqual(plan.build_type, "Debug")
        self.assertIn("-g", plan.flags)

    def test_size_profile_shrinks_the_binary(self) -> None:
        plan = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("size"))
        self.assertEqual(plan.build_type, "MinSizeRel")
        self.assertIn("-Os", plan.flags)

    def test_performance_profile_needs_approval(self) -> None:
        plan = build_optimization_plan("rust-cargo", OptimizationRequest.from_args("performance"))
        self.assertTrue(plan.requires_approval)
        self.assertTrue(plan.notes)

    def test_interpreter_projects_cannot_be_optimized(self) -> None:
        for adapter in ("python", "node"):
            plan = build_optimization_plan(adapter, OptimizationRequest.from_args("release"))
            self.assertFalse(plan.active)
            self.assertIn("interpreter", plan.unsupported_reason)

    def test_unknown_profile_is_reported(self) -> None:
        plan = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("turbo"))
        self.assertIn("not a known profile", plan.unsupported_reason)

    def test_custom_profile_needs_flags(self) -> None:
        plan = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("custom"))
        self.assertIn("--custom-flags", plan.unsupported_reason)

    def test_custom_profile_uses_the_given_flags(self) -> None:
        plan = build_optimization_plan(
            "cpp-cmake", OptimizationRequest.from_args("custom", ["-O1", "-Wall"])
        )
        self.assertTrue(plan.active)
        self.assertEqual(plan.flags, ["-O1", "-Wall"])

    def test_conflicting_flags_are_reported(self) -> None:
        plan = build_optimization_plan(
            "cpp-cmake", OptimizationRequest.from_args("custom", ["-O0", "-O3"])
        )
        self.assertTrue(any("optimization level" in w for w in plan.warnings))

    def test_native_cpu_flag_is_warned_about(self) -> None:
        plan = build_optimization_plan(
            "cpp-cmake", OptimizationRequest.from_args("custom", ["-march=native"])
        )
        self.assertTrue(any("-march=native" in w for w in plan.warnings))


class ApplyOptimizationTests(unittest.TestCase):
    def test_cmake_configure_gets_the_build_type(self) -> None:
        plan = _plan([BuildPlanStep(id="cmake-configure", title="cmake", cmd_args=["cmake", "-S", ".", "-B", "build"])])
        optimization = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("release"))
        apply_optimization(plan, optimization)
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", plan.steps[0].cmd_args)
        self.assertTrue(any(a.startswith("-DCMAKE_CXX_FLAGS=") for a in plan.steps[0].cmd_args))

    def test_cmake_build_step_gets_the_configuration(self) -> None:
        plan = _plan([BuildPlanStep(id="cmake-build", title="cmake", cmd_args=["cmake", "--build", "build"])])
        optimization = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("release"))
        apply_optimization(plan, optimization)
        self.assertIn("--config", plan.steps[0].cmd_args)
        self.assertIn("Release", plan.steps[0].cmd_args)

    def test_cargo_release_profile_adds_the_flag_and_the_environment(self) -> None:
        plan = _plan([BuildPlanStep(id="cargo-build", title="cargo", cmd_args=["cargo", "build"])])
        optimization = build_optimization_plan("rust-cargo", OptimizationRequest.from_args("performance"))
        apply_optimization(plan, optimization)
        self.assertIn("--release", plan.steps[0].cmd_args)
        self.assertEqual(plan.steps[0].env.get("CARGO_PROFILE_RELEASE_LTO"), "fat")

    def test_cargo_debug_profile_removes_the_release_flag(self) -> None:
        plan = _plan([BuildPlanStep(id="cargo-build", title="cargo", cmd_args=["cargo", "build", "--release"])])
        optimization = build_optimization_plan("rust-cargo", OptimizationRequest.from_args("debug"))
        apply_optimization(plan, optimization)
        self.assertNotIn("--release", plan.steps[0].cmd_args)

    def test_dotnet_configuration_is_replaced(self) -> None:
        plan = _plan([BuildPlanStep(id="dotnet", title="dotnet", cmd_args=["dotnet", "publish", "-c", "Release"])])
        optimization = build_optimization_plan("dotnet", OptimizationRequest.from_args("debug"))
        apply_optimization(plan, optimization)
        self.assertIn("Debug", plan.steps[0].cmd_args)

    def test_go_size_profile_strips_the_binary(self) -> None:
        plan = _plan([BuildPlanStep(id="go", title="go", cmd_args=["go", "build", "./..."])])
        optimization = build_optimization_plan("go", OptimizationRequest.from_args("size"))
        apply_optimization(plan, optimization)
        self.assertIn("-ldflags=-s -w", plan.steps[0].cmd_args)

    def test_other_toolchains_are_never_touched(self) -> None:
        plan = _plan([BuildPlanStep(id="x", title="x", cmd_args=["python", "-m", "compileall", "-q", "."])])
        optimization = build_optimization_plan("python", OptimizationRequest.from_args("release"))
        apply_optimization(plan, optimization)
        self.assertEqual(plan.steps[0].cmd_args, ["python", "-m", "compileall", "-q", "."])

    def test_no_profile_leaves_the_plan_alone(self) -> None:
        plan = _plan([BuildPlanStep(id="x", title="x", cmd_args=["cmake", "-S", ".", "-B", "build"])])
        optimization = build_optimization_plan("cpp-cmake", OptimizationRequest.from_args("none"))
        apply_optimization(plan, optimization)
        self.assertEqual(plan.steps[0].cmd_args, ["cmake", "-S", ".", "-B", "build"])


class MeasurementTests(unittest.TestCase):
    def test_percentage_change_is_computed(self) -> None:
        report = compare(
            Measurement(profile="default", compile_seconds=10.0, total_size=1000),
            Measurement(profile="release", compile_seconds=12.0, total_size=500),
        )
        self.assertEqual(report.time_change_pct, 20.0)
        self.assertEqual(report.size_change_pct, -50.0)

    def test_no_percentage_without_measurements(self) -> None:
        report = compare(
            Measurement(profile="default", compile_seconds=0.0, total_size=0),
            Measurement(profile="release", compile_seconds=5.0, total_size=10),
        )
        self.assertIsNone(report.time_change_pct)
        self.assertIsNone(report.size_change_pct)

    def test_runtime_is_only_claimed_with_a_benchmark(self) -> None:
        without = compare(
            Measurement(profile="default", compile_seconds=1.0, total_size=10),
            Measurement(profile="release", compile_seconds=1.0, total_size=10),
        )
        self.assertIsNone(without.runtime_change_pct)
        self.assertTrue(any("benchmark" in note for note in without.notes))

        with_benchmark = compare(
            Measurement(profile="default", compile_seconds=1.0, total_size=10, benchmark={"seconds": 2.0}),
            Measurement(profile="release", compile_seconds=1.0, total_size=10, benchmark={"seconds": 1.0}),
        )
        self.assertEqual(with_benchmark.runtime_change_pct, -50.0)

    def test_failing_tests_are_called_out(self) -> None:
        report = compare(
            Measurement(profile="default", compile_seconds=1.0, total_size=10, tests_ok=False),
            Measurement(profile="release", compile_seconds=1.0, total_size=10, tests_ok=True),
        )
        self.assertTrue(any("test" in note for note in report.notes))

    def test_report_is_written_to_the_optimization_log(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        report = compare(
            Measurement(profile="default", compile_seconds=1.0, total_size=10),
            Measurement(profile="release", compile_seconds=1.0, total_size=10),
        )
        record_optimization(tmp, report)
        log = tmp / "optimizations.jsonl"
        self.assertTrue(log.is_file())
        self.assertIn("baseline", log.read_text(encoding="utf-8"))


class RecommendationTests(unittest.TestCase):
    def test_interpreter_projects_get_a_bundling_hint(self) -> None:
        advice = recommendations("python")
        self.assertTrue(advice)
        self.assertTrue(any("native executable" in item.text for item in advice))

    def test_default_profile_suggests_a_release_build(self) -> None:
        advice = recommendations("cpp-cmake")
        self.assertTrue(any("Release" in item.text for item in advice))

    def test_no_benchmark_means_no_runtime_claim(self) -> None:
        report = compare(
            Measurement(profile="default", compile_seconds=1.0, total_size=10),
            Measurement(profile="release", compile_seconds=1.0, total_size=10),
        )
        advice = recommendations("cpp-cmake", None, report)
        self.assertTrue(any("benchmark" in item.text for item in advice))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
