"""Data types for the advanced package generator.

The generator is honest by design: a result either points at a real file that
was written and verified, or it carries the reason it failed. Nothing here
fabricates a package, a checksum or a dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PackageMetadata:
    """User-facing identity of the application being packaged."""

    name: str = "application"
    version: str = "0.0.0"
    description: str = ""
    publisher: str = ""
    architecture: str = "x64"
    entry_point: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "publisher": self.publisher,
            "architecture": self.architecture,
            "entry_point": self.entry_point,
        }


@dataclass
class PackageFile:
    """One file that will be part of the package."""

    source: Path
    rel_path: str            # POSIX path inside the package
    size: int = 0
    sha256: str = ""
    kind: str = "file"       # file|binary|asset|license|readme|manifest
    executable: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.rel_path,
            "size": self.size,
            "sha256": self.sha256,
            "kind": self.kind,
        }


@dataclass
class DependencyIssue:
    """A dependency fact the user should know about before shipping."""

    name: str
    kind: str                # missing|system|license|unresolved|bundled
    severity: str = "warning"   # warning|info|error
    detail: str = ""
    action: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "severity": self.severity,
            "detail": self.detail,
            "action": self.action,
        }


@dataclass
class PackageRequest:
    """Everything the generator needs to produce one package."""

    project: Path
    output_dir: Path
    fmt: str = "zip"
    metadata: PackageMetadata = field(default_factory=PackageMetadata)
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    run_build: bool = False
    consent: bool = False
    prefer_adapter: str | None = None
    reproducible: bool = True
    allow_copyleft: bool = False
    icon: Path | None = None
    license_file: Path | None = None
    readme: Path | None = None
    overwrite: bool = False
    timeout: int = 1800
    #: Optimization plan applied to the build (see ``ece_autocompiler.optimize``).
    optimization: object = None

    def to_dict(self) -> dict:
        return {
            "project": str(self.project),
            "output_dir": str(self.output_dir),
            "format": self.fmt,
            "metadata": self.metadata.to_dict(),
            "include": list(self.include),
            "exclude": list(self.exclude),
            "run_build": self.run_build,
            "reproducible": self.reproducible,
        }


@dataclass
class PackagePlan:
    """What a packaging run would do, decided without doing it."""

    ok: bool = True
    fmt: str = ""
    files: list[PackageFile] = field(default_factory=list)
    dependencies: list[DependencyIssue] = field(default_factory=list)
    entry_point: str = ""
    adapter_id: str = ""
    licenses: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "format": self.fmt,
            "file_count": len(self.files),
            "total_bytes": sum(f.size for f in self.files),
            "files": [f.to_dict() for f in self.files],
            "dependencies": [d.to_dict() for d in self.dependencies],
            "entry_point": self.entry_point,
            "adapter_id": self.adapter_id,
            "licenses": list(self.licenses),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "metadata": dict(self.metadata),
        }

    def text(self) -> str:
        from ..usability.summary import human_size

        lines = [f"Package plan ({self.fmt})"]
        if self.metadata:
            lines.append(
                f"  Application: {self.metadata.get('name', '')} "
                f"{self.metadata.get('version', '')}"
            )
        if self.adapter_id:
            lines.append(f"  Project    : {self.adapter_id}")
        if self.entry_point:
            lines.append(f"  Entry point: {self.entry_point}")
        lines.append(
            f"  Files      : {len(self.files)} "
            f"({human_size(sum(f.size for f in self.files))})"
        )
        for item in self.files[:40]:
            lines.append(f"    {item.rel_path}  ({human_size(item.size)})")
        if len(self.files) > 40:
            lines.append(f"    ... and {len(self.files) - 40} more file(s)")
        for issue in self.dependencies:
            lines.append(f"  [{issue.severity}] {issue.name}: {issue.detail}")
            if issue.action:
                lines.append(f"      {issue.action}")
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        for error in self.errors:
            lines.append(f"  x {error}")
        if not self.ok:
            lines.append("This package cannot be created as it is.")
        return "\n".join(lines)


@dataclass
class PackageResult:
    """Outcome of a packaging run."""

    ok: bool = False
    fmt: str = ""
    path: Path | None = None
    message: str = ""
    files: list[PackageFile] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)
    sha256: str = ""
    size: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dependencies: list[DependencyIssue] = field(default_factory=list)
    entry_point: str = ""
    adapter_id: str = ""
    validated: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "format": self.fmt,
            "path": str(self.path) if self.path else "",
            "message": self.message,
            "sha256": self.sha256,
            "size": self.size,
            "file_count": len(self.files),
            "entry_point": self.entry_point,
            "adapter_id": self.adapter_id,
            "validated": self.validated,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "dependencies": [d.to_dict() for d in self.dependencies],
            "manifest": self.manifest,
        }

    def text(self) -> str:
        """Human summary with the same what/why/what-to-do contract."""
        from ..usability.summary import human_size

        lines: list[str] = []
        lines.append(f"Package: {self.path}" if self.path else "Package: (not created)")
        lines.append(f"  Format     : {self.fmt or 'unknown'}")
        lines.append(f"  Files      : {len(self.files)}")
        if self.size:
            lines.append(f"  Size       : {human_size(self.size)}")
        if self.sha256:
            lines.append(f"  SHA-256    : {self.sha256}")
        if self.entry_point:
            lines.append(f"  Entry point: {self.entry_point}")
        if self.validated:
            lines.append("  Validation : OK (contents and checksums verified)")
        for issue in self.dependencies:
            lines.append(f"  [{issue.severity}] {issue.name}: {issue.detail}")
            if issue.action:
                lines.append(f"      {issue.action}")
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        for error in self.errors:
            lines.append(f"  x {error}")
        if not self.ok:
            if self.message:
                lines.insert(1, f"  Message    : {self.message}")
            lines.append("What happened: the package was not created.")
            lines.append("Why: see the errors above.")
            lines.append("What to do: fix the errors above and run the command again.")
        return "\n".join(lines)
