"""WCAG 2.1 contrast checks for the dark palette (no display needed).

WCAG AA requires >= 4.5:1 for normal-size text. Every pair tested here is a
pair the UI actually renders (a foreground colour on its real surface), so a
palette change that would make text unreadable fails CI before it reaches the
screen. Values are computed from the hex tokens in ``theme.COLORS`` with the
official relative-luminance formula.
"""

import sys
import unittest
from pathlib import Path


def _add_src() -> None:
    root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


_add_src()

from ece_autocompiler.ui.state import STATE_UI, UiState  # noqa: E402
from ece_autocompiler.ui.theme import COLORS  # noqa: E402

AA = 4.5

#: Surfaces that carry text (the large painted areas of the UI).
SURFACES = ("bg", "panel", "panel_alt", "header", "console")

#: Colours used for text/icons drawn *on* those surfaces.
TEXT_COLORS = ("fg", "muted", "accent", "ok", "warn", "err")


def _channel(value: int) -> float:
    c = value / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    """WCAG relative luminance of an #rrggbb colour."""
    raw = hex_color.lstrip("#")
    if len(raw) != 6:
        raise ValueError(f"expected #rrggbb, got {hex_color!r}")
    r, g, b = (int(raw[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1.0 .. 21.0)."""
    la, lb = luminance(a), luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


class TextOnSurfaceTests(unittest.TestCase):
    """Body text, muted text and status colours on every painted surface."""

    def test_text_colors_meet_aa_on_every_surface(self) -> None:
        for fg_name in TEXT_COLORS:
            for bg_name in SURFACES:
                with self.subTest(fg=fg_name, bg=bg_name):
                    ratio = contrast(COLORS[fg_name], COLORS[bg_name])
                    self.assertGreaterEqual(
                        ratio, AA,
                        f"{fg_name} ({COLORS[fg_name]}) on {bg_name} "
                        f"({COLORS[bg_name]}) = {ratio:.2f}:1 < {AA}:1",
                    )

    def test_all_text_tokens_are_well_formed(self) -> None:
        for name in (*SURFACES, *TEXT_COLORS):
            with self.subTest(token=name):
                self.assertRegex(COLORS[name], r"^#[0-9a-fA-F]{6}$")


class FilledSurfaceTests(unittest.TestCase):
    """Text painted on filled pills/buttons/badges (inverse text)."""

    def test_white_on_accent_fills_meets_aa(self) -> None:
        # Accent buttons/chips/busy badge carry white text.
        for name in ("accent_fill", "accent_fill_active", "selection"):
            with self.subTest(fill=name):
                ratio = contrast("#ffffff", COLORS[name])
                self.assertGreaterEqual(
                    ratio, AA, f"white on {name} ({COLORS[name]}) = {ratio:.2f}:1"
                )

    def test_dark_badge_text_meets_aa(self) -> None:
        # The ok/warn/err state pills use dark text (white would fail AA).
        for fg, bg in (("on_ok", "ok"), ("on_warn", "warn"), ("on_err", "err")):
            with self.subTest(fg=fg, bg=bg):
                ratio = contrast(COLORS[fg], COLORS[bg])
                self.assertGreaterEqual(
                    ratio, AA,
                    f"{fg} ({COLORS[fg]}) on {bg} ({COLORS[bg]}) = {ratio:.2f}:1",
                )

    def test_badge_text_colours_differ_from_their_fills(self) -> None:
        # A pill whose "text" equals its background would be invisible.
        for fg, bg in (("on_ok", "ok"), ("on_warn", "warn"), ("on_err", "err")):
            self.assertNotEqual(COLORS[fg], COLORS[bg])


class StateStyleContractTests(unittest.TestCase):
    """state.py and theme.py must stay in sync (data-level checks only)."""

    def test_every_state_has_exactly_one_spec(self) -> None:
        self.assertEqual(set(STATE_UI.keys()), set(UiState))
        self.assertEqual(len(STATE_UI), len(UiState))

    def test_compile_disabled_only_while_compiling(self) -> None:
        for state, spec in STATE_UI.items():
            with self.subTest(state=state.value):
                self.assertIn(spec.build_state, ("normal", "disabled"))
                expected = "disabled" if state is UiState.COMPILING else "normal"
                self.assertEqual(spec.build_state, expected)

    def test_cancel_enabled_only_while_compiling(self) -> None:
        for state, spec in STATE_UI.items():
            with self.subTest(state=state.value):
                expected = "normal" if state is UiState.COMPILING else "disabled"
                self.assertEqual(spec.cancel_state, expected)

    def test_badge_texts_are_unique_and_short(self) -> None:
        texts = [spec.badge_text for spec in STATE_UI.values()]
        self.assertEqual(len(texts), len(set(texts)), "badge texts must be unique")
        for text in texts:
            self.assertTrue(0 < len(text) <= 12, f"badge too long: {text!r}")

    def test_badge_styles_follow_the_theme_naming(self) -> None:
        # theme.py configures styles named State*.TLabel; smoke tests verify
        # they resolve to the right colours on a real Tk instance.
        for state, spec in STATE_UI.items():
            with self.subTest(state=state.value):
                self.assertRegex(spec.badge_style, r"^State[A-Za-z]+\.TLabel$")

    def test_log_tags_are_valid_console_tags(self) -> None:
        valid = {"info", "ok", "warn", "err", "muted", "accent"}
        for state, spec in STATE_UI.items():
            with self.subTest(state=state.value):
                self.assertIn(spec.log_tag, valid)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
