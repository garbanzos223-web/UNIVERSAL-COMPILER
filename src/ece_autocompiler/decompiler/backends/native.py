"""Native Windows PE and Linux ELF analysis backends.

Native binaries are not decompiled to their original source (that source is
gone). This backend produces the artefacts that are actually recoverable:
headers, sections, imports/exports, dependencies, symbols, strings, debug
information, extracted resources and a real disassembly when a disassembler is
available.
"""

from __future__ import annotations

from pathlib import Path

from .. import disassembly, elf, pe
from ..models import (
    CATEGORY_METADATA,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    BackendOutput,
    FileAnalysis,
)
from .base import Backend, BackendContext, add_disassembly, add_metadata, add_readme


class NativePEBackend(Backend):
    id = "native-pe"
    label = "Windows PE analysis"
    kinds = ("pe-native",)

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="native")
        info = pe.parse_pe(source)
        ctx.log(f"PE: {info.machine}, {len(info.sections)} sections, {sum(len(v) for v in info.imports.values())} imports", "info")
        add_metadata(output, ctx, "pe", info.to_dict())

        imports_lines = []
        for dll in sorted(info.imports):
            imports_lines.append(f"{dll}:")
            imports_lines.extend(f"    {name}" for name in info.imports[dll])
            imports_lines.append("")
        output.add(
            ctx.write_text(
                "analysis/imports.txt",
                "\n".join(imports_lines) or "(no import table)\n",
                category=CATEGORY_METADATA,
                status="generated",
                provenance="Read from the PE import directory.",
                language="text",
            )
        )
        output.add(
            ctx.write_text(
                "analysis/exports.txt",
                "\n".join(info.exports) + ("\n" if info.exports else "(no exports)\n"),
                category=CATEGORY_METADATA,
                status="generated",
                provenance="Read from the PE export directory.",
                language="text",
            )
        )
        sections = [
            f"{s.name:<10} va=0x{s.virtual_address:08x} vsize={s.virtual_size:<8} "
            f"raw={s.raw_size:<8} entropy={s.entropy:.2f}"
            for s in info.sections
        ]
        output.add(
            ctx.write_text(
                "analysis/sections.txt",
                "\n".join(sections) + "\n",
                category=CATEGORY_METADATA,
                status="generated",
                provenance="Read from the PE section table.",
                language="text",
            )
        )
        if info.version_info:
            version_text = "\n".join(f"{k}: {v}" for k, v in info.version_info.items())
            output.add(
                ctx.write_text(
                    "analysis/version-info.txt",
                    version_text + "\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Read from the PE version resource.",
                    language="text",
                )
            )
        if info.pdb_path:
            output.add(
                ctx.write_text(
                    "analysis/debug.txt",
                    f"PDB path: {info.pdb_path}\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Read from the CodeView debug directory.",
                    language="text",
                )
            )
        if ctx.options.include_strings:
            output.add(
                ctx.write_text(
                    "analysis/strings.txt",
                    "\n".join(info.strings) + ("\n" if info.strings else ""),
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Printable strings extracted from the binary.",
                    language="text",
                )
            )
        if ctx.options.extract_resources:
            try:
                for name, blob in pe.extract_resource_bytes(source):
                    rel = f"resources/{name}"
                    output.add(
                        ctx.write_bytes(
                            rel,
                            blob,
                            category=CATEGORY_RESOURCE,
                            status="resource",
                            provenance="Extracted from the PE resource directory.",
                        )
                    )
            except Exception as exc:
                output.warnings.append(f"Resource extraction was incomplete: {exc}")
        if ctx.options.disassemble:
            text, note = disassembly.pe_disassembly(source, info)
            if text:
                add_disassembly(output, ctx, source.stem, text, note)
            else:
                output.warnings.append(note)
                add_disassembly(output, ctx, source.stem, "", note)

        if info.has_signature:
            output.warnings.append(
                "An Authenticode signature directory is present; it is reported, "
                "not verified, and cannot be preserved by a rebuild."
            )
        add_readme(
            output,
            ctx,
            "Native PE recovered project\n"
            "===========================\n\n"
            f"Source binary: {source}\n"
            f"Architecture: {info.machine}\n\n"
            "The original C/C++ source cannot be restored from a native binary.\n"
            "This folder contains real recovered artefacts: headers, imports,\n"
            "exports, sections, resources, strings, debug information and a\n"
            "disassembly listing. No pseudocode is invented here.\n",
        )
        output.warnings.append(
            "Native binaries cannot be reconstructed to original source; this is "
            "analysis output, not a compilable project."
        )
        output.metadata = {
            "kind": "pe-native",
            "machine": info.machine,
            "imports": sum(len(v) for v in info.imports.values()),
            "exports": len(info.exports),
            "resources": len(info.resources),
        }
        return output


