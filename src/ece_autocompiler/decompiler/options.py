"""Options accepted by a decompilation run."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DecompileOptions:
    extract_resources: bool = True
    disassemble: bool = True
    include_strings: bool = True
    recursive_containers: bool = True
    max_nested_files: int = 40
    ilspy_path: str = ""
    javap_path: str = ""
    java_timeout_seconds: int = 300
    ilspy_timeout_seconds: int = 900
