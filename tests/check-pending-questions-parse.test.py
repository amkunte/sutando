"""Regression: get_waiting_questions() must not phantom-count the canonical
empty template (`## Active` / `_(none)_` + footer) and must skip RESOLVED
headers, while still detecting genuine waiting questions. (PR: parser hardening)"""
import importlib.util, pathlib, sys

_spec = importlib.util.spec_from_file_location(
    "cpq", str(pathlib.Path(__file__).resolve().parent.parent / "src" / "check-pending-questions.py")
)
cpq = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(cpq)
except SystemExit:
    pass


def _count(tmp_path, content):
    f = tmp_path / "pending-questions.md"
    f.write_text(content)
    cpq.PQ_FILE = f
    return [q["title"] for q in cpq.get_waiting_questions()]


def test_canonical_empty_template_is_zero(tmp_path):
    content = (
        "# Pending Questions\n\n## Active\n\n_(none)_\n\n---\n\n"
        "Resolved questions are not archived here — see git history.\n"
    )
    assert _count(tmp_path, content) == []


def test_resolved_struck_header_skipped(tmp_path):
    content = "# PQ\n## ~~Verify PNR 8OE3AL~~ — RESOLVED 2026-06-03\nwas a past trip\n"
    assert _count(tmp_path, content) == []


def test_real_freeform_question_detected(tmp_path):
    content = "# PQ\n## Should I buy the PC12?\nNeed your call on financing.\n"
    assert _count(tmp_path, content) == ["Should I buy the PC12?"]


def test_status_waiting_detected_and_resolved_skipped(tmp_path):
    waiting = "# PQ\n## Q1 — Deploy?\n- **Status:** unanswered\nbody\n"
    resolved = "# PQ\n## Q2 — Old\n- **Status:** resolved\nbody\n"
    assert _count(tmp_path, waiting) == ["Q1 — Deploy?"]
    assert _count(tmp_path, resolved) == []
