"""
voice/tts.py

Text-to-speech using Kokoro-MLX (high-quality neural TTS).
Supports sentence-level streaming: feed() chunks text in real time,
enqueuing complete sentences so speech starts before the LLM finishes.

Includes barge-in: while Elvis is speaking, the mic is monitored in a
background thread. Sustained user speech triggers stop() immediately.

Run this module via: conda run -n elvis-kokoro python voice/tts.py
"""

import re
import threading
import queue as _queue
from typing import Optional

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------------------
# Kokoro model (lazy-loaded on first use)
# ---------------------------------------------------------------------------

VOICE = "af_heart"
SPEED = 1.0
SAMPLE_RATE = 24000

_tts = None
_tts_lock = threading.Lock()


def _get_tts():
    global _tts
    with _tts_lock:
        if _tts is None:
            from kokoro_mlx import KokoroTTS
            _tts = KokoroTTS.from_pretrained()
    return _tts


# ---------------------------------------------------------------------------
# Playback state
# ---------------------------------------------------------------------------

_playing = threading.Event()  # set while audio is playing through speakers
_sentence_queue: _queue.Queue = _queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_buf = ""  # partial-sentence accumulation


# ---------------------------------------------------------------------------
# Barge-in constants (same thresholds as stt.py)
# ---------------------------------------------------------------------------

_BARGE_IN_SR = 16000
_BARGE_IN_CHUNK = 1600       # 0.1 s
_BARGE_IN_THRESHOLD = 0.01
_BARGE_IN_CONSECUTIVE = 3    # ~0.3 s of sustained speech triggers stop

_barge_in_stop_event: Optional[threading.Event] = None
_barge_in_thread: Optional[threading.Thread] = None
_barge_in_lock = threading.Lock()


def _barge_in_monitor(stop_event: threading.Event) -> None:
    """Monitor mic RMS while TTS plays; call stop() on sustained speech."""
    consecutive = 0
    try:
        with sd.InputStream(samplerate=_BARGE_IN_SR, channels=1, dtype="float32",
                            blocksize=_BARGE_IN_CHUNK) as stream:
            while not stop_event.is_set() and is_speaking():
                chunk, _ = stream.read(_BARGE_IN_CHUNK)
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > _BARGE_IN_THRESHOLD:
                    consecutive += 1
                    if consecutive >= _BARGE_IN_CONSECUTIVE:
                        stop()
                        return
                else:
                    consecutive = 0
    except Exception:
        pass  # audio device errors shouldn't crash the TTS thread


def _start_barge_in() -> None:
    global _barge_in_stop_event, _barge_in_thread
    with _barge_in_lock:
        if _barge_in_thread is not None and _barge_in_thread.is_alive():
            return
        _barge_in_stop_event = threading.Event()
        _barge_in_thread = threading.Thread(
            target=_barge_in_monitor, args=(_barge_in_stop_event,), daemon=True
        )
        _barge_in_thread.start()


def _stop_barge_in() -> None:
    with _barge_in_lock:
        if _barge_in_stop_event is not None:
            _barge_in_stop_event.set()


# ---------------------------------------------------------------------------
# Internal worker
# ---------------------------------------------------------------------------

def _play_audio(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Play audio array, update _playing flag, start/stop barge-in monitor."""
    _playing.set()
    _start_barge_in()
    try:
        sd.play(audio, samplerate=sample_rate)
        sd.wait()
    finally:
        _playing.clear()
        _stop_barge_in()


def _worker() -> None:
    while True:
        try:
            text = _sentence_queue.get(timeout=0.2)
        except _queue.Empty:
            continue
        try:
            result = _get_tts().generate(text, voice=VOICE, speed=SPEED)
            _play_audio(result.audio, result.sample_rate)
        except Exception as exc:
            print(f"[tts] error playing '{text[:40]}': {exc}")
        finally:
            _sentence_queue.task_done()


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker, daemon=True)
            _worker_thread.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def speak(text: str, voice: str = VOICE, blocking: bool = False) -> None:
    """Speak text immediately, bypassing the streaming queue."""
    stop()
    result = _get_tts().generate(text, voice=voice, speed=SPEED)
    _playing.set()
    _start_barge_in()
    sd.play(result.audio, samplerate=result.sample_rate)
    if blocking:
        sd.wait()
        _playing.clear()
        _stop_barge_in()
    else:
        def _cleanup():
            sd.wait()
            _playing.clear()
            _stop_barge_in()
        threading.Thread(target=_cleanup, daemon=True).start()


def feed(chunk: str) -> None:
    """
    Feed a streaming text chunk. Sentences completed by .!? are enqueued
    for immediate TTS; the remainder is buffered until the next chunk.
    """
    global _buf
    _ensure_worker()
    _buf += chunk
    parts = re.split(r"(?<=[.!?])\s+", _buf)
    for sentence in parts[:-1]:
        sentence = sentence.strip()
        if sentence:
            _sentence_queue.put(sentence)
    _buf = parts[-1]


def flush() -> None:
    """Enqueue whatever remains in the buffer (call after LLM finishes streaming)."""
    global _buf
    _ensure_worker()
    if _buf.strip():
        _sentence_queue.put(_buf.strip())
    _buf = ""


def drain() -> None:
    """Block until all queued sentences have finished playing."""
    _sentence_queue.join()


def stop() -> None:
    """Interrupt current speech and discard the queue."""
    global _buf
    _buf = ""
    _stop_barge_in()
    while not _sentence_queue.empty():
        try:
            _sentence_queue.get_nowait()
            _sentence_queue.task_done()
        except _queue.Empty:
            break
    sd.stop()
    _playing.clear()


def is_speaking() -> bool:
    """True if speech is playing or sentences are queued."""
    return _playing.is_set() or not _sentence_queue.empty()


# ---------------------------------------------------------------------------
# Quick self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import time

    print("Loading Kokoro model...")
    _get_tts()
    print("Model ready.\n")

    print("Test 1: speak() blocking")
    speak("Hello. Kokoro TTS is now powering Elvis.", blocking=True)
    print("Done.\n")

    print("Test 2: feed() + flush() + drain()")
    feed("This is the first sentence. ")
    feed("Here comes the second one. ")
    feed("And a third to finish things off.")
    flush()
    drain()
    print("Done.\n")

    print("Test 3: stop() mid-speech")
    feed("I am going to say something very long that you will interrupt. " * 5)
    flush()
    time.sleep(1.5)
    stop()
    print("Stopped early.\n")

    print("All self-tests complete.")
