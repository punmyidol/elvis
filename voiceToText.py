import sounddevice as sd
from scipy.io.wavfile import write
import whisper
import tempfile
import numpy as np
import os

def voiceToText():

    # Load a small Whisper model (tiny = fastest, base = better quality)
    model = whisper.load_model("tiny")

    samplerate = 16000  # Hz
    duration = 5  # seconds

    print("🎙️ Speak now... (5 seconds)")
    audio = sd.rec(int(duration * samplerate), samplerate=samplerate, channels=1, dtype='float32')
    sd.wait()
    print("✅ Done recording")

    # Save temporarily as WAV
    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    write(temp_file.name, samplerate, (audio * 32767).astype(np.int16))

    # Transcribe
    result = model.transcribe(temp_file.name)
    results = result["text"]
    os.remove(temp_file.name)
    print(results)
    return results