#!/usr/bin/env bash
# Boot the server, wait for schema warmup, run the live pipeline test, tear down.
# Everything runs in one process group that stays alive for the whole run — a
# detached server gets cleaned up the moment its launching shell exits.
set -u

PORT="${1:-8210}"
cd "$(dirname "$0")/.."

rm -f .servare-state.json
./venv/bin/uvicorn app.main:app --port "$PORT" --log-level warning > "/tmp/servare-$PORT.log" 2>&1 &
SRV=$!

cleanup() { kill "$SRV" 2>/dev/null; }
trap cleanup EXIT

echo "server pid $SRV on :$PORT — waiting for schema warmup"
sleep 26

if grep -q "address already in use" "/tmp/servare-$PORT.log"; then
  echo "PORT $PORT ALREADY IN USE — aborting"
  exit 1
fi

./venv/bin/python -m tests.live_pipeline "$PORT"
STATUS=$?

echo ""
echo "deepgram idle-timeouts: $(grep -c 'did not receive audio' "/tmp/servare-$PORT.log")"
echo "server tracebacks:      $(grep -c 'Traceback' "/tmp/servare-$PORT.log")"
exit $STATUS
