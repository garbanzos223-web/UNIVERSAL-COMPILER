"""The toolchain probe cache.

Probing every tool starts one process per tool, which is slow. The cache
keeps the result for a short while and drops it when the search path changes.
These tests pin that behaviour, including the invalidation rules.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.detection import toolchain_cache  # noqa: E402
from ece_autocompiler.detection import toolchains  # noqa: E402


class ToolchainCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_round_trip(self) -> None:
        tools = {"python": {"id": "python", "name": "Python", "found": True, "path": "python.exe",
                            "version": "3.12.0", "error": None, "install_hint": ""}}
        self.assertTrue(toolchain_cache.save(tools, base=self.tmp))
        loaded = toolchain_cache.load(base=self.tmp)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["python"]["version"], "3.12.0")

    def test_a_changed_path_invalidates_the_cache(self) -> None:
        toolchain_cache.save({"python": {"id": "python", "found": True}}, base=self.tmp)
        original = os.environ.get("PATH", "")
        os.environ["PATH"] = original + os.pathsep + str(self.tmp)
        self.addCleanup(os.environ.__setitem__, "PATH", original)
        self.assertIsNone(toolchain_cache.load(base=self.tmp))

    def test_an_expired_cache_is_ignored(self) -> None:
        toolchain_cache.save({"python": {"id": "python", "found": True}}, base=self.tmp)
        self.assertIsNone(toolchain_cache.load(base=self.tmp, ttl=-1))

    def test_a_damaged_cache_file_is_ignored(self) -> None:
        path = toolchain_cache.cache_path(self.tmp)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")
        self.assertIsNone(toolchain_cache.load(base=self.tmp))

    def test_invalidate_removes_the_file(self) -> None:
        toolchain_cache.save({"python": {"id": "python", "found": True}}, base=self.tmp)
        self.assertTrue(toolchain_cache.invalidate(base=self.tmp))
        self.assertFalse(toolchain_cache.cache_path(self.tmp).exists())
        self.assertFalse(toolchain_cache.invalidate(base=self.tmp))

    def test_missing_cache_returns_none(self) -> None:
        self.assertIsNone(toolchain_cache.load(base=self.tmp / "nothing"))


class DiscoveryUsesTheCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        toolchains.discover_all.cache_clear()
        self.addCleanup(toolchains.discover_all.cache_clear)

    def test_discovery_is_served_from_the_cache_when_it_matches(self) -> None:
        cached = {
            spec.id: {
                "id": spec.id,
                "name": spec.name,
                "found": False,
                "path": None,
                "version": None,
                "error": "Not found on PATH",
                "install_hint": spec.install_hint,
            }
            for spec in toolchains.SPECS
        }
        toolchain_cache.save(cached, base=self.tmp)
        with mock.patch.object(
            toolchain_cache, "cache_path", side_effect=lambda base=None: self.tmp / "toolchains.json"
        ):
            with mock.patch.object(toolchains, "_discover_one") as probe:
                tools = toolchains.discover_all()
        self.assertTrue(tools)
        probe.assert_not_called()

    def test_refresh_drops_the_cached_result(self) -> None:
        with mock.patch.object(
            toolchain_cache, "cache_path", side_effect=lambda base=None: self.tmp / "toolchains.json"
        ):
            toolchain_cache.save(
                {
                    "python": {
                        "id": "python", "name": "fake", "found": True, "path": None,
                        "version": None, "error": None, "install_hint": "",
                    }
                },
                base=self.tmp,
            )
            toolchains.refresh()
            cached = toolchain_cache.load(base=self.tmp)
            self.assertIsNotNone(cached)
            # The fake entry was replaced by a real probe.
            self.assertNotEqual(cached["python"].get("name"), "fake")


class CacheFreshnessTests(unittest.TestCase):
    def test_the_ttl_is_short_enough_to_notice_a_new_tool(self) -> None:
        # A newly installed tool must show up soon, not in a week.
        self.assertLessEqual(toolchain_cache.TTL_SECONDS, 3600)
        self.assertGreater(toolchain_cache.TTL_SECONDS, 0)

    def test_the_signature_changes_with_the_path(self) -> None:
        original = os.environ.get("PATH", "")
        first = toolchain_cache.path_signature()
        os.environ["PATH"] = original + os.pathsep + str(Path(tempfile.gettempdir()))
        self.addCleanup(os.environ.__setitem__, "PATH", original)
        self.assertNotEqual(first, toolchain_cache.path_signature())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
