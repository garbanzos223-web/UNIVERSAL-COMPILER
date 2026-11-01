"""ECE container backend.

Extraction reuses the project's own validated reader/extractor (the same code
the "Inspect .ece" dialog uses), so path traversal, duplicate Windows paths,
hashes and size limits are enforced by the existing, tested implementation.
Extracted Python bytecode and Java classes are then analysed recursively.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import java_class, python_bytecode
from ...container.reader import ContainerReader
from ...extraction import Extractor
from ..models import (
    CATEGORY_METADATA,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    STATUS_GENERATED,
    STATUS_PARTIAL,
    STATUS_RESOURCE,
    BackendOutput,
    FileAnalysis,
)
from .base import Backend, BackendContext, add_metadata, add_readme, is_noise
from .java import java_to_json
from .python_backend import _write_module

RECOMPILE_SUFFIXES = {
    ".py", ".java", ".cs", ".vb", ".fs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp",
    ".go", ".rs", ".js", ".ts", ".php", ".kt",
}


class EceBackend(Backend):
    id = "ece"
    label = "ECE container"
    kinds = ("ece",)

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="ece")
        with ContainerReader(source) as reader:
            manifest = reader.manifest.to_dict() if reader.manifest else {}
            payload_kind = (reader.manifest.adapter_id if reader.manifest else "") or "unknown"
            entries = reader.entries()
        output.add(
            ctx.write_text(
                "analysis/ece-manifest.json",
                json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
                category=CATEGORY_METADATA,
                status=STATUS_GENERATED,
                provenance="Manifest read by the project's own .ece reader.",
                language="json",
            )
        )
        detailed = ctx.output_dir.parent / f"{ctx.output_dir.name}_payload"
        report = Extractor().extract(source, detailed, overwrite=True)
        output.warnings.extend(report.warnings)
        ctx.log(f"Extracted {report.files_written} file(s) from the .ece container.", "info")

        sources = 0
        for path in sorted(detailed.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(detailed).as_posix()
            if is_noise(rel):
                continue
            suffix = path.suffix.lower()
            if suffix == ".pyc":
                try:
                    module = python_bytecode.read_module(path)
                except Exception as exc:
                    output.warnings.append(f"{rel}: {exc}")
                    module = None
                if module is not None and module.code is not None:
                    _write_module(ctx, output, module, rel, raw=path.read_bytes())
                else:
                    output.add(
                        ctx.copy_file(
                            path,
                            f"resources/{rel}",
                            category=CATEGORY_RESOURCE,
                            status=STATUS_RESOURCE,
                            provenance="Bytecode extracted unchanged from the container.",
                        )
                    )
                continue
            if suffix == ".class":
                try:
                    model = java_class.parse_class(path)
                except Exception as exc:
                    output.warnings.append(f"{rel}: {exc}")
                else:
                    package_path = model.package.replace(".", "/")
                    prefix = f"{package_path}/" if package_path else ""
                    output.add(
                        ctx.write_text(
                            f"java/{prefix}{model.simple_name}.java",
                            java_class.generate_outline(
                                model,
                                tool_note="Generated from a class extracted from the .ece container.",
                            ),
                            category=CATEGORY_SOURCE,
                            status=STATUS_GENERATED,
                            provenance="Class outline generated from the container payload.",
                            language="java",
                        )
                    )
                    output.add(
                        ctx.write_text(
                            f"analysis/classes/{prefix}{model.simple_name}.json",
                            java_to_json(model),
                            category=CATEGORY_METADATA,
                            status=STATUS_GENERATED,
                            provenance="Class model read from the container payload.",
                            language="json",
                        )
                    )
                continue
            if suffix in RECOMPILE_SUFFIXES and suffix != ".pyc":
                sources += 1
                if suffix == ".py":
                    text = path.read_text(encoding="utf-8", errors="replace")
                    output.add(
                        ctx.write_text(
                            f"source/{rel}",
                            text,
                            category=CATEGORY_SOURCE,
                            status=STATUS_PARTIAL,
                            provenance="Source file extracted unchanged from the .ece container.",
                            language="python",
                        )
                    )
                else:
                    output.add(
                        ctx.copy_file(
                            path,
                            f"source/{rel}",
                            category=CATEGORY_SOURCE,
                            status=STATUS_PARTIAL,
                            provenance="Source file extracted unchanged from the .ece container.",
                        )
                    )
                continue
            output.add(
                ctx.copy_file(
                    path,
                    f"resources/{rel}",
                    category=CATEGORY_RESOURCE,
                    status=STATUS_RESOURCE,
                    provenance="File extracted from the .ece payload.",
                )
            )

        entry_list = [
            {"path": entry.path, "size": entry.size, "executable": entry.executable}
            for entry in entries
        ]
        output.add(
            ctx.write_text(
                "analysis/ece-index.json",
                json.dumps(entry_list, indent=2, ensure_ascii=False),
                category=CATEGORY_METADATA,
                status=STATUS_GENERATED,
                provenance="File index read by the project's own .ece reader.",
                language="json",
            )
        )
        add_metadata(
            output,
            ctx,
            "ece-summary",
            {
                "container": str(source),
                "payload_kind": payload_kind,
                "entries": len(entries),
                "files_written": report.files_written,
                "source_files": sources,
                "manifest": manifest,
            },
        )
        add_readme(
            output,
            ctx,
            "ECE container recovered project\n"
            "===============================\n\n"
            f"Container: {source}\n"
            f"Payload type: {payload_kind}\n"
            f"Entries: {len(entries)}\n"
            f"Files extracted: {report.files_written}\n"
            f"Source files found: {sources}\n\n"
            "Extraction used the same verified reader as the rest of the application\n"
            "(hashes, path traversal, Windows path collisions and size limits).\n\n"
            + (
                "The container describes the technology below:\n"
                f"  adapter: {manifest.get('adapter_id', 'unknown')}\n"
                f"  platform: {manifest.get('platform', 'unknown')}\n"
                f"  architecture: {manifest.get('architecture', 'unknown')}\n"
                if manifest else ""
            ),
        )
        output.metadata = {
            "kind": "ece",
            "payload_kind": payload_kind,
            "entries": len(entries),
            "source_files": sources,
        }
        output.recompile_ready = sources > 0
        output.project_kind = payload_kind if payload_kind in (
            "python", "node", "dotnet", "java-maven", "java-gradle", "cpp-cmake", "cpp-make",
            "go", "rust-cargo", "php",
        ) else "ece"
        if sources == 0:
            output.warnings.append(
                "The container holds no source files; only compiled output and "
                "resources were recovered, so it cannot be recompiled as-is."
            )
        return output
