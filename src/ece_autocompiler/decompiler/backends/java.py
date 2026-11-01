"""Java class and JAR backends.

The class-file reader is bundled, so classes are always analysed. A JDK's
``javap`` (when present) adds real declaration and bytecode listings. Java
source itself is not recovered without a dedicated Java decompiler, so the
generated Java files are class outlines and are labelled as such.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .. import archive, java_class, tools
from ..models import (
    CATEGORY_CONFIG,
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

MAX_CLASSES = 3000


def _safe_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _run_javap(javap: str, class_name: str, classpath: Path, extra: list[str]) -> tuple[str, str]:
    command = [javap, "-p", *extra, "-classpath", str(classpath), class_name]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"javap failed: {exc}"
    text = (completed.stdout or "") + (completed.stderr or "")
    return text, "" if completed.returncode == 0 else f"javap exited with {completed.returncode}"


def _class_outline(ctx: BackendContext, output: BackendOutput, model: java_class.ClassModel,
                   source_label: str, tool_note: str) -> None:
    package_path = model.package.replace(".", "/") if model.package else ""
    relative = f"java/{package_path + '/' if package_path else ''}{model.simple_name}.java"
    output.add(
        ctx.write_text(
            relative,
            java_class.generate_outline(model, tool_note=tool_note),
            category=CATEGORY_SOURCE,
            status=STATUS_GENERATED,
            provenance=f"Generated from the class file {source_label}; bodies are not original.",
            language="java",
        )
    )
    model_name = f"{package_path + '/' if package_path else ''}{model.simple_name}.json"
    output.add(
        ctx.write_text(
            f"analysis/classes/{model_name}",
            java_to_json(model),
            category=CATEGORY_METADATA,
            status="generated",
            provenance="Class model read from the constant pool and method table.",
            language="json",
        )
    )


def java_to_json(model: java_class.ClassModel) -> str:
    import json

    return json.dumps(model.to_dict(), indent=2, ensure_ascii=False)


class JavaBackend(Backend):
    id = "java"
    label = "Java"
    kinds = ("java-class", "jar", "java-archive")

    def decompile(self, source: Path, analysis: FileAnalysis, ctx: BackendContext) -> BackendOutput:
        output = BackendOutput(backend=self.id, project_kind="java")
        javap = tools.find_javap(getattr(ctx.options, "javap_path", ""))
        if analysis.format.kind == "java-class":
            return self._decompile_class(source, output, ctx, javap, source.parent, source.name)
        return self._decompile_jar(source, output, ctx, javap)

    # ------------------------------------------------------------------ class
    def _decompile_class(
        self,
        source: Path,
        output: BackendOutput,
        ctx: BackendContext,
        javap: str,
        classpath: Path,
        label: str,
    ) -> BackendOutput:
        try:
            model = java_class.parse_class(source)
        except Exception as exc:
            output.ok = False
            output.errors.append(f"Could not read the class file: {exc}")
            return output
        ctx.log(
            f"Java class {model.this_class}: {len(model.fields)} fields, {len(model.methods)} methods",
            "info",
        )
        note = "Java source not recovered; compile-ready decompiler not available."
        if javap:
            declarations, error = _run_javap(javap, model.this_class, classpath, [])
            if declarations:
                output.add(
                    ctx.write_text(
                        f"analysis/javap/{model.simple_name}.txt",
                        declarations,
                        category=CATEGORY_METADATA,
                        status="generated",
                        provenance="Output of javap -p run against the class file.",
                        language="text",
                    )
                )
                note = "Class declarations listed by javap; bodies still generated."
            bytecode, error2 = _run_javap(javap, model.this_class, classpath, ["-c"])
            if bytecode:
                output.add(
                    ctx.write_text(
                        f"disassembly/{model.simple_name}.jvm.txt",
                        bytecode,
                        category="disassembly",
                        status="generated",
                        provenance="JVM bytecode listing produced by javap -c.",
                        language="text",
                    )
                )
            if error:
                output.warnings.append(error)
            if error2 and error2 != error:
                output.warnings.append(error2)
        else:
            output.warnings.append(
                "javap was not found. The class model and outline are complete, but "
                "declaration and bytecode listings need a JDK (JAVA_HOME/bin/javap)."
            )
        _class_outline(ctx, output, model, label, note)
        add_metadata(output, ctx, "java-summary", model.to_dict())
        add_readme(
            output,
            ctx,
            "Java recovered project\n"
            "======================\n\n"
            f"Class: {model.this_class}\n"
            f"Class file version: {model.major}.{model.minor}\n"
            f"Methods: {len(model.methods)}\nFields: {len(model.fields)}\n\n"
            "The Java outline in java/ preserves real names, field types and method\n"
            "signatures. Method bodies are generated placeholders, not original\n"
            "source. A dedicated Java decompiler is required for complete source\n"
            "recovery; this project does not invent one.\n",
        )
        output.metadata = {
            "kind": "java",
            "class": model.this_class,
            "methods": len(model.methods),
            "fields": len(model.fields),
            "javap": bool(javap),
        }
        return output

    # -------------------------------------------------------------------- jar
    def _decompile_jar(
        self, source: Path, output: BackendOutput, ctx: BackendContext, javap: str
    ) -> BackendOutput:
        extract_dir = ctx.output_dir / "extracted"
        report = archive.extract_archive(source, extract_dir)
        output.warnings.extend(report.warnings)
        for skipped in report.skipped[:20]:
            output.warnings.append(f"skipped: {skipped}")
        if not report.files_written:
            output.ok = False
            output.errors.append("The archive contained no extractable files.")
            return output

        classes = [p for p in report.written if p.suffix.lower() == ".class"][:MAX_CLASSES]
        models: list[java_class.ClassModel] = []
        jar_root = extract_dir
        for path in classes:
            try:
                model = java_class.parse_class(path)
            except Exception as exc:
                output.warnings.append(f"{path.name}: {exc}")
                continue
            models.append(model)
            rel = path.relative_to(extract_dir).as_posix()
            _class_outline(ctx, output, model, rel, "Java class outline generated from the JAR.")
            if javap and len(models) <= 80:
                classpath = jar_root
                declarations, error = _run_javap(javap, model.this_class, classpath, [])
                if declarations:
                    output.add(
                        ctx.write_text(
                            f"analysis/javap/{model.this_class}.txt",
                            declarations,
                            category=CATEGORY_METADATA,
                            status="generated",
                            provenance="Output of javap -p run against the JAR classpath.",
                            language="text",
                        )
                    )
                elif error:
                    output.warnings.append(error)
        ctx.log(f"JAR: {len(models)} classes analysed out of {len(classes)} class files.", "info")

        # Non-class entries are resources; Java sources, if present, are real.
        for path in report.written:
            rel = path.relative_to(extract_dir).as_posix()
            suffix = path.suffix.lower()
            if suffix == ".class" or is_noise(rel):
                continue
            if suffix == ".java":
                category, status = CATEGORY_SOURCE, STATUS_PARTIAL
                language = "java"
                provenance = "Source file found inside the archive."
            elif rel.upper().startswith("META-INF/") and suffix in (".SF", ".RSA", ".DSA", ".EC"):
                category, status = CATEGORY_METADATA, STATUS_RESOURCE
                language = ""
                provenance = "Signature metadata found in the archive (not verified)."
            else:
                category, status = CATEGORY_RESOURCE, STATUS_RESOURCE
                language = ""
                provenance = "Resource extracted from the archive."
            output.add(
                ctx.copy_file(
                    path,
                    f"jar/{rel}",
                    category=category,
                    status=status,
                    provenance=provenance,
                )
            )

        has_sources = any(p.suffix.lower() == ".java" for p in report.written)
        jar_names = {p.relative_to(extract_dir).as_posix() for p in report.written}
        if not javap:
            output.warnings.append(
                "javap was not found; class outlines were generated from the bundled "
                "reader. Install a JDK for javap listings."
            )
        output.add(
            ctx.write_text(
                "pom.xml",
                _pom_skeleton(source.stem, jar_names),
                category=CATEGORY_CONFIG,
                status=STATUS_GENERATED,
                provenance="Build file written for the recovered outline; not verified.",
                language="xml",
            )
        )
        add_metadata(
            output,
            ctx,
            "java-jar",
            {
                "archive": str(source),
                "classes": len(models),
                "extracted_files": report.files_written,
                "has_java_sources": has_sources,
                "classes_detail": [m.to_dict() for m in models[:400]],
            },
        )
        add_readme(
            output,
            ctx,
            "Java recovered project (JAR)\n"
            "============================\n\n"
            f"Archive: {source}\n"
            f"Classes analysed: {len(models)}\n"
            f"Files extracted: {report.files_written}\n\n"
            "Class outlines in java/ use real names and signatures; bodies are\n"
            "generated placeholders. Resources and META-INF files were extracted\n"
            "unchanged. The generated pom.xml is a starting point, not a verified\n"
            "rebuild of the original artifact.\n",
        )
        output.metadata = {
            "kind": "java",
            "classes": len(models),
            "files": report.files_written,
            "javap": bool(javap),
        }
        output.recompile_ready = has_sources
        return output


def _pom_skeleton(name: str, entries: set[str]) -> str:
    java_version = "17"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://maven.apache.org/POM/4.0.0"\n'
        '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 '
        'http://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
        "  <modelVersion>4.0.0</modelVersion>\n"
        f"  <groupId>recovered</groupId>\n"
        f"  <artifactId>{name}</artifactId>\n"
        "  <version>0.0.0</version>\n"
        "  <properties>\n"
        f"    <maven.compiler.source>{java_version}</maven.compiler.source>\n"
        f"    <maven.compiler.target>{java_version}</maven.compiler.target>\n"
        "  </properties>\n"
        f"  <!-- Extracted entries: {len(entries)}. This skeleton was generated by "
        "ECE Universal AutoCompiler. -->\n"
        "</project>\n"
    )
