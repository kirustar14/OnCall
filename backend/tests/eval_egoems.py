"""Evaluate OnCall's speech layer on EgoEMS.

EgoEMS (https://github.com/UVA-DSA/EgoEMS, CC0 subset) is real EMS professionals
working real scenarios in the field — multi-party, noisy, and clinically dense.
It is the closest public proxy we have found to a trauma bay, and unlike a
dyadic doctor-patient corpus it has three or more co-present speakers, which is
the condition ownership attribution actually depends on.

What this measures, and what it deliberately does NOT:

  MEASURED  Word error rate of the exact streaming Deepgram configuration we
            ship (nova-3-medical + keyterm prompting + diarization), scored with
            EgoEMS's own jiwer transform so the number is comparable to the
            Whisper / Gemini / Google baselines published in the repo.
  MEASURED  Recall of the clinical terms the ledger depends on.
  MEASURED  Speaker-count agreement against the gold role labels.
  NOT       Diarization error rate. The human transcripts carry no timestamps,
            so DER is not computable without forced alignment. Any DER figure
            here would be invented.

Run:  ./venv/bin/python -m tests.eval_egoems [--dirs wa1] [--limit N]
"""

import argparse
import asyncio
import glob
import json
import os
import re
import string
import subprocess
import sys
import time
from collections import Counter

import inflect
import jiwer

from app.deepgram_stt import DeepgramSTTSession

EGOEMS = os.path.expanduser(
    "~/servare-eval/EgoEMS/Benchmarks/ActionRecognition/Audio/manual_check_transcripts"
)
SAMPLE_RATE = 16000
# Audio is streamed faster than real time. Deepgram's endpointing works off
# audio timestamps rather than wall clock, so this changes runtime, not results.
SPEEDUP = 20.0

_p = inflect.engine()


# --- EgoEMS's own scoring, copied verbatim so numbers are comparable ----------


def digits_to_words(text: str) -> str:
    text = text.translate(str.maketrans("", "", string.punctuation))
    return " ".join(
        _p.number_to_words(w) if w.isdigit() else w for w in text.split()
    )


_TRANSFORM = jiwer.Compose(
    [
        jiwer.ExpandCommonEnglishContractions(),
        jiwer.RemoveEmptyStrings(),
        jiwer.ToLowerCase(),
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
        jiwer.RemovePunctuation(),
        jiwer.ReduceToListOfListOfWords(),
    ]
)


def get_wer(gt: str, pred: str) -> float:
    """EgoEMS's own scoring. Their calculate_wer.py passes `truth_transform=`,
    which current jiwer removed in favour of `reference_transform=` — their
    script no longer runs unmodified. The transform pipeline is unchanged, so
    the numbers remain comparable to their published baselines."""
    try:
        return jiwer.wer(
            digits_to_words(gt),
            digits_to_words(pred),
            reference_transform=_TRANSFORM,
            hypothesis_transform=_TRANSFORM,
        )
    except TypeError:  # older jiwer
        return jiwer.wer(
            digits_to_words(gt),
            digits_to_words(pred),
            truth_transform=_TRANSFORM,
            hypothesis_transform=_TRANSFORM,
        )


# --- data --------------------------------------------------------------------


def load_utterances(path: str) -> list[dict]:
    with open(path) as fh:
        return json.load(fh)


def joined_text(utterances: list[dict]) -> str:
    return " ".join(u.get("Utterance", "") for u in utterances).strip()


def normalize_role(role: str) -> str:
    """'First responder' / 'First Responder' / 'First Responder 1' all appear."""
    r = (role or "").strip().lower()
    if r.startswith("first responder"):
        suffix = r.replace("first responder", "").strip()
        return f"FR{suffix}" if suffix else "FR"
    if r == "patient":
        return "PATIENT"
    if r == "aed":
        return "AED"
    if r == "background":
        return "BACKGROUND"
    return r.upper() or "UNKNOWN"


def decode_to_pcm(mp3_path: str) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-i", mp3_path, "-f", "s16le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True,
        check=True,
    )
    return out.stdout


