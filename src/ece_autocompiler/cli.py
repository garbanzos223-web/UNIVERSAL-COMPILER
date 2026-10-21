"""Command line interface for ECE Universal AutoCompiler.

Subcommands mirror the GUI workflow so the tool is usable head-less and in CI:

    analyze   inspect a project and report detection, toolchains and health
    plan      show the exact build plan without running anything
    build     run a real build and package the result into an .ece container
    exe       bundle a project into a standalone .exe
    package   create a distributable package (ZIP, TAR.GZ, installer, ...)
    optimize  compare a baseline build with an optimized build
    repair    analyse a build failure and repair what is safe to repair
    validate  validate an existing container (optionally deep hash check)
    inspect   print a container's manifest and file index
    extract   safely extract a container
    diff      compare two .ece containers
    tools     list detected toolchains and what is missing
    doctor    check that this machine is ready to build
    init      create a new project from a template
    history   list, filter, export or replay past builds
    gui       launch the desktop interface

Exit codes (stable contract):

    0  success
    1  operation failed (build, validation, missing path, ...)
    2  usage error (argparse)

All user-facing text is English; every failure is printed in the fixed
*what happened / why / what to do* shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .version import APP_NAME, APP_VERSION
from .config import AppConfig
from .detection import discover_all, scan
from .detection.detector import ADAPTER_LABELS
from .container import validate_container
from .container.reader import ContainerReader
from .extraction import Extractor
from .errors import EceError
from .logging_config import redact, setup_logging
from .toolchain_hints import hint_lines, hint_summary
from .toolchain_versions import toolchain_versions
from .usability.guidance import diagnose_failure, explain_extraction, explain_validation
from .usability.summary import human_size

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _print_json(payload) -> None:
    sys.stdout.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def _wants_json(args) -> bool:
    return bool(getattr(args, "json", False))


def _quiet(args) -> bool:
    return bool(getattr(args, "quiet", False))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ece-autocompiler",
        description=f"{APP_NAME} {APP_VERSION} - detect, build and package projects into .ece containers.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Analyze a project: detection, toolchains and health.")
    p_analyze.add_argument("path", help="Project directory.")
    p_analyze.add_argument("--json", action="store_true", help="Emit JSON.")

    p_plan = sub.add_parser("plan", help="Show the build plan without running anything.")
    p_plan.add_argument("path", help="Project directory.")
    p_plan.add_argument("--mode", choices=("ece", "exe"), default="ece", help="Target output (default: ece).")
    p_plan.add_argument("--adapter", help="Force a specific adapter (for monorepos).")
    p_plan.add_argument("--json", action="store_true", help="Emit JSON.")

    p_build = sub.add_parser("build", help="Build a project and package it into an .ece container.")
    p_build.add_argument("path", help="Project directory.")
    p_build.add_argument("-o", "--output", help="Output .ece file (default: <name>.ece in output dir).")
    p_build.add_argument(
        "--compression",
        choices=("zlib", "lzma", "store"),
        default="zlib",
        help="Payload compression (default: zlib).",
    )
    p_build.add_argument("--consent", action="store_true", help="Allow steps that run project scripts.")
    p_build.add_argument("--overwrite", action="store_true", help="Overwrite an existing output file.")
    p_build.add_argument("--skip-install", action="store_true", help="Skip dependency installation steps.")
    p_build.add_argument("--package-only", action="store_true", help="Skip the build; package what is on disk.")
    p_build.add_argument("--plan", action="store_true", help="Print the plan and exit (dry run).")
    p_build.add_argument("--adapter", help="Force a specific adapter (for monorepos).")
    p_build.add_argument("--no-integrity", action="store_true", help="Do not write a .sha256 sidecar.")
    p_build.add_argument("--no-input", action="store_true", help="Never prompt; fail instead of asking.")
    p_build.add_argument("--quiet", action="store_true", help="Suppress progress, keep the final summary.")
    p_build.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_build.add_argument("--json", action="store_true", help="Emit a JSON summary.")
    p_build.add_argument(
        "--profile",
        default="none",
        help="Optimization profile for this build (default: none, the project builds as it asks).",
    )
    p_build.add_argument(
        "--custom-flags",
        action="append",
        default=[],
        help="Extra compiler options for the 'custom' profile (repeatable).",
    )
    p_build.add_argument(
        "--repair",
        action="store_true",
        help="When the build fails, analyse it and repair what is safe to repair.",
    )
    p_build.add_argument(
        "--repair-attempts",
        type=int,
        default=3,
        help="Maximum number of automatic repair attempts (default: 3).",
    )

    p_validate = sub.add_parser("validate", help="Validate an .ece container.")
    p_validate.add_argument("file", help="Container file.")
    p_validate.add_argument("--deep", action="store_true", help="Also verify every entry hash.")
    p_validate.add_argument(
        "--verify-signature", action="store_true",
        help="Verify the manifest signature with its external tool (minisign/cosign/gpg).",
    )
    p_validate.add_argument(
        "--integrity", action="store_true",
        help="Also check the file against its .sha256 sidecar, if present.",
    )
    p_validate.add_argument("--explain", action="store_true", help="Add plain-language explanation lines.")
    p_validate.add_argument("--json", action="store_true", help="Emit JSON.")

    p_inspect = sub.add_parser("inspect", help="Print a container's manifest and file index.")
    p_inspect.add_argument("file", help="Container file.")
    p_inspect.add_argument("--explain", action="store_true", help="Add plain-language explanation lines.")
    p_inspect.add_argument("--json", action="store_true", help="Emit JSON.")

    p_extract = sub.add_parser("extract", help="Safely extract a container.")
    p_extract.add_argument("file", help="Container file.")
    p_extract.add_argument("dest", help="Destination directory.")
    p_extract.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    p_extract.add_argument(
        "--verify-signature", action="store_true",
        help="Verify the manifest signature first; an invalid signature aborts the extraction.",
    )
    p_extract.add_argument("--json", action="store_true", help="Emit JSON.")

    p_diff = sub.add_parser("diff", help="Compare two .ece containers.")
    p_diff.add_argument("old", help="Base container.")
    p_diff.add_argument("new", help="Container to compare against the base.")
    p_diff.add_argument("--json", action="store_true", help="Emit JSON.")

    p_tools = sub.add_parser("tools", help="List detected toolchains and what is missing.")
    p_tools.add_argument("--json", action="store_true", help="Emit JSON.")
    p_tools.add_argument(
        "--refresh",
        action="store_true",
        help="Probe every tool again instead of using the cached result.",
    )

    p_doctor = sub.add_parser("doctor", help="Check whether this machine is ready to build.")
    p_doctor.add_argument("--tools", help="Comma-separated tool ids to check (default: all).")
    p_doctor.add_argument("--json", action="store_true", help="Emit JSON.")
    p_doctor.add_argument(
        "--refresh",
        action="store_true",
        help="Probe every tool again instead of using the cached result.",
    )

    p_init = sub.add_parser("init", help="Create a new project from a template.")
    p_init.add_argument("path", nargs="?", help="Destination directory.")
    p_init.add_argument("--template", help="Template key (see --list).")
    p_init.add_argument("--name", help="Project name (default: destination folder name).")
    p_init.add_argument("--force", action="store_true", help="Write into a non-empty directory.")
    p_init.add_argument("--list", action="store_true", help="List available templates and exit.")

    p_history = sub.add_parser("history", help="List, filter, export or replay past builds.")
    p_history.add_argument("--search", help="Free-text filter over path, type, adapter and message.")
    p_history.add_argument("--ok", action="store_true", help="Only successful builds.")
    p_history.add_argument("--failed", action="store_true", help="Only failed builds.")
    p_history.add_argument("--adapter", help="Only builds using this adapter.")
    p_history.add_argument("--limit", type=int, default=20, help="Maximum rows to show (default 20).")
    p_history.add_argument("--export", help="Write the filtered records as JSON to this file.")
    p_history.add_argument("--export-csv", help="Write the filtered records as CSV to this file.")
    p_history.add_argument("--replay", action="store_true", help="Print the command to repeat each build.")
    p_history.add_argument("--clear", action="store_true", help="Delete the whole history.")
    p_history.add_argument("--yes", action="store_true", help="Confirm a destructive action.")
    p_history.add_argument("--json", action="store_true", help="Emit JSON.")

    p_exe = sub.add_parser(
        "exe", help="Detect a project's language and compile it into a standalone .exe."
    )
    p_exe.add_argument("path", help="Project directory or a single .py file.")
    p_exe.add_argument("-o", "--output", help="Output .exe path (default: <name>.exe in output dir).")
    p_exe.add_argument("--name", help="Executable name (default: project folder name).")
    p_exe.add_argument("--entry", help="Entry-point .py for Python projects (default: auto-detect).")
    p_exe.add_argument("--onedir", action="store_true", help="Folder mode instead of a single file (Python).")
    p_exe.add_argument("--console", action="store_true", help="Attach a console (default: windowed for Python).")
    p_exe.add_argument("--timeout", type=int, default=1800, help="Build timeout in seconds.")
    p_exe.add_argument(
        "--consent",
        action="store_true",
        help="Allow the steps that run commands defined by the project (a CMake "
             "configure, a Cargo build script, an MSBuild target).",
    )
    p_exe.add_argument("--plan", action="store_true", help="Print the plan and exit (dry run).")
    p_exe.add_argument("--no-integrity", action="store_true", help="Do not write a .sha256 sidecar.")
    p_exe.add_argument("--quiet", action="store_true", help="Suppress progress.")
    p_exe.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_exe.add_argument("--json", action="store_true", help="Emit a JSON summary.")

    p_package = sub.add_parser(
        "package", help="Create a distributable package from a project."
    )
    p_package.add_argument("path", help="Project directory.")
    p_package.add_argument(
        "--format",
        dest="fmt",
        default="zip",
        help="Package format: zip, tar.gz, folder, inno, nsis, deb, appimage.",
    )
    p_package.add_argument("--output-dir", help="Where the package is written.")
    p_package.add_argument("--name", help="Application name (default: project folder name).")
    p_package.add_argument("--version", default="1.0.0", help="Application version.")
    p_package.add_argument("--description", default="", help="One-line description.")
    p_package.add_argument("--publisher", default="", help="Publisher or vendor name.")
    p_package.add_argument(
        "--arch", default="x64", choices=("x64", "x86", "arm64"), help="Target architecture."
    )
    p_package.add_argument("--icon", help="Icon file to include in the package.")
    p_package.add_argument("--license", dest="license_file", help="License file to include.")
    p_package.add_argument("--readme", help="README file to include.")
    p_package.add_argument(
        "--include",
        action="append",
        default=[],
        help="Keep only the files that match this pattern (repeatable).",
    )
    p_package.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Leave out the files that match this pattern (repeatable).",
    )
    p_package.add_argument(
        "--build", action="store_true", help="Run the build before packaging the result."
    )
    p_package.add_argument(
        "--profile",
        default="none",
        help="Optimization profile for the build (default: none).",
    )
    p_package.add_argument(
        "--custom-flags",
        action="append",
        default=[],
        help="Extra compiler options for the 'custom' profile (repeatable).",
    )
    p_package.add_argument("--consent", action="store_true", help="Allow steps that run project scripts.")
    p_package.add_argument("--adapter", help="Force a specific adapter (for monorepos).")
    p_package.add_argument("--overwrite", action="store_true", help="Overwrite an existing package.")
    p_package.add_argument(
        "--no-reproducible",
        action="store_true",
        help="Write the real creation time instead of a pinned one (archives stop being "
             "byte-identical between runs).",
    )
    p_package.add_argument(
        "--allow-copyleft",
        action="store_true",
        help="Confirm that a copyleft license may be redistributed.",
    )
    p_package.add_argument("--quiet", action="store_true", help="Suppress progress lines.")
    p_package.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_package.add_argument(
        "--plan",
        action="store_true",
        help="Show what would be packaged (files, dependencies, licenses) and exit.",
    )
    p_package.add_argument("--json", action="store_true", help="Emit JSON.")

    p_optimize = sub.add_parser(
        "optimize", help="Compare a baseline build with an optimized build."
    )
    p_optimize.add_argument("path", help="Project directory.")
    p_optimize.add_argument(
        "--profile",
        default="release",
        help="Optimization profile: debug, release, performance, size, balanced, custom.",
    )
    p_optimize.add_argument(
        "--custom-flags",
        action="append",
        default=[],
        help="Compiler options for the 'custom' profile (repeatable).",
    )
    p_optimize.add_argument("--adapter", help="Force a specific adapter (for monorepos).")
    p_optimize.add_argument("--consent", action="store_true", help="Allow steps that run project scripts.")
    p_optimize.add_argument("--benchmark", help="Command that runs the project benchmark.")
    p_optimize.add_argument("--tests", help="Command that runs the project tests.")
    p_optimize.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_optimize.add_argument("--json", action="store_true", help="Emit JSON.")

    p_repair = sub.add_parser(
        "repair", help="Analyse a build failure and repair what is safe to repair."
    )
    p_repair.add_argument("path", help="Project directory.")
    p_repair.add_argument(
        "--apply",
        action="store_true",
        help="Apply the proposed fixes. Without this flag nothing is changed.",
    )
    p_repair.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum number of repair attempts (default: 3).",
    )
    p_repair.add_argument(
        "--timeout", type=int, default=300, help="Time limit for the whole repair run, in seconds."
    )
    p_repair.add_argument("--tests", help="Command that runs the project tests.")
    p_repair.add_argument("--adapter", help="Force a specific adapter (for monorepos).")
    p_repair.add_argument("--consent", action="store_true", help="Allow steps that run project scripts.")
    p_repair.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_repair.add_argument("--json", action="store_true", help="Emit JSON.")

    sub.add_parser("gui", help="Launch the desktop interface.")

    # -- fusion engine ---------------------------------------------------
    p_ws = sub.add_parser(
        "workspace", help="Create and manage a multi-input fusion workspace."
    )
    ws_sub = p_ws.add_subparsers(dest="workspace_command", required=True)
    p_ws_create = ws_sub.add_parser("create", help="Create a new fusion workspace.")
    p_ws_create.add_argument("directory", help="New workspace directory.")
    p_ws_create.add_argument("--name", help="Application name (default: folder name).")
    p_ws_add = ws_sub.add_parser("add", help="Import files or directories into the workspace.")
    p_ws_add.add_argument("workspace", help="Workspace directory.")
    p_ws_add.add_argument("paths", nargs="+", help="Files/directories to import.")
    p_ws_add.add_argument(
        "--kind",
        choices=("auto", "file", "directory", "project", "asset", "library"),
        default="auto",
        help="Input kind (default: auto-detected).",
    )
    p_ws_add.add_argument(
        "--link", action="store_true",
        help="Reference the input in place instead of copying it into the workspace.",
    )
    p_ws_add.add_argument("--module", help="Assign the input to a module id.")
    p_ws_remove = ws_sub.add_parser("remove", help="Remove an input from the workspace.")
    p_ws_remove.add_argument("workspace", help="Workspace directory.")
    p_ws_remove.add_argument("input_id", help="Input id (see 'workspace list').")
    p_ws_remove.add_argument(
        "--keep-files", action="store_true",
        help="Leave the imported copy on disk; just forget the input.",
    )
    p_ws_replace = ws_sub.add_parser("replace", help="Update an input from a new source.")
    p_ws_replace.add_argument("workspace", help="Workspace directory.")
    p_ws_replace.add_argument("input_id", help="Input id to replace.")
    p_ws_replace.add_argument("source", help="New source path.")
    p_ws_assign = ws_sub.add_parser("assign", help="Assign an input to a module id.")
    p_ws_assign.add_argument("workspace", help="Workspace directory.")
    p_ws_assign.add_argument("input_id", help="Input id.")
    p_ws_assign.add_argument("module_id", help="Module id to assign the input to.")
    p_ws_list = ws_sub.add_parser("list", help="List the workspace inventory.")
    p_ws_list.add_argument("workspace", help="Workspace directory.")
    p_ws_list.add_argument("--json", action="store_true", help="Emit JSON.")
    p_ws_config = ws_sub.add_parser("config", help="Show or change workspace configuration.")
    p_ws_config.add_argument("workspace", help="Workspace directory.")
    p_ws_config.add_argument("--show", action="store_true", help="Print the current configuration.")
    p_ws_config.add_argument("--entry-point", help="Set the application entry module.")
    p_ws_config.add_argument("--name", help="Set the application name.")
    p_ws_config.add_argument("--version", help="Set the application version.")
    p_ws_config.add_argument(
        "--package-format", choices=("ece", "zip", "folder"),
        help="Packaging format of the fused application.",
    )
    p_ws_config.add_argument(
        "--depend", action="append", default=[],
        help="Declare a module dependency 'consumer=provider' (repeatable).",
    )
    p_ws_config.add_argument(
        "--asset-dir", action="append", default=[],
        help="Declare a workspace-relative asset directory (repeatable).",
    )

    p_fuse = sub.add_parser("fuse", help="Fusion engine: plan, validate or build a workspace.")
    fuse_sub = p_fuse.add_subparsers(dest="fuse_command", required=True)
    p_fuse_plan = fuse_sub.add_parser("plan", help="Show modules, order, bridges and conflicts; build nothing.")
    p_fuse_plan.add_argument("workspace", help="Workspace directory.")
    p_fuse_plan.add_argument("--json", action="store_true", help="Emit JSON.")
    p_fuse_validate = fuse_sub.add_parser("validate", help="Compatibility report for the workspace.")
    p_fuse_validate.add_argument("workspace", help="Workspace directory.")
    p_fuse_validate.add_argument("--json", action="store_true", help="Emit JSON.")
    p_fuse_build = fuse_sub.add_parser("build", help="Assemble, build and package the application.")
    p_fuse_build.add_argument("workspace", help="Workspace directory.")
    p_fuse_build.add_argument(
        "--format", choices=("ece", "zip", "folder"),
        help="Package format (default: the configured one).",
    )
    p_fuse_build.add_argument("-o", "--output", help="Output file for ece/zip formats.")
    p_fuse_build.add_argument("--json", action="store_true", help="Emit JSON summary.")
    p_fuse_build.add_argument(
        "--no-cache", action="store_true", help="Rebuild every module even when its inputs are unchanged."
    )
    p_fuse_build.add_argument(
        "--repair-attempts", type=int, default=0,
        help="When a module build fails, try the repair engine this many times (default: 0 = off).",
    )

    return parser


# ---------------------------------------------------------------------------
# analyze / plan
# ---------------------------------------------------------------------------


def _missing_toolchains_for(candidates) -> list[str]:
    """Tool ids that are missing for the detected candidates' adapters."""
    from .toolchain_hints import ADAPTER_TO_TOOL
    from .detection import get_tool

    missing: list[str] = []
    for candidate in candidates:
        tool_id = ADAPTER_TO_TOOL.get(candidate.adapter_id, "")
        if not tool_id:
            continue
        info = get_tool(tool_id)
        if info is not None and not info.found and tool_id not in missing:
            missing.append(tool_id)
    return missing


