"""Python backend: .pyc modules, PyInstaller bundles and Python archives.

Bytecode is read with the running interpreter's ``marshal`` plus ``dis``; the
reconstruction keeps real imports, names, signatures and the complete
instruction stream. Method bodies are not recreated, and the generated module
says so. Packaging (PyInstaller) bundles are opened with the bundled CArchive
reader, so frozen modules become analysable bytecode instead of an opaque .exe.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .. import archive, pyinstaller, python_bytecode
from ..models import (
    CATEGORY_DISASSEMBLY,
    CATEGORY_METADATA,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    STATUS_GENERATED,
    STATUS_RECOVERED,
    STATUS_RESOURCE,
    BackendOutput,
    FileAnalysis,
)
from .base import Backend, BackendContext, add_metadata, add_readme, is_noise

MAX_MODULES = 4000


def _write_module(ctx: BackendContext, output: BackendOutput, module, label: str,
                  *, write_bytecode: bool = True, raw: bytes | None = None) -> None:
    raw_name = Path(label).name
    raw_name = raw_name[:-4] if raw_name.lower().endswith(".pyc") else raw_name
    raw_name = raw_name[:-3] if raw_name.lower().endswith(".py") else raw_name
    raw_name = re.sub(r"\.cpython-\d+$", "", raw_name, flags=re.IGNORECASE)
    stem = raw_name or "module"
    parts = stem.split(".")
    if len(parts) > 1 and all(part.replace("-", "_").isidentifier() for part in parts):
        safe = "/".join("".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in part) for part in parts)
    else:
        safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in stem)
    outline = python_bytecode.recover_outline(module, source_name=label)
    output.add(
        ctx.write_text(
            f"python/{safe}.py",
            outline,
            category=CATEGORY_SOURCE,
            status=STATUS_GENERATED,
            provenance=f"Structural reconstruction of {label}; function bodies are omitted.",
            language="python",
        )
    )
    if write_bytecode:
        header = python_bytecode.current_magic() + b"\x00" * 12
        payload = raw
        if payload is None:
            try:
                payload = header + __import__("marshal").dumps(module.code) if module.code else b""
            except Exception:
                payload = b""
        if payload:
            output.add(
                ctx.write_bytes(
                    f"python/bytecode/{safe}.pyc",
                    payload,
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance=f"Normalized bytecode for {label}.",
                    language="bytecode",
                )
            )
    disassembly = python_bytecode.disassembly(module)
    output.add(
        ctx.write_text(
            f"disassembly/{safe}.py.dis.txt",
            disassembly,
            category=CATEGORY_DISASSEMBLY,
            status="generated",
            provenance="CPython bytecode listing produced by dis.Bytecode.",
            language="text",
            editable=False,
        )
    )


class PythonBackend(Backend):
    id = "python"
    label = "Python"
    kinds = ("python-bytecode", "python-packaged", "python-wheel", "zip-python")

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        kind = analysis.format.kind
        if kind == "python-packaged":
            return self._packaged(source, output := BackendOutput(backend=self.id, project_kind="python"), ctx)
        if kind == "python-bytecode":
            return self._bytecode(source, BackendOutput(backend=self.id, project_kind="python"), ctx)
        return self._archive_python(source, BackendOutput(backend=self.id, project_kind="python"), ctx)

    # ------------------------------------------------------------------- pyc
    def _bytecode(self, source: Path, output: BackendOutput, ctx: BackendContext) -> BackendOutput:
        try:
            module = python_bytecode.read_module(source)
        except Exception as exc:
            output.ok = False
            output.errors.append(f"Could not read the bytecode: {exc}")
            return output
        add_metadata(output, ctx, "python-bytecode", python_bytecode.summary(module))
        _write_module(ctx, output, module, source.name, write_bytecode=False)
        warnings = list(module.warnings)
        if not module.loadable_here:
            warnings.append(
                f"This bytecode targets CPython {module.python_version}, but this build "
                f"runs CPython {python_bytecode.python_runtime_version()}; the instruction "
                "listing is only produced when the formats match."
            )
        output.warnings.extend(warnings)
        ctx.log(f"Python bytecode {source.name}: {module.python_version}", "info")
        add_readme(
            output,
            ctx,
            "Python recovered project\n"
            "========================\n\n"
            f"Bytecode: {source}\n"
            f"CPython version: {module.python_version}\n\n"
            "The .py file in python/ keeps the real imports, assignments, function\n"
            "and class names and argument lists. Function bodies are omitted and\n"
            "clearly marked; this is not the original source and is not claimed to\n"
            "run. The disassembly folder contains the exact CPython instruction\n"
            "stream, which is useful for analysis.\n",
        )
        output.metadata = {"kind": "python", "python_version": module.python_version}
        return output

    # ------------------------------------------------------------ pyinstaller
    def _packaged(self, source: Path, output: BackendOutput, ctx: BackendContext) -> BackendOutput:
        try:
            info = pyinstaller.read_archive(source)
            entries = pyinstaller.extract_entries(source, info)
        except Exception as exc:
            output.ok = False
            output.errors.append(f"The PyInstaller archive could not be read: {exc}")
            return output
        output.warnings.extend(info.warnings)
        ctx.log(f"PyInstaller archive: {len(entries)} entries (Python {info.python_version})", "info")
        modules = resources = 0
        pyz_modules = 0
        for typecode, name, blob in entries:
            lower = name.lower()
            if typecode == "z" or lower.endswith((".pyz", "pyz-00.pyz")):
                try:
                    _pymagic, code_objects, pyz_warnings = pyinstaller.read_pyz(blob)
                except Exception as exc:
                    output.warnings.append(f"{name}: the embedded PYZ could not be read: {exc}")
                    continue
                output.warnings.extend(f"{name}: {warning}" for warning in pyz_warnings)
                for module_name, code in code_objects:
                    if modules >= MAX_MODULES:
                        break
                    module = python_bytecode.module_from_code(code, name=module_name)
                    _write_module(ctx, output, module, f"{module_name}.pyc")
                    modules += 1
                    pyz_modules += 1
                continue
            is_module = typecode in ("m", "M", "s") or lower.endswith((".pyc", ".py"))
            if not is_module:
                if lower.endswith((".pyd", ".dll", ".so")):
                    rel = f"resources/native/{Path(name).name}"
                    output.add(
                        ctx.write_bytes(
                            rel,
                            blob,
                            category=CATEGORY_RESOURCE,
                            status=STATUS_RESOURCE,
                            provenance="Native dependency extracted from the PyInstaller archive.",
                        )
                    )
                else:
                    rel = f"resources/{Path(name).name}"
                    output.add(
                        ctx.write_bytes(
                            rel,
                            blob,
                            category=CATEGORY_RESOURCE,
                            status=STATUS_RESOURCE,
                            provenance="Data file extracted from the PyInstaller archive.",
                        )
                    )
                resources += 1
                continue
            if modules >= MAX_MODULES:
                break
            safe_name, payload = pyinstaller.module_payload(name, blob)
            module = python_bytecode.module_from_bytes(payload, name=name)
            if module.code is None:
                output.warnings.append(f"{name}: {', '.join(module.warnings)}")
                continue
            _write_module(ctx, output, module, name, raw=payload)
            modules += 1
        output.add(
            ctx.write_text(
                "analysis/pyinstaller-archive.json",
                json.dumps(
                    {
                        "python_version": info.python_version,
                        "entry_count": len(info.entries),
                        "archive_start": info.start,
                        "archive_length": info.length,
                        "entries": [
                            {"name": e.name, "type": e.typecode, "size": e.uncompressed_length}
                            for e in info.entries[:2000]
                        ],
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                category=CATEGORY_METADATA,
                status=STATUS_GENERATED,
                provenance="Table of contents read from the PyInstaller CArchive.",
                language="json",
            )
        )
        add_readme(
            output,
            ctx,
            "PyInstaller recovered project\n"
            "=============================\n\n"
            f"Bundle: {source}\n"
            f"Embedded Python: {info.python_version}\n"
            f"Frozen modules reconstructed: {modules} "
            f"(from the embedded PYZ: {pyz_modules})\n"
            f"Resources extracted: {resources}\n\n"
            "The CArchive was read by this tool; nothing was executed. Modules are\n"
            "reconstructed as structural Python (imports, names, signatures) plus a\n"
            "complete CPython bytecode listing. They are not the original .py files.\n"
            "Native libraries and data files are extracted unchanged as resources.\n",
        )
        output.metadata = {
            "kind": "pyinstaller",
            "python_version": info.python_version,
            "modules": modules,
            "pyz_modules": pyz_modules,
            "resources": resources,
        }
        output.recompile_ready = False
        return output

    # ---------------------------------------------------------------- archives
    def _archive_python(self, source: Path, output: BackendOutput, ctx: BackendContext) -> BackendOutput:
        extract_dir = ctx.output_dir / "extracted"
        report = archive.extract_archive(source, extract_dir)
        output.warnings.extend(report.warnings)
        if not report.files_written:
            output.ok = False
            output.errors.append("The archive contained no extractable files.")
            return output
        sources = 0
        for path in report.written:
            rel = path.relative_to(extract_dir).as_posix()
            if is_noise(rel):
                continue
            suffix = path.suffix.lower()
            if suffix == ".py":
                output.add(
                    ctx.copy_file(
                        path,
                        f"python/{rel}",
                        category=CATEGORY_SOURCE,
                        status=STATUS_RECOVERED,
                        provenance="Source file stored in the archive; recovered unchanged.",
                    )
                )
                sources += 1
            elif suffix == ".pyc":
                module = python_bytecode.module_from_bytes(path.read_bytes(), name=rel)
                if module.code is not None:
                    _write_module(ctx, output, module, rel, raw=path.read_bytes())
                else:
                    output.add(
                        ctx.copy_file(
                            path,
                            f"python/bytecode/{rel}",
                            category=CATEGORY_METADATA,
                            status=STATUS_RESOURCE,
                            provenance="Bytecode extracted from the archive.",
                        )
                    )
            else:
                output.add(
                    ctx.copy_file(
                        path,
                        f"resources/{rel}",
                        category=CATEGORY_RESOURCE,
                        status=STATUS_RESOURCE,
                        provenance="Resource extracted from the archive.",
                    )
                )
        add_metadata(
            output,
            ctx,
            "python-archive",
            {
                "archive": str(source),
                "files": report.files_written,
                "python_sources": sources,
            },
        )
        add_readme(
            output,
            ctx,
            "Python archive recovered project\n"
            "================================\n\n"
            f"Archive: {source}\n"
            f"Files extracted: {report.files_written}\n"
            f"Python source files recovered unchanged: {sources}\n\n"
            "Sources were extracted exactly as stored. Bytecode modules are shown\n"
            "as structural outlines plus disassembly. The archive is not rebuilt\n"
            "automatically; the Recompile button verifies and rebuilds whatever\n"
            "source the project actually contains.\n",
        )
        output.metadata = {"kind": "python", "sources": sources}
        output.recompile_ready = sources > 0
        return output
