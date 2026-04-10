"""
test_stt.py — Minimal mlx-whisper mic test.
Loops continuously: listen → transcribe → print. Ctrl-C to quit.
"""

import queue
import time
import sys

import mlx_whisper
import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHUNK_SIZE = 1600           # 0.1s
RMS_THRESHOLD = 0.01
SPEECH_START_CHUNKS = 3     # 0.3s of loud audio to start recording
END_SILENCE_CHUNKS = 8      # 0.8s of silence to end utterance
MODEL_REPO = "mlx-community/whisper-base-mlx"


def _rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def listen_once(audio_q):
    """Block until one utterance is captured. Returns numpy audio array."""
    state = "WAITING"
    speech_run = silence_run = 0
    utterance_chunks = []

    while True:
        try:
            chunk = audio_q.get(timeout=0.2)
        except queue.Empty:
            continue

        loud = _rms(chunk) >= RMS_THRESHOLD

        if state == "WAITING":
            if loud:
                speech_run += 1
                print(f"  [ waiting  | rms={_rms(chunk):.4f} | speech={speech_run}/{SPEECH_START_CHUNKS} ]", end="\r")
                if speech_run >= SPEECH_START_CHUNKS:
                    utterance_chunks = [chunk]
                    silence_run = 0
                    state = "RECORDING"
                    print("\n  [ recording... ]")
            else:
                speech_run = 0

        elif state == "RECORDING":
            utterance_chunks.append(chunk)
            if loud:
                silence_run = 0
            else:
                silence_run += 1
                print(f"  [ recording | silence={silence_run}/{END_SILENCE_CHUNKS} ]", end="\r")
                if silence_run >= END_SILENCE_CHUNKS:
                    print()
                    return np.concatenate(utterance_chunks)


def main():
    print(f"Model : {MODEL_REPO}")
    print(f"Device: {sd.query_devices(kind='input')['name']}")
    print()
    print("Loading model...", flush=True)
    silence = np.zeros(SAMPLE_RATE, dtype=np.float32)
    mlx_whisper.transcribe(silence, path_or_hf_repo=MODEL_REPO)
    print("Model ready. Speak — Ctrl-C to quit.\n")

    audio_q: queue.Queue = queue.Queue()

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    with sd.InputStream(
        channels=1,
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="float32",
        callback=callback,
    ):
        while True:
            print("Listening...")
            audio = listen_once(audio_q)
            print("Transcribing...", flush=True)
            t0 = time.time()
            result = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL_REPO)
            elapsed = time.time() - t0
            text = result.get("text", "").strip()
            print(f">> {text!r}  ({elapsed:.2f}s)\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDone.")
        sys.exit(0)
