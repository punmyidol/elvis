"""
voice/tts.py

Text-to-speech using macOS built-in `say` command.
Supports sentence-level streaming: feed() chunks text in real time,
enqueuing complete sentences so speech starts before the LLM finishes.
"""

import re
import subprocess
import threading
import queue as _queue
from typing import Optional

DEFAULT_VOICE = "Evan (Enhanced)"

_current_process: Optional[subprocess.Popen] = None
_sentence_queue: _queue.Queue = _queue.Queue()
_worker_thread: Optional[threading.Thread] = None
_worker_lock = threading.Lock()
_buf = ""  # partial-sentence accumulation buffer


# ---------------------------------------------------------------------------
# Internal worker
# ---------------------------------------------------------------------------

def _worker():
    global _current_process
    while True:
        try:
            text = _sentence_queue.get(timeout=0.2)
        except _queue.Empty:
            continue
        _current_process = subprocess.Popen(
            ["say", "-v", DEFAULT_VOICE, text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _current_process.wait()
        _current_process = None
        _sentence_queue.task_done()


def _ensure_worker():
    global _worker_thread
    with _worker_lock:
        if _worker_thread is None or not _worker_thread.is_alive():
            _worker_thread = threading.Thread(target=_worker, daemon=True)
            _worker_thread.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def speak(text: str, voice: str = DEFAULT_VOICE, blocking: bool = False) -> None:
    """Speak text immediately, bypassing the streaming queue."""
    global _current_process
    stop()
    _current_process = subprocess.Popen(
        ["say", "-v", voice, text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if blocking:
        _current_process.wait()
        _current_process = None


def feed(chunk: str) -> None:
    """
    Feed a streaming text chunk. Sentences completed by .!? are enqueued
    for immediate TTS; the remainder is buffered until the next chunk.
    """
    global _buf
    _ensure_worker()
    _buf += chunk
    parts = re.split(r'(?<=[.!?])\s+', _buf)
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
    global _current_process, _buf
    _buf = ""
    while not _sentence_queue.empty():
        try:
            _sentence_queue.get_nowait()
            _sentence_queue.task_done()
        except _queue.Empty:
            break
    if _current_process is not None and _current_process.poll() is None:
        _current_process.kill()
    _current_process = None


def is_speaking() -> bool:
    """True if speech is playing or sentences are queued."""
    return (
        not _sentence_queue.empty()
        or (_current_process is not None and _current_process.poll() is None)
    )
