import sounddevice as sd
import numpy as np
import whisper
import queue
import time
from faster_whisper import WhisperModel

def voiceToText():
    model_size = "medium.en"
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    samplerate = 16000        
    chunk = 1600              
    buffer_seconds = 2.0
    buffer_size = int(samplerate * buffer_seconds)

    audio_q = queue.Queue()
    audio_buffer = np.zeros(buffer_size, dtype=np.float32)

    last_output = ""

    def callback(indata, frames, time_info, status):
        audio_q.put(indata[:, 0].copy())

    stream = sd.InputStream(
        channels=1,
        samplerate=samplerate,
        blocksize=chunk,
        dtype="float32",
        callback=callback,
    )

    def rms_energy(x):
        return np.sqrt(np.mean(x**2))

    print("Recording... Press CTRL+C to stop.")
    stream.start()

    try:
        last_transcribe_time = 0

        while True:
            chunk_data = audio_q.get()

            # slide buffer
            audio_buffer[:-chunk] = audio_buffer[chunk:]
            audio_buffer[-chunk:] = chunk_data

            if rms_energy(audio_buffer) < 0.01:
                continue

            # only transcribe every 0.5s
            if time.time() - last_transcribe_time < 0.5:
                continue

            last_transcribe_time = time.time()

            result, info = model.transcribe(
                audio_buffer,
                beam_size=5,
            )

            print("Detected language '%s' with probability %f" % (info.language, info.language_probability))

            for segment in result:
                print("[%.2fs -> %.2fs] %s" % (segment.start, segment.end, segment.text))

    except KeyboardInterrupt:
        print("Stopping...")
        stream.stop()
        stream.close()

if __name__ == "__main__":
    voiceToText()
