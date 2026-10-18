def _add_src() -> None:
    import sys
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()
