"""
voice/test_tts.py — manual TTS test suite

Run from project root:
    conda run -n elvis-kokoro python voice/test_tts.py

Tests (in order):
  1. speak() blocking
  2. feed() + flush() + drain() streaming
  3. stop() mid-speech
  4. Barge-in: speak loudly after "now speaking" prompt to interrupt
"""

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts import speak, feed, flush, drain, stop, is_speaking, _get_tts

PASS = "[PASS]"
FAIL = "[FAIL]"


def test_speak_blocking():
    print("\n--- Test 1: speak() blocking ---")
    speak("Hello. Kokoro TTS is now powering Elvis.", blocking=True)
    assert not is_speaking(), "should not be speaking after blocking speak()"
    print(PASS)


def test_feed_flush_drain():
    print("\n--- Test 2: feed() + flush() + drain() ---")
    feed("This is sentence one. ")
    feed("Sentence two follows. ")
    feed("And here is the third.")
    flush()
    drain()
    assert not is_speaking(), "should be silent after drain()"
    print(PASS)


def test_stop_mid_speech():
    print("\n--- Test 3: stop() mid-speech ---")
    feed("I am about to say something very long. " * 8)
    flush()
    time.sleep(1.5)
    stop()
    assert not is_speaking(), "should be silent after stop()"
    print(PASS)


def test_barge_in():
    print("\n--- Test 4: barge-in ---")
    print("  >> Elvis will now speak for ~10 seconds.")
    print("  >> Speak loudly into your mic to interrupt.\n")
    feed("I will keep talking until you interrupt me. " * 20)
    flush()
    start = time.time()
    while is_speaking() and time.time() - start < 15:
        time.sleep(0.1)
    elapsed = time.time() - start
    if not is_speaking() and elapsed < 14:
        print(f"  Interrupted after {elapsed:.1f}s")
        print(PASS)
    else:
        print("  No barge-in detected within 15s (did you speak?)")
        stop()


if __name__ == "__main__":
    print("Loading Kokoro model (first run downloads ~170 MB)...")
    _get_tts()
    print("Model ready.\n")

    test_speak_blocking()
    test_feed_flush_drain()
    test_stop_mid_speech()
    test_barge_in()

    print("\nAll tests done.")
