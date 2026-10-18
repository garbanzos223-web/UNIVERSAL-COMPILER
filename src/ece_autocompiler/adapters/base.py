"""Base classes and data types for build adapters.

Adapters are the only way to run external build tools. They never concatenate
untrusted paths into a single shell string: all commands are expressed as argv
lists. Every command that may run project-controlled scripts requires explicit
user consent (``requires_approval=True``).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

ProgressCallback = Callable[[str, int, int], None]
CancelToken = threading.Event


@dataclass
class DetectionResult:
    adapter_id: str
    project_type: str
    root: Path
    markers: list[str] = field(default_factory=list)
    confidence: float = 0.0
    notes: str = ""


@dataclass
class Requirement:
    id: str
    name: str
    required: bool = True
    found: bool = False
    version: str | None = None
    path: str | None = None
    install_hint: str = ""
    kind: str = "tool"  # tool|package|sdk


@dataclass
class RequirementsReport:
    requirements: list[Requirement] = field(default_factory=list)
    ready: bool = True
    notes: list[str] = field(default_factory=list)


@dataclass
class Issue:
    level: str = "info"  # error|warning|info
    code: str = "ece.adapter.issue"
    message: str = ""
    detail: str = ""
    fix: str = ""


@dataclass
class BuildPlanStep:
    id: str
    title: str
    cmd_args: list[str] = field(default_factory=list)
    workdir: Path | None = None
    env: dict[str, str] | None = None
    timeout: int | None = None
    requires_approval: bool = False
    capture_output: bool = True


@dataclass
class BuildPlan:
    id: str
    adapter_id: str
    workdir: Path
    steps: list[BuildPlanStep] = field(default_factory=list)
    output_dirs: list[Path] = field(default_factory=list)
    artifacts_expected: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requires_consent: bool = False


@dataclass
class Artifact:
    path: Path
    kind: str = "binary"  # source|binary|archive|asset|unknown
    size: int = 0
    executable: bool = False
    rel_path: str = ""


@dataclass
class BuildResult:
    ok: bool = False
    exit_code: int = -1
    duration_seconds: float = 0.0
    artifacts: list[Artifact] = field(default_factory=list)
    log_path: Path | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class BuildAdapter(ABC):
    adapter_id: str = "base"
    display_name: str = "Generic"

    @abstractmethod
    def detect_project(self, root: Path, scan_result=None) -> list[DetectionResult]:
        raise NotImplementedError

    @abstractmethod
    def inspect_requirements(self, root: Path, config: dict | None = None) -> RequirementsReport:
        raise NotImplementedError

    @abstractmethod
    def validate_configuration(self, root: Path, config: dict | None = None) -> list[Issue]:
        raise NotImplementedError

    @abstractmethod
    def create_build_plan(self, root: Path, config: dict | None = None) -> BuildPlan:
        raise NotImplementedError

    @abstractmethod
    def execute_build(
        self,
        plan: BuildPlan,
        *,
        progress: ProgressCallback | None = None,
        cancel: CancelToken | None = None,
        log_path: Path | None = None,
        consent_granted: bool = False,
    ) -> BuildResult:
        raise NotImplementedError

    @abstractmethod
    def collect_artifacts(self, root: Path, plan: BuildPlan) -> list[Artifact]:
        raise NotImplementedError

    @abstractmethod
    def classify_errors(self, output: str) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    def describe_supported_targets(self) -> list[dict]:
        raise NotImplementedError

    def supports(self, detected: DetectionResult) -> bool:
        return detected.adapter_id == self.adapter_id
