"""Local faster-whisper with word times, cached per file (SOP-11c: transcribe locally, never send footage out for it)."""
import hashlib
import json
from pathlib import Path

from .media import audio16k, SR

_MODELS = {}
# a verbatim prompt makes whisper write restarts and fillers out instead of tidying them (NOTES "relisten")
VERBATIM = "Umm, so, uh, I- I mean, like, we- we do the, the thing. Yeah, so, um."


def model(name):
    if name not in _MODELS:
        from faster_whisper import WhisperModel
        _MODELS[name] = WhisperModel(name, device="cpu", compute_type="int8")
    return _MODELS[name]


def _run(audio, name, prompt=None):
    segs, _ = model(name).transcribe(audio, word_timestamps=True, language="en", initial_prompt=prompt,
                                     condition_on_previous_text=False)
    return [[round(w.start, 3), round(w.end, 3), w.word.strip()] for s in segs for w in s.words if w.word.strip()]


def words(path, name, work):
    """[[start, end, word], ...] on the file's clock, cached in work/tx/."""
    path = Path(path)
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{name}".encode()).hexdigest()[:10]
    cache = Path(work) / "tx" / f"{path.stem}.{key}.{name}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    cache.parent.mkdir(parents=True, exist_ok=True)
    out = _run(audio16k(path), name)
    cache.write_text(json.dumps(out))
    return out


def words_from_audio(audio, name, verbatim=False):
    return _run(audio, name, VERBATIM if verbatim else None)


def relisten(path, t0, t1, name):
    """NOTES: a word longer than 0.9 s hides a restart. Re-hear just that stretch with a verbatim prompt."""
    a = audio16k(path, start=max(0.0, t0 - 0.05), dur=(t1 - t0) + 0.1)
    if len(a) < SR * 0.2:
        return []
    return [[t0 - 0.05 + s, t0 - 0.05 + e, w] for s, e, w in _run(a, name, VERBATIM)]
