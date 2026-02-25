import sounddevice as sd
import numpy as np
import queue
import time
import soundfile as sf
import tempfile
from parakeet_mlx import from_pretrained

def voiceToText(audio_file=None):
    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    output_text = ""
    samplerate = 16000
    chunk = 1600                 
    buffer_seconds = 2.0
    buffer_size = int(samplerate * buffer_seconds)

    audio_q = queue.Queue()
    audio_buffer = np.zeros(buffer_size, dtype=np.float32)

    last_transcribe_time = 0
    last_text = ""

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    def rms_energy(x):
        return np.sqrt(np.mean(x**2))

    stream = sd.InputStream(
        channels=1,
        samplerate=samplerate,
        blocksize=chunk,
        dtype="float32",
        callback=callback,
    )

    print("🎙️ Recording... Press CTRL+C to stop.")
    stream.start()

    try:
        if audio_file:
            result = model.transcribe(audio_file)
            return result.text.strip()
        else:
            while True:
                chunk_data = audio_q.get()

                # Slide buffer
                audio_buffer[:-chunk] = audio_buffer[chunk:]
                audio_buffer[-chunk:] = chunk_data

                # Silence gate
                if rms_energy(audio_buffer) < 0.01:
                    continue

                # Throttle transcription
                if time.time() - last_transcribe_time < 1.0:
                    continue

                last_transcribe_time = time.time()

                # 🔑 Write temp WAV file
                with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                    sf.write(f.name, audio_buffer, samplerate)
                    result = model.transcribe(f.name)

                text = result.text.strip()
                if text and text != last_text:
                    output_text += text
                    last_text = text

    except KeyboardInterrupt:
        print("\nStopping...")
        stream.stop()
        stream.close()
        return output_text

if __name__ == "__main__":
    print(from_pretrained("mlx-community/parakeet-tdt-0.6b-v3"))
    # voiceToText()
