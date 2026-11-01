""".NET assembly backend.

Full C# recovery is delegated to ILSpy's command-line tool (``ilspycmd``),
when installed. Without it the backend still parses the CLR metadata with the
bundled ECMA-335 reader and produces a namespace/class outline that is clearly
marked as generated. The outline is never labelled original source and is not
offered for recompilation.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import dotnet_metadata, pe, tools
from ..models import (
    CATEGORY_CONFIG,
    CATEGORY_RESOURCE,
    CATEGORY_SOURCE,
    STATUS_GENERATED,
    STATUS_PARTIAL,
    STATUS_RESOURCE,
    BackendOutput,
    FileAnalysis,
)
from .base import Backend, BackendContext, add_metadata, add_readme, is_noise


def _access_word(flags: int) -> str:
    visibility = flags & 0x7
    return {
        0: "internal", 1: "public", 2: "public", 3: "private",
        4: "protected", 5: "protected internal", 6: "private protected",
    }.get(visibility, "internal")


def _type_keyword(flags: int) -> str:
    if flags & 0x00000020:  # tdInterface
        return "interface"
    return "class"


def generate_outline(meta: dotnet_metadata.DotNetMetadata, assembly_name: str) -> tuple[str, str]:
    """Return (namespace, C# outline)."""
    namespace = ""
    for row in meta.types:
        if row.namespace:
            namespace = row.namespace
            break
    lines = [
        "// ---------------------------------------------------------------------------",
        "// Generated .NET outline produced by ECE Universal AutoCompiler.",
        "// Type and method names come from the CLR metadata tables. Method bodies",
        "// are NOT present: this is a reading aid, not the original C# source.",
        "// Install ILSpy (ilspycmd) for decompiled, recompilable C#.",
        "// ---------------------------------------------------------------------------",
        "",
        "using System;",
        "",
    ]
    if namespace:
        lines.append(f"namespace {namespace}")
        lines.append("{")
    indent = "    " if namespace else ""
    for row in meta.types[:2000]:
        name = row.name
        if name.startswith("<"):
            continue
        lines.append(f"{indent}{_access_word(row.flags)} {_type_keyword(row.flags)} {name}")
        lines.append(f"{indent}{{")
        methods = [m for m in row.methods if m and not m.startswith(".")]
        for method in methods[:200]:
            if method in (".ctor", ".cctor"):
                continue
            lines.append(f"{indent}    public void {method}()")
            lines.append(f"{indent}    {{")
            lines.append(f'{indent}        throw new NotImplementedException("recovered outline");')
            lines.append(f"{indent}    }}")
        if not methods:
            lines.append(f"{indent}    // (no methods in the metadata table)")
        lines.append(f"{indent}}}")
        lines.append("")
    if namespace:
        lines.append("}")
    lines.append("")
    lines.append(f"// Assembly: {assembly_name}")
    return namespace, "\n".join(lines)


def _normalize_recovered_project(project_dir: Path, ctx: BackendContext) -> list[str]:
    """Make ILSpy's generated project build with a current SDK.

    ILSpy writes ``netcoreapp8.0`` (not a valid SDK TFM) and a
    ``[module: RefSafetyRules]`` attribute that the compiler reserves for its
    own use. Both make an otherwise-correct recovered project fail to build.
    These are mechanical compatibility fixes, logged as such.
    """
    notes: list[str] = []
    for csproj in project_dir.rglob("*.csproj"):
        try:
            text = csproj.read_text(encoding="utf-8")
        except OSError:
            continue
        fixed = re.sub(r">netcoreapp(\d+)\.(\d+)<", r">net\1.\2<", text)
        if fixed != text:
            csproj.write_text(fixed, encoding="utf-8", newline="\n")
            notes.append(f"normalized the target framework in {csproj.name}")
    for source in project_dir.rglob("AssemblyInfo.cs"):
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        lines = [line for line in text.splitlines() if "RefSafetyRules" not in line]
        fixed = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
        if fixed != text:
            source.write_text(fixed, encoding="utf-8", newline="\n")
            notes.append(f"removed the compiler-reserved RefSafetyRules attribute from {source.name}")
    for note in notes:
        ctx.log(f"ILSpy project fix: {note}", "muted")
    return notes


class DotNetBackend(Backend):
    id = "dotnet"
    label = ".NET / C#"
    kinds = ("pe-dotnet",)

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="dotnet")
        info = pe.parse_pe(source)
        meta = dotnet_metadata.parse_metadata(source, info)
        assembly_name = source.stem
        add_metadata(
            output,
            ctx,
            "dotnet-metadata",
            {
                "assembly_file": str(source),
                "machine": info.machine,
                "runtime_version": meta.runtime_version,
                "type_count": len(meta.types),
                "method_count": meta.method_count,
                "metadata": meta.to_dict(),
            },
        )
        ctx.log(
            f".NET metadata: {len(meta.types)} types, {meta.method_count} methods "
            f"({meta.runtime_version or 'unknown runtime'})",
            "info",
        )

        if ctx.options.extract_resources:
            try:
                for name, blob in pe.extract_resource_bytes(source):
                    output.add(
                        ctx.write_bytes(
                            f"resources/{name}",
                            blob,
                            category=CATEGORY_RESOURCE,
                            status=STATUS_RESOURCE,
                            provenance="Extracted from the PE resource directory.",
                        )
                    )
            except Exception as exc:
                output.warnings.append(f"Resource extraction was incomplete: {exc}")

        ilspy = tools.find_ilspy(getattr(ctx.options, "ilspy_path", ""))
        project_dir = ctx.output_dir / "dotnet"
        decompiled = False
        if ilspy:
            project_dir.mkdir(parents=True, exist_ok=True)
            command = [ilspy, "-p", "-o", str(project_dir), str(source)]
            ctx.log("Running ILSpy: " + " ".join(command), "muted")
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=getattr(ctx.options, "ilspy_timeout_seconds", 900),
                    stdin=subprocess.DEVNULL,
                )
                stdout = (completed.stdout or "").strip()
                stderr = (completed.stderr or "").strip()
                if stdout:
                    output.log.append((stdout[-4000:], "muted"))
                if completed.returncode == 0:
                    decompiled = True
                    ctx.log("ILSpy completed.", "ok")
                else:
                    output.warnings.append(
                        f"ILSpy exited with {completed.returncode}: {stderr[-800:] or stdout[-800:]}"
                    )
            except subprocess.TimeoutExpired:
                output.warnings.append("ILSpy timed out; only metadata is available.")
            except OSError as exc:
                output.warnings.append(f"ILSpy could not start: {exc}")
        else:
            output.warnings.append(
                "ILSpy (ilspycmd) is not installed. Metadata is complete, but C# "
                "source recovery needs ILSpy. Use 'Prepare .NET decompiler' in the "
                "application to install it, or run: dotnet tool install ilspycmd "
                f"--version {tools.ILSPY_VERSION} --tool-path \"<folder>\""
            )

        if decompiled and project_dir.is_dir():
            fixes = _normalize_recovered_project(project_dir, ctx)
            output.metadata["project_fixes"] = fixes
            for path in sorted(project_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(project_dir).as_posix()
                if is_noise(rel):
                    continue
                if path.suffix.lower() == ".cs":
                    category, status = CATEGORY_SOURCE, STATUS_PARTIAL
                    language = "csharp"
                elif path.suffix.lower() in (".csproj", ".sln", ".props", ".targets"):
                    category, status = CATEGORY_CONFIG, STATUS_PARTIAL
                    language = "xml"
                elif path.suffix.lower() in (".resx", ".resources", ".png", ".ico", ".dll"):
                    category, status = CATEGORY_RESOURCE, STATUS_RESOURCE
                    language = ""
                else:
                    category, status = CATEGORY_RESOURCE, STATUS_RESOURCE
                    language = ""
                output.add(
                    ctx.copy_file(
                        path,
                        f"dotnet/{rel}",
                        category=category,
                        status=status,
                        provenance="Decompiled by ILSpy 8.2 from the assembly metadata; "
                                   "not the original source file and not byte-identical.",
                    )
                )
            output.recompile_ready = True
            output.log.append(("Recovered C# project from ILSpy.", "ok"))
        else:
            namespace, outline = generate_outline(meta, assembly_name)
            safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in assembly_name) or "assembly"
            output.add(
                ctx.write_text(
                    f"outline/{safe_name}.cs",
                    outline,
                    category=CATEGORY_SOURCE,
                    status=STATUS_GENERATED,
                    provenance="Generated from CLR metadata; method bodies are absent.",
                    language="csharp",
                )
            )
            csproj = (
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup>\n"
                "    <TargetFramework>net8.0</TargetFramework>\n"
                "    <Nullable>enable</Nullable>\n"
                "    <AssemblyName>" + safe_name + "</AssemblyName>\n"
                "  </PropertyGroup>\n"
                "</Project>\n"
            )
            output.add(
                ctx.write_text(
                    f"outline/{safe_name}.csproj",
                    csproj,
                    category=CATEGORY_CONFIG,
                    status=STATUS_GENERATED,
                    provenance="Project file written for the generated outline.",
                    language="xml",
                )
            )
            output.warnings.append(
                "Only a generated outline is available; it is not recompilable as the "
                "original application because the method bodies are missing."
            )

        add_readme(
            output,
            ctx,
            ".NET recovered project\n"
            "======================\n\n"
            f"Assembly: {source}\n"
            f"Runtime metadata: {meta.runtime_version or 'unknown'}\n"
            f"Types: {len(meta.types)}\nMethods: {meta.method_count}\n\n"
            + (
                "C# source was decompiled with ILSpy. Decompiled source is not the\n"
                "original file: comments, names and formatting may differ, and the\n"
                "rebuilt assembly will not be byte-identical to the original.\n"
                if decompiled
                else "ILSpy is not installed, so only metadata and a class outline are\n"
                     "present. The outline is generated and is not recompilable as the\n"
                     "original application.\n"
            ),
        )
        output.metadata = {
            "kind": "dotnet",
            "types": len(meta.types),
            "methods": meta.method_count,
            "ilspy": bool(ilspy),
            "decompiled": decompiled,
        }
        return output
