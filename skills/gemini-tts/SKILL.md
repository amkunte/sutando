---
name: gemini-tts
description: "Render text to WAV via Gemini 3.1 Flash TTS (free tier). Use for video narration, demo voiceovers, audio notes."
user-invocable: true
---

# Gemini TTS

Synthesize speech via Gemini 3.1 Flash TTS. Reads `GEMINI_API_KEY` from `.env`. Free within 1500 req/day quota.

This is offline synthesis — distinct from voice-agent's bidirectional Gemini Live audio.

**Usage**: `/gemini-tts [text]`

ARGUMENTS: $ARGUMENTS

## Voices

`Kore` (default), `Puck`, `Charon`, `Fenrir`, `Aoede`, `Leda`, `Orus`, `Zephyr`.

Use audio tags in text for expression: `[whispers]`, `[excitedly]`, `[slowly]`.

## Examples

```bash
bash "$SKILL_DIR/scripts/synthesize.sh" -- "Hello, this is Sutando."
bash "$SKILL_DIR/scripts/synthesize.sh" --voice Puck --out /tmp/intro.wav -- "Welcome to the demo."
```

Default output: `results/gemini-tts-{epoch}.wav`. Cost: $0 (free tier, 1500 req/day).

## If Invoked As A Slash Command

If ARGUMENTS is empty, ask the user for the text. Otherwise:

```bash
bash "$SKILL_DIR/scripts/synthesize.sh" -- "$ARGUMENTS"
```
