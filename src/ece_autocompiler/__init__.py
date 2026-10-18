"""ECE Universal AutoCompiler.

A plugin-based build front-end that detects a project's toolchain, runs a real
build through the appropriate adapter, and packages the distributable output
into a versioned .ece container.

The package has no mandatory third-party runtime dependencies; it is designed
to run on a stock CPython installation. See docs/ARCHITECTURE.md for the module
map and docs/ECE_FORMAT.md for the container specification.
"""

from .version import APP_NAME, APP_SLUG, APP_VERSION, CONTAINER_FORMAT_VERSION

__all__ = [
    "APP_NAME",
    "APP_SLUG",
    "APP_VERSION",
    "CONTAINER_FORMAT_VERSION",
]
