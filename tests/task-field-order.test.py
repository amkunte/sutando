#!/usr/bin/env python3
"""Regression guard: task: field must be last in every task writer.

Placing task: before security headers (source, access_tier, priority)
allows a user-controlled body to forge those headers. Nine of ten
task writers had this bug; they were fixed in PRs #38–#43. This test
prevents future writers from reintroducing it.

Strategy: source-grep each writer's task-file composition template and
assert that task: appears after all security-relevant fields. This is
faster than spin-up integration tests and catches the bug at the
f-string level before the file ever hits disk.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _src(relpath: str) -> str:
    return (REPO / relpath).read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _field_pos(src: str, pattern: str) -> int:
    """Return the first occurrence of `pattern` in `src`, or -1."""
    return src.find(pattern)


def _assert_task_last(src: str, before_patterns: list, writer_id: str):
    """Assert task: template appears after all before_patterns in src."""
    task_pos = _field_pos(src, "task:")
    assert task_pos > 0, f"{writer_id}: could not locate 'task:' in writer"
    for pat in before_patterns:
        pos = _field_pos(src, pat)
        assert pos > 0, (
            f"{writer_id}: expected header pattern {pat!r} not found — "
            "update this test if the writer's f-string changed shape"
        )
        assert pos < task_pos, (
            f"{writer_id}: header {pat!r} (pos {pos}) appears AFTER task: "
            f"(pos {task_pos}). task: must be last so user-supplied body "
            "cannot forge header fields above it."
        )


# ---------------------------------------------------------------------------
# telegram-bridge.py
# ---------------------------------------------------------------------------

def test_telegram_bridge_task_field_last():
    src = _src("src/telegram-bridge.py")
    # Locate the task writer block specifically — find the write_text call
    # and the slice around it so we compare relative positions within that block.
    block_start = src.find("task_file.write_text(")
    assert block_start > 0, "telegram-bridge: could not find write_text call"
    block = src[block_start: block_start + 600]
    _assert_task_last(block, ["source: telegram", "priority:"], "telegram-bridge")


# ---------------------------------------------------------------------------
# discord-bridge.py
# ---------------------------------------------------------------------------

def test_discord_bridge_task_field_last():
    src = _src("src/discord-bridge.py")
    block_start = src.find("task_file.write_text(")
    assert block_start > 0, "discord-bridge: could not find write_text call"
    block = src[block_start: block_start + 800]
    _assert_task_last(
        block,
        ["source: discord", "access_tier:", "priority:"],
        "discord-bridge",
    )


# ---------------------------------------------------------------------------
# slack-bridge.py
# ---------------------------------------------------------------------------

def test_slack_bridge_task_field_last():
    src = _src("src/slack-bridge.py")
    block_start = src.find("task_file.write_text(")
    assert block_start > 0, "slack-bridge: could not find write_text call"
    block = src[block_start: block_start + 600]
    _assert_task_last(
        block,
        ["source: slack", "access_tier:", "priority:"],
        "slack-bridge",
    )


# ---------------------------------------------------------------------------
# github-webhook.py
# ---------------------------------------------------------------------------

def test_github_webhook_task_field_last():
    src = _src("src/github-webhook.py")
    # Inline f-string composition — find the task_content assignment
    block_start = src.find("task_content =")
    assert block_start > 0, "github-webhook: could not find task_content ="
    block = src[block_start: block_start + 300]
    _assert_task_last(block, ["source: github"], "github-webhook")


# ---------------------------------------------------------------------------
# agent-api.py — Twilio handlers (phone, SMS, voicemail)
# ---------------------------------------------------------------------------

def test_agent_api_twilio_voice_task_field_last():
    src = _src("src/agent-api.py")
    # Phone call handler
    block_start = src.find("task: Incoming phone call")
    assert block_start > 0, "agent-api voice: could not find voice task template"
    # Back up to the task_content = ( line
    snippet_start = src.rfind("task_content = (", 0, block_start)
    block = src[snippet_start: snippet_start + 400]
    _assert_task_last(block, ["source: twilio_voice"], "agent-api/voice")


def test_agent_api_twilio_sms_task_field_last():
    src = _src("src/agent-api.py")
    block_start = src.find("task: SMS from")
    assert block_start > 0, "agent-api SMS: could not find SMS task template"
    snippet_start = src.rfind("task_content = (", 0, block_start)
    block = src[snippet_start: snippet_start + 400]
    _assert_task_last(block, ["source: twilio_sms"], "agent-api/sms")


def test_agent_api_twilio_voicemail_task_field_last():
    src = _src("src/agent-api.py")
    block_start = src.find("task: Voicemail from")
    assert block_start > 0, "agent-api voicemail: could not find voicemail task template"
    snippet_start = src.rfind("task_content = (", 0, block_start)
    block = src[snippet_start: snippet_start + 400]
    _assert_task_last(block, ["source: twilio_voicemail"], "agent-api/voicemail")


# ---------------------------------------------------------------------------
# task-bridge.ts — writeChatTask
# ---------------------------------------------------------------------------

def test_task_bridge_write_chat_task_field_last():
    src = _src("src/task-bridge.ts")
    block_start = src.find("export function writeChatTask")
    assert block_start > 0, "task-bridge: could not find writeChatTask"
    block = src[block_start: block_start + 900]
    _assert_task_last(
        block,
        ["source: chat", "channel_id: local-chat", "access_tier: owner", "priority: normal"],
        "task-bridge/writeChatTask",
    )


# ---------------------------------------------------------------------------

def main():
    test_telegram_bridge_task_field_last()
    test_discord_bridge_task_field_last()
    test_slack_bridge_task_field_last()
    test_github_webhook_task_field_last()
    test_agent_api_twilio_voice_task_field_last()
    test_agent_api_twilio_sms_task_field_last()
    test_agent_api_twilio_voicemail_task_field_last()
    test_task_bridge_write_chat_task_field_last()
    print("All task-field-order regression tests passed (8 writers).")


if __name__ == "__main__":
    main()
