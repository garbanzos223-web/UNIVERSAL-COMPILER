"""Command line interface for ECE Universal AutoCompiler.

Subcommands mirror the GUI workflow so the tool is usable head-less and in CI:

    analyze   inspect a project and report detection + toolchains
    build     run a real build and package the result into an .ece container
    exe       bundle a Python project into a standalone .exe (PyInstaller)
    validate  validate an existing container (optionally deep hash check)
    inspect   print a container's manifest and file index
    extract   safely extract a container
    tools     list detected toolchains
    gui       launch the desktop interface
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
from .logging_config import setup_logging

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ece-autocompiler",
        description=f"{APP_NAME} {APP_VERSION} - detect, build and package projects into .ece containers.",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Analyze a project and print detections and toolchains.")
    p_analyze.add_argument("path", help="Project directory.")
    p_analyze.add_argument("--json", action="store_true", help="Emit JSON.")

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

    p_validate = sub.add_parser("validate", help="Validate an .ece container.")
    p_validate.add_argument("file", help="Container file.")
    p_validate.add_argument("--deep", action="store_true", help="Also verify every entry hash.")
    p_validate.add_argument("--json", action="store_true", help="Emit JSON.")

    p_inspect = sub.add_parser("inspect", help="Print a container's manifest and file index.")
    p_inspect.add_argument("file", help="Container file.")
    p_inspect.add_argument("--json", action="store_true", help="Emit JSON.")

    p_extract = sub.add_parser("extract", help="Safely extract a container.")
    p_extract.add_argument("file", help="Container file.")
    p_extract.add_argument("dest", help="Destination directory.")
    p_extract.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")

    p_tools = sub.add_parser("tools", help="List detected toolchains.")
    p_tools.add_argument("--json", action="store_true", help="Emit JSON.")

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

    sub.add_parser("gui", help="Launch the desktop interface.")
    return parser


def cmd_analyze(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR
    result = scan(root)
    if args.json:
        payload = {
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
                }
                for c in result.candidates
            ],
        }
        _print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK

    _print(f"Project: {root}")
    _print(f"Files scanned: {result.total_files}")
    if not result.candidates:
        _print("No supported project type detected.")
        return EXIT_OK
    _print("Candidates:")
    for c in result.candidates:
        label = ADAPTER_LABELS.get(c.adapter_id, c.adapter_id)
        where = f" at {c.root}" if c.root else ""
        _print(f"  - {label}{where}  ({int(c.confidence * 100)}%)  {', '.join(c.evidence)}")
    if result.ambiguous:
        _print("Note: more than one project was detected; the GUI lets you choose.")
    for note in result.notes:
        _print(f"Note: {note}")
    return EXIT_OK


def cmd_build(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    cfg = AppConfig()
    cfg.ensure_directories()
    output = Path(args.output) if args.output else cfg.output_dir / f"{root.name}.ece"

    from .build import BuildOrchestrator
    from .build.history import append as history_append, make_record
    from .diagnostics import classify, first_actionable, summarize

    overrides: dict = {}
    if args.skip_install:
        overrides["skip_npm_install"] = True

    orchestrator = BuildOrchestrator(config=cfg)

    def progress(message: str, done: int, total: int) -> None:
        if total:
            _print(f"[{done:>3}/{total}] {message}")

    try:
        result, package = orchestrator.build_and_package(
            root,
            output,
            config_overrides=overrides,
            consent_granted=args.consent,
            overwrite=args.overwrite,
            compression=args.compression,
            progress=progress,
        )
    except EceError as exc:
        _print(f"error: {exc}")
        history_append(
            make_record(root, ok=False, message=str(exc)), base=cfg.work_dir
        )
        return EXIT_ERROR

    if not result.ok:
        _print("Build failed.")
        combined = "\n".join(result.errors) or ""
        diags = classify(combined)
        if diags:
            _print(f"Diagnosis: {summarize(diags)}")
            best = first_actionable(diags)
            if best:
                _print(f"  {best.message}: {best.line}")
                if best.hint:
                    _print(f"  hint: {best.hint}")
        if result.log_path:
            _print(f"Log: {result.log_path}")
        history_append(
            make_record(root, ok=False, exit_code=result.exit_code, message="Build failed"),
            base=cfg.work_dir,
        )
        return EXIT_ERROR

    if not package.ok:
        _print(f"Packaging failed: {package.message}")
        history_append(
            make_record(root, ok=False, message=package.message), base=cfg.work_dir
        )
        return EXIT_ERROR

    _print(f"Build succeeded in {result.duration_seconds:.1f}s.")
    _print(f"Package: {package.container_path}")

    report = validate_container(package.container_path, deep=True)
    if report.ok:
        _print(f"Validation: OK ({report.entry_count} files, {report.total_uncompressed} bytes)")
    else:
        _print("Validation: FAILED")
        for issue in report.errors:
            _print(f"  {issue}")

    history_append(
        make_record(
            root,
            ok=True,
            exit_code=0,
            duration_seconds=result.duration_seconds,
            container_path=package.container_path,
            message="Build and package succeeded",
        ),
        base=cfg.work_dir,
    )
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_validate(args) -> int:
    report = validate_container(args.file, deep=args.deep)
    if args.json:
        _print(
            json.dumps(
                {
                    "path": str(report.path),
                    "ok": report.ok,
                    "format_version": report.format_version,
                    "compression": report.compression,
                    "hash_algo": report.hash_algo,
                    "entry_count": report.entry_count,
                    "total_uncompressed": report.total_uncompressed,
                    "hashes_verified": report.hashes_verified,
                    "failed_entries": report.failed_entries,
                    "issues": [
                        {"severity": i.severity, "code": i.code, "message": i.message, "detail": i.detail}
                        for i in report.issues
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return EXIT_OK if report.ok else EXIT_ERROR

    _print(f"File: {report.path}")
    _print(f"Format: {report.format_version}  compression={report.compression}  hash={report.hash_algo}")
    _print(f"Entries: {report.entry_count}  uncompressed={report.total_uncompressed} bytes")
    for issue in report.issues:
        _print(f"  {issue}")
    _print("Result: " + ("VALID" if report.ok else "INVALID"))
    return EXIT_OK if report.ok else EXIT_ERROR


def cmd_inspect(args) -> int:
    with ContainerReader(args.file) as reader:
        manifest = reader.manifest
        index = reader.index
        assert manifest is not None and index is not None
        if args.json:
            _print(
                json.dumps(
                    {"manifest": manifest.to_dict(), "entries": [e.to_dict() for e in index.entries]},
                    indent=2,
                    ensure_ascii=False,
                )
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
        _print("Files:")
        for entry in index.entries:
            _print(f"  {entry.size:>10}  {entry.path}")
    return EXIT_OK


def cmd_extract(args) -> int:
    try:
        report = Extractor().extract(args.file, args.dest, overwrite=args.overwrite)
    except EceError as exc:
        _print(f"error: {exc}")
        return EXIT_ERROR
    _print(f"Extracted to {args.dest}: {report.summary}")
    return EXIT_OK


def cmd_tools(args) -> int:
    tools = discover_all()
    if args.json:
        _print(json.dumps({k: v.to_dict() for k, v in tools.items()}, indent=2, ensure_ascii=False))
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
        hint = f"  ({t.install_hint})" if t.install_hint else ""
        _print(f"  [ ] {t.name}{hint}")
    return EXIT_OK


def cmd_exe(args) -> int:
    root = Path(args.path)
    if not root.exists():
        _print(f"error: path does not exist: {root}")
        return EXIT_ERROR

    from .build.exe_exporter import compile_exe

    cfg = AppConfig()
    cfg.ensure_directories()

    def progress(message: str, done: int, total: int) -> None:
        if total:
            _print(f"[{done:>3}/{total}] {message}")

    result = compile_exe(
        root,
        output=args.output,
        name=args.name,
        entry=args.entry,
        onefile=not args.onedir,
        console=args.console,
        timeout=args.timeout,
        progress=progress,
        config=cfg,
    )

    if not result.ok:
        _print(f"Executable export failed: {result.message}")
        for err in result.errors[:8]:
            _print(f"  {err}")
        if result.log_path:
            _print(f"Log: {result.log_path}")
        return EXIT_ERROR

    _print(f"Executable created in {result.duration_seconds:.1f}s.")
    _print(f"  .exe : {result.exe_path}")
    if result.language:
        _print(f"  lang : {result.language}")
    size = result.exe_path.stat().st_size
    _print(f"  size : {size:,} bytes")
    for note in result.notes:
        _print(f"  note : {note}")
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
        "build": cmd_build,
        "exe": cmd_exe,
        "validate": cmd_validate,
        "inspect": cmd_inspect,
        "extract": cmd_extract,
        "tools": cmd_tools,
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
