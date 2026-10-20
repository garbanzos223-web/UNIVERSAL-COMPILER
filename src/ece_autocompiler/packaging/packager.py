"""The advanced package generator.

It turns a project into a distributable package:

1. detect the project and pick the adapter;
2. optionally run the real build (with the usual consent rules);
3. collect the artifacts, the entry point and the assets;
4. check the runtime dependencies and the redistribution licenses;
5. stage the files in a clean, organised folder;
6. write the manifest (metadata, files, checksums, toolchains);
7. produce the package in the requested format;
8. read the package back and verify it before reporting success.

A package is only reported as successful when the file exists, its contents
match the manifest and every checksum verifies.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..config import AppConfig
from ..logging_config import get_logger
from .collection import collect_files, detect_dependencies, detect_entry_point
from .formats import get_format
from .licenses import find_licenses, redistribution_warnings
from .manifest import build_manifest, detected_toolchains
from .models import PackageFile, PackageRequest, PackageResult
from .validation import digest_of, size_of, validate_package

log = get_logger("packaging")


class AdvancedPackager:
    """Creates distributable packages from a project."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self.config = config or AppConfig()

    # ------------------------------------------------------------------
    def _staging_dir(self, request: PackageRequest) -> Path:
        from .formats.base import safe_name

        name = safe_name(request.metadata.name, "application")
        version = safe_name(request.metadata.version, "0.0.0")
        return Path(self.config.work_dir) / "package-staging" / f"{name}-{version}-{request.fmt}"

    def _stage_files(
        self,
        staging: Path,
        files: list[PackageFile],
        request: PackageRequest,
    ) -> list[str]:
        """Copy the selected files into a clean staging folder."""
        problems: list[str] = []
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for item in files:
            target = staging / Path(item.rel_path)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.source, target)
            except OSError as exc:
                problems.append(f"{item.rel_path} could not be staged: {exc}")
        return problems

    def _stage_extra(self, staging: Path, request: PackageRequest) -> list[PackageFile]:
        """Add the license, the README and the icon the user asked for."""
        extra: list[PackageFile] = []
        from ..container.integrity import sha256_file

        if request.license_file and request.license_file.is_file():
            extra.append(
                PackageFile(
                    source=request.license_file,
                    rel_path="LICENSE",
                    size=request.license_file.stat().st_size,
                    sha256=sha256_file(request.license_file),
                    kind="license",
                )
            )
        if request.readme and request.readme.is_file():
            extra.append(
                PackageFile(
                    source=request.readme,
                    rel_path="README.md",
                    size=request.readme.stat().st_size,
                    sha256=sha256_file(request.readme),
                    kind="readme",
                )
            )
        if request.icon and request.icon.is_file():
            icon = request.icon
            extra.append(
                PackageFile(
                    source=icon,
                    rel_path=icon.name,
                    size=icon.stat().st_size,
                    sha256=sha256_file(icon),
                    kind="asset",
                )
            )
        for item in extra:
            target = staging / item.rel_path
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item.source, target)
            except OSError as exc:
                log.warning("Could not stage %s: %s", item.rel_path, exc)
        return extra

    # ------------------------------------------------------------------
    def package(self, request: PackageRequest, *, progress=None) -> PackageResult:
        """Create the package described by *request*."""
        result = PackageResult(fmt=request.fmt)

        project = Path(request.project)
        if not project.exists():
            result.message = "The project folder does not exist."
            result.errors.append(f"Path not found: {project}")
            return result
        if not project.is_dir():
            result.message = "The selected path is a file, not a project folder."
            result.errors.append(f"Path not found: {project}")
            return result

        fmt = get_format(request.fmt)
        if fmt is None:
            from .formats import format_ids

            result.message = f"'{request.fmt}' is not a supported package format."
            result.errors.append("Supported formats: " + ", ".join(format_ids()))
            return result

        self.config.ensure_directories()
        output_dir = Path(request.output_dir)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            result.message = f"The output folder could not be created: {exc}"
            result.errors.append(f"Check that {output_dir} is writable.")
            return result

        # --- detect and build ------------------------------------------
        from ..build import BuildOrchestrator

        orchestrator = BuildOrchestrator(config=self.config)
        try:
            compiled = orchestrator.compile_project(
                project,
                consent_granted=request.consent,
                prefer_adapter=request.prefer_adapter,
                progress=progress,
                package_only=not request.run_build,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised to the user
            result.message = f"The project could not be prepared: {exc}"
            result.errors.append(str(exc))
            return result

        result.adapter_id = compiled.adapter_id
        if not compiled.result.ok:
            result.message = "The build failed, so no package was created."
            result.errors.extend(compiled.result.errors[-10:])
            result.errors.append(
                "Fix the build errors and run the packaging command again."
            )
            return result

        project_dir = compiled.project_dir or project
        artifacts = compiled.artifacts

        # --- collect ----------------------------------------------------
        files, issues = collect_files(project_dir, artifacts, request)
        result.dependencies.extend(issues)
        if not files:
            result.message = "There was nothing to package."
            result.errors.append(
                "No file matched the selection. Check the include and exclude patterns."
            )
            return result

        entry_point = detect_entry_point(project_dir, artifacts, compiled.adapter_id)
        if not entry_point:
            result.warnings.append(
                "The application entry point could not be determined automatically; "
                "the package still contains the built files."
            )
        request.metadata.entry_point = entry_point

        result.dependencies.extend(
            detect_dependencies(project_dir, compiled.adapter_id, files)
        )

        license_findings = find_licenses(project_dir)
        result.warnings.extend(
            redistribution_warnings(
                license_findings, allow_copyleft=request.allow_copyleft
            )
        )

        # --- stage ------------------------------------------------------
        staging = self._staging_dir(request)
        stage_problems = self._stage_files(staging, files, request)
        if stage_problems:
            result.message = "The package files could not be staged."
            result.errors.extend(stage_problems)
            return result
        extra = self._stage_extra(staging, request)
        files.extend(extra)

        manifest = build_manifest(
            metadata=request.metadata,
            files=files,
            request=request,
            toolchains=detected_toolchains(),
            entry_point=entry_point,
            dependencies=result.dependencies,
            licenses=[f.to_dict() for f in license_findings],
            format_id=fmt.fmt_id,
        )

        manifest_path = staging / "package-manifest.json"
        try:
            import json

            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            result.message = f"The manifest could not be written: {exc}"
            result.errors.append(f"Check that {staging} is writable.")
            return result

        expected = {f.rel_path: f.sha256 for f in files}
        from ..container.integrity import sha256_file

        try:
            expected["package-manifest.json"] = sha256_file(manifest_path)
        except OSError:
            pass

        # --- produce ----------------------------------------------------
        format_result = fmt.create(staging, request, manifest)
        result.warnings.extend(format_result.notes)
        if not format_result.ok:
            result.message = format_result.message
            result.errors.extend(format_result.errors)
            return result

        produced = format_result.path
        if produced is None:
            result.message = "The package format did not report an output file."
            result.errors.append("Nothing was written.")
            return result

        # --- verify -----------------------------------------------------
        problems = validate_package(produced, fmt.fmt_id, expected)
        if problems:
            result.message = "The package was created but did not pass verification."
            result.errors.extend(problems)
            result.path = produced
            return result

        try:
            digest = digest_of(produced)
            size = size_of(produced)
        except OSError as exc:
            result.message = f"The package could not be checked: {exc}"
            result.errors.append(str(exc))
            result.path = produced
            return result

        result.ok = True
        result.path = produced
        result.files = files
        result.manifest = manifest
        result.sha256 = digest
        result.size = size
        result.entry_point = entry_point
        result.validated = True
        result.message = f"The {fmt.display_name} package was created and verified."
        return result
