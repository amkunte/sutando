#!/usr/bin/env python3
"""Regression guard: Twilio HMAC-SHA1 webhook signature validation.

Verifies that conversation-server.ts:
1. Imports the necessary crypto primitives (createHmac, timingSafeEqual)
2. Defines validateTwilioSignature() and calls it for /twilio/* POST paths
3. Reads the X-Twilio-Signature header
4. Buffers the body once and routes via getTwilioBody() (no double-consume)
5. All three Twilio POST handlers use getTwilioBody() instead of readBody(req)

Strategy: source-grep the TypeScript file. Matches the pattern used by
tests/task-field-order.test.py — no server spin-up needed.
"""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "skills/phone-conversation/scripts/conversation-server.ts"


def _src() -> str:
    return SRC.read_text()


# ---------------------------------------------------------------------------
# 1. Crypto imports
# ---------------------------------------------------------------------------

def test_crypto_imports():
    src = _src()
    assert "createHmac" in src, "createHmac must be imported from node:crypto"
    assert "timingSafeEqual" in src, "timingSafeEqual must be imported from node:crypto"
    assert "from 'node:crypto'" in src, "crypto imports must come from node:crypto"


# ---------------------------------------------------------------------------
# 2. validateTwilioSignature function defined
# ---------------------------------------------------------------------------

def test_validate_function_defined():
    src = _src()
    assert "function validateTwilioSignature(" in src, (
        "validateTwilioSignature must be defined — missing the HMAC validation function"
    )


def test_validate_reads_signature_header():
    src = _src()
    func_start = src.find("function validateTwilioSignature(")
    assert func_start > 0
    func_body = src[func_start: func_start + 1200]
    assert "x-twilio-signature" in func_body, (
        "validateTwilioSignature must read the x-twilio-signature header "
        "(lowercase — Node.js normalises headers)"
    )


def test_validate_uses_timing_safe_equal():
    src = _src()
    func_start = src.find("function validateTwilioSignature(")
    assert func_start > 0
    func_body = src[func_start: func_start + 1200]
    assert "timingSafeEqual" in func_body, (
        "validateTwilioSignature must use timingSafeEqual for comparison — "
        "string equality leaks timing information"
    )


def test_validate_uses_hmac_sha1():
    src = _src()
    func_start = src.find("function validateTwilioSignature(")
    assert func_start > 0
    func_body = src[func_start: func_start + 1200]
    assert "createHmac('sha1'" in func_body or 'createHmac("sha1"' in func_body, (
        "validateTwilioSignature must use HMAC-SHA1 (Twilio's algorithm)"
    )


# ---------------------------------------------------------------------------
# 3. Validation gate is called before routing
# ---------------------------------------------------------------------------

def test_validation_called_in_server_handler():
    src = _src()
    # The validation gate must appear before the try { if (path === '/health') block.
    # Anchor: look for the _twilioBody buffer + validateTwilioSignature call together.
    gate_pattern = "validateTwilioSignature(req, _twilioBody)"
    assert gate_pattern in src, (
        "The server handler must call validateTwilioSignature(req, _twilioBody) "
        "before routing — missing the pre-route validation gate"
    )
    # Ensure it rejects on failure (403)
    gate_pos = src.find(gate_pattern)
    gate_block = src[gate_pos: gate_pos + 200]
    assert "403" in gate_block, (
        "Failed signature validation must respond with 403 — "
        "found the call but not the rejection response"
    )


# ---------------------------------------------------------------------------
# 4. Twilio handlers use getTwilioBody() — no double-consume of the stream
# ---------------------------------------------------------------------------

def test_twilio_connect_uses_buffered_body():
    src = _src()
    block_start = src.find("path === '/twilio/connect'")
    assert block_start > 0, "could not find /twilio/connect handler"
    block = src[block_start: block_start + 200]
    assert "getTwilioBody()" in block, (
        "/twilio/connect must use getTwilioBody() — calling readBody(req) again "
        "would consume an already-drained stream and return empty string"
    )


def test_twilio_sms_uses_buffered_body():
    src = _src()
    block_start = src.find("path === '/twilio/sms'")
    assert block_start > 0, "could not find /twilio/sms handler"
    block = src[block_start: block_start + 300]
    assert "getTwilioBody()" in block, (
        "/twilio/sms must use getTwilioBody() — not readBody(req)"
    )


def test_twilio_status_uses_buffered_body():
    src = _src()
    block_start = src.find("path === '/twilio/status'")
    assert block_start > 0, "could not find /twilio/status handler"
    block = src[block_start: block_start + 200]
    assert "getTwilioBody()" in block, (
        "/twilio/status must use getTwilioBody() — not readBody(req)"
    )


# ---------------------------------------------------------------------------

def main():
    test_crypto_imports()
    test_validate_function_defined()
    test_validate_reads_signature_header()
    test_validate_uses_timing_safe_equal()
    test_validate_uses_hmac_sha1()
    test_validation_called_in_server_handler()
    test_twilio_connect_uses_buffered_body()
    test_twilio_sms_uses_buffered_body()
    test_twilio_status_uses_buffered_body()
    print("All Twilio HMAC regression tests passed (9 checks).")


if __name__ == "__main__":
    main()
