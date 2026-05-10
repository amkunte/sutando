#!/bin/bash
# Render text to speech via Gemini 3.1 Flash TTS (free tier).
# Usage: synthesize.sh [--voice <name>] [--out <path>] -- "text"
set -euo pipefail

VOICE="Kore"
OUT=""
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --voice) VOICE="$2"; shift 2 ;;
    --out)   OUT="$2"; shift 2 ;;
    --) shift; ARGS+=("$@"); break ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

TEXT="${ARGS[*]-}"
[[ -n "$TEXT" ]] || { echo "Usage: synthesize.sh [--voice <name>] [--out <path>] -- \"text\"" >&2; exit 2; }

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
KEY="${GEMINI_API_KEY:-$(grep -E '^GEMINI_API_KEY=' "$REPO/.env" 2>/dev/null | cut -d= -f2-)}"
[[ -n "$KEY" ]] || { echo "GEMINI_API_KEY missing" >&2; exit 1; }

[[ -n "$OUT" ]] || OUT="$REPO/results/gemini-tts-$(date +%s).wav"
mkdir -p "$(dirname "$OUT")"

# Build JSON payload
PAYLOAD=$(python3 -c '
import json, sys
print(json.dumps({
    "contents": [{"parts": [{"text": sys.argv[1]}]}],
    "generationConfig": {
        "responseModalities": ["AUDIO"],
        "speechConfig": {
            "voiceConfig": {
                "prebuiltVoiceConfig": {"voiceName": sys.argv[2]}
            }
        }
    }
}))
' "$TEXT" "$VOICE")

# Call Gemini TTS API
RESPONSE=$(curl -sSf \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-tts-preview:generateContent?key=$KEY" \
  -X POST \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

# Extract base64 audio and decode to WAV
echo "$RESPONSE" | python3 -c '
import json, sys, struct

data = json.load(sys.stdin)
audio_b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]

import base64
pcm = base64.b64decode(audio_b64)

# Write WAV header (24kHz, mono, 16-bit PCM)
out = sys.argv[1]
with open(out, "wb") as f:
    num_samples = len(pcm) // 2
    sample_rate = 24000
    bits = 16
    channels = 1
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    data_size = len(pcm)
    # RIFF header
    f.write(b"RIFF")
    f.write(struct.pack("<I", 36 + data_size))
    f.write(b"WAVE")
    # fmt chunk
    f.write(b"fmt ")
    f.write(struct.pack("<I", 16))
    f.write(struct.pack("<HHIIHH", 1, channels, sample_rate, byte_rate, block_align, bits))
    # data chunk
    f.write(b"data")
    f.write(struct.pack("<I", data_size))
    f.write(pcm)
' "$OUT"

echo "$OUT"
