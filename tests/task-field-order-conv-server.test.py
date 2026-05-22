#!/usr/bin/env python3
"""Regression guard: task: field must be last in conversation-server.ts task writers.

Companion to tests/task-field-order.test.py (which covers Python bridges
and task-bridge.ts). This file covers the TypeScript conversation-server
writers that were missed in the original sweep.

Writers covered:
  - /twilio/sms handler (fixed in this PR — was field-order wrong + raw interpolation)
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _src(relpath: str) -> str:
    return (REPO / relpath).read_text()


def _assert_task_last(src: str, before_patterns: list, writer_id: str):
    task_pos = src.find("task:")
    assert task_pos > 0, f"{writer_id}: could not locate 'task:' in writer block"
    for pat in before_patterns:
        pos = src.find(pat)
        assert pos > 0, (
            f"{writer_id}: expected header pattern {pat!r} not found — "
            "update this test if the writer's template changed shape"
        )
        assert pos < task_pos, (
            f"{writer_id}: header {pat!r} (pos {pos}) appears AFTER task: "
            f"(pos {task_pos}). task: must be last so user-supplied body "
            "cannot forge header fields above it."
        )


def test_conv_server_sms_task_field_last():
    src = _src("skills/phone-conversation/scripts/conversation-server.ts")
    # Anchor on the taskContent assignment — not the handler block, which
    # contains a comment mentioning "task:" and would trip the position check.
    marker = "task: SMS from"
    marker_pos = src.find(marker)
    assert marker_pos > 0, "conv-server: could not find SMS task template"
    snippet_start = src.rfind("const taskContent", 0, marker_pos)
    assert snippet_start > 0, "conv-server: could not find taskContent assignment"
    block = src[snippet_start: snippet_start + 400]
    _assert_task_last(
        block,
        ["source: twilio_sms", "access_tier: owner", "priority: normal"],
        "conv-server/twilio-sms",
    )


def test_conv_server_sms_newline_sanitized():
    src = _src("skills/phone-conversation/scripts/conversation-server.ts")
    handler_start = src.find("path === '/twilio/sms'")
    assert handler_start > 0, "conv-server: could not find /twilio/sms handler"
    block = src[handler_start: handler_start + 800]
    # Verify newline collapse is applied to user-controlled fields
    assert r"replace(/[\r\n]+/g, ' ')" in block, (
        "conv-server/twilio-sms: sender and body must have newlines collapsed "
        "before interpolation into the task file header block"
    )


def main():
    test_conv_server_sms_task_field_last()
    test_conv_server_sms_newline_sanitized()
    print("All conv-server task-field-order regression tests passed (2 checks).")


if __name__ == "__main__":
    main()
