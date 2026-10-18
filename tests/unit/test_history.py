import sys
import tempfile
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.build import history  # noqa: E402


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())

    def test_append_and_load(self) -> None:
        history.append(
            history.make_record(Path("/tmp/p"), ok=True, exit_code=0, message="done"),
            base=self.base,
        )
        records = history.load(base=self.base)
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].ok)
        self.assertEqual(records[0].message, "done")

    def test_load_empty(self) -> None:
        self.assertEqual(history.load(base=self.base), [])

    def test_limit_returns_newest(self) -> None:
        for i in range(5):
            history.append(
                history.make_record(Path(f"/tmp/p{i}"), ok=bool(i % 2), message=str(i)),
                base=self.base,
            )
        records = history.load(base=self.base, limit=2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1].message, "4")

    def test_corrupt_line_skipped(self) -> None:
        history.append(history.make_record(Path("/tmp/p"), ok=True), base=self.base)
        with open(self.base / "history.jsonl", "a", encoding="utf-8") as fh:
            fh.write("{ this is not json\n")
        history.append(history.make_record(Path("/tmp/q"), ok=False), base=self.base)
        records = history.load(base=self.base)
        self.assertEqual(len(records), 2)

    def test_clear(self) -> None:
        history.append(history.make_record(Path("/tmp/p"), ok=True), base=self.base)
        history.clear(base=self.base)
        self.assertEqual(history.load(base=self.base), [])


if __name__ == "__main__":
    unittest.main()