def cmd_analyze(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR
    result = scan(root)

    from .build.exe_exporter import ExeReadiness, exe_readiness
    from .usability.health import check_project_health
    from .usability.guidance import GuidanceContext, next_step

    readiness: dict[str, ExeReadiness] = {}
    for c in result.candidates:
        readiness.setdefault(c.adapter_id, exe_readiness(c.adapter_id))
    missing = _missing_toolchains_for(result.candidates)
    health = check_project_health(root, candidate_root=(result.best.root if result.best else ""))
    step = next_step(
        GuidanceContext(
            project_selected=True,
            project_exists=True,
            detected=bool(result.candidates),
            candidates=len(result.candidates),
            ambiguous=result.ambiguous,
            missing_toolchains=missing,
            health_issues=len(health.issues),
        )
    )

    if args.json:
        _print_json(
            {
                "root": str(root),
                "total_files": result.total_files,
                "ambiguous": result.ambiguous,
                "truncated": result.truncated,
                "notes": result.notes,
                "candidates": [
                    {
                        "adapter_id": c.adapter_id,
                        "label": ADAPTER_LABELS.get(c.adapter_id, c.adapter_id),
                        "project_type": c.project_type,
                        "root": c.root,
                        "confidence": round(c.confidence, 3),
                        "evidence": c.evidence,
                        "notes": c.notes,
                        "exe_readiness": readiness[c.adapter_id].as_dict(),
                    }
                    for c in result.candidates
                ],
                "missing_toolchains": [
                    {"id": t, **({"install": hint_lines(t)} if True else {})} for t in missing
                ],
                "install": {t: hint_summary(t) for t in missing},
                "health": health.as_dict(),
                "next_step": {
                    "kind": step.kind,
                    "title": step.title,
                    "detail": step.detail,
                },
            }
        )
        return EXIT_OK

    _print(f"Project: {root}")
    _print(f"Files scanned: {result.total_files}")
    if not result.candidates:
        _print("No supported project type detected.")
        _print("Tip: point at the folder that contains the project files (package.json, pyproject.toml, *.py, ...).")
        return EXIT_OK
    _print("Candidates:")
    for c in result.candidates:
        label = ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)
        where = f" at {c.root}" if c.root else ""
        _print(f"  - {label}{where}  ({int(c.confidence * 100)}%)  {', '.join(c.evidence)}")
    if result.ambiguous:
        _print("Note: more than one project was detected; choose one with --adapter or in the GUI.")
    for note in result.notes:
        _print(f"Note: {note}")

    _print("")
    _print("Export readiness (.exe):")
    for c in result.candidates:
        label = ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)
        ready = exe_readiness(c.adapter_id)
        _print(f"  - {label}: {ready.summary}")
        for action in ready.actions:
            _print(f"      {action}")

    _print("")
    for line in health.lines():
        _print(line)

    if missing:
        _print("")
        _print("Missing toolchain(s) needed to build for real:")
        for tool_id in missing:
            for line in hint_lines(tool_id):
                _print(f"  {line}")

    _print("")
    _print(f"Next: {step.title}")
    _print(f"      {step.detail}")
    _print("Packaging as .ece always works:  ece-autocompiler build <folder>")
    return EXIT_OK


def cmd_plan(args) -> int:
    from .usability.plan import preview_plan, render_plan_text

    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR
    preview = preview_plan(root, prefer_adapter=args.adapter, mode=args.mode)
    if _wants_json(args):
        _print_json(preview.to_dict())
        return EXIT_OK if preview.ready else EXIT_ERROR
    _print(render_plan_text(preview))
    if not preview.ready:
        _print("")
        _print("This plan cannot run as-is; see the blockers above.")
        return EXIT_ERROR
    return EXIT_OK


# ---------------------------------------------------------------------------
# build / exe
# ---------------------------------------------------------------------------


def _progress_printer(args):
    if _quiet(args) or _wants_json(args) or getattr(args, "no_progress", False):
        return None

    from .build.timing import Ticker

    ticker = Ticker()

    def progress(message: str, done: int, total: int) -> None:
        if total:
            fraction = done / total if total else None
            _print(f"[{done:>3}/{total}] {message}  ({ticker.summary(fraction)})")

    return progress


def _write_sidecar_if_requested(path: Path, args) -> str:
    if getattr(args, "no_integrity", False):
        return ""
    from .container.integrity import write_sidecar

    try:
        write_sidecar(path)
    except OSError:
        return ""
    return str(path.with_name(path.name + ".sha256"))


