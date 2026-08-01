"""Drug class lookup against NIH RxNav.

The safety check turns on one question: does the ordered drug belong to the
class this patient is documented allergic to? Asking a language model that
question works, but the answer is an assertion. RxNav returns the FDA's own
Established Pharmacologic Class for the ingredient — so the claim becomes
checkable rather than trusted, which is the same reason every assertion in this
system carries a Provenance resource.

Free, public, no API key. https://rxnav.nlm.nih.gov/

    ampicillin / sulbactam -> RxCUI 1009148
        EPC     Penicillin-class Antibacterial
        CHEM    Penicillins
        ATC1-4  Combinations of penicillins, incl. beta-lactamase inhibitors
"""

import asyncio
import logging
import re
from typing import Optional

import httpx

logger = logging.getLogger("servare.rxnav")

BASE = "https://rxnav.nlm.nih.gov/REST"
TIMEOUT = 6.0

# The classification systems worth showing a clinician. EPC is the FDA's own
# "Established Pharmacologic Class" and is the one to quote.
WANTED_TYPES = ("EPC", "CHEM", "ATC1-4", "MOA")

# Dose, route and form as spoken — "ampicillin-sulbactam 3 g IV push" will not
# resolve, "ampicillin sulbactam" will.
_NOISE = re.compile(
    # dose with unit, infusion times, route, form, and administration adverbs
    r"\b(\d+(\.\d+)?\s*(mg|mcg|g|gram|grams|ml|units?|u|min|mins|minutes?|hr|hrs|hours?)\b|"
    r"\d+(\.\d+)?\b|"
    r"iv|im|po|pr|sub ?q|subcutaneous|intravenous|oral|push|bolus|drip|infusion|"
    r"stat|now|q\d+h|prn|per|over|x\d+)\b",
    re.IGNORECASE,
)

_cache: dict[str, list[str]] = {}
_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT)
    return _client


def normalize_drug_name(name: str) -> str:
    """Strip dose, route and administration noise from a spoken order."""
    cleaned = _NOISE.sub(" ", name.lower())
    cleaned = re.sub(r"[^a-z0-9\-/ ]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # RxNav resolves combination products better with a slash than a hyphen.
    return cleaned.replace("-", " / ") if "-" in cleaned and "/" not in cleaned else cleaned


async def _rxcui(name: str) -> Optional[str]:
    """search=2 is RxNav's normalized search — tolerant of spacing and order."""
    resp = await _http().get(f"{BASE}/rxcui.json", params={"name": name, "search": "2"})
    resp.raise_for_status()
    ids = (resp.json().get("idGroup") or {}).get("rxnormId") or []
    return ids[0] if ids else None


async def _classes_for_rxcui(rxcui: str) -> list[str]:
    resp = await _http().get(f"{BASE}/rxclass/class/byRxcui.json", params={"rxcui": rxcui})
    resp.raise_for_status()
    info = (resp.json().get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo") or []

    seen: list[str] = []
    for entry in info:
        item = entry.get("rxclassMinConceptItem") or {}
        if item.get("classType") not in WANTED_TYPES:
            continue
        label = f"{item['classType']}: {item['className']}"
        if label not in seen:
            seen.append(label)
    # EPC first — it's the one worth quoting to a clinician.
    return sorted(seen, key=lambda s: (not s.startswith("EPC"), s))


async def drug_classes(drug_name: str) -> list[str]:
    """FDA/NIH drug classes for a spoken medication order.

    Returns [] on anything unresolvable. A miss must never block the safety
    check — the model still reasons, it just does so unverified.
    """
    if not drug_name.strip():
        return []

    normalized = normalize_drug_name(drug_name)
    if normalized in _cache:
        return _cache[normalized]

    # Try the normalized name, then a progressively shorter head, so
    # "ampicillin / sulbactam" falls back to "ampicillin".
    candidates = [normalized]
    if "/" in normalized:
        candidates.append(normalized.split("/")[0].strip())
    first_word = normalized.split(" ")[0] if normalized else ""
    if first_word and first_word not in candidates:
        candidates.append(first_word)

    for candidate in candidates:
        try:
            rxcui = await _rxcui(candidate)
            if not rxcui:
                continue
            classes = await _classes_for_rxcui(rxcui)
            if classes:
                _cache[normalized] = classes
                logger.info("rxnav: %r -> RxCUI %s, %d class(es)", candidate, rxcui, len(classes))
                return classes
        except (httpx.HTTPError, asyncio.TimeoutError):
            logger.warning("rxnav lookup failed for %r", candidate)
            break
        except Exception:
            logger.exception("rxnav unexpected error for %r", candidate)
            break

    _cache[normalized] = []
    return []


def epc_classes(classes: list[str]) -> list[str]:
    """Just the FDA Established Pharmacologic Class entries."""
    return [c.split(": ", 1)[1] for c in classes if c.startswith("EPC: ")]
