in /voice: 
  STT: mlx-whisper (instead of faster_whisper)                                                                                                                  
  - MLX-accelerated, runs on the M3 Neural Engine/Metal GPU                                                                                                     
  - Dramatically faster than CPU-based faster_whisper on Apple Silicon                                                                                          
  - pip install mlx-whisper                                           
                            
  TTS: macOS built-in say command                                                                                                                               
  - Your M3 already has Apple Neural voices (Ava, Samantha, etc.) built-in — zero install, zero latency, excellent quality                                      
  - Called via subprocess.Popen(['say', '-v', 'Ava', text]) — non-blocking, interrupt with .kill()                                                              
  - This is likely why gTTS was abandoned — say is faster and works offline                       
                                                                                                                                                                
  Why not kokoro-onnx on M3?                                                                                                                                    
  - ONNX doesn't leverage Metal, runs on CPU — slower than say
  - say uses Apple's on-device Neural TTS which is tuned for the M3                                                                                             
                                                                   
  Updated model summary:                                                                                                                                        
                                                                                                                                                                
  ┌───────────┬───────────────────────────────────┬─────────────────────────────────────────┐
  │ Component │               Model               │                   Why                   │
  ├───────────┼───────────────────────────────────┼─────────────────────────────────────────┤
  │ STT       │ mlx-whisper (medium.en)           │ MLX-native, uses M3 Neural Engine       │
  ├───────────┼───────────────────────────────────┼─────────────────────────────────────────┤
  │ TTS       │ macOS say (-v Ava or -v Samantha) │ Built-in Neural voice, instant, offline │                                                                   
  ├───────────┼───────────────────────────────────┼─────────────────────────────────────────┤                                                                   
  │ LLM       │ qwen2.5:14b via Ollama            │ Already running                         │