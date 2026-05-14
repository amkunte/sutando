#!/usr/bin/env python3
"""
Tests for the send_reply() bool-return contract in
src/telegram-bridge.py, added 2026-05-13.

Background: previously send_reply() was a void function that called
api("sendMessage", ...) for each chunk + send_file() for each
attachment, but never inspected return values. The caller in the
result-delivery loop archived the result+task files unconditionally,
so any transient Telegram failure (429 rate-limit, 502 gateway, network
blip mid-poll) silently lost the owner's reply.

The fix:
  - send_reply() now returns True iff every api() and send_file() call
    returned {"ok": True}.
  - The delivery loop in main() gates archival on this bool: True →
    pop pending_replies + archive files; False AND under retry-cap →
    leave files in place for next iteration; False AND at retry-cap →
    log loudly, append [DELIVERY-FAILED:N] marker, archive.

This file tests the send_reply contract in isolation by mocking api()
and send_file(). Pattern duplicates the function rather than importing
because telegram-bridge.py runs main() loop at import time.

Run: python3 tests/telegram-bridge-send-reply-bool.test.py
Exit 0 on pass, 1 on fail.
"""

from __future__ import annotations
import os
import re
import tempfile
import unittest
from typing import Any


def send_reply(
    chat_id,
    text,
    *,
    api,
    send_file,
    is_path_sendable,
) -> bool:
    """Mirror of src/telegram-bridge.py:send_reply (line 310, after the
    PR that adds the bool return). Kept in sync manually.

    Dependencies are injected (api, send_file, is_path_sendable) so the
    test can exercise success / failure / exception paths without
    network calls.
    """
    file_pattern = re.compile(r'\[(?:file|send|attach):\s*([^\]]+)\]')
    files = file_pattern.findall(text)
    clean_text = file_pattern.sub('', text).strip()

    all_ok = True

    if clean_text:
        for i in range(0, len(clean_text), 4000):
            try:
                resp = api("sendMessage", chat_id=chat_id, text=clean_text[i:i+4000])
            except Exception:
                all_ok = False
                continue
            if not (isinstance(resp, dict) and resp.get("ok")):
                all_ok = False

    for fpath in files:
        fpath = fpath.strip()
        if is_path_sendable(fpath):
            try:
                resp = send_file(chat_id, fpath)
            except Exception:
                all_ok = False
                continue
            if not (isinstance(resp, dict) and resp.get("ok")):
                all_ok = False
        elif os.path.isfile(fpath):
            try:
                resp = api("sendMessage", chat_id=chat_id, text=f"(file access denied: {fpath})")
                if not (isinstance(resp, dict) and resp.get("ok")):
                    all_ok = False
            except Exception:
                all_ok = False
        else:
            try:
                resp = api("sendMessage", chat_id=chat_id, text=f"(file not found: {fpath})")
                if not (isinstance(resp, dict) and resp.get("ok")):
                    all_ok = False
            except Exception:
                all_ok = False

    return all_ok


class MockApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if not self.responses:
            return {"ok": True}
        return self.responses.pop(0)


class MockSendFile:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, chat_id, fpath, caption=""):
        self.calls.append((chat_id, fpath))
        if not self.responses:
            return {"ok": True}
        return self.responses.pop(0)


class SendReplyContractTests(unittest.TestCase):

    def test_happy_path_returns_true(self):
        api = MockApi([{"ok": True}])
        result = send_reply(
            123, "hello world",
            api=api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertTrue(result)
        self.assertEqual(len(api.calls), 1)

    def test_api_returns_ok_false(self):
        api = MockApi([{"ok": False}])
        result = send_reply(
            123, "hello",
            api=api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertFalse(result)

    def test_api_returns_non_dict(self):
        # Defensive: if api() returns something unexpected (None, string,
        # malformed), treat as failure.
        api = MockApi([None])
        result = send_reply(
            123, "hello",
            api=api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertFalse(result)

    def test_api_raises_exception(self):
        def bad_api(*a, **k):
            raise ConnectionError("network blip")

        result = send_reply(
            123, "hello",
            api=bad_api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertFalse(result)

    def test_chunking_all_succeed(self):
        # 12000-char text → 3 chunks of 4000 each
        big = "x" * 12000
        api = MockApi([{"ok": True}] * 3)
        result = send_reply(
            123, big,
            api=api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertTrue(result)
        self.assertEqual(len(api.calls), 3)

    def test_chunking_middle_fails(self):
        # Middle chunk fails — overall False, but all 3 chunks should still
        # be attempted (we don't short-circuit on first failure).
        big = "x" * 12000
        api = MockApi([{"ok": True}, {"ok": False}, {"ok": True}])
        result = send_reply(
            123, big,
            api=api,
            send_file=lambda *a, **k: {"ok": True},
            is_path_sendable=lambda p: False,
        )
        self.assertFalse(result)
        self.assertEqual(len(api.calls), 3)

    def test_file_send_succeeds(self):
        # File-attachment-only message (no text body after extraction).
        with tempfile.NamedTemporaryFile(delete=False) as tmpf:
            tmpf.write(b"test content")
            tmp_path = tmpf.name
        try:
            sf = MockSendFile([{"ok": True}])
            result = send_reply(
                123, f"[file: {tmp_path}]",
                api=MockApi([]),
                send_file=sf,
                is_path_sendable=lambda p: True,
            )
            self.assertTrue(result)
            self.assertEqual(len(sf.calls), 1)
        finally:
            os.unlink(tmp_path)

    def test_file_send_fails(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmpf:
            tmpf.write(b"test")
            tmp_path = tmpf.name
        try:
            sf = MockSendFile([{"ok": False}])
            result = send_reply(
                123, f"[file: {tmp_path}]",
                api=MockApi([]),
                send_file=sf,
                is_path_sendable=lambda p: True,
            )
            self.assertFalse(result)
        finally:
            os.unlink(tmp_path)

    def test_text_plus_file_partial_failure(self):
        # Text succeeds, file fails → False overall.
        with tempfile.NamedTemporaryFile(delete=False) as tmpf:
            tmpf.write(b"test")
            tmp_path = tmpf.name
        try:
            api = MockApi([{"ok": True}])
            sf = MockSendFile([{"ok": False}])
            result = send_reply(
                123, f"see attached [file: {tmp_path}]",
                api=api,
                send_file=sf,
                is_path_sendable=lambda p: True,
            )
            self.assertFalse(result)
            # Both still attempted.
            self.assertEqual(len(api.calls), 1)
            self.assertEqual(len(sf.calls), 1)
        finally:
            os.unlink(tmp_path)

    def test_blocked_file_path_sends_notice(self):
        # File exists but is_path_sendable=False → bridge sends a "access
        # denied" notice instead of the file. That notice is a regular
        # api("sendMessage") and counts toward all_ok.
        with tempfile.NamedTemporaryFile(delete=False) as tmpf:
            tmpf.write(b"test")
            tmp_path = tmpf.name
        try:
            api = MockApi([{"ok": True}])
            result = send_reply(
                123, f"[file: {tmp_path}]",
                api=api,
                send_file=lambda *a, **k: {"ok": True},  # not called
                is_path_sendable=lambda p: False,
            )
            self.assertTrue(result)
            # Sent the "access denied" notice.
            self.assertEqual(len(api.calls), 1)
        finally:
            os.unlink(tmp_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
