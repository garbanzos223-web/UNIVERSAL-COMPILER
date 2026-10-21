"""Unreal Engine integration: commands built from the engine's own tools.

ECE never compiles Unreal sources by bypassing the engine. Everything here
constructs the commands *UnrealBuildTool* and *RunUAT* would run, verifies
the engine and project actually exist on this host, and only then executes
them. When the engine is absent the plan is returned honestly with
``ready=False`` so callers report "not built" instead of pretending.

Asset import: raw assets (textures, FBX, ...) are NEVER treated as cooked
Unreal assets. The plan exposes the editor-Cmd import command for asset
processing scripts, but the project's own import pipeline must run; ECE
marks unreferenced raw assets as "not packaged".
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .compatibility import unreal_engine_installations
from .models import Module

logger = logging.getLogger(__name__)


@dataclass
class UnrealPlan:
    """What a real Unreal build for one module looks like on this machine."""

    module_id: str = ""
    project: Path | None = None
    engine_root: Path | None = None
    engine_version: str = ""
    target_name: str = ""
    platform: str = "Win64"
    configuration: str = "Development"
    #: UnrealBuildTool command: compiles the project's C++ modules.
    build_cmd: list[str] = field(default_factory=list)
    #: RunUAT BuildCookRun command: cooks, stages and packages the game.
    package_cmd: list[str] = field(default_factory=list)
    #: Editor-Cmd command for asset-processing Python (build time only).
    asset_script_cmd: list[str] = field(default_factory=list)
    ready: bool = False
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "project": str(self.project or ""),
            "engine_root": str(self.engine_root or ""),
            "engine_version": self.engine_version,
            "target_name": self.target_name,
            "platform": self.platform,
            "configuration": self.configuration,
            "build_cmd": list(self.build_cmd),
            "package_cmd": list(self.package_cmd),
            "asset_script_cmd": list(self.asset_script_cmd),
            "ready": self.ready,
            "missing": list(self.missing),
            "notes": list(self.notes),
        }


def find_uproject(module_root: Path) -> Path | None:
    if module_root.is_file() and module_root.suffix.lower() == ".uproject":
        return module_root
    for candidate in sorted(module_root.glob("*.uproject")):
        return candidate
    return None


def _engine_root_for(version: str, engine_root_hint: Path | None = None) -> Path | None:
    if engine_root_hint is not None and engine_root_hint.exists():
        return engine_root_hint
    wanted = (f"UE_{version}" if version else "").upper()
    generation = version.split(".")[0] if version else ""
    matches: list[Path] = []
    for install in unreal_engine_installations():
        name = install.name.upper()
        if wanted and name == wanted:
            return install
        if generation and name.startswith(f"UE_{generation}"):
            matches.append(install)
    return matches[0] if matches else None


def plan_unreal_module(
    module: Module,
    root: Path,
    *,
    configuration: str = "Development",
    package_unreal: bool = False,
    archive_dir: Path | None = None,
    engine_root_hint: Path | None = None,
) -> UnrealPlan:
    """Build the real Unreal build/packaging plan for one module."""
    plan = UnrealPlan(module_id=module.module_id, configuration=configuration)
    uproject = find_uproject(root)
    if uproject is None:
        plan.missing.append("no .uproject found in the module")
        return plan
    plan.project = uproject
    plan.target_name = uproject.stem

    try:
        data = json.loads(uproject.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        plan.missing.append(f".uproject is not valid JSON ({exc})")
        return plan
    version = str(data.get("EngineAssociation", ""))
    plan.engine_version = version
    engine_root = _engine_root_for(version, engine_root_hint)
    if engine_root is None:
        plan.missing.append(
            f"Unreal Engine {version or ('(version unknown)')} is not installed "
            "in the standard Epic Games locations"
        )
        return plan
    plan.engine_root = engine_root

    ubt = engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool" / "UnrealBuildTool.exe"
    if not ubt.exists():
        # 4.x layout keeps UnrealBuildTool directly under Binaries/DotNET.
        ubt = engine_root / "Engine" / "Binaries" / "DotNET" / "UnrealBuildTool.exe"
        if not ubt.exists():
            ubt = engine_root / "Engine" / "Build" / "BatchFiles" / "Build.bat"
    if not ubt.exists():
        plan.missing.append("UnrealBuildTool not found under the engine root")
        return plan
    plan.build_cmd = [
        str(ubt),
        f"{plan.target_name}Win64",
        configuration,
        f"-project={uproject}",
        "-platform=Win64",
    ]
    uat = engine_root / "Engine" / "Build" / "BatchFiles" / "RunUAT.bat"
    if package_unreal:
        if not uat.exists():
            plan.missing.append("RunUAT.bat not found; cannot construct packaging")
        else:
            archive = archive_dir or (root / "Packaged")
            plan.package_cmd = [
                str(uat),
                "BuildCookRun",
                f"-project={uproject}",
                "-platform=Win64",
                "-build",
                "-cook",
                "-stage",
                "-pak",
                "-archive",
                f"-archivedirectory={archive}",
            ]

    # Asset-processing scripts run in the editor at build time only:
    editor_cmd = engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"
    if editor_cmd.exists():
        for py_file in sorted(root.glob("Scripts/*.py")):
            plan.asset_script_cmd = [
                str(editor_cmd),
                f"{uproject}",
                "-run=pythonscript",
                f"-script={py_file}",
                "-unattended",
            ]
            break  # one representative command; scripts run in declared order
    plan.ready = True
    plan.notes.append(
        "editor-only Python scripts are never copied into the packaged game"
    )
    return plan


def execute_cmd(
    cmd: list[str],
    *,
    timeout_seconds: int = 60 * 60,
    cancel=None,
) -> tuple[bool, str]:
    """Run one engine command and capture its combined output honestly."""
    if not cmd:
        return False, "empty command"
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return False, f"could not start engine tool: {exc}"
    lines: list[str] = []
    try:
        for line in process.stdout:  # noqa: NOBuggyAttributeType; text mode pipes
            lines.append(line.rstrip())
            if cancel is not None and cancel.is_set():
                process.kill()
                return False, "cancelled by user"
        code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        return False, f"engine tool timed out after {timeout_seconds}s"
    output = "\n".join(lines[-120:])
    if code != 0:
        return False, f"engine tool exited with code {code}:\n{output[-2000:]}"
    return True, output[-2000:]
