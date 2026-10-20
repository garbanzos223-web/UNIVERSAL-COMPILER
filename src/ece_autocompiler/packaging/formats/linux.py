"""Linux package formats: DEB and AppImage.

Both need tooling that exists on Linux (``dpkg-deb``) or that has to be
downloaded (``appimagetool``). On Windows the formats report the exact
missing tool and stop - they never produce a broken or fake package.

The DEB layout follows the Debian policy: ``DEBIAN/control`` plus the payload
under ``/usr/lib/<name>``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..models import PackageRequest
from ..tools import run_tool, tool_path
from .base import FormatResult, PackageFormat, safe_name

#: Debian architectures this generator knows how to declare.
DEB_ARCHITECTURES = {
    "x64": "amd64",
    "x86": "i386",
    "arm64": "arm64",
}


def _control_text(request: PackageRequest) -> str:
    metadata = request.metadata
    arch = DEB_ARCHITECTURES.get(metadata.architecture.lower(), "amd64")
    lines = [
        f"Package: {safe_name(metadata.name, 'application').lower()}",
        f"Version: {metadata.version}",
        "Section: utils",
        "Priority: optional",
        "Maintainer: " + (metadata.publisher or "unknown"),
        f"Architecture: {arch}",
        f"Description: {metadata.description or metadata.name}",
    ]
    return "\n".join(lines) + "\n"


class DebFormat(PackageFormat):
    """A Debian package built with dpkg-deb."""

    fmt_id = "deb"
    display_name = "Debian package (.deb)"
    extension = ".deb"
    tool_id = "dpkg-deb"
    platform_note = "This format needs dpkg-deb, which ships with Debian/Ubuntu."

    def target_name(self, request: PackageRequest) -> str:
        metadata = request.metadata
        stem = f"{safe_name(metadata.name)}-{safe_name(metadata.version, '0.0.0')}"
        return stem + self.extension

    def build(
        self, staging: Path, request: PackageRequest, manifest: dict, target: Path
    ) -> FormatResult:
        staging = Path(staging)
        root = staging.parent / f"{target.stem}-root"
        payload = root / "usr" / "lib" / safe_name(request.metadata.name, "application")
        try:
            if root.exists():
                shutil.rmtree(root)
            (root / "DEBIAN").mkdir(parents=True, exist_ok=True)
            (root / "DEBIAN" / "control").write_text(_control_text(request), encoding="utf-8")
            payload.mkdir(parents=True, exist_ok=True)
            for item in staging.iterdir():
                if item.is_file():
                    shutil.copy2(item, payload / item.name)
                elif item.is_dir():
                    shutil.copytree(item, payload / item.name)
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The Debian package layout could not be prepared: {exc}",
                errors=[f"Check that {root.parent} is writable."],
            )

        code, output = run_tool(
            [tool_path(self.tool_id), "--build", str(root), str(target)],
            cwd=str(root.parent),
            timeout=request.timeout,
            reproducible=request.reproducible,
        )
        if code != 0:
            return FormatResult(
                ok=False,
                message="dpkg-deb could not build the package.",
                errors=[line for line in output.splitlines() if line.strip()][-12:]
                or ["dpkg-deb reported no output."],
                notes=[f"Package layout kept at {root}"],
            )
        if not target.is_file():
            return FormatResult(
                ok=False,
                message="dpkg-deb reported success but no package was produced.",
                errors=[f"Expected file: {target}"],
            )
        return FormatResult(
            ok=True,
            path=target,
            message=f"Debian package written to {target}",
            notes=[f"Package layout kept at {root}"],
        )


class AppImageFormat(PackageFormat):
    """An AppImage built with appimagetool."""

    fmt_id = "appimage"
    display_name = "AppImage"
    extension = ".AppImage"
    tool_id = "appimage"
    platform_note = (
        "This format needs appimagetool, which is only run on Linux; the generated "
        "AppDir is kept so it can be packaged on a Linux machine."
    )

    def target_name(self, request: PackageRequest) -> str:
        metadata = request.metadata
        stem = f"{safe_name(metadata.name)}-{safe_name(metadata.version, '0.0.0')}"
        return stem + self.extension

    def build(
        self, staging: Path, request: PackageRequest, manifest: dict, target: Path
    ) -> FormatResult:
        staging = Path(staging)
        app_dir = staging.parent / f"{target.stem}-AppDir"
        metadata = request.metadata
        app_name = metadata.name or "application"
        entry = ""
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".exe", ".sh", ""):
                entry = path.relative_to(staging).as_posix()
                break
        try:
            if app_dir.exists():
                shutil.rmtree(app_dir)
            app_dir.mkdir(parents=True, exist_ok=True)
            for item in staging.iterdir():
                if item.is_file():
                    shutil.copy2(item, app_dir / item.name)
                elif item.is_dir():
                    shutil.copytree(item, app_dir / item.name)
            desktop = (
                "[Desktop Entry]\n"
                f"Name={app_name}\n"
                "Type=Application\n"
                f"Exec={entry or app_name}\n"
                "Categories=Utility;\n"
                "Terminal=true\n"
            )
            (app_dir / f"{safe_name(app_name)}.desktop").write_text(desktop, encoding="utf-8")
            app_run = app_dir / "AppRun"
            app_run.write_text(
                "#!/bin/sh\n"
                "HERE=$(dirname \"$(readlink -f \"$0\")\")\n"
                f'exec "$HERE/{entry or app_name}" "$@"\n',
                encoding="utf-8",
            )
            app_run.chmod(0o755)
        except OSError as exc:
            return FormatResult(
                ok=False,
                message=f"The AppDir could not be prepared: {exc}",
                errors=[f"Check that {app_dir.parent} is writable."],
            )

        code, output = run_tool(
            [tool_path(self.tool_id), str(app_dir), str(target)],
            cwd=str(app_dir.parent),
            timeout=request.timeout,
            reproducible=request.reproducible,
        )
        if code != 0:
            return FormatResult(
                ok=False,
                message="appimagetool could not build the AppImage.",
                errors=[line for line in output.splitlines() if line.strip()][-12:]
                or ["appimagetool reported no output."],
                notes=[f"AppDir kept at {app_dir}"],
            )
        if not target.is_file():
            return FormatResult(
                ok=False,
                message="appimagetool reported success but no AppImage was produced.",
                errors=[f"Expected file: {target}"],
                notes=[f"AppDir kept at {app_dir}"],
            )
        return FormatResult(
            ok=True,
            path=target,
            message=f"AppImage written to {target}",
            notes=[f"AppDir kept at {app_dir}"],
        )
