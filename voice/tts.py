"""
voice/tts.py

Text-to-speech using macOS built-in `say` command.
Uses Apple Neural voices (Ava, Samantha, etc.) — zero install, works offline.
"""

import subprocess
from typing import Optional

DEFAULT_VOICE = "Ava"

_current_process: Optional[subprocess.Popen] = None


def speak(text: str, voice: str = DEFAULT_VOICE, blocking: bool = False) -> None:
    """
    Speak text using macOS `say`.

    Args:
        text: The text to speak.
        voice: Apple Neural voice name (e.g. "Ava", "Samantha").
        blocking: If True, wait for speech to finish before returning.
    """
    global _current_process
    stop()  # kill any ongoing speech before starting new

    _current_process = subprocess.Popen(
        ["say", "-v", voice, text],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if blocking:
        _current_process.wait()


def stop() -> None:
    """Interrupt any currently playing speech."""
    global _current_process
    if _current_process is not None and _current_process.poll() is None:
        _current_process.kill()
    _current_process = None


def is_speaking() -> bool:
    """Return True if speech is currently playing."""
    return _current_process is not None and _current_process.poll() is None
