"""Where to get a missing toolchain, with exact, copyable commands.

Single source of truth for the "what is missing / where do I install it"
message used by ``doctor``, ``analyze``, ``tools``, the ``.exe`` readiness
report, the CLI ``--json`` ``install`` object and the desktop toolchain card.

This application never installs anything by itself: it prints a command the
user can copy. Every entry always carries an official download URL as the
fallback, so a package-manager id that changes over time is never the only way
out.
"""

from __future__ import annotations

from dataclasses import dataclass

PACKAGE_MANAGERS = ("winget", "choco", "scoop", "pip")


@dataclass(frozen=True)
class InstallHint:
    """Install commands for one tool, plus the always-valid download URL."""

    tool_id: str
    name: str
    winget: str = ""
    choco: str = ""
    scoop: str = ""
    pip: str = ""
    url: str = ""
    note: str = ""


_VS_NOTE = "select the 'Desktop development with C++' workload"
_MAKE_NOTE = "Make ships with MinGW-w64 or MSYS2"

HINTS: dict[str, InstallHint] = {
    hint.tool_id: hint
    for hint in (
        InstallHint(
            "python", "Python",
            winget="Python.Python.3.12", choco="python", scoop="python",
            url="https://www.python.org/downloads/",
        ),
        InstallHint(
            "node", "Node.js (LTS)",
            winget="OpenJS.NodeJS.LTS", choco="nodejs-lts", scoop="nodejs-lts",
            url="https://nodejs.org/",
        ),
        InstallHint(
            "npm", "npm",
            winget="OpenJS.NodeJS.LTS", choco="nodejs-lts", scoop="nodejs-lts",
            url="https://nodejs.org/",
            note="npm is installed together with Node.js",
        ),
        InstallHint(
            "dotnet", ".NET SDK",
            winget="Microsoft.DotNet.SDK.8", choco="dotnet-sdk", scoop="dotnet-sdk",
            url="https://dotnet.microsoft.com/download",
        ),
        InstallHint(
            "java", "Java JDK (Temurin 21)",
            winget="EclipseAdoptium.Temurin.21.JDK", choco="temurin21", scoop="temurin21-jdk",
            url="https://adoptium.net/",
        ),
        InstallHint(
            "javac", "Java compiler (Temurin 21 JDK)",
            winget="EclipseAdoptium.Temurin.21.JDK", choco="temurin21", scoop="temurin21-jdk",
            url="https://adoptium.net/",
        ),
        InstallHint(
            "mvn", "Apache Maven",
            winget="Apache.Maven", choco="maven", scoop="maven",
            url="https://maven.apache.org/download.cgi",
        ),
        InstallHint(
            "gradle", "Gradle",
            winget="Gradle.Gradle", choco="gradle", scoop="gradle",
            url="https://gradle.org/releases/",
        ),
        InstallHint(
            "cargo", "Rust (cargo)",
            winget="Rustlang.Rustup", choco="rustup.install", scoop="rustup",
            url="https://rustup.rs/",
        ),
        InstallHint(
            "rustc", "Rust (rustc)",
            winget="Rustlang.Rustup", choco="rustup.install", scoop="rustup",
            url="https://rustup.rs/",
        ),
        InstallHint(
            "go", "Go",
            winget="GoLang.Go", choco="golang", scoop="go",
            url="https://go.dev/dl/",
        ),
        InstallHint(
            "cmake", "CMake",
            winget="Kitware.CMake", choco="cmake", scoop="cmake",
            url="https://cmake.org/download/",
        ),
        InstallHint(
            "ninja", "Ninja",
            winget="Ninja-build.Ninja", choco="ninja", scoop="ninja",
            url="https://github.com/ninja-build/ninja/releases",
        ),
        InstallHint(
            "gcc", "MinGW-w64 (GCC)",
            winget="BrechtSanders.WinLibs.POSIX.UCRT", choco="mingw", scoop="mingw",
            url="https://www.mingw-w64.org/downloads/",
        ),
        InstallHint(
            "gpp", "MinGW-w64 (G++)",
            winget="BrechtSanders.WinLibs.POSIX.UCRT", choco="mingw", scoop="mingw",
            url="https://www.mingw-w64.org/downloads/",
        ),
        InstallHint(
            "clang", "Clang / LLVM",
            winget="LLVM.LLVM", choco="llvm", scoop="llvm",
            url="https://releases.llvm.org/",
        ),
        InstallHint(
            "cl", "MSVC Build Tools (cl.exe)",
            winget="Microsoft.VisualStudio.2022.BuildTools",
            choco="visualstudio2022buildtools",
            url="https://visualstudio.microsoft.com/downloads/",
            note=_VS_NOTE,
        ),
        InstallHint(
            "msbuild", "MSBuild (Visual Studio Build Tools)",
            winget="Microsoft.VisualStudio.2022.BuildTools",
            choco="visualstudio2022buildtools",
            url="https://visualstudio.microsoft.com/downloads/",
            note=_VS_NOTE,
        ),
        InstallHint(
            "make", "GNU Make",
            choco="make", scoop="make",
            url="https://www.mingw-w64.org/downloads/",
            note=_MAKE_NOTE,
        ),
        InstallHint(
            "php", "PHP",
            winget="PHP.PHP", choco="php", scoop="php",
            url="https://windows.php.net/download/",
        ),
        InstallHint(
            "pyinstaller", "PyInstaller",
            pip="pyinstaller",
            url="https://pyinstaller.org/",
            note="run it with the same Python that runs this tool",
        ),
        InstallHint(
            "minisign", "minisign",
            winget="jedisct1.minisign", choco="minisign", scoop="minisign",
            url="https://github.com/jedisct1/minisign/releases",
        ),
        InstallHint(
            "cosign", "cosign",
            winget="Sigstore.Cosign", choco="cosign", scoop="cosign",
            url="https://github.com/sigstore/cosign/releases",
        ),
        InstallHint(
            "gpg", "GnuPG (gpg)",
            winget="GnuPG.Gpg4win", choco="gnupg", scoop="gnupg",
            url="https://gnupg.org/download/",
        ),
        InstallHint(
            "inno-setup", "Inno Setup (Windows installer compiler)",
            winget="JRSoftware.InnoSetup", choco="innosetup", scoop="innosetup",
            url="https://jrsoftware.org/isdl.php",
            note="needed for the 'inno' package format",
        ),
        InstallHint(
            "nsis", "NSIS (Windows installer generator)",
            winget="NSIS.NSIS", choco="nsis", scoop="nsis",
            url="https://nsis.sourceforge.io/Download",
            note="needed for the 'nsis' package format",
        ),
        InstallHint(
            "appimage", "AppImageKit (appimagetool)",
            url="https://github.com/AppImage/AppImageKit/releases",
            note="needed for the 'appimage' package format",
        ),
        InstallHint(
            "dpkg-deb", "dpkg-deb (Debian package builder)",
            url="https://wiki.debian.org/debian",
            note="ships with dpkg on Debian/Ubuntu; not available on Windows",
        ),
    )
}

