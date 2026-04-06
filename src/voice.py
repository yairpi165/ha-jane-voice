import sounddevice as sd
import soundfile as sf
import numpy as np
import tempfile
import os
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

SAMPLE_RATE = 16000
CHANNELS = 1

def record_audio(duration=5):
    """מקליט קול מהמיקרופון"""
    print(f"🎙️  מקשיב... ({duration} שניות)")
    audio = sd.rec(
        int(duration * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype=np.int16
    )
    sd.wait()
    return audio

def transcribe(audio_data):
    """ממיר קול לטקסט עברי דרך Whisper"""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        sf.write(f.name, audio_data, SAMPLE_RATE)
        tmp_path = f.name
    try:
        with open(tmp_path, "rb") as f:
            result = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="he"
            )
        return result.text.strip()
    finally:
        os.unlink(tmp_path)

def speak(text):
    """ממיר טקסט לקול ומנגן אותו"""
    print(f"🔊 ג'יין: {text}")
    response = client.audio.speech.create(
        model="tts-1",
        voice="nova",
        input=text
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(response.content)
        tmp_path = f.name
    try:
        data, sr = sf.read(tmp_path)
        sd.play(data, sr)
        sd.wait()
    finally:
        os.unlink(tmp_path)
