"""Backend registry."""

from __future__ import annotations

from .archive_backend import ArchiveBackend
from .base import Backend, BackendContext
from .dotnet import DotNetBackend
from .ece import EceBackend
from .java import JavaBackend
from .native import ELFBackend, NativePEBackend
from .python_backend import PythonBackend

BACKENDS: tuple[Backend, ...] = (
    DotNetBackend(),
    JavaBackend(),
    PythonBackend(),
    NativePEBackend(),
    ELFBackend(),
    EceBackend(),
    ArchiveBackend(),
)


def backend_for(kind: str) -> Backend | None:
    for backend in BACKENDS:
        if kind in backend.kinds:
            return backend
    return None


def backend_ids() -> list[str]:
    return [backend.id for backend in BACKENDS]


__all__ = ["BACKENDS", "Backend", "BackendContext", "backend_for", "backend_ids"]