#: Detected adapter -> the toolchain it needs to build for real.
ADAPTER_TO_TOOL: dict[str, str] = {
    "python": "python",
    "node": "node",
    "dotnet": "dotnet",
    "rust-cargo": "cargo",
    "go": "go",
    "java-maven": "mvn",
    "java-gradle": "gradle",
    "kotlin-gradle": "gradle",
    "cpp-cmake": "cmake",
    "cpp-meson": "ninja",
    "cpp-make": "make",
    "php": "php",
    "unity": "",
    "unreal": "",
}


def hint_for(tool_id: str) -> InstallHint | None:
    return HINTS.get(tool_id)


def hint_for_adapter(adapter_id: str) -> InstallHint | None:
    """Install hint for the toolchain an adapter needs, if it has one."""
    tool_id = ADAPTER_TO_TOOL.get(adapter_id, "")
    return HINTS.get(tool_id) if tool_id else None


def install_commands(tool_id: str) -> dict[str, str]:
    """Machine-readable install commands, always including the download URL."""
    hint = HINTS.get(tool_id)
    if hint is None:
        return {}
    commands = {
        "winget": f"winget install {hint.winget}" if hint.winget else "",
        "choco": f"choco install {hint.choco}" if hint.choco else "",
        "scoop": f"scoop install {hint.scoop}" if hint.scoop else "",
        "pip": f"pip install {hint.pip}" if hint.pip else "",
        "url": hint.url,
    }
    return commands


def hint_json(tool_id: str) -> dict:
    """The ``install`` object embedded in every ``--json`` payload."""
    hint = HINTS.get(tool_id)
    if hint is None:
        return {}
    install = {k: v for k, v in install_commands(tool_id).items() if v}
    return {
        "missing": hint.name,
        "install": install,
        "note": hint.note,
    }


def hint_lines(tool_id: str) -> list[str]:
    """Human lines: what is missing, then one copyable command per manager."""
    hint = HINTS.get(tool_id)
    if hint is None:
        return []
    lines = [f"Missing: {hint.name}"]
    for manager in PACKAGE_MANAGERS:
        value = getattr(hint, manager)
        if value:
            lines.append(f"  {manager} install {value}")
    if hint.url:
        lines.append(f"  download: {hint.url}")
    if hint.note:
        lines.append(f"  note: {hint.note}")
    return lines


def hint_summary(tool_id: str) -> str:
    """One-line summary used in dense reports."""
    hint = HINTS.get(tool_id)
    if hint is None:
        return ""
    parts = [
        f"{manager} install {getattr(hint, manager)}"
        for manager in PACKAGE_MANAGERS
        if getattr(hint, manager)
    ]
    if not parts:
        parts = [f"download {hint.url}"] if hint.url else []
    return f"{hint.name}: " + " | ".join(parts)


def every_detected_tool_has_a_hint(tool_ids: list[str]) -> list[str]:
    """Return the tool ids that lack a hint (used by a unit test)."""
    return [tool_id for tool_id in tool_ids if tool_id not in HINTS]
