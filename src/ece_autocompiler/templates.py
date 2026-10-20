"""Project templates for ``ece-autocompiler init`` (item 26).

A new project starts from a small, honest skeleton: a manifest the detector
recognises, a real entry point and a README that says how to run it. Templates
are plain text with a ``{name}`` placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigurationError
from .security import sanitize_filename

_GITIGNORE_PYTHON = ".venv/\nvenv/\n__pycache__/\nbuild/\ndist/\n*.spec\n"
_GITIGNORE_NODE = "node_modules/\ndist/\n"
_GITIGNORE_CPP = "build/\n*.obj\n*.exe\n"


@dataclass(frozen=True)
class Template:
    """One project skeleton."""

    key: str
    name: str
    description: str
    #: Relative file path -> file contents (``{name}`` is substituted).
    files: dict[str, str] = field(default_factory=dict)

    def manifest(self) -> str:
        """The file the detector keys on, for a friendly message."""
        for candidate in ("pyproject.toml", "package.json", "CMakeLists.txt", "main.py"):
            if candidate in self.files:
                return candidate
        return next(iter(self.files), "")


TEMPLATES: dict[str, Template] = {
    "python-script": Template(
        key="python-script",
        name="Python script",
        description="A single-file Python program (detected as a Python project).",
        files={
            "main.py": (
                '"""Entry point for {name}."""\n\n\n'
                "def main() -> int:\n"
                '    print(\"Hello from {name}!\")\n'
                "    return 0\n\n\n"
                'if __name__ == "__main__":\n'
                "    raise SystemExit(main())\n"
            ),
            "requirements.txt": "# Add runtime dependencies here, one per line.\n",
            "README.md": "# {name}\n\nRun it with:\n\n```\npython main.py\n```\n",
            ".gitignore": _GITIGNORE_PYTHON,
        },
    ),
    "python-package": Template(
        key="python-package",
        name="Python package",
        description="A src-layout Python package with a pyproject.toml.",
        files={
            "pyproject.toml": (
                "[build-system]\n"
                'requires = ["setuptools>=68"]\n'
                'build-backend = "setuptools.build_meta"\n\n'
                "[project]\n"
                'name = "{name}"\n'
                'version = "0.1.0"\n'
                'requires-python = ">=3.10"\n'
                "dependencies = []\n"
            ),
            "src/{name}/__init__.py": '"""The {name} package."""\n',
            "src/{name}/main.py": (
                "def main() -> int:\n"
                '    print("Hello from {name}!")\n'
                "    return 0\n\n\n"
                'if __name__ == "__main__":\n'
                "    raise SystemExit(main())\n"
            ),
            "README.md": "# {name}\n\nA Python package skeleton.\n",
            ".gitignore": _GITIGNORE_PYTHON,
        },
    ),
    "node-app": Template(
        key="node-app",
        name="Node.js app",
        description="A minimal Node.js application with a package.json.",
        files={
            "package.json": (
                "{\n"
                '  "name": "{name}",\n'
                '  "version": "0.1.0",\n'
                '  "private": true,\n'
                '  "main": "index.js",\n'
                '  "scripts": { "start": "node index.js" }\n'
                "}\n"
            ),
            "index.js": 'console.log("Hello from {name}!");\n',
            "README.md": "# {name}\n\nRun with ``npm start``.\n",
            ".gitignore": _GITIGNORE_NODE,
        },
    ),
    "cpp-cmake": Template(
        key="cpp-cmake",
        name="C++ (CMake)",
        description="A CMake C++17 console program.",
        files={
            "CMakeLists.txt": (
                "cmake_minimum_required(VERSION 3.20)\n"
                "project({name} CXX)\n"
                "set(CMAKE_CXX_STANDARD 17)\n"
                "add_executable({name} src/main.cpp)\n"
            ),
            "src/main.cpp": (
                "#include <iostream>\n\n"
                "int main() {\n"
                '    std::cout << "Hello from {name}!" << std::endl;\n'
                "    return 0;\n"
                "}\n"
            ),
            "README.md": "# {name}\n\nConfigure and build with CMake.\n",
            ".gitignore": _GITIGNORE_CPP,
        },
    ),
}


def list_templates() -> list[Template]:
    """Every template in a stable order."""
    return [TEMPLATES[key] for key in sorted(TEMPLATES)]


def get_template(key: str) -> Template | None:
    return TEMPLATES.get(key)


def default_project_name(destination: Path) -> str:
    return sanitize_filename(destination.name or "project", default="app")


def create_project(
    template_key: str,
    destination: Path | str,
    *,
    name: str | None = None,
    force: bool = False,
) -> list[Path]:
    """Write a template into *destination*; return the paths created.

    Refuses to write into a non-empty folder unless *force* is true, so a typo
    never overwrites an existing project.
    """
    template = TEMPLATES.get(template_key)
    if template is None:
        raise ConfigurationError("Unknown template", detail=template_key)

    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()) and not force:
        raise ConfigurationError(
            "The destination folder is not empty",
            detail=f"{destination} (use --force to write into it)",
        )
    destination.mkdir(parents=True, exist_ok=True)

    project_name = sanitize_filename(name or destination.name or "app")
    written: list[Path] = []
    for relative, content in template.files.items():
        target = destination / relative.replace("/", "\\").replace("{name}", project_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.replace("{name}", project_name), encoding="utf-8")
        written.append(target)
    return written


def list_template_names() -> list[str]:
    return sorted(TEMPLATES)