class ELFBackend(Backend):
    id = "elf"
    label = "Linux ELF analysis"
    kinds = ("elf",)

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="native")
        info = elf.parse_elf(source)
        add_metadata(output, ctx, "elf", info.to_dict())
        lines = [
            f"Class: {info.elf_class}-bit {'little' if info.little_endian else 'big'} endian",
            f"Type: {info.type}",
            f"Machine: {info.machine}",
            f"Entry point: 0x{info.entry:x}",
            f"Interpreter: {info.interpreter or '(none)'}",
            f"SONAME: {info.soname or '(none)'}",
            f"RPATH: {info.rpath or '(none)'}",
            f"RUNPATH: {info.runpath or '(none)'}",
            "",
            "Needed libraries:",
            *[f"  {item}" for item in info.dependencies],
            "",
            "Sections:",
            *[f"  [{s.index:>2}] {s.name:<24} {s.to_dict()['type']:<12} size={s.size}" for s in info.sections],
        ]
        output.add(
            ctx.write_text(
                "analysis/elf.txt",
                "\n".join(lines) + "\n",
                category=CATEGORY_METADATA,
                status="generated",
                provenance="Read from the ELF header, program headers and section table.",
                language="text",
            )
        )
        if info.symbols:
            output.add(
                ctx.write_text(
                    "analysis/symbols.txt",
                    "\n".join(info.symbols) + "\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Read from the ELF symbol table.",
                    language="text",
                )
            )
        if info.undefined:
            output.add(
                ctx.write_text(
                    "analysis/undefined-symbols.txt",
                    "\n".join(info.undefined) + "\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Symbols imported from shared libraries.",
                    language="text",
                )
            )
        if ctx.options.include_strings:
            data = source.read_bytes()
            strings = elf.extract_strings(data)
            output.add(
                ctx.write_text(
                    "analysis/strings.txt",
                    "\n".join(strings) + "\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Printable strings extracted from the binary.",
                    language="text",
                )
            )
        if info.debug_sections:
            output.add(
                ctx.write_text(
                    "analysis/debug.txt",
                    "Debug sections present:\n" + "\n".join(info.debug_sections) + "\n",
                    category=CATEGORY_METADATA,
                    status="generated",
                    provenance="Read from the ELF section table.",
                    language="text",
                )
            )
        if ctx.options.disassemble:
            text, note = disassembly.elf_disassembly(source, info)
            if text:
                add_disassembly(output, ctx, source.stem, text, note)
            else:
                output.warnings.append(note)

        # Recovered "source": a documentation file, clearly not compilable code.
        output.add(
            ctx.write_text(
                "recovered/README.md",
                "# ELF recovery report\n\n"
                f"- Binary: `{source.name}`\n"
                f"- Architecture: {info.machine}\n"
                f"- Entry point: `0x{info.entry:x}`\n"
                f"- Dependencies: {', '.join(info.dependencies) or 'none'}\n"
                f"- Symbols recovered: {len(info.symbols)}\n"
                f"- Debug sections: {', '.join(info.debug_sections) or 'none'}\n\n"
                "The original source is not present in an ELF executable and cannot\n"
                "be restored from it. This report and the analysis folder are the\n"
                "real recovered artefacts.\n",
                category=CATEGORY_SOURCE,
                status="partial",
                provenance="Generated report; the original source is not recoverable.",
                language="markdown",
            )
        )
        add_readme(
            output,
            ctx,
            "Native ELF recovered project\n"
            "============================\n\n"
            f"Source binary: {source}\n"
            f"Architecture: {info.machine}\n\n"
            "ELF binaries do not contain their original source. Headers, sections,\n"
            "symbols, dependencies, strings, debug information and the disassembly\n"
            "listing in this folder are real; no source is fabricated.\n",
        )
        output.warnings.append(
            "Native binaries cannot be reconstructed to original source; this is "
            "analysis output, not a compilable project."
        )
        output.metadata = {
            "kind": "elf",
            "machine": info.machine,
            "dependencies": info.dependencies,
            "symbols": len(info.symbols),
        }
        return output
