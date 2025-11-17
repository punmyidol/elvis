import requests
import json

# Keep session open to reuse the TCP connection
session = requests.Session()

def ask_gemma(query):
    url = "http://localhost:11434/api/generate"
    payload = {
        "model": "gemma3-tutor",
        "prompt": query,
        "stream": True
    }

    response = ""

    # Send request and stream response
    with session.post(url, json=payload, stream=True) as r:
        for line in r.iter_lines():
            if line:
                data = json.loads(line.decode('utf-8'))
                if 'response' in data:
                    response += data['response']
    return response