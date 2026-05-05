#!/usr/bin/env bash
# rehearse_demo.sh — one-command end-to-end dry run of the 5-min PromptSeal demo.
#
# What this does:
#   1. Runs the agent on res_003 (Carol Singh, expected hire — milestone 5 path)
#   2. Reads the freshly-created run_id back from SQLite (no hardcoding)
#   3. Anchors that run's Merkle root to Base Sepolia
#   4. Starts a static file server for the verifier on :8000 (background)
#   5. Looks up the final_decision receipt id for the run
#   6. Prints the three textarea contents (paste-ready)
#
# What this does NOT do:
#   - Run the tamper demo (run scripts/99_tamper_demo.py manually on stage)
#   - Clean stale runs (run scripts/clean_demo_runs.py --execute first if needed)
#   - Tear itself down — kill the http.server with the printed PID when done
#
# Requirements:
#   - .venv/ exists with `pip install -e .` already run
#   - .env populated with LLM creds + Base Sepolia DEPLOYER_PRIVATE_KEY
#   - agent_key.pem + agent_id.json in repo root (token #633 already registered)

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

PYTHON="$REPO/.venv/bin/python"
DB="${PROMPTSEAL_DB_PATH:-$REPO/promptseal.sqlite}"
PORT="${PROMPTSEAL_VERIFIER_PORT:-8000}"

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: $PYTHON not found. Run: python3.11 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

echo
echo "===== Step 1: run hiring agent on res_003 (Carol Singh) ====="
"$PYTHON" scripts/02_run_demo.py res_003

echo
echo "===== Step 2: discover newest run_id from SQLite ====="
RUN_ID=$(sqlite3 "$DB" "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1")
if [[ -z "$RUN_ID" ]]; then
  echo "ERROR: no runs found in $DB — agent invocation may have failed." >&2
  exit 2
fi
echo "  run_id = $RUN_ID"

echo
echo "===== Step 3: anchor $RUN_ID's Merkle root to Base Sepolia ====="
"$PYTHON" scripts/03_anchor_run.py "$RUN_ID"

echo
echo "===== Step 4: start static verifier server on :$PORT (background) ====="
# Kill any prior leftover on the same port (best effort; ignore if nothing).
if lsof -ti :$PORT >/dev/null 2>&1; then
  echo "  port $PORT already in use — killing previous server"
  lsof -ti :$PORT | xargs kill -9 || true
  sleep 0.3
fi
"$PYTHON" -m http.server "$PORT" --directory verifier >/tmp/promptseal_verifier.log 2>&1 &
SERVER_PID=$!
echo "  server PID: $SERVER_PID  (log: /tmp/promptseal_verifier.log)"
echo "  stop later with:  kill $SERVER_PID"

echo
echo "===== Step 5: locate final_decision receipt id for $RUN_ID ====="
RECEIPT_ID=$(sqlite3 "$DB" "SELECT id FROM receipts WHERE run_id='$RUN_ID' AND event_type='final_decision' ORDER BY id DESC LIMIT 1")
if [[ -z "$RECEIPT_ID" ]]; then
  echo "ERROR: no final_decision receipt found for $RUN_ID" >&2
  echo "  available event_types:"
  sqlite3 "$DB" "SELECT event_type, COUNT(*) FROM receipts WHERE run_id='$RUN_ID' GROUP BY event_type"
  exit 3
fi
echo "  receipt_id = $RECEIPT_ID"

echo
echo "===== Step 6: paste-ready verifier inputs (3 textareas) ====="
"$PYTHON" scripts/generate_verifier_inputs.py "$RUN_ID" "$RECEIPT_ID"

echo
echo "============================================================"
echo "  Rehearsal ready."
echo "  Now open: http://localhost:$PORT"
echo "  Paste the three blocks above into the matching textareas."
echo "  When done, stop the verifier server: kill $SERVER_PID"
echo "============================================================"
