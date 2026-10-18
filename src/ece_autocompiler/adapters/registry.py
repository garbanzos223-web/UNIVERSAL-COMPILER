"""Registry and factory for build adapters."""

from __future__ import annotations

from pathlib import Path
from typing import List

from ..detection import ScanResult
from .base import BuildAdapter, DetectionResult
from .external_tool_adapter import ExternalToolAdapter
from .node_adapter import NodeAdapter
from .python_adapter import PythonAdapter

DEFAULT_ADAPTERS: list[type[BuildAdapter]] = [
    PythonAdapter,
    NodeAdapter,
    # Generic detection-only / tool-gated adapters are instantiated on demand.
]


class AdapterRegistry:
    def __init__(self, adapters: List[type[BuildAdapter]] | None = None) -> None:
        self._adapters: List[BuildAdapter] = []
        for cls in adapters or DEFAULT_ADAPTERS:
            self._adapters.append(cls())
        self._ensure_external()

    def _ensure_external(self) -> None:
        known = {a.adapter_id for a in self._adapters}
        mappings = [
            ("dotnet", "dotnet", ".NET"),
            ("rust-cargo", "cargo", "Rust (Cargo)"),
            ("go", "go", "Go"),
            ("java-maven", "mvn", "Java (Maven)"),
            ("java-gradle", "gradle", "Java (Gradle)"),
            ("kotlin-gradle", "gradle", "Kotlin (Gradle)"),
            ("cpp-cmake", "cmake", "C/C++ (CMake)"),
            ("cpp-meson", "ninja", "C/C++ (Meson)"),
            ("cpp-make", "make", "C/C++ (Make)"),
            ("php", "php", "PHP"),
        ]
        for aid, tool, disp in mappings:
            if aid in known:
                continue
            self._adapters.append(ExternalToolAdapter(aid, tool, display_name=disp))
        # unity/unreal are high-level; use generic tool-gated stance
        if "unity" not in known:
            self._adapters.append(ExternalToolAdapter("unity", None, display_name="Unity"))
        if "unreal" not in known:
            self._adapters.append(ExternalToolAdapter("unreal", None, display_name="Unreal Engine"))

    def all(self) -> List[BuildAdapter]:
        return list(self._adapters)

    def detect(self, root: Path, scan: ScanResult | None = None) -> List[DetectionResult]:
        results: List[DetectionResult] = []
        for adapter in self._adapters:
            try:
                res = adapter.detect_project(root, scan)
                results.extend(res)
            except Exception:
                continue  # never let a buggy adapter kill detection
        return results

    def for_detected(self, d: DetectionResult) -> BuildAdapter | None:
        for adapter in self._adapters:
            if adapter.supports(d):
                return adapter
        return None
