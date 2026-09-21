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


def _strip_comments(src: str) -> str:
    """Drop whole-line `#` comments before position matching.

    Upstream prose legitimately mentions the field names this test locates
    (e.g. "Must stay above `task:`"), and a bare find() hits the comment
    before the real f-string field — reporting a field-order defect that is
    not there. Only emitted fields should count.
    """
    return "\n".join(
        l for l in src.split("\n") if not l.lstrip().startswith("#")
    )


def _assert_task_last(src: str, before_patterns: list, writer_id: str):
    """Assert task: template appears after all before_patterns in src."""
    src = _strip_comments(src)
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
    # Anchor on the CONTENT assignment, not write_text: the body is built into
    # _task_content (and HMAC-stamped) before it is written, so a window after
    # write_text() no longer contains the fields. Same shape as the other writers.
    block_start = src.find("_task_content = (")
    if block_start < 0:
        block_start = src.find("task_file.write_text(")
    assert block_start > 0, "telegram-bridge: could not find the task-content writer"
    block = src[block_start: block_start + 1200]
    _assert_task_last(block, ["source: telegram", "priority:"], "telegram-bridge")


# ---------------------------------------------------------------------------
# discord-bridge.py
# ---------------------------------------------------------------------------

def test_discord_bridge_task_field_last():
    src = _src("src/discord-bridge.py")
    # The 2026-07-23 upstream merge refactored this writer: the task-content
    # f-string moved out of an inline `task_file.write_text(...)` and into
    # `_build_task_content()`, which `_write_task_file()` invokes inside its
    # try (so a build failure is logged rather than raised). Anchor on the
    # builder; fall back to the legacy inline form so this guard still works
    # against a pre-refactor writer.
    # Slice the builder's actual `return (` block rather than a character window:
    # upstream grew the leading comments past the old 1800-char budget, pushing
    # priority:/task: outside it. A budget that must be re-tuned whenever a
    # comment grows is not an invariant.
    block_start = src.find("def _build_task_content")
    if block_start >= 0:
        _ret = src.find("return (", block_start)
        if _ret > 0:
            block_start = _ret
    if block_start < 0:
        block_start = src.find("task_file.write_text(")
    assert block_start > 0, "discord-bridge: could not find task-content builder"
    # Wider window than the other writers: the builder carries a docstring and
    # the collaborator/rulebook preamble before the f-string proper.
    block = src[block_start: block_start + 1800]
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
    # The 2026-07-23 upstream merge routed the write through
    # `_write_routed_task(task_file, task_content, ...)`, so the first
    # `task_file.write_text(` in this file is now that generic helper writing an
    # already-built string — not the f-string we need to inspect. Anchor on the
    # header literal itself, which is stable across both shapes.
    block_start = src.find('f"source: slack')
    if block_start < 0:
        block_start = src.find("task_file.write_text(")
    assert block_start > 0, "slack-bridge: could not find task-content builder"
    block = src[block_start: block_start + 1200]
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
