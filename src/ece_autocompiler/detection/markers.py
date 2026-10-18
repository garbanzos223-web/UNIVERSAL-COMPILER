"""Marker definitions used by the project detector.

A marker is a file name or suffix plus the adapter it suggests and how strongly
it suggests it. The detector collects matches and groups them by directory so a
monorepo with several subprojects is reported as several candidates rather than
one lump.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Confidence assigned to a marker when it is the only evidence.
STRONG = 0.9
MEDIUM = 0.7
WEAK = 0.45


@dataclass(frozen=True)
class Marker:
    """A single detection rule."""

    adapter_id: str
    project_type: str
    confidence: float
    # Exact file names (case-insensitive on Windows).
    names: tuple[str, ...] = ()
    # File suffixes, e.g. (".csproj",).
    suffixes: tuple[str, ...] = ()
    # When set, all these names must be present in the same directory.
    requires: tuple[str, ...] = ()
    description: str = ""


# Ordered so that more specific manifests are evaluated first.
MARKERS: tuple[Marker, ...] = (
    # --- .NET ---
    Marker("dotnet", "dotnet", STRONG, suffixes=(".sln",), description=".NET solution"),
    Marker("dotnet", "dotnet", STRONG, suffixes=(".csproj",), description="C# project file"),
    Marker("dotnet", "dotnet", STRONG, suffixes=(".fsproj",), description="F# project file"),
    Marker("dotnet", "dotnet", STRONG, suffixes=(".vbproj",), description="Visual Basic project file"),
    # --- Rust ---
    Marker("rust-cargo", "rust", STRONG, names=("cargo.toml",), description="Cargo manifest"),
    # --- Go ---
    Marker("go", "go", STRONG, names=("go.mod",), description="Go module"),
    # --- Java / Kotlin ---
    Marker("java-maven", "java", STRONG, names=("pom.xml",), description="Maven project"),
    Marker(
        "kotlin-gradle",
        "kotlin",
        STRONG,
        names=("build.gradle.kts",),
        description="Kotlin Gradle build",
    ),
    Marker(
        "java-gradle",
        "java",
        STRONG,
        names=("build.gradle",),
        description="Gradle build",
    ),
    # --- C / C++ ---
    Marker("cpp-cmake", "cpp", STRONG, names=("cmakelists.txt",), description="CMake project"),
    Marker("cpp-meson", "cpp", STRONG, names=("meson.build",), description="Meson project"),
    Marker("cpp-make", "cpp", MEDIUM, names=("makefile", "gnumakefile"), description="Makefile project"),
    # --- Python ---
    Marker("python", "python", STRONG, names=("pyproject.toml",), description="PEP 517 project"),
    Marker("python", "python", MEDIUM, names=("setup.py",), description="setuptools project"),
    Marker("python", "python", MEDIUM, names=("setup.cfg",), description="setuptools config"),
    Marker("python", "python", MEDIUM, names=("requirements.txt",), description="pip requirements"),
    Marker("python", "python", WEAK, names=("pipfile",), description="Pipenv project"),
    # --- Node / web ---
    Marker(
        "node",
        "typescript",
        STRONG,
        names=("package.json",),
        requires=("tsconfig.json",),
        description="TypeScript package",
    ),
    Marker("node", "node", STRONG, names=("package.json",), description="npm package"),
    # --- PHP ---
    Marker("php", "php", STRONG, names=("composer.json",), description="Composer project"),
    # --- Game engines ---
    Marker("unreal", "unreal", STRONG, suffixes=(".uproject",), description="Unreal Engine project"),
    # Unity is detected by directory shape, handled separately in the detector.

    # --- Lock files (corroborate the manifest and reveal the package manager).
    Marker("node", "node", MEDIUM, names=("yarn.lock",), description="Yarn lock file"),
    Marker("node", "node", MEDIUM, names=("pnpm-lock.yaml",), description="pnpm lock file"),
    Marker("node", "node", MEDIUM, names=("bun.lockb", "bun.lock"), description="Bun lock file"),
    Marker("node", "node", WEAK, names=("package-lock.json",), description="npm lock file"),
    Marker("python", "python", MEDIUM, names=("poetry.lock",), description="Poetry lock file"),
    Marker("python", "python", MEDIUM, names=("uv.lock",), description="uv lock file"),
    Marker("python", "python", MEDIUM, names=("pdm.lock",), description="PDM lock file"),
)

# Map a detected lock file to the package manager that produced it. Used both to
# label a candidate and to choose the right install/build commands.
NODE_LOCKFILES: dict[str, str] = {
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "bun.lockb": "bun",
    "bun.lock": "bun",
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
}

PYTHON_LOCKFILES: dict[str, str] = {
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "pdm.lock": "pdm",
    "requirements.lock": "pip",
}

# Fallback package manager per adapter when no lock file is present.
DEFAULT_PACKAGE_MANAGERS: dict[str, str] = {
    "node": "npm",
    "python": "pip",
}

# Suffix-only hints for source files, used to raise confidence and to classify
# projects that ship no manifest at all.
SOURCE_HINTS: dict[str, tuple[str, ...]] = {
    "python": (".py", ".pyw", ".pyi"),
    "node": (".js", ".mjs", ".cjs", ".jsx"),
    "typescript": (".ts", ".tsx"),
    "csharp": (".cs",),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh"),
    "rust": (".rs",),
    "go": (".go",),
    "php": (".php",),
    "web": (".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte"),
    "ruby": (".rb",),
    "lua": (".lua",),
}

# Directories that never belong to the project's own sources. Used to bound the
# scan and to avoid matching manifests inside vendored dependencies.
IGNORED_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vs",
        ".vscode",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
        "env",
        ".env",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "out",
        "target",
        "bin",
        "obj",
        "packages",
        ".next",
        ".nuxt",
        ".gradle",
        ".dart_tool",
        "Pods",
        "vendor",
        ".cache",
    }
)

# Files that indicate the project root and should not be packaged by default.
IGNORED_FILE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".pdb", ".obj", ".o", ".class")


@dataclass
class DetectionCandidate:
    adapter_id: str
    project_type: str
    root: str  # relative path using '/' separators, "" for the scanned root
    confidence: float
    evidence: list[str] = field(default_factory=list)
    notes: str = ""
    # Detected package manager for this candidate (e.g. "yarn", "pnpm",
    # "poetry"). ``None`` when not applicable or not determined.
    package_manager: str | None = None
