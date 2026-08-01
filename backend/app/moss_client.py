"""moss.dev semantic search: a fast fuzzy-recall layer over everything captured
for a patient, across every case they've been in — complementary to Medplum
(structured FHIR, stays the source of truth) and to search_patient_history's
exact Medplum lookups. One dedicated index ("oncall-patient-context"),
separate from the account's auto-generated onboarding starter index. One
MossClient instance, constructed lazily and reused for the app's lifetime.

SDK quirk observed repeatedly in testing (moss 1.7.2): the first authenticated
call after constructing a client can intermittently fail with "Auth token
request failed (projectId/projectKey should not be empty)" even with valid
credentials passed to the constructor — a startup race in the SDK, not a
credentials problem. It resolves on retry. _call() below wraps every request
with backoff specifically for that error; every public method here also
degrades to a no-op / empty result (logged, never raised) if moss is
unconfigured or setup ultimately fails, the same posture this app takes
toward Medplum being unavailable.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

try:
    import moss
except ImportError:  # pragma: no cover — environment-dependent
    # Three modules import this one, so a bare `import moss` at module scope
    # takes the entire backend down at startup on any machine where the SDK is
    # not installed. Semantic recall is an enhancement over Medplum, never a
    # dependency of it, so an absent SDK degrades to exactly the same no-op path
    # as absent credentials rather than to a dead server.
    moss = None

from app.config import MOSS_PROJECT_ID, MOSS_PROJECT_KEY

logger = logging.getLogger("oncall.moss_client")

INDEX_NAME = "oncall-patient-context"

_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY = 1.0
_RETRY_MAX_DELAY = 8.0


class MossPatientContext:
    def __init__(self) -> None:
        self._client: Optional["moss.MossClient"] = None
        self._index_ready = False
        self._setup_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(moss is not None and MOSS_PROJECT_ID and MOSS_PROJECT_KEY)

    async def _call(self, fn, *args, **kwargs):
        delay = _RETRY_BASE_DELAY
        last_err: Optional[Exception] = None
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                return await fn(*args, **kwargs)
            except RuntimeError as e:
                if "Auth token request failed" not in str(e):
                    raise
                last_err = e
                logger.warning(
                    "moss: transient startup auth race (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    _RETRY_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 1.5, _RETRY_MAX_DELAY)
        assert last_err is not None
        raise last_err

    async def _ensure_ready(self) -> bool:
        """Lazily builds the client and ensures the dedicated index exists and is
        loaded (loading is required for both fast in-memory queries AND for
        metadata filtering to work at all — an unloaded index silently ignores
        filters and falls back to an unfiltered cloud query). Returns False if
        moss isn't configured or setup fails; callers no-op rather than crash."""

        if not self.configured:
            return False
        if self._index_ready:
            return True

        async with self._setup_lock:
            if self._index_ready:
                return True
            try:
                if self._client is None:
                    self._client = moss.MossClient(MOSS_PROJECT_ID, MOSS_PROJECT_KEY)

                # Warm-up call — also doubles as working around the first-call
                # auth race described above.
                await self._call(self._client.list_indexes)

                try:
                    await self._call(self._client.get_index, INDEX_NAME)
                except Exception:
                    # Doesn't exist yet. create_index requires >=1 seed doc.
                    seed = moss.DocumentInfo(
                        id=str(uuid.uuid4()),
                        text="OnCall patient-context index initialized.",
                        metadata={
                            "case_id": "system",
                            "patient_name": "",
                            "fact_type": "system",
                            "timestamp": str(time.time()),
                        },
                    )
                    await self._call(self._client.create_index, INDEX_NAME, [seed], wait=True)

                # auto_refresh is a belt-and-suspenders backstop — index_fact()
                # explicitly reloads after every write for immediate freshness;
                # this just guards against any future write path that forgets to.
                await self._call(self._client.load_index, INDEX_NAME, True, 30)
                self._index_ready = True
                logger.info("moss: index '%s' ready", INDEX_NAME)
                return True
            except Exception:
                logger.exception("moss: setup failed — semantic search disabled until next attempt")
                return False

    async def warmup(self) -> None:
        """Call once at app startup so the client/index setup (and any retry of
        the SDK's first-call auth race) happens during boot, not mid-demo."""
        await self._ensure_ready()

    async def index_fact(
        self, case_id: str, patient_name: Optional[str], fact_type: str, text: str, timestamp: float
    ) -> None:
        """Push one captured fact into the index. Best-effort — failures are
        logged, never raised, so a Moss hiccup never breaks the extraction
        pipeline (Medplum stays the source of truth regardless)."""
        if not await self._ensure_ready():
            return

        doc = moss.DocumentInfo(
            id=str(uuid.uuid4()),
            text=text,
            metadata={
                "case_id": case_id,
                "patient_name": (patient_name or "").strip().lower(),
                "fact_type": fact_type,
                "timestamp": str(timestamp),
            },
        )
        try:
            await self._call(self._client.add_docs, INDEX_NAME, [doc])
            # load_index() snapshots the index into memory; add_docs() does NOT
            # update that snapshot on its own (auto_refresh defaults to False
            # and even with it on, the poll interval would leave a stale window
            # right after a fact is added). Without this, queries against the
            # already-loaded index keep serving pre-write state indefinitely —
            # reproduced directly in testing. Re-loading is cheap at this data
            # scale and guarantees the very next query sees this fact.
            await self._call(self._client.load_index, INDEX_NAME)
        except Exception:
            logger.exception("moss: failed to index fact for case %s", case_id)

    async def search(
        self, query_text: str, *, patient_name: Optional[str] = None, case_id: Optional[str] = None, top_k: int = 5
    ) -> list[dict[str, Any]]:
        """Semantic search, filtered by patient identity when known (so it finds
        facts from OTHER cases for the same patient — the whole point of this
        layer), falling back to the current case only when no name is on file
        yet. Returns a list of {text, metadata, score} dicts, or [] on any
        failure/unavailability."""
        if not await self._ensure_ready():
            return []

        normalized_name = (patient_name or "").strip().lower()
        if normalized_name:
            filt = {"field": "patient_name", "condition": {"$eq": normalized_name}}
        elif case_id:
            filt = {"field": "case_id", "condition": {"$eq": case_id}}
        else:
            filt = None

        try:
            result = await self._call(
                self._client.query,
                INDEX_NAME,
                query_text,
                options=moss.QueryOptions(top_k=top_k, filter=filt),
            )
        except Exception:
            logger.exception("moss: query failed")
            return []

        return [
            {"text": d.text, "metadata": d.metadata or {}, "score": d.score}
            for d in result.docs
            if (d.metadata or {}).get("fact_type") != "system"
        ]


moss_client = MossPatientContext()
