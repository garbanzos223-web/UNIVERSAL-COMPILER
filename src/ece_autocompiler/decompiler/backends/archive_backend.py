"""Generic archive backend: safe listing and extraction.

The extracted tree is inspected with the same format detector; anything with a
dedicated backend (bytecode, classes, assemblies) is listed for a follow-up
decompile, but this backend never pretends to recover source from a plain
archive.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import archive, formats
from ..models import (
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

SOURCE_SUFFIXES = {
    ".py", ".java", ".cs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".go",
    ".rs", ".js", ".ts", ".php", ".kt", ".rb",
}


class ArchiveBackend(Backend):
    id = "archive"
    label = "Archive"
    kinds = ("zip", "tar", "gzip", "bzip2", "xz")

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="archive")
        extract_dir = ctx.output_dir / "extracted"
        report = archive.extract_archive(source, extract_dir)
        output.warnings.extend(report.warnings)
        output.warnings.extend(f"skipped: {item}" for item in report.skipped[:20])
        listing = archive.inspect_archive(source)
        output.add(
            ctx.write_text(
                "analysis/archive.json",
                json.dumps(
                    {
                        "kind": listing.kind,
                        "entries": listing.names[:5000],
                        "total_uncompressed": listing.total_uncompressed,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                category=CATEGORY_METADATA,
                status=STATUS_GENERATED,
                provenance="Archive directory read without running any entry.",
                language="json",
            )
        )
        if not report.files_written:
            output.ok = False
            output.errors.append("No file could be extracted from the archive.")
            return output

        sources = 0
        nested = 0
        formats_seen: dict[str, int] = {}
        for path in report.written:
            rel = path.relative_to(extract_dir).as_posix()
            if is_noise(rel):
                continue
            detected = formats.detect_format(path)
            formats_seen[detected.kind] = formats_seen.get(detected.kind, 0) + 1
            suffix = path.suffix.lower()
            if suffix in SOURCE_SUFFIXES:
                sources += 1
                output.add(
                    ctx.copy_file(
                        path,
                        f"source/{rel}",
                        category=CATEGORY_SOURCE,
                        status=STATUS_RECOVERED,
                        provenance="Source file extracted unchanged from the archive.",
                    )
                )
            else:
                if detected.family in ("java", "python", "dotnet", "pe", "elf"):
                    nested += 1
                output.add(
                    ctx.copy_file(
                        path,
                        f"resources/{rel}",
                        category=CATEGORY_RESOURCE,
                        status=STATUS_RESOURCE,
                        provenance=f"Extracted unchanged ({detected.label}).",
                    )
                )
        add_metadata(
            output,
            ctx,
            "archive-summary",
            {
                "archive": str(source),
                "files_written": report.files_written,
                "source_files": sources,
                "nested_compiled_files": nested,
                "formats": formats_seen,
            },
        )
        add_readme(
            output,
            ctx,
            "Archive recovered project\n"
            "=========================\n\n"
            f"Archive: {source}\n"
            f"Kind: {listing.kind}\n"
            f"Entries: {len(listing.names)}\n"
            f"Files extracted: {report.files_written}\n"
            f"Source files found: {sources}\n"
            f"Compiled files that have their own backend: {nested}\n\n"
            "Source files were extracted unchanged. Compiled files (classes,\n"
            "bytecode, assemblies, native binaries) are listed as resources here;\n"
            "import them in the Decompiler to analyse them with their own backend.\n",
        )
        output.metadata = {
            "kind": "archive",
            "files": report.files_written,
            "sources": sources,
            "formats": formats_seen,
        }
        output.recompile_ready = sources > 0
        return output


__all__ = ["ArchiveBackend"]