# --- our system --------------------------------------------------------------


async def transcribe_stream(pcm: bytes, speedup: float) -> tuple[str, list[tuple[int | None, str]]]:
    """Stream PCM through the exact session class the server uses.

    IMPORTANT: speedup must stay at 1.0 for a valid number. Measured on
    GX010387 (360 s, 615 gold words): 20x returned 31 words, 4x returned 283,
    1x returns the lot. Deepgram's streaming socket expects roughly real-time
    audio and silently drops the excess rather than erroring — so an
    accelerated run produces a plausible-looking transcript and a meaningless
    WER. This cost an entire eval round; do not "optimise" it back.
    """
    segments: list[tuple[int | None, str]] = []

    async def on_transcript(text, is_final, speaker=None):
        if is_final and text.strip():
            segments.append((speaker, text.strip()))

    session = DeepgramSTTSession(on_transcript=on_transcript)
    await session.start()
    chunk = SAMPLE_RATE * 2 // 10  # 100 ms
    delay = 0.1 / speedup
    for i in range(0, len(pcm), chunk):
        await session.send_audio(pcm[i : i + chunk])
        await asyncio.sleep(delay)
    await asyncio.sleep(4.0)  # let trailing finals land
    await session.finish()

    return " ".join(t for _, t in segments), segments


async def transcribe_batch(mp3_path: str) -> tuple[str, list[tuple[int | None, str]]]:
    """Deepgram prerecorded, same model and keyterms as the live path.

    This is the apples-to-apples comparison against EgoEMS's published
    Whisper / Gemini / Google baselines, which are all batch systems.
    """
    import httpx

    from app.config import DEEPGRAM_API_KEY
    from app.deepgram_stt import KEYTERMS

    params = [
        ("model", "nova-3-medical"),
        ("smart_format", "true"),
        ("punctuate", "true"),
        ("diarize", "true"),
        ("language", "en-US"),
    ] + [("keyterm", t) for t in KEYTERMS]

    with open(mp3_path, "rb") as fh:
        audio = fh.read()

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/listen",
            params=params,
            headers={
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": "audio/mpeg",
            },
            content=audio,
        )
        resp.raise_for_status()
        data = resp.json()

    alt = data["results"]["channels"][0]["alternatives"][0]
    words = alt.get("words") or []
    speakers = {w.get("speaker") for w in words if isinstance(w.get("speaker"), int)}
    segments = [(s, "") for s in speakers]
    return alt.get("transcript", ""), segments


# --- clinical term recall ----------------------------------------------------


def term_recall(gt: str, pred: str) -> tuple[int, int, list[str]]:
    """Of the clinically loaded terms the gold transcript actually contains,
    how many survive transcription? A ledger built on a mangled drug name is
    worse than no ledger."""
    terms = [
        "aspirin", "nitroglycerin", "lisinopril", "epinephrine", "naloxone",
        "albuterol", "morphine", "oxygen", "penicillin", "allerg",
        "twelve lead", "12 lead", "st elevation", "stemi", "pericarditis",
        "blood pressure", "heart rate", "pulse", "airway", "compressions",
        "defibrillat", "shock", "sats", "oxygen saturation", "chest pain",
        "diabetic", "insulin", "seizure", "stroke", "milligrams",
    ]
    g = digits_to_words(gt).lower()
    p = digits_to_words(pred).lower()
    present = [t for t in terms if t in g]
    missed = [t for t in present if t not in p]
    return len(present) - len(missed), len(present), missed


