#!/usr/bin/env bash
# blitz_test.sh — Volume render test: 10 sequential slots, no publishing.
#
# Tests the full rendering pipeline end-to-end and reports pass/fail per stage.
# Output videos land in ads_ready_for_review/ for human review before distribution.
#
# Usage:
#   ./scripts/blitz_test.sh              # render 10 slots
#   ./scripts/blitz_test.sh --slots 5   # render N slots instead
#   ./scripts/blitz_test.sh --dry-distribute  # also run distribute --dry-run after render
#
# After completion, distribute manually:
#   python3 scripts/archive/distribute.py --slot <N> --live

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
AGENTS_DIR="$SCRIPT_DIR/agents"
ARCHIVE_DIR="$SCRIPT_DIR/archive"
LOG_DIR="$PROJECT_DIR/logs"
ADS_READY="$PROJECT_DIR/ads_ready_for_review"
BLITZ_LOG="$LOG_DIR/blitz_test_$(date -u +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR" "$ADS_READY"

# --- Args ---
TOTAL=10
DRY_DISTRIBUTE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --slots)          TOTAL="$2"; shift 2 ;;
    --dry-distribute) DRY_DISTRIBUTE=1; shift ;;
    *) shift ;;
  esac
done

log() {
  local msg="[$(date -u '+%Y-%m-%d %H:%M:%S') UTC] $*"
  echo "$msg" | tee -a "$BLITZ_LOG"
}

hr() { log "$(printf '%.0s─' {1..60})"; }

# --- Load env ---
if [[ -f "$PROJECT_DIR/.env" ]]; then
  set -a; source "$PROJECT_DIR/.env"; set +a
fi

# --- Counters ---
PASS_SCRIPT=0; FAIL_SCRIPT=0
PASS_AUDIO=0;  FAIL_AUDIO=0
PASS_VIDEO=0;  FAIL_VIDEO=0
declare -a RESULT_ROWS=()

hr
log "  HARBINGER BLITZ TEST — $TOTAL slots"
log "  Log: $BLITZ_LOG"
hr

START_TIME=$(date +%s)

for i in $(seq 1 "$TOTAL"); do
  # Rotate through slots 1-7
  SLOT=$(( (i - 1) % 7 + 1 ))

  hr
  log "  Iteration $i / $TOTAL  │  Slot arg: --slot $SLOT"
  hr

  RS="FAIL"; RA="FAIL"; RV="FAIL"; VIDEO_FILE="—"
  SLOT_START=$(date +%s)

  # ── Step A: Script generation ───────────────────────────────────────────
  log "[A] creative_synthesis.py --slot $SLOT"
  if python3 "$AGENTS_DIR/creative_synthesis.py" --slot "$SLOT" \
       >> "$BLITZ_LOG" 2>&1; then
    RS="PASS"; (( PASS_SCRIPT++ )) || true
    log "[A] PASS — creative brief generated"
  else
    (( FAIL_SCRIPT++ )) || true
    log "[A] FAIL — creative_synthesis.py exited non-zero"
    RESULT_ROWS+=("$i|$SLOT|$RS|$RA|$RV|$VIDEO_FILE")
    continue
  fi

  # ── Step B + C: Audio generation + FFmpeg render ────────────────────────
  log "[B+C] production_agent.py --slot $SLOT"
  PROD_OUT=$(python3 "$AGENTS_DIR/production_agent.py" --slot "$SLOT" 2>&1)
  PROD_EXIT=$?
  echo "$PROD_OUT" >> "$BLITZ_LOG"

  # Audio pass/fail — look for ElevenLabs success lines
  if echo "$PROD_OUT" | grep -qE "Audio saved:|Fallback audio saved:"; then
    RA="PASS"; (( PASS_AUDIO++ )) || true
    log "[B] PASS — audio generated"
  else
    (( FAIL_AUDIO++ )) || true
    log "[B] FAIL — no audio output detected"
    log "[B] Hint: check ELEVENLABS_API_KEY in .env"
  fi

  # Video pass/fail — look for render completion line
  if echo "$PROD_OUT" | grep -q "Render complete:"; then
    RV="PASS"; (( PASS_VIDEO++ )) || true
    log "[C] PASS — FFmpeg render complete"
  else
    (( FAIL_VIDEO++ )) || true
    log "[C] FAIL — render did not complete"
    if (( PROD_EXIT != 0 )); then
      log "[C] Hint: production_agent.py exited $PROD_EXIT"
      # Extract last error line from output for quick diagnosis
      LAST_ERR=$(echo "$PROD_OUT" | grep -iE "ABORT|ERROR|failed|error" | tail -1)
      [[ -n "$LAST_ERR" ]] && log "[C] Last error: $LAST_ERR"
    fi
  fi

  # Find most-recently written file in ads_ready_for_review/
  VIDEO_FILE=$(ls -t "$ADS_READY"/post_*.mp4 2>/dev/null | head -1 || echo "—")

  SLOT_END=$(date +%s)
  ELAPSED=$(( SLOT_END - SLOT_START ))
  log "    Duration: ${ELAPSED}s  │  File: $(basename "$VIDEO_FILE" 2>/dev/null || echo '—')"

  # ── Step D (optional): Dry-run distribution ─────────────────────────────
  if [[ $DRY_DISTRIBUTE -eq 1 && "$RV" == "PASS" ]]; then
    log "[D] distribute.py --slot $SLOT --dry-run"
    if python3 "$ARCHIVE_DIR/distribute.py" --slot "$SLOT" --dry-run \
         >> "$BLITZ_LOG" 2>&1; then
      log "[D] PASS — dry-run payload logged"
    else
      log "[D] WARN — dry-run distribution returned non-zero"
    fi
  fi

  RESULT_ROWS+=("$i|$SLOT|$RS|$RA|$RV|$VIDEO_FILE")
