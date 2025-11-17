import pathlib
import os
import textwrap

import google.generativeai as genai

model = genai.GenerativeModel("gemini-2.5-flash")
GOOGLE_API_KEY = os.getenv("GEMINI_API_KEY")
genai.configure(api_key=GOOGLE_API_KEY)

def prompt(input):
    input = f"{input} Answer in 3 sentences."
    response = model.generate_content(input)
    return response

if __name__ == "__main__":
   prompt()