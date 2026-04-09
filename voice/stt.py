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
    Record a complete utterance and return the transcribed text.

    Waits for speech to begin (RMS above threshold for SPEECH_START_CHUNKS
    consecutive chunks), accumulates audio until silence is sustained for
    END_SILENCE_CHUNKS, then transcribes the full utterance.

    Args:
        timeout: max seconds to wait for speech to BEGIN before returning "".
        silence_threshold: RMS level below which audio is considered silence.

    Returns:
        Transcribed string, or "" if no speech detected within timeout.
    """
    SPEECH_START_CHUNKS = 3                              # 0.3s to confirm speech
    END_SILENCE_CHUNKS = int(0.8 / (CHUNK_SIZE / SAMPLE_RATE))  # 0.8s of silence = EOU

    audio_q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    state = "WAITING"
    speech_run = 0
    silence_run = 0
    utterance_chunks: list = []
    deadline = time.time() + timeout

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="float32",
        callback=callback,
    ):
        while True:
            if state == "WAITING" and time.time() >= deadline:
                return ""

            try:
                chunk = audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            is_loud = _rms(chunk) >= silence_threshold

            if state == "WAITING":
                if is_loud:
                    speech_run += 1
                    if speech_run >= SPEECH_START_CHUNKS:
                        utterance_chunks = [chunk]
                        silence_run = 0
                        state = "RECORDING"
                else:
                    speech_run = 0

            elif state == "RECORDING":
                utterance_chunks.append(chunk)
                if is_loud:
                    silence_run = 0
                else:
                    silence_run += 1
                    if silence_run >= END_SILENCE_CHUNKS:
                        audio = np.concatenate(utterance_chunks)
                        result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL_REPO)
                        return result.get("text", "").strip()


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
