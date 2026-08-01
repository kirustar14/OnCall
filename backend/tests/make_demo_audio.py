"""Generate the demo script as audio, using a different Aura voice per speaker.

Two uses:

1. It closes the loop for testing — Aura produces speech, Deepgram STT
   transcribes it, and we can measure transcription accuracy and diarization
   without a microphone or a quiet room.
2. The .wav it writes is a rehearsable demo track. A live mic in a loud venue
   is the single biggest demo risk; playing a file into the pipeline is
   deterministic.

    ./venv/bin/python -m tests.make_demo_audio
"""

import asyncio
import os
import wave

import httpx

from app.config import DEEPGRAM_API_KEY

SAMPLE_RATE = 16000
OUT_DIR = "/tmp/servare-audio"

# Distinct voices so diarization has something to separate. Two male, one
# female — closer to a real bay than three obviously different voices.
VOICE = {
    "MEDIC": "aura-2-apollo-en",
    "DR. REYES": "aura-orion-en",
    "NURSE OKAFOR": "aura-2-thalia-en",
}

SCRIPT = [
    ("MEDIC", "Medic 6, second patient, same collision. Nineteen year old female, front seat passenger. G C S thirteen, she's confused, she can't give us a history. Open left tibia fracture, gross contamination at the wound. Heart rate one twenty two, blood pressure one oh four over sixty eight, respirations twenty two, sat ninety seven on four liters. Splinted in the field, no antibiotics given."),
    ("DR. REYES", "Okay, someone find out whether she's anticoagulated, get ortho down here, and repeat that pressure in five minutes."),
    ("NURSE OKAFOR", "I've got ortho, I'm calling them now."),
    ("NURSE OKAFOR", "Doctor Reyes, I just spoke with the patient's mother in the waiting room. She says Ava has a severe penicillin allergy. Anaphylaxis as a child, she was hospitalized for it."),
    ("DR. REYES", "Alright, wound's contaminated, she needs coverage now. Let's get ampicillin sulbactam, three grams I V, push it."),
]


async def synth(client: httpx.AsyncClient, text: str, voice: str) -> bytes:
    resp = await client.post(
        f"https://api.deepgram.com/v1/speak?model={voice}"
        f"&encoding=linear16&sample_rate={SAMPLE_RATE}",
        headers={
            "Authorization": f"Token {DEEPGRAM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"text": text},
    )
    resp.raise_for_status()
    return resp.content


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(SAMPLE_RATE * seconds)


async def build() -> tuple[bytes, list[tuple[str, str, float]]]:
    """Returns the concatenated PCM plus a timeline of (speaker, text, start_s)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    chunks: list[bytes] = []
    timeline: list[tuple[str, str, float]] = []
    elapsed = 0.0

    async with httpx.AsyncClient(timeout=60) as client:
        for i, (speaker, text) in enumerate(SCRIPT):
            pcm = await synth(client, text, VOICE[speaker])
            timeline.append((speaker, text, elapsed))
            chunks.append(pcm)
            elapsed += len(pcm) / (SAMPLE_RATE * 2)

            # A real pause between turns — this is what lets endpointing close a
            # segment and diarization commit to a speaker change.
            gap = silence(0.9)
            chunks.append(gap)
            elapsed += 0.9

            path = os.path.join(OUT_DIR, f"{i:02d}-{speaker.replace(' ', '_').replace('.', '')}.pcm")
            with open(path, "wb") as fh:
                fh.write(pcm)

    return b"".join(chunks), timeline


def write_wav(pcm: bytes, path: str) -> None:
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)


async def main() -> None:
    pcm, timeline = await build()
    wav_path = os.path.join(OUT_DIR, "demo-script.wav")
    write_wav(pcm, wav_path)

    print(f"wrote {wav_path}")
    print(f"  {len(pcm)/(SAMPLE_RATE*2):.1f}s total, {len(pcm)} bytes PCM\n")
    for speaker, text, start in timeline:
        print(f"  {start:6.1f}s  {speaker:<13} ({VOICE[speaker]})  {text[:52]}…")


if __name__ == "__main__":
    asyncio.run(main())
