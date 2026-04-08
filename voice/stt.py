"""
voice/stt.py

Speech-to-text using mlx-whisper (MLX-accelerated, M3 Neural Engine).
Replaces faster-whisper with mlx-native transcription.
"""

import queue
import time

import mlx_whisper
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK_SIZE = 1600          # 0.1 s per chunk
BUFFER_SECONDS = 2.0
RMS_THRESHOLD = 0.01
TRANSCRIBE_INTERVAL = 0.5  # seconds between transcriptions
MODEL_REPO = "mlx-community/whisper-medium.en-mlx"


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2)))


def listen_once(timeout: float = 5.0, silence_threshold: float = RMS_THRESHOLD) -> str:
    """
    Record until speech is detected, then return the transcribed text.
    Blocks until audio above the threshold is captured and transcribed.

    Args:
        timeout: max seconds to wait for speech before returning "".
        silence_threshold: RMS level below which audio is considered silence.

    Returns:
        Transcribed string, or "" if nothing detected within timeout.
    """
    audio_q: queue.Queue = queue.Queue()
    buffer = np.zeros(int(SAMPLE_RATE * BUFFER_SECONDS), dtype=np.float32)

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    deadline = time.time() + timeout
    last_transcribe = 0.0

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="float32",
        callback=callback,
    ):
        while time.time() < deadline:
            try:
                chunk = audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            buffer[:-CHUNK_SIZE] = buffer[CHUNK_SIZE:]
            buffer[-CHUNK_SIZE:] = chunk

            if _rms(buffer) < silence_threshold:
                continue

            if time.time() - last_transcribe < TRANSCRIBE_INTERVAL:
                continue

            last_transcribe = time.time()
            result = mlx_whisper.transcribe(buffer, path_or_hf_repo=MODEL_REPO)
            text = result.get("text", "").strip()
            if text:
                return text

    return ""


def stream_transcribe(on_text, stop_event=None, silence_threshold: float = RMS_THRESHOLD):
    """
    Continuously transcribe microphone input and call on_text(text) for each
    non-empty transcription.

    Args:
        on_text: callable(str) invoked with each transcription result.
        stop_event: threading.Event — set it to stop the loop.
        silence_threshold: RMS level below which audio is considered silence.
    """
    import threading

    if stop_event is None:
        stop_event = threading.Event()

    audio_q: queue.Queue = queue.Queue()
    buffer = np.zeros(int(SAMPLE_RATE * BUFFER_SECONDS), dtype=np.float32)
    last_transcribe = 0.0

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="float32",
        callback=callback,
    ):
        while not stop_event.is_set():
            try:
                chunk = audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            buffer[:-CHUNK_SIZE] = buffer[CHUNK_SIZE:]
            buffer[-CHUNK_SIZE:] = chunk

            if _rms(buffer) < silence_threshold:
                continue

            if time.time() - last_transcribe < TRANSCRIBE_INTERVAL:
                continue

            last_transcribe = time.time()
            result = mlx_whisper.transcribe(buffer, path_or_hf_repo=MODEL_REPO)
            text = result.get("text", "").strip()
            if text:
                on_text(text)
