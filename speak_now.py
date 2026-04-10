import mlx_whisper, numpy as np, sounddevice as sd, queue, time

SAMPLE_RATE = 16000
CHUNK_SIZE = 1600
RMS_THRESHOLD = 0.01
SPEECH_START_CHUNKS = 3
END_SILENCE_CHUNKS = int(0.8 / (CHUNK_SIZE / SAMPLE_RATE))
print('Speak now (10s)...')
audio_q = queue.Queue()
def callback(indata, frames, t, status):
    audio_q.put(indata[:, 0].copy())
state = 'WAITING'
speech_run = silence_run = 0
utterance_chunks = []
deadline = time.time() + 10
with sd.InputStream(channels=1, samplerate=SAMPLE_RATE, blocksize=CHUNK_SIZE, dtype='float32', callback=callback):
    while True:
        if state == 'WAITING' and time.time() >= deadline:
            print('TIMEOUT'); break
        try: chunk = audio_q.get(timeout=0.2)
        except queue.Empty: continue
        rms = float(np.sqrt(np.mean(chunk**2)))
        is_loud = rms >= RMS_THRESHOLD
        if state == 'WAITING':
            if is_loud:
                speech_run += 1
                print(f'WAITING loud {speech_run}/3 rms={rms:.4f}')
                if speech_run >= SPEECH_START_CHUNKS:
                    utterance_chunks=[chunk]; silence_run=0; state='RECORDING'; print('-> RECORDING')
            else: speech_run = 0
        elif state == 'RECORDING':
            utterance_chunks.append(chunk)
            if is_loud: silence_run = 0
            else:
                silence_run += 1
                print(f'RECORDING silence {silence_run}/8')
                if silence_run >= END_SILENCE_CHUNKS:
                    audio = np.concatenate(utterance_chunks)
                    result = mlx_whisper.transcribe(audio, path_or_hf_repo='mlx-community/whisper-medium-mlx')
                    print(f'Result: {result.get(
                        "text","").strip()!r}'); break