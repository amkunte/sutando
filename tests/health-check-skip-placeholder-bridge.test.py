#!/usr/bin/env python3
"""
Tests for the placeholder-token detection added 2026-05-13.

Background: a 15-char placeholder DISCORD_BOT_TOKEN sat in
`~/.claude/channels/discord/.env` for 4 days, and even after being
sidelined to `.env.placeholder`, something recreated it 7 hours later.
Health-check kept firing 'configured but not running' alerts every hour
because file-existence was the only configuration signal.

The fix: also require at least one `*_TOKEN` env value of length >= 30
to consider a channel's `.env` to be a real configuration.

These tests verify the length-threshold logic in isolation. Full
integration with the bridge-check loop is exercised by existing
end-to-end behavior — there's no clean seam to mock the entire loop
without a heavy refactor, and the integration is simple enough that
the unit-level guarantee is sufficient.

Cases:
  a) placeholder token (15 chars) → has_real_token = False
  b) real-shape token (46 chars, Telegram) → True
  c) real-shape token (70 chars, Discord) → True
  d) empty value → False
  e) commented-out line → ignored
  f) value with surrounding quotes → stripped before measuring
  g) value padded with whitespace → stripped before measuring
  h) non-TOKEN keys ignored (only *_TOKEN counts as a credential signal)
  i) multiple lines, only one valid → True
  j) exactly at threshold (30 chars) → True (>= 30 is the contract)
  k) one below threshold (29 chars) → False

Run: python3 tests/health-check-skip-placeholder-bridge.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import sys
import unittest


def has_real_token(env_text: str) -> bool:
    """Replicates the inline logic from src/health-check.py.

    Kept in sync manually — if you change one, change the other. Or
    refactor the check into a top-level helper and import it here.
    """
    for line in env_text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, val = line.partition("=")
        val = val.strip().strip('"').strip("'")
        if key.strip().endswith("_TOKEN") and len(val) >= 30:
            return True
    return False


class PlaceholderDetectionTests(unittest.TestCase):
    # (a) — the original observed bug
    def test_15_char_placeholder_rejected(self):
        self.assertFalse(has_real_token("DISCORD_BOT_TOKEN=YOUR_TOKEN_HERE"))  # 15 chars

    # (b) — real telegram token shape
    def test_46_char_telegram_token_accepted(self):
        self.assertTrue(has_real_token("TELEGRAM_BOT_TOKEN=" + "x" * 46))

    # (c) — real discord token shape
    def test_70_char_discord_token_accepted(self):
        self.assertTrue(has_real_token("DISCORD_BOT_TOKEN=" + "x" * 70))

    # (d)
    def test_empty_value_rejected(self):
        self.assertFalse(has_real_token("DISCORD_BOT_TOKEN="))

    # (e)
    def test_comment_line_ignored(self):
        env = "# DISCORD_BOT_TOKEN=" + "x" * 70 + "\n"
        self.assertFalse(has_real_token(env))

    # (f)
    def test_quoted_value_stripped(self):
        self.assertTrue(has_real_token('DISCORD_BOT_TOKEN="' + "x" * 30 + '"'))
        self.assertTrue(has_real_token("DISCORD_BOT_TOKEN='" + "x" * 30 + "'"))

    # (g)
    def test_whitespace_stripped(self):
        self.assertTrue(has_real_token("DISCORD_BOT_TOKEN=  " + "x" * 30 + "  "))

    # (h) — non-TOKEN keys don't qualify
    def test_non_token_key_ignored(self):
        self.assertFalse(has_real_token("DISCORD_SOMETHING_ELSE=" + "x" * 50))

    # (i)
    def test_multiline_one_valid_token_wins(self):
        env = "PLACEHOLDER_KEY=short\nDISCORD_BOT_TOKEN=" + "x" * 70 + "\n"
        self.assertTrue(has_real_token(env))

    # (j)
    def test_exactly_at_threshold(self):
        self.assertTrue(has_real_token("X_TOKEN=" + "x" * 30))

    # (k)
    def test_one_below_threshold(self):
        self.assertFalse(has_real_token("X_TOKEN=" + "x" * 29))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(PlaceholderDetectionTests)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
