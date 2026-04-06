"""
Jane Web Voice API — endpoint for browser-based voice interaction.
Run: python web_api.py
"""

import os
import base64
import tempfile

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI

from brain import think, execute
from memory import process_memory, append_action, rebuild_home_map
from config import OPENAI_API_KEY

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=OPENAI_API_KEY)


@app.on_event("startup")
def startup():
    rebuild_home_map()


@app.post("/api/voice")
async def voice(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    user: str = Form("default"),
):
    # Save uploaded audio to temp file
    suffix = os.path.splitext(audio.filename or "recording.webm")[1] or ".webm"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await audio.read())
        tmp_path = tmp.name

    # Transcribe with Whisper (verbose_json to detect silence)
    try:
        with open(tmp_path, "rb") as f:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="he",
                response_format="verbose_json",
            )
        # Filter silence hallucinations
        no_speech = (
            not transcription.segments
            or all(s.no_speech_prob > 0.5 for s in transcription.segments)
        )
        user_text = "" if no_speech else transcription.text.strip()
    finally:
        os.unlink(tmp_path)

    if not user_text:
        return JSONResponse({"user_text": "", "response_text": "", "audio": ""})

    print(f"🌐 Web: {user_text}")

    # Reuse existing brain logic
    result = think(user_text, user_name=user)
    response_text = execute(result)

    print(f"🔊 Jane: {response_text}")

    # Log action
    action = result.get("action", "speak")
    append_action(user, response_text)

    # Schedule memory extraction in background (unless silent mode)
    silent = any(phrase in user_text for phrase in ["אל תזכרי", "אל תזכור", "מצב שקט"])
    if not silent:
        background_tasks.add_task(process_memory, user, user_text, response_text, action)

    # Generate TTS audio
    tts = client.audio.speech.create(model="tts-1", voice="nova", input=response_text)
    audio_b64 = base64.b64encode(tts.content).decode()

    return JSONResponse({
        "user_text": user_text,
        "response_text": response_text,
        "audio": audio_b64,
    })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5050)
