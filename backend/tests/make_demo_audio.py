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
OUT_DIR = "/tmp/oncall-audio"

# Distinct voices so diarization has something to separate. Two male, one
# female — closer to a real bay than three obviously different voices.
VOICE = {
    "MEDIC": "aura-2-apollo-en",
    "DR. LEE": "aura-orion-en",
    "NURSE KATE": "aura-2-thalia-en",
}

# Written to be said out loud by a nervous human in a loud room, not read off a
# chart. Every word that a presenter could stumble on, or that Deepgram got
# wrong in testing, has been swapped for a plainer one that carries the same
# clinical beat:
#
#   "Medic 6"              transcribed as "Lennox six", and the extractor then
#                          took "Lennox" for the patient's name. The callsign
#                          earns nothing here, so the line just opens with the
#                          patient.
#   "anticoagulated"       -> "on blood thinners"
#   "tibia", "gross        -> "leg", "the wound is dirty"
#    contamination"
#   "ampicillin sulbactam" -> "Unasyn", its brand name
#   "Ava Lennox"           -> "Emma", one short common name
#
# Unasyn is the important one, and it makes the demo stronger rather than
# weaker. It is the same drug, and NIH RxNav still resolves it to the FDA class
# "Penicillin-class Antibacterial" — but unlike "ampicillin sulbactam" there is
# nothing in the word for a human or a string match to catch. The alert can only
# come from actually resolving the drug.
#
# The clinical beats are unchanged: vitals and GCS, an open contaminated
# fracture with no antibiotic, three requests of which only one gets claimed,
# an allergy that arrives secondhand from a relative, and an order that
# collides with it.
SCRIPT = [
    ("MEDIC", "Second patient from the same crash. Nineteen year old woman, front seat. G C S thirteen, she's confused, she can't tell us anything. Open fracture, left leg, and the wound is dirty. Heart rate one twenty two, blood pressure one oh four over sixty eight, breathing twenty two, oxygen ninety seven on four liters. We splinted it. No antibiotics yet."),
    ("DR. LEE", "Okay. Someone find out if she's on blood thinners, get ortho down here, and repeat that pressure in five minutes."),
    ("NURSE KATE", "I've got ortho. Calling them now."),
    ("NURSE KATE", "Doctor Lee, I just spoke to Emma's mother in the waiting room. Emma has a severe penicillin allergy. She had anaphylaxis as a child and ended up in hospital."),
    ("DR. LEE", "Alright, that wound is dirty, she needs cover now. Let's give Unasyn, three grams, I V."),
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