done

END_TIME=$(date +%s)
TOTAL_ELAPSED=$(( END_TIME - START_TIME ))

# ── Summary table ──────────────────────────────────────────────────────────
hr
log "  BLITZ TEST RESULTS"
hr

printf "\n%-5s %-5s %-12s %-12s %-14s %s\n" \
  "ITER" "SLOT" "SCRIPT_GEN" "AUDIO_GEN" "VIDEO_RENDER" "OUTPUT_FILE"
printf "%-5s %-5s %-12s %-12s %-14s %s\n" \
  "----" "----" "----------" "---------" "------------" "-----------"

for row in "${RESULT_ROWS[@]}"; do
  IFS='|' read -r i s rs ra rv vf <<< "$row"
  FNAME="$(basename "$vf" 2>/dev/null || echo '—')"
  printf "%-5s %-5s %-12s %-12s %-14s %s\n" "$i" "$s" "$rs" "$ra" "$rv" "$FNAME"
done | tee -a "$BLITZ_LOG"

echo "" | tee -a "$BLITZ_LOG"
printf "Script gen:    %2d PASS  /  %2d FAIL\n" "$PASS_SCRIPT" "$FAIL_SCRIPT" | tee -a "$BLITZ_LOG"
printf "Audio gen:     %2d PASS  /  %2d FAIL\n" "$PASS_AUDIO"  "$FAIL_AUDIO"  | tee -a "$BLITZ_LOG"
printf "Video render:  %2d PASS  /  %2d FAIL\n" "$PASS_VIDEO"  "$FAIL_VIDEO"  | tee -a "$BLITZ_LOG"
printf "Total time:    %ds\n" "$TOTAL_ELAPSED"                                  | tee -a "$BLITZ_LOG"

# ── One-click review list ──────────────────────────────────────────────────
hr
log "  READY FOR HUMAN REVIEW"
hr
echo ""
echo "  Review each video, approve, then publish manually:"
echo "  python3 scripts/archive/distribute.py --slot <N> --live"
echo ""

if ls "$ADS_READY"/post_*.mp4 &>/dev/null 2>&1; then
  echo "  Files in ads_ready_for_review/ (newest first):"
  echo ""
  ls -lht "$ADS_READY"/post_*.mp4 2>/dev/null \
    | awk '{printf "  [REVIEW]  %-10s  %s\n", $5, $9}'
  echo ""
  # Quick-copy block: one distribute command per file
  echo "  ── Quick publish commands (run after review) ──"
  IDX=1
  while IFS= read -r fpath; do
    SLOT_NUM=$(( (IDX - 1) % 7 + 1 ))
    printf "  python3 scripts/archive/distribute.py --slot %d --live   # %s\n" \
      "$SLOT_NUM" "$(basename "$fpath")"
    (( IDX++ )) || true
  done < <(ls -t "$ADS_READY"/post_*.mp4 2>/dev/null)
else
  echo "  (no output files found in ads_ready_for_review/)"
fi

echo ""
echo "  Blitz log: $BLITZ_LOG"
echo "  Dry-run distribution log: $LOG_DIR/dry_run_distribution.log"
echo ""
hr
