#!/usr/bin/env python3
"""Local, offline transcription via faster-whisper — no API key required.

Runs a Whisper model on-device (CPU or GPU via CTranslate2). The first call
for a given model size downloads model weights from Hugging Face Hub (~500 MB
for "small"); after that the weights are cached locally and every subsequent
run is fully offline. Only the model weights are ever fetched over the
network — the audio itself never leaves the machine.

Segments come back in the same {start, end, text} shape whisper.py produces,
so the rest of the pipeline (filter_range, format_transcript) doesn't care
which backend produced them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from whisper import extract_audio  # noqa: E402 — reuse ffmpeg audio extraction


DEFAULT_MODEL = "small"


def is_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe_video_local(
    video_path: str,
    audio_out: Path,
    model_size: str = DEFAULT_MODEL,
) -> tuple[list[dict], str]:
    """Extract audio, transcribe locally with faster-whisper, return segments.

    Returns (segments, label). Raises SystemExit on failure.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "faster-whisper is not installed. Install with: pip install faster-whisper"
        )

    print(f"[watch] extracting audio for local Whisper ({model_size})…", file=sys.stderr)
    audio_path = extract_audio(video_path, audio_out)

    print(
        f"[watch] transcribing locally with faster-whisper ({model_size}) — "
        "first run may download model weights…",
        file=sys.stderr,
    )
    try:
        model = WhisperModel(model_size, device="auto", compute_type="int8")
        raw_segments, _info = model.transcribe(str(audio_path))
    except Exception as exc:
        raise SystemExit(f"local Whisper transcription failed: {exc}")

    segments: list[dict] = []
    for seg in raw_segments:
        text = (seg.text or "").strip()
        if not text:
            continue
        segments.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": text,
        })

    if not segments:
        raise SystemExit("local Whisper returned no transcript segments")

    print(f"[watch] transcribed {len(segments)} segments locally (faster-whisper)", file=sys.stderr)
    return segments, f"local ({model_size}, faster-whisper)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: local_whisper.py <video-path> [<audio-out.mp3>] [--model small]", file=sys.stderr)
        raise SystemExit(2)

    video = sys.argv[1]
    audio_out = Path(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else Path("audio.mp3")
    model_size = DEFAULT_MODEL
    if "--model" in sys.argv:
        model_size = sys.argv[sys.argv.index("--model") + 1]

    segments, label = transcribe_video_local(video, audio_out, model_size=model_size)
    print(json.dumps({"backend": label, "segments": segments}, indent=2))
