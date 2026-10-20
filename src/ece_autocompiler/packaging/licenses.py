"""License detection and redistribution checks for packaged files.

The generator never ships a file whose license forbids it silently: it looks
for the license files that ship with the project, classifies them, and reports
what the user has to decide (copyleft, unknown, proprietary).

The check is deliberately conservative and honest: it reads the license files
that are really on disk, it never guesses from a package name, and it never
blocks a build - it warns, because only the user knows the obligations.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: File names that usually hold a license.
LICENSE_NAMES = (
    "license",
    "licence",
    "license.txt",
    "license.md",
    "licence.txt",
    "licence.md",
    "copying",
    "copying.txt",
    "notice",
    "license.rst",
)

#: Substrings that identify a license family in the license text.
_SIGNATURES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AGPL", ("gnu affero general public license", "agpl")),
    ("GPL", ("gnu general public license", "gpl")),
    ("LGPL", ("gnu lesser general public license", "lgpl")),
    ("MPL", ("mozilla public license", "mpl")),
    ("Apache-2.0", ("apache license", "apache-2.0")),
    ("BSD", ("redistribution and use in source and binary forms", "bsd")),
    ("MIT", ("permission is hereby granted, free of charge", "mit license")),
    ("Unlicense", ("this is free and unencumbered software released into the public domain",)),
    ("ISC", ("permission to use, copy, modify, and/or distribute this software",)),
)

#: Licenses whose redistribution imposes extra obligations.
COPYLEFT = ("GPL", "AGPL", "LGPL", "MPL")


@dataclass
class LicenseFinding:
    """One license file found in the project."""

    path: str
    spdx: str = "Unknown"
    excerpt: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "spdx": self.spdx, "excerpt": self.excerpt}


def classify_license(text: str) -> str:
    """Return the license family of *text* ("MIT", "GPL", ... or "Unknown")."""
    lowered = text.lower()
    for spdx, needles in _SIGNATURES:
        for needle in needles:
            if needle in lowered:
                return spdx
    return "Unknown"


def _read_head(path: Path, limit: int = 4000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def find_licenses(root: Path, *, max_files: int = 50) -> list[LicenseFinding]:
    """Find the license files of a project directory."""
    findings: list[LicenseFinding] = []
    root = Path(root)
    if not root.is_dir():
        return findings
    try:
        entries = sorted(root.rglob("*"))
    except OSError:
        return findings
    for path in entries:
        if len(findings) >= max_files:
            break
        if not path.is_file():
            continue
        if path.name.lower() not in LICENSE_NAMES:
            continue
        text = _read_head(path)
        findings.append(
            LicenseFinding(
                path=path.as_posix(),
                spdx=classify_license(text),
                excerpt=" ".join(text.split())[:160],
            )
        )
    return findings


def redistribution_warnings(
    findings: list[LicenseFinding],
    *,
    allow_copyleft: bool = False,
) -> list[str]:
    """Explain what the user must check before redistributing the package."""
    warnings: list[str] = []
    spdx = {f.spdx for f in findings}
    for name in sorted(spdx & set(COPYLEFT)):
        if allow_copyleft:
            warnings.append(
                f"Copyleft license detected ({name}); redistribution is allowed because "
                "you enabled --allow-copyleft. Make sure the obligations are met."
            )
        else:
            warnings.append(
                f"Copyleft license detected ({name}). Redistributing this package may "
                "require you to release the sources under the same license. "
                "Re-run with --allow-copyleft after confirming that this is correct."
            )
    if findings and spdx == {"Unknown"}:
        warnings.append(
            "The license file could not be identified. Confirm that redistribution is "
            "allowed before shipping this package."
        )
    if not findings:
        warnings.append(
            "No license file was found in the project. Confirm that redistribution is "
            "allowed before shipping this package."
        )
    return warnings
