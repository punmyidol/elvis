"""
voice/kokoro_demo.py — Kokoro MLX TTS smoke test.

Installs kokoro-mlx on first run (if not present), then speaks a fixed
sentence through your speakers. No Elvis wiring — pure library check.

Run from the project root:
    python voice/kokoro_demo.py

First run downloads ~170 MB of weights from HuggingFace (cached after that).
Subsequent runs are instant.

Requirements (install once):
    pip install kokoro-mlx sounddevice soundfile misaki[en]
"""

import sys
import subprocess


def _ensure_deps():
    """Install missing packages without aborting if already present."""
    pkgs = ["kokoro-mlx", "sounddevice", "soundfile", "misaki[en]"]
    for pkg in pkgs:
        try:
            # Quick import check for the ones that map 1:1 to import names
            importable = pkg.split("[")[0].replace("-", "_")
            __import__(importable)
        except ImportError:
            print(f"[demo] Installing {pkg}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])


_ensure_deps()

import numpy as np  # noqa: E402 — after dep install
import sounddevice as sd  # noqa: E402

# ---------------------------------------------------------------------------
# Kokoro-MLX import
# ---------------------------------------------------------------------------
try:
    from kokoro_mlx import KokoroTTS
except ImportError:
    print(
        "\n[demo] Could not import kokoro_mlx even after install attempt.\n"
        "Try manually:  pip install kokoro-mlx misaki[en]\n"
        "Then re-run this script."
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SENTENCE = (
    "Hey, I'm Elvis — your home assistant. "
    "Kokoro is now handling my voice, and it sounds a lot better than before."
)

VOICE = "af_heart"   # warm American-English female; see tts.list_voices() for options
SPEED = 1.0           # 1.0 = natural pace; try 0.9 for slightly slower delivery
SAMPLE_RATE = 24000   # Kokoro native output rate


def play(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> None:
    """Play a float32 numpy array through the default audio output."""
    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def main() -> None:
    print("[demo] Loading Kokoro model (downloads weights on first run)...")
    tts = KokoroTTS.from_pretrained()

    available = tts.list_voices()
    print(f"[demo] {len(available)} voices available: {', '.join(available[:6])} ...")
    print(f"[demo] Speaking with voice={VOICE!r}, speed={SPEED}")
    print(f"[demo] Text: {SENTENCE!r}\n")

    result = tts.generate(SENTENCE, voice=VOICE, speed=SPEED)
    play(result.audio, result.sample_rate)

    print("[demo] Done. If you heard natural-sounding speech, Kokoro is working.")
    print("\nNext step: replace voice/tts.py with a Kokoro-backed implementation.")


if __name__ == "__main__":
    main()