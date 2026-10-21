"""Cross-module repair for the fusion engine (§11).

When one module fails, the existing repair engine analyses *its real build
output*, proposes minimal repairs, keeps them only when they help, rolls
back everything else (the engine's own backup/restore mechanism) and reports
the verified result. ECE never invents an API to fill an interface gap - a
missing interface is reported, not "repaired".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.registry import AdapterRegistry
from ..detection import scan
from ..repair.engine import BuildOutcome, RepairEngine, RepairOutcome
from ..config import AppConfig
from .models import Module
from .workspace import FusionWorkspace

logger = logging.getLogger(__name__)


@dataclass
class ModuleRepairResult:
    module_id: str
    outcome: RepairOutcome
    repaired: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "repaired": self.repaired,
            "outcome": self.outcome.as_dict(),
        }


def repair_module(
    module: Module,
    root: Path,
    workspace: FusionWorkspace,
    *,
    max_attempts: int = 3,
    progress=None,
) -> ModuleRepairResult:
    """Run the existing repair engine against one failed module build."""
    registry = AdapterRegistry()
    detection = registry.detect(root, scan(root))
    adapter = None
    for d in detection:
        if d.adapter_id == module.adapter_id:
            adapter = registry.for_detected(d)
            break
    if adapter is None:
        outcome = RepairOutcome()
        outcome.message = f"No adapter plans builds for module {module.module_id}; repair cannot run."
        return ModuleRepairResult(module_id=module.module_id, outcome=outcome, repaired=False)

    try:
        plan = adapter.create_build_plan(root)
    except Exception as exc:
        outcome = RepairOutcome()
        outcome.message = f"Build plan failed: {exc}"
        return ModuleRepairResult(module_id=module.module_id, outcome=outcome, repaired=False)

    from ..build.process import run_command

    def build_runner() -> BuildOutcome:
        result = run_command(
            steps=plan.steps,
            workdir=root,
            consent_granted=True,
        )
        lines: list[str] = []
        for step in result.step_results:
            lines.extend(step.output_lines)
        return BuildOutcome(ok=result.ok, output="\n".join(lines))

    engine = RepairEngine(AppConfig(), work_dir=workspace.generated_dir / "repair")
    outcome = engine.repair(
        root,
        build_runner,
        adapter_id=module.adapter_id,
        max_attempts=max_attempts,
        tests_command=None,
        progress=progress,
    )
    repaired = bool(outcome.final_ok)
    return ModuleRepairResult(module_id=module.module_id, outcome=outcome, repaired=repaired)
