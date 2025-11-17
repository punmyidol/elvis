from gtts import gTTS
from playsound import playsound
import os
from voiceToText import voiceToText
from llamaLLM import ask_gemma
import re
# use pydub to increase speed of audio

def response(response):
    response = response.replace('*', '')
    response = response.replace('-', ' to ') # fix with re
    response = response.replace('#', '')
    return response

def string_to_array(s):
    arr = s.split('\n')
    arr = set(arr)
    return arr

input = voiceToText()
text = ask_gemma(input)
text = response(text)
speech = string_to_array(text)

for line in speech:
    print(line)
    if line:
        tts = gTTS(text=line, lang='en', slow=False)
        filename = "speech.mp3"
        tts.save(filename)
        playsound(filename)
        os.remove(filename)