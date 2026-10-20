"""Command line interface for ECE Universal AutoCompiler.

Subcommands mirror the GUI workflow so the tool is usable head-less and in CI:

    analyze   inspect a project and report detection, toolchains and health
    plan      show the exact build plan without running anything
    build     run a real build and package the result into an .ece container
    exe       bundle a project into a standalone .exe
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

    p_doctor = sub.add_parser("doctor", help="Check whether this machine is ready to build.")
    p_doctor.add_argument("--tools", help="Comma-separated tool ids to check (default: all).")
    p_doctor.add_argument("--json", action="store_true", help="Emit JSON.")

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
    p_exe.add_argument("--plan", action="store_true", help="Print the plan and exit (dry run).")
    p_exe.add_argument("--no-integrity", action="store_true", help="Do not write a .sha256 sidecar.")
    p_exe.add_argument("--quiet", action="store_true", help="Suppress progress.")
    p_exe.add_argument("--no-progress", action="store_true", help="Suppress progress lines only.")
    p_exe.add_argument("--json", action="store_true", help="Emit a JSON summary.")

    sub.add_parser("gui", help="Launch the desktop interface.")
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
    try:
        detected = scan(root)
        guess = args.adapter or (detected.best.adapter_id if detected.best else "")
        if guess:
            estimate = estimate_build(cfg.work_dir, guess, timing_mode)
            if not (_quiet(args) or _wants_json(args)):
                _print(estimate.message)
    except EceError:
        estimate = None

    overrides: dict = {}
    if args.skip_install:
        overrides["skip_npm_install"] = True

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
    try:
        result, package = orchestrator.build_and_package(
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
        )
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
        ),
        base=cfg.work_dir,
    )

    if _wants_json(args):
        _print_json(summary.as_dict())
    else:
        _print(summary.text())
    return EXIT_OK


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
        "validate": cmd_validate,
        "inspect": cmd_inspect,
        "extract": cmd_extract,
        "diff": cmd_diff,
        "tools": cmd_tools,
        "doctor": cmd_doctor,
        "init": cmd_init,
        "history": cmd_history,
        "gui": cmd_gui,
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
