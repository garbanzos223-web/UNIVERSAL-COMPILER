"""Exception hierarchy shared across the application.

Every error carries a stable machine-readable ``code`` and a human-readable
message in English. Codes are used by the GUI and CLI to present consistent,
translatable messages and by tests to assert failure modes.
"""

from __future__ import annotations


class EceError(Exception):
    """Base class for every error raised by the application."""

    code = "ece.error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def __str__(self) -> str:
        if self.detail:
            return f"{self.message} ({self.detail})"
        return self.message


class ConfigurationError(EceError):
    code = "ece.configuration"


class ContainerError(EceError):
    """Raised for malformed, truncated or unsupported .ece files."""

    code = "ece.container"


class ContainerFormatError(ContainerError):
    code = "ece.container.format"


class UnsupportedVersionError(ContainerError):
    code = "ece.container.unsupported_version"


class IntegrityError(ContainerError):
    """Raised when a stored hash does not match the actual bytes."""

    code = "ece.container.integrity"


class UnsafePathError(ContainerError):
    """Raised when an archive path would escape the destination."""

    code = "ece.container.unsafe_path"


class LimitExceededError(ContainerError):
    code = "ece.container.limit_exceeded"


class DetectionError(EceError):
    code = "ece.detection"


class ToolchainError(EceError):
    code = "ece.toolchain"


class ToolchainMissingError(ToolchainError):
    code = "ece.toolchain.missing"


class BuildError(EceError):
    code = "ece.build"


class BuildCancelledError(BuildError):
    code = "ece.build.cancelled"


class BuildTimeoutError(BuildError):
    code = "ece.build.timeout"


class ExtractionError(EceError):
    code = "ece.extraction"


class ExtractionConflictError(ExtractionError):
    """Raised when the destination already contains a file and overwrite is off."""

    code = "ece.extraction.conflict"