# --- main --------------------------------------------------------------------


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", nargs="*", default=None, help="subset dirs, e.g. wa1 ng8")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--mode", choices=["batch", "stream"], default="batch")
    ap.add_argument("--speedup", type=float, default=1.0, help="stream only; MUST be 1.0 to be valid")
    args = ap.parse_args()

    pattern = os.path.join(EGOEMS, "*", "*_human.json")
    human_files = sorted(glob.glob(pattern))
    if args.dirs:
        human_files = [f for f in human_files if os.path.basename(os.path.dirname(f)) in args.dirs]
    if args.limit:
        human_files = human_files[: args.limit]

    print(f"EgoEMS eval — {len(human_files)} file(s)\n")
    print(f"{'file':<26} {'utt':>4} {'our WER':>9} {'gemini':>8} {'spk':>5} {'gold':>5} {'terms':>9}  {'audio':>7}")
    print("-" * 92)

    rows = []
    for hpath in human_files:
        base = os.path.basename(hpath).replace("_human.json", "")
        folder = os.path.dirname(hpath)
        mp3 = os.path.join(folder, base + ".mp3")
        gem = os.path.join(folder, base + "_gemini.json")
        if not os.path.exists(mp3):
            continue

        gold_utts = load_utterances(hpath)
        gold_text = joined_text(gold_utts)
        gold_roles = {normalize_role(u.get("Role", "")) for u in gold_utts if u.get("Role")}
        # AED is a machine voice, not a speaker whose ownership we track.
        gold_human_roles = {r for r in gold_roles if r not in ("AED", "BACKGROUND")}

        pcm = decode_to_pcm(mp3)
        secs = len(pcm) / (SAMPLE_RATE * 2)

        t0 = time.time()
        if args.mode == "batch":
            pred_text, segments = await transcribe_batch(mp3)
        else:
            pred_text, segments = await transcribe_stream(pcm, args.speedup)
        elapsed = time.time() - t0

        our_wer = get_wer(gold_text, pred_text) if pred_text.strip() else 1.0

        gem_wer = None
        if os.path.exists(gem):
            gem_text = joined_text(load_utterances(gem))
            if gem_text.strip():
                gem_wer = get_wer(gold_text, gem_text)

        spk = {s for s, _ in segments if s is not None}
        hit, tot, missed = term_recall(gold_text, pred_text)

        rows.append(
            dict(
                file=f"{os.path.basename(folder)}/{base}",
                utts=len(gold_utts),
                wer=our_wer,
                gem=gem_wer,
                spk=len(spk),
                gold_spk=len(gold_human_roles),
                terms_hit=hit,
                terms_tot=tot,
                missed=missed,
                secs=secs,
                elapsed=elapsed,
                degenerate=len(gold_utts) <= 1,
            )
        )
        r = rows[-1]
        gemstr = f"{gem_wer:8.3f}" if gem_wer is not None else "       -"
        print(
            f"{r['file']:<26} {r['utts']:>4} {r['wer']:>9.3f} {gemstr} "
            f"{r['spk']:>5} {r['gold_spk']:>5} {hit:>4}/{tot:<4} {secs:>6.0f}s"
        )

    real = [r for r in rows if not r["degenerate"]]
    print("\n" + "=" * 92)
    print(f"FILES: {len(rows)} total, {len(real)} substantive (>1 gold utterance)")
    if real:
        n = len(real)
        mean_wer = sum(r["wer"] for r in real) / n
        gems = [r["gem"] for r in real if r["gem"] is not None]
        audio = sum(r["secs"] for r in real)
        th = sum(r["terms_hit"] for r in real)
        tt = sum(r["terms_tot"] for r in real)
        spk_exact = sum(1 for r in real if r["spk"] == r["gold_spk"])
        print(f"  audio scored          : {audio/60:.1f} min")
        print(f"  MEAN WER (ours)       : {mean_wer:.3f}")
        if gems:
            print(f"  MEAN WER (gemini)     : {sum(gems)/len(gems):.3f}   [repo baseline, n={len(gems)}]")
        print(f"  clinical term recall  : {th}/{tt} = {th/tt:.3f}" if tt else "  clinical term recall  : n/a")
        print(f"  speaker count exact   : {spk_exact}/{n}")
        allmissed = Counter(m for r in real for m in r["missed"])
        if allmissed:
            print(f"  terms lost            : {dict(allmissed)}")

    with open("/tmp/egoems_results.json", "w") as fh:
        json.dump(rows, fh, indent=2)
    print("\nwrote /tmp/egoems_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