def cmd_build(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    if args.plan:
        # Dry run delegates to `plan` so both share the exact same output.
        plan_args = argparse.Namespace(path=args.path, mode="ece", adapter=args.adapter, json=args.json)
        return cmd_plan(plan_args)

    cfg = AppConfig()
    cfg.ensure_directories()
    output = Path(args.output) if args.output else cfg.output_dir / f"{root.name}.ece"

    from .build import BuildOrchestrator
    from .build.history import append as history_append, make_record
    from .build.timing import compare_to_estimate, estimate_build, record_sample

    # Item 34: an estimate from this machine's own history, before anything runs.
    # Package-only runs are timed separately so they never skew real builds.
    timing_mode = "package" if args.package_only else "ece"
    estimate = None
    adapter_guess = args.adapter or ""
    try:
        detected = scan(root)
        if not adapter_guess and detected.best is not None:
            adapter_guess = detected.best.adapter_id
        if adapter_guess:
            estimate = estimate_build(cfg.work_dir, adapter_guess, timing_mode)
            if not (_quiet(args) or _wants_json(args)):
                _print(estimate.message)
    except EceError:
        estimate = None

    overrides: dict = {}
    if args.skip_install:
        overrides["skip_npm_install"] = True

    # Optimization profile: "none" keeps the previous behaviour exactly.
    optimization = None
    if getattr(args, "profile", "none") and getattr(args, "profile", "none") != "none":
        from .optimize.plan import OptimizationRequest, build_optimization_plan

        optimization = build_optimization_plan(
            adapter_guess,
            OptimizationRequest.from_args(
                args.profile, getattr(args, "custom_flags", []) or []
            ),
        )
        if not (_quiet(args) or _wants_json(args)):
            for line in optimization.text().splitlines():
                _print(line)
        if optimization.unsupported_reason:
            _print("")
            _print("What happened: this project cannot be optimized with that profile.")
            _print(f"Why: {optimization.unsupported_reason}")
            _print("What to do: pick another profile, or build without --profile.")
            return EXIT_ERROR

    if args.no_input and not args.consent:
        # Non-interactive: a consent-requiring build must be asked for up front.
        from .usability.plan import preview_plan

        preview = preview_plan(root, prefer_adapter=args.adapter, mode="ece")
        if preview.requires_consent:
            _print("error: this build runs project scripts and needs consent.")
            _print("Re-run with --consent to allow it (nothing was executed).")
            return EXIT_ERROR

    orchestrator = BuildOrchestrator(config=cfg)
    selected: list[str] = []
    started = _now_iso()

    def run_build():
        return orchestrator.build_and_package(
            root,
            output,
            config_overrides=overrides,
            consent_granted=args.consent,
            overwrite=args.overwrite,
            compression=args.compression,
            prefer_adapter=args.adapter,
            package_only=args.package_only,
            selected_adapter=selected,
            progress=_progress_printer(args),
            optimization=optimization,
        )

    try:
        result, package = run_build()
    except EceError as exc:
        report = diagnose_failure(str(exc), what=str(exc.message))
        for line in report.lines():
            _print(redact(line))
        record_sample(
            cfg.work_dir, adapter_id=args.adapter or "", mode=timing_mode,
            duration_seconds=0.0, ok=False,
        )
        history_append(
            make_record(root, ok=False, exit_code=EXIT_ERROR, message=str(exc), mode="ece"),
            base=cfg.work_dir,
        )
        return EXIT_ERROR

    adapter_id = selected[0] if selected else ""
    label = ADAPTER_LABELS.get(adapter_id, adapter_id)

    if not result.ok and getattr(args, "repair", False):
        # Automatic repair: analyse the failure, apply what is safe, rebuild.
        from .build.compile_service import make_build_runner
        from .repair import RepairEngine

        _print("")
        _print("The build failed. Analysing the error...")
        engine = RepairEngine(config=cfg)
        runner = make_build_runner(
            root,
            config=cfg,
            config_overrides=overrides,
            consent_granted=args.consent,
            prefer_adapter=args.adapter,
            optimization=optimization,
        )
        outcome = engine.repair(
            root,
            runner,
            max_attempts=getattr(args, "repair_attempts", 3),
            timeout=300.0,
            apply=True,
            progress=_message_printer(args),
        )
        for line in outcome.text().splitlines():
            _print(redact(line))
        if outcome.final_ok:
            _print("")
            _print("The repair worked. Building the package again...")
            try:
                result, package = run_build()
            except EceError as exc:
                report = diagnose_failure(str(exc), what=str(exc.message))
                for line in report.lines():
                    _print(redact(line))
                return EXIT_ERROR

    if not result.ok:
        combined = "\n".join(result.errors) or "the build tool failed"
        report = diagnose_failure(combined, what="the build failed")
        for line in report.lines():
            _print(redact(line))
        if result.log_path:
            _print(f"log: {result.log_path}")
        record_sample(
            cfg.work_dir, adapter_id=adapter_id, mode=timing_mode,
            duration_seconds=result.duration_seconds, ok=False,
        )
        history_append(
            make_record(
                root, ok=False, exit_code=result.exit_code, duration_seconds=result.duration_seconds,
                adapter_id=adapter_id, mode="ece", message="Build failed",
                diagnostics=[d for d in result.errors],
            ),
            base=cfg.work_dir,
        )
        return EXIT_ERROR

    if not package.ok:
        _print(f"What happened: packaging failed ({package.message})")
        _print("What to do: check free disk space and that the output path is writable.")
        record_sample(
            cfg.work_dir, adapter_id=adapter_id, mode=timing_mode,
            duration_seconds=result.duration_seconds, ok=False,
        )
        history_append(
            make_record(root, ok=False, adapter_id=adapter_id, mode="ece", message=package.message),
            base=cfg.work_dir,
        )
        return EXIT_ERROR

    sidecar = _write_sidecar_if_requested(package.container_path, args)
    record_sample(
        cfg.work_dir, adapter_id=adapter_id, mode=timing_mode,
        duration_seconds=result.duration_seconds, ok=True,
        file_count=package.file_count, input_bytes=package.size,
    )
    estimate_note = (
        compare_to_estimate(estimate, result.duration_seconds) if estimate is not None else ""
    )

    from .usability.summary import build_summary

    summary = build_summary(
        ok=True,
        mode="ece",
        project=root,
        adapter_id=adapter_id,
        label=label,
        output_path=package.container_path,
        output_size=package.size,
        artifact_count=package.file_count,
        duration_seconds=result.duration_seconds,
        estimated_seconds=(
            estimate.average if estimate is not None and estimate.available else None
        ),
        estimate_note=estimate_note,
        started=started,
        finished=_now_iso(),
        sha256=package.sha256,
        verified="ok" if package.sha256 else "",
        notes=list(result.notes) + ([f"sidecar: {sidecar}"] if sidecar else []),
        log_path=result.log_path,
    )
    history_append(
        make_record(
            root, ok=True, exit_code=0, duration_seconds=result.duration_seconds,
            adapter_id=adapter_id, container_path=package.container_path,
            artifact_count=package.file_count, sha256=package.sha256, mode="ece",
            message="Build and package succeeded",
            profile=getattr(args, "profile", "") or "",
        ),
        base=cfg.work_dir,
    )

    if _wants_json(args):
        _print_json(summary.as_dict())
    else:
        _print(summary.text())
    return EXIT_OK


# ---------------------------------------------------------------------------
# package / optimize / repair
# ---------------------------------------------------------------------------


def _message_printer(args):
    """A printer for operations that report a message, not a progress ratio."""
    if _quiet(args) or _wants_json(args):
        return None

    def show(message: str) -> None:
        _print(f"  {message}")

    return show


def _split_command(text: str) -> list[str]:
    """Split a command line into an argv list, keeping quoted paths together."""
    import shlex

    parts = shlex.split(text, posix=False)
    cleaned: list[str] = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in "\"'":
            cleaned.append(part[1:-1])
        else:
            cleaned.append(part)
    return cleaned


def cmd_package(args) -> int:
    from .packaging import AdvancedPackager, PackageMetadata, PackageRequest

    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    cfg = AppConfig()
    cfg.ensure_directories()
    output_dir = Path(args.output_dir) if args.output_dir else cfg.output_dir

    metadata = PackageMetadata(
        name=args.name or root.name,
        version=args.version,
        description=args.description,
        publisher=args.publisher,
        architecture=args.arch,
    )
    request = PackageRequest(
        project=root,
        output_dir=output_dir,
        fmt=args.fmt,
        metadata=metadata,
        include=list(args.include or []),
        exclude=list(args.exclude or []),
        run_build=bool(args.build),
        consent=bool(args.consent),
        prefer_adapter=args.adapter,
        reproducible=not args.no_reproducible,
        allow_copyleft=bool(args.allow_copyleft),
        icon=Path(args.icon) if args.icon else None,
        license_file=Path(args.license_file) if args.license_file else None,
        readme=Path(args.readme) if args.readme else None,
        overwrite=bool(args.overwrite),
    )

    packager = AdvancedPackager(config=cfg)

    optimization = None
    if args.profile and args.profile != "none":
        from .optimize.plan import OptimizationRequest, build_optimization_plan

        adapter_guess = args.adapter or ""
        if not adapter_guess:
            try:
                detected = scan(root)
                if detected.best is not None:
                    adapter_guess = detected.best.adapter_id
            except EceError:
                detected = None
        optimization = build_optimization_plan(
            adapter_guess,
            OptimizationRequest.from_args(args.profile, args.custom_flags),
        )
        if optimization.unsupported_reason:
            _print("What happened: this project cannot be optimized with that profile.")
            _print(f"Why: {optimization.unsupported_reason}")
            _print("What to do: pick another profile, or package without --profile.")
            return EXIT_ERROR
        if not (_quiet(args) or _wants_json(args)):
            for line in optimization.text().splitlines():
                _print(line)

    request.optimization = optimization

    if getattr(args, "plan", False):
        # A dry run: show what would be packaged and write nothing.
        plan = packager.plan(request, progress=_progress_printer(args))
        if _wants_json(args):
            _print_json(plan.as_dict())
        else:
            _print(plan.text())
        return EXIT_OK if plan.ok else EXIT_ERROR

    result = packager.package(request, progress=_progress_printer(args))

    from .build.history import append as history_append, make_record

    history_append(
        make_record(
            root,
            ok=result.ok,
            exit_code=0 if result.ok else EXIT_ERROR,
            duration_seconds=0.0,
            adapter_id=result.adapter_id,
            mode="package",
            container_path=result.path,
            artifact_count=len(result.files),
            sha256=result.sha256,
            message=result.message or "Package created",
            format=args.fmt,
        ),
        base=cfg.work_dir,
    )

    if _wants_json(args):
        _print_json(result.as_dict())
        return EXIT_OK if result.ok else EXIT_ERROR

    _print(result.text())
    if result.ok:
        _print("")
        _print(f"Package: {result.path}")
        _print("The package was verified: every file matches its checksum in the manifest.")
    return EXIT_OK if result.ok else EXIT_ERROR


def cmd_optimize(args) -> int:
    from .build.compile_service import compile_project
    from .optimize.measure import Measurement, compare, record_optimization
    from .optimize.plan import OptimizationRequest, build_optimization_plan
    from .optimize.recommend import recommendations
    from .packaging.manifest import detected_toolchains

    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    cfg = AppConfig()
    cfg.ensure_directories()

    adapter_guess = args.adapter or ""
    try:
        detected = scan(root)
        if not adapter_guess and detected.best is not None:
            adapter_guess = detected.best.adapter_id
    except EceError:
        detected = None

    optimization = build_optimization_plan(
        adapter_guess,
        OptimizationRequest.from_args(args.profile, args.custom_flags),
    )
    if optimization.unsupported_reason:
        _print("What happened: this project cannot be optimized with that profile.")
        _print(f"Why: {optimization.unsupported_reason}")
        _print("What to do: pick another profile, or build without --profile.")
        return EXIT_ERROR
    if not (_quiet(args) or _wants_json(args)):
        for line in optimization.text().splitlines():
            _print(line)
        _print("")

    tests_command = _split_command(args.tests) if args.tests else None
    benchmark_command = _split_command(args.benchmark) if args.benchmark else None

    def measure_run(profile: str, opt) -> Measurement:
        outcome = compile_project(
            root,
            config=cfg,
            consent_granted=args.consent,
            prefer_adapter=args.adapter,
            optimization=opt,
            progress=_progress_printer(args),
        )
        tests_ok: bool | None = None
        if tests_command:
            from .build.process import run_single

            tests_ok = run_single(tests_command, root, timeout=900).ok
        benchmark: dict[str, float] = {}
        if benchmark_command:
            from .build.process import run_single

            result = run_single(benchmark_command, root, timeout=1800)
            benchmark = {"seconds": result.duration_seconds}
        return Measurement(
            profile=profile,
            ok=outcome.ok,
            compile_seconds=outcome.duration_seconds,
            total_size=outcome.total_size,
            file_count=outcome.file_count,
            tests_ok=tests_ok,
            benchmark=benchmark,
            toolchains=detected_toolchains(),
            artifacts=[str(a.path) for a in outcome.artifacts[:20]],
        )

    if not (_quiet(args) or _wants_json(args)):
        _print("Building the baseline (no optimization)...")
    baseline = measure_run("default", None)
    if not baseline.ok:
        _print("What happened: the baseline build failed, so there is nothing to compare.")
        _print("Why: the project does not build in its current state.")
        _print("What to do: fix the build first (run 'ece-autocompiler repair' for help).")
        return EXIT_ERROR

    if not (_quiet(args) or _wants_json(args)):
        _print(f"Building with the '{optimization.profile}' profile...")
    optimized = measure_run(optimization.profile, optimization)

    report = compare(baseline, optimized)
    advice = recommendations(adapter_guess, optimization, report)
    record_optimization(cfg.work_dir, report)

    from .build.history import append as history_append, make_record

    history_append(
        make_record(
            root,
            ok=optimized.ok,
            exit_code=0 if optimized.ok else EXIT_ERROR,
            duration_seconds=optimized.compile_seconds,
            adapter_id=adapter_guess,
            mode="optimize",
            artifact_count=optimized.file_count,
            message=(
                f"Optimization comparison ({optimization.profile}): "
                f"build {report.time_change_pct}%, size {report.size_change_pct}%"
                if report.time_change_pct is not None and report.size_change_pct is not None
                else f"Optimization comparison ({optimization.profile})"
            ),
            profile=optimization.profile,
        ),
        base=cfg.work_dir,
    )

    if _wants_json(args):
        payload = report.as_dict()
        payload["optimization"] = optimization.as_dict()
        payload["recommendations"] = [r.to_dict() for r in advice]
        _print_json(payload)
        return EXIT_OK if optimized.ok else EXIT_ERROR

    _print(report.text())
    if advice:
        _print("")
        _print("Recommendations:")
        for item in advice:
            state = "applied" if item.applied else (
                "needs your approval" if item.requires_approval else "information"
            )
            _print(f"  [{state}] {item.text}")
    if not optimized.ok:
        _print("")
        _print("What happened: the optimized build failed.")
        _print("What to do: check the log above; the project was left as it was.")
        return EXIT_ERROR
    return EXIT_OK


def cmd_repair(args) -> int:
    from .build.compile_service import make_build_runner
    from .repair import RepairEngine

    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    cfg = AppConfig()
    cfg.ensure_directories()
    tests_command = _split_command(args.tests) if args.tests else None

    runner = make_build_runner(
        root,
        config=cfg,
        consent_granted=args.consent,
        prefer_adapter=args.adapter,
    )
    engine = RepairEngine(config=cfg)
    outcome = engine.repair(
        root,
        runner,
        max_attempts=max(1, int(args.max_attempts)),
        timeout=float(max(1, int(args.timeout))),
        apply=bool(args.apply),
        tests_command=tests_command,
        progress=_message_printer(args),
    )

    if _wants_json(args):
        _print_json(outcome.as_dict())
        return EXIT_OK if outcome.final_ok else EXIT_ERROR

    _print(outcome.text())
    if outcome.analysis is not None:
        _print("")
        _print("Analysis:")
        for line in outcome.analysis.text().splitlines():
            _print(f"  {line}")
    if not args.apply and outcome.proposals:
        _print("")
        _print("Nothing was changed. Re-run with --apply to apply the safe repairs.")
    return EXIT_OK if outcome.final_ok else EXIT_ERROR


def cmd_exe(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    if args.plan:
        from .usability.plan import preview_plan, render_plan_text

        preview = preview_plan(root, mode="exe")
        if _wants_json(args):
            _print_json(preview.to_dict())
            return EXIT_OK if preview.ready else EXIT_ERROR
        _print(render_plan_text(preview))
        return EXIT_OK if preview.ready else EXIT_ERROR

    from .build.exe_exporter import compile_exe

    cfg = AppConfig()
    cfg.ensure_directories()
    started = _now_iso()

    from .build.timing import compare_to_estimate, estimate_build, record_sample

    estimate = None
    guess = ""
    try:
        if root.is_dir():
            detected = scan(root)
            guess = detected.best.adapter_id if detected.best else ""
        elif root.suffix.lower() == ".py":
            guess = "python"
        if guess:
            estimate = estimate_build(cfg.work_dir, guess, "exe")
            if not (_quiet(args) or _wants_json(args)):
                _print(estimate.message)
    except EceError:
        estimate = None

    result = compile_exe(
        root,
        output=args.output,
        name=args.name,
        entry=args.entry,
        onefile=not args.onedir,
        console=args.console,
        timeout=args.timeout,
        progress=_progress_printer(args),
        config=cfg,
        consent_granted=bool(getattr(args, "consent", False)),
    )

    from .build.history import append as history_append, make_record

    if not result.ok:
        for line in result.failure_lines():
            _print(redact(line))
        record_sample(
            cfg.work_dir, adapter_id=result.adapter_id or result.language or "", mode="exe",
            duration_seconds=result.duration_seconds, ok=False,
        )
        history_append(
            make_record(root, ok=False, exit_code=EXIT_ERROR, mode="exe", message="Exe export failed"),
            base=cfg.work_dir,
        )
        return EXIT_ERROR

    size = result.exe_path.stat().st_size
    sidecar = _write_sidecar_if_requested(result.exe_path, args)
    from .container.integrity import sha256_file

    try:
        digest = sha256_file(result.exe_path)
    except OSError:
        digest = ""

    adapter_id = result.adapter_id or result.language or ""
    record_sample(
        cfg.work_dir, adapter_id=adapter_id, mode="exe",
        duration_seconds=result.duration_seconds, ok=True, file_count=1,
        input_bytes=size,
    )
    estimate_note = (
        compare_to_estimate(estimate, result.duration_seconds) if estimate is not None else ""
    )

    from .usability.summary import build_summary

    summary = build_summary(
        ok=True,
        mode="exe",
        project=root,
        adapter_id=adapter_id,
        label=result.language or "",
        output_path=result.exe_path,
        output_size=size,
        duration_seconds=result.duration_seconds,
        estimated_seconds=(
            estimate.average if estimate is not None and estimate.available else None
        ),
        estimate_note=estimate_note,
        started=started,
        finished=_now_iso(),
        sha256=digest,
        verified="ok" if digest else "",
        notes=list(result.notes) + ([f"sidecar: {sidecar}"] if sidecar else []),
    )
    history_append(
        make_record(
            root, ok=True, exit_code=0, duration_seconds=result.duration_seconds,
            adapter_id=result.language or "", container_path=result.exe_path,
            artifact_count=1, sha256=digest, mode="exe",
            message="Executable exported",
        ),
        base=cfg.work_dir,
    )
    if _wants_json(args):
        _print_json(summary.as_dict())
    else:
        _print(summary.text())
    return EXIT_OK


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# validate / inspect / extract / diff
# ---------------------------------------------------------------------------


def cmd_validate(args) -> int:
    report = validate_container(
        args.file, deep=args.deep, verify_signature=args.verify_signature
    )
    integrity = None
    if args.integrity:
        from .container.integrity import verify_container

        integrity = verify_container(args.file, deep=args.deep)

    if args.json:
        payload = {
            "path": str(report.path),
            "ok": report.ok,
            "format_version": report.format_version,
            "compression": report.compression,
            "hash_algo": report.hash_algo,
            "entry_count": report.entry_count,
            "total_uncompressed": report.total_uncompressed,
            "hashes_verified": report.hashes_verified,
            "failed_entries": report.failed_entries,
            "signature_state": report.signature_state,
            "issues": [
                {"severity": i.severity, "code": i.code, "message": i.message, "detail": i.detail}
                for i in report.issues
            ],
        }
        if integrity is not None:
            payload["integrity"] = integrity.as_dict()
        _print_json(payload)
        return EXIT_OK if report.ok else EXIT_ERROR

    _print(f"File: {report.path}")
    _print(f"Format: {report.format_version}  compression={report.compression}  hash={report.hash_algo}")
    _print(f"Entries: {report.entry_count}  uncompressed={report.total_uncompressed} bytes")
    if report.signature_state != "not-checked":
        _print(f"Signature: {report.signature_state}")
    for issue in report.issues:
        _print(f"  {issue}")
    if integrity is not None:
        for line in integrity.lines():
            _print(line)
    if args.explain:
        for line in explain_validation(report):
            _print(f"  {line}")
    _print("Result: " + ("VALID" if report.ok else "INVALID"))
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_inspect(args) -> int:
    with ContainerReader(args.file) as reader:
        manifest = reader.manifest
        index = reader.index
        assert manifest is not None and index is not None
        if args.json:
            _print_json(
                {"manifest": manifest.to_dict(), "entries": [e.to_dict() for e in index.entries]}
            )
            return EXIT_OK
        _print(f"Manifest for {args.file}")
        data = manifest.to_dict()
        _print(f"  project: {data['project']['name']} ({data['project']['detected_type']})")
        _print(f"  payload: {data['package']['payload_type']}")
        _print(f"  target : {data['target']['platform']}/{data['target']['architecture']} {data['target']['configuration']}")
        _print(f"  created: {data['created_utc']}")
        _print(f"  files  : {data['content']['file_count']}  bytes={data['content']['total_uncompressed']}")
        _print(f"  sha256 : {data['content']['content_hash']}")
        if args.explain:
            _print(f"  This packages {index.entries.__len__()} file(s) for "
                   f"{data['target']['platform']}/{data['target']['architecture']}.")
        _print("Files:")
        for entry in index.entries:
            _print(f"  {entry.path}  ({human_size(entry.size)})")
    return EXIT_OK


def cmd_extract(args) -> int:
    try:
        report = Extractor().extract(
            args.file,
            args.dest,
            overwrite=args.overwrite,
            verify_signature=args.verify_signature,
        )
    except EceError as exc:
        _print(f"error [{exc.code}]: {exc}")
        return EXIT_ERROR

    if _wants_json(args):
        _print_json(
            {
                "destination": str(report.destination),
                "files_written": report.files_written,
                "bytes_written": report.bytes_written,
                "overwritten": report.overwritten,
                "skipped": list(report.skipped),
                "warnings": list(report.warnings),
            }
        )
        return EXIT_OK
    for line in explain_extraction(report):
        _print(line)
    return EXIT_OK


def cmd_diff(args) -> int:
    from .container.diff import diff_containers, explain

    try:
        result = diff_containers(args.old, args.new)
    except EceError as exc:
        _print(f"error [{exc.code}]: {exc}")
        return EXIT_ERROR
    if _wants_json(args):
        _print_json(result.as_dict())
        return EXIT_OK
    for line in result.text().splitlines():
        _print(line)
    _print("")
    for line in explain(result):
        _print(f"  {line}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# tools / doctor / init / history
# ---------------------------------------------------------------------------


def cmd_tools(args) -> int:
    if getattr(args, "refresh", False):
        from .detection import refresh

        refresh()
    tools = discover_all()
    versions = {v.tool_id: v for v in toolchain_versions(list(tools.keys()))}
    if args.json:
        payload = {}
        for tool_id, info in tools.items():
            data = info.to_dict()
            data["min_version"] = versions[tool_id].minimum_text if tool_id in versions else ""
            data["version_ok"] = versions[tool_id].satisfies if tool_id in versions else True
            data["install"] = hint_lines(tool_id) if not info.found else []
            payload[tool_id] = data
        _print_json(payload)
        return EXIT_OK

    found = [t for t in tools.values() if t.found]
    missing = [t for t in tools.values() if not t.found]
    _print("Available toolchains:")
    for t in found:
        version = f"  {t.version}" if t.version else ""
        _print(f"  [x] {t.name}{version}")
        if t.path:
            _print(f"      {t.path}")
    _print("Not installed:")
    for t in missing:
        summary = hint_summary(t.id)
        _print(f"  [ ] {t.name}")
        if summary:
            _print(f"      {summary}")
    return EXIT_OK


def cmd_doctor(args) -> int:
    from .diagnostics.doctor import run_checks

    if getattr(args, "refresh", False):
        from .detection import refresh

        refresh()
    tool_ids = None
    if args.tools:
        tool_ids = [t.strip() for t in args.tools.split(",") if t.strip()]
    report = run_checks(tool_ids=tool_ids)
    if args.json:
        _print_json(report.as_dict())
        return EXIT_OK if report.ok else EXIT_ERROR
    for line in report.lines():
        _print(line)
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_init(args) -> int:
    from . import templates

    if args.list or not args.template:
        _print("Available templates:")
        for template in templates.list_templates():
            _print(f"  {template.key:<16} {template.description}")
        if args.list:
            return EXIT_OK
        if not args.template:
            _print("")
            _print("Usage: ece-autocompiler init <folder> --template <key>")
            return EXIT_USAGE
        return EXIT_OK

    if not args.path:
        _print("error: a destination folder is required.")
        return EXIT_USAGE
    try:
        written = templates.create_project(
            args.template, Path(args.path), name=args.name, force=args.force
        )
    except EceError as exc:
        _print(f"error [{exc.code}]: {exc}")
        return EXIT_ERROR
    _print(f"Created {len(written)} file(s) in {Path(args.path)} using '{args.template}':")
    for path in written:
        _print(f"  {path}")
    return EXIT_OK


def cmd_history(args) -> int:
    from .build import history as build_history
    from .usability import history_query

    cfg = AppConfig()
    base = cfg.work_dir

    if args.clear:
        if not args.yes:
            _print("error: refusing to clear the history without --yes.")
            return EXIT_ERROR
        build_history.clear(base=base)
        _print("Build history cleared.")
        return EXIT_OK

    records, note = build_history.load_report(base=base)
    if note:
        _print(f"Note: {note}")

    ok_filter = None
    if args.ok and not args.failed:
        ok_filter = True
    elif args.failed and not args.ok:
        ok_filter = False

    filtered = history_query.filter_records(
        records, query=args.search or "", ok=ok_filter, adapter_id=args.adapter or ""
    )

    if args.export:
        history_query.export_json(filtered, Path(args.export))
        _print(f"Wrote {len(filtered)} record(s) to {args.export}")
    if args.export_csv:
        history_query.export_csv(filtered, Path(args.export_csv))
        _print(f"Wrote {len(filtered)} record(s) to {args.export_csv}")

    shown = filtered[-max(0, args.limit):] if args.limit else []

    if _wants_json(args):
        _print_json(
            {
                "summary": history_query.summarize(filtered),
                "replay": [history_query.replay_command(r) for r in shown] if args.replay else [],
                "records": [history_query.as_dict(r) for r in shown],
            }
        )
        return EXIT_OK

    for line in history_query.describe(filtered):
        _print(line)
    if not shown:
        return EXIT_OK
    _print("")
    for record in reversed(shown):
        data = history_query.as_dict(record)
        status = "ok  " if data.get("ok") else "FAIL"
        _print(f"  [{status}] {data.get('timestamp', '')}  {data.get('adapter_id', '')}  {data.get('project_path', '')}")
        if data.get("message"):
            _print(f"          {data['message']}")
        if args.replay:
            _print(f"          repeat: {history_query.replay_command(record)}")
    return EXIT_OK


def cmd_gui(args) -> int:
    from .ui.main_window import run_app

    return run_app()


# ---------------------------------------------------------------------------
# fusion engine commands
# ---------------------------------------------------------------------------


def _load_workspace_or_exit(path: str):
    from .fusion.workspace import FusionWorkspace, WorkspaceError

    try:
        return FusionWorkspace.load(path)
    except WorkspaceError as exc:
        _print(f"error: {exc}")
        raise SystemExit(EXIT_USAGE) from exc


def _workspace_input_line(inp) -> str:
    kind = inp.kind if not inp.linked else f"{inp.kind} (linked)"
    origin = inp.source if len(inp.source) < 60 else inp.source[:57] + "..."
    return (
        f"{inp.input_id:12} {kind:22} {inp.detected_type or '?':12} "
        f"{inp.file_count:6} files  {inp.total_bytes:12,} B  {origin}"
    )


def cmd_workspace(args) -> int:
    from .fusion.workspace import FusionWorkspace, WorkspaceError

    command = args.workspace_command
    try:
        if command == "create":
            from pathlib import Path as _Path

            directory = _Path(args.directory)
            name = args.name or directory.name
            workspace = FusionWorkspace(directory)
            workspace.create(name=name)
            workspace.save()
            _print(f"created fusion workspace: {workspace.root}")
            _print(f"author configuration in: {workspace.config_path.name}")
            return EXIT_OK

        if command == "add":
            workspace = _load_workspace_or_exit(args.workspace)
            kind = None if args.kind == "auto" else args.kind
            for path in args.paths:
                inp = workspace.add(path, kind=kind, copy=not args.link, module_id=args.module or "")
                _print(
                    f"added {inp.input_id}: {inp.workspace_path or inp.source} "
                    f"({inp.kind}, detected: {inp.detected_type or '?'}, {inp.file_count} files)"
                )
            return EXIT_OK

        if command == "remove":
            workspace = _load_workspace_or_exit(args.workspace)
            inp = workspace.get_input(args.input_id)
            workspace.remove(args.input_id, delete_files=not args.keep_files)
            _print(f"removed {args.input_id} ({inp.workspace_path or inp.source})")
            return EXIT_OK

        if command == "replace":
            workspace = _load_workspace_or_exit(args.workspace)
            old = workspace.replace(args.input_id, args.source)
            _print(f"replaced {args.input_id} with {args.source} (now {old.file_count} files)")
            return EXIT_OK

        if command == "assign":
            workspace = _load_workspace_or_exit(args.workspace)
            workspace.assign(args.input_id, args.module_id)
            _print(f"{args.input_id} assigned to module {args.module_id}")
            return EXIT_OK

        if command == "list":
            workspace = _load_workspace_or_exit(args.workspace)
            inventory = workspace.inventory()
            if args.json:
                _print(json.dumps(inventory, indent=2, ensure_ascii=False))
                return EXIT_OK
            _print(f"workspace: {workspace.root}")
            _print(f"application: {inventory['config']['name']} v{inventory['config']['version']}")
            _print(f"inputs: {len(workspace.inputs)}")
            for inp in workspace.inputs:
                _print("  " + _workspace_input_line(inp))
            for problem in inventory["problems"]:
                _print(f"problem: {problem}")
            conflicts = workspace.conflicts()
            for conflict in conflicts:
                _print(f"{conflict['level']}: {conflict['message']}")
            return EXIT_OK

        if command == "config":
            workspace = _load_workspace_or_exit(args.workspace)
            if args.entry_point is not None:
                workspace.config.entry_point = args.entry_point
            if args.name is not None:
                workspace.config.name = args.name
            if args.version is not None:
                workspace.config.version = args.version
            if args.package_format is not None:
                workspace.config.package_format = args.package_format
            for pair in args.depend or []:
                if "=" not in pair:
                    _print(f"error: --depend expects 'consumer=provider', got {pair!r}")
                    return EXIT_USAGE
                consumer, provider = pair.split("=", 1)
                deps = workspace.config.module_dependencies.setdefault(consumer.strip(), [])
                if provider.strip() not in deps:
                    deps.append(provider.strip())
            for path in args.asset_dir or []:
                if path not in workspace.config.asset_dirs:
                    workspace.config.asset_dirs.append(path)
            workspace.save()
            if args.show or not any(
                [args.entry_point, args.name, args.version, args.package_format, args.depend, args.asset_dir]
            ):
                _print(json.dumps(workspace.config.to_dict(), indent=2, ensure_ascii=False))
            return EXIT_OK

    except WorkspaceError as exc:
        _print(f"error: {exc}")
        return EXIT_ERROR
    _print(f"error: unknown workspace command {command!r}")
    return EXIT_USAGE


def cmd_fuse(args) -> int:
    import json as _json
    from pathlib import Path as _Path

    from .fusion.modules import build_graph
    from .fusion.orchestrator import FusionOrchestrator

    command = args.fuse_command
    workspace = _load_workspace_or_exit(args.workspace)

    if command == "plan":
        graph = build_graph(workspace)
        _ = workspace.conflicts()
        if args.json:
            _print(_json.dumps(graph.as_dict(), indent=2, ensure_ascii=False))
            return EXIT_OK
        _print(f"application: {workspace.config.name} v{workspace.config.version}")
        _print(f"modules: {len(graph.modules)}")
        for module in graph.modules:
            deps = ", ".join(module.dependencies) or "(none)"
            _print(
                f"  {module.module_id:20} {module.language:12} {module.adapter_id:12}"
                f" {len(module.source_files):4} sources  deps: {deps}"
            )
        _print(f"build order: {' -> '.join(graph.order)}")
        _print(f"entry point: {graph.entry_point or '(not decided)'}")
        for conflict in graph.conflicts:
            _print(f"conflict: {conflict}")
        for cycle in graph.cycles:
            _print(f"cycle: {' -> '.join(cycle)}")
        return EXIT_OK

    if command == "validate":
        from .fusion.orchestrator import FusionOrchestrator as _Orchestrator

        graph = build_graph(workspace)
        orchestrator = _Orchestrator(workspace)
        compat = orchestrator.validate_compatibility(graph)
        if args.json:
            _print(_json.dumps({"tools": compat.tools, "checks": compat.checks}, indent=2, ensure_ascii=False))
            return EXIT_OK
        for check in compat.checks:
            _print(f"  {check['level']}: {check['message']}")
        if not compat.checks:
            _print("no compatibility findings (target architecture/OS are consistent)")
        return EXIT_OK

    if command == "build":
        output = _Path(args.output) if getattr(args, "output", None) else None
        orchestrator = FusionOrchestrator(
            workspace, progress=lambda stage, detail: _print(f"[{stage}] {detail}")
        )
        result = orchestrator.run(
            package_format=args.format, output=output,
            use_cache=not args.no_cache, repair_attempts=max(0, args.repair_attempts),
        )
        if args.json:
            _print(_json.dumps(result.report, indent=2, ensure_ascii=False))
        else:
            for message in result.messages:
                _print(message)
            for task in result.report.get("tasks", []):
                status = task["status"]
                if task.get("cached"):
                    status += " (cached)"
                line = f"  {task['module_id']}: {status}"
                if task.get("error"):
                    line += f" - {task['error']}"
                _print(line)
            if result.package_path:
                _print(f"package: {result.package_path}")
            for finding in result.validation:
                _print(f"validation: {finding}")
        return EXIT_OK if result.ok else EXIT_ERROR
    return EXIT_USAGE



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = AppConfig()
    config.ensure_directories()
    setup_logging(config.work_dir / "logs")

    dispatch = {
        "analyze": cmd_analyze,
        "plan": cmd_plan,
        "build": cmd_build,
        "exe": cmd_exe,
        "package": cmd_package,
        "optimize": cmd_optimize,
        "repair": cmd_repair,
        "validate": cmd_validate,
        "inspect": cmd_inspect,
        "extract": cmd_extract,
        "diff": cmd_diff,
        "tools": cmd_tools,
        "doctor": cmd_doctor,
        "init": cmd_init,
        "history": cmd_history,
        "gui": cmd_gui,
        "workspace": cmd_workspace,
        "fuse": cmd_fuse,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE

    try:
        return handler(args)
    except EceError as exc:
        _print(f"error [{exc.code}]: {exc}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        _print("Interrupted.")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
