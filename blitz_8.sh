#!/usr/bin/env bash
# blitz_8.sh — 8-slot niche-routed volume orchestrator with auto-heal retry.
#
# Niche split across 8 slots:
#   Slots 1, 4, 7  → tech_ai         (3 slots)
#   Slots 2, 5, 8  → dark_psychology  (3 slots)
#   Slots 3, 6     → micro_mystery    (2 slots)
#
# Scheduling via distribute.py: scheduled_time = NOW + (slot * 3h)
#   Slot 1 = +3h, Slot 2 = +6h, ..., Slot 8 = +24h (full 24-hour window).
#
# Pipeline per slot:
#   1. creative_synthesis.py  — generate script
#   2. quality_assessor.py    — pre-flight QA gate (auto-retries on fail)
#   3. production_agent.py    — render + auto-publish via distribute.py
#
# Usage:
#   ./blitz_8.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$SCRIPT_DIR/scripts/agents"
ADS_READY="$SCRIPT_DIR/ads_ready_for_review"
LOG_DIR="$SCRIPT_DIR/logs"
BLITZ_LOG="$LOG_DIR/blitz_8_$(date -u +%Y%m%d_%H%M%S).log"

mkdir -p "$ADS_READY" "$LOG_DIR"

# Load .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S') UTC] $*" | tee -a "$BLITZ_LOG"; }

# Niche map (1-based index, element 0 unused)
NICHE_MAP=("" "tech_ai" "dark_psychology" "micro_mystery" "tech_ai" "dark_psychology" "micro_mystery" "tech_ai" "dark_psychology")

PASS=0
FAIL=0
declare -a RESULT_ROWS=()

log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  HARBINGER BLITZ 8 — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Meta-manager: run scheduled audit / evolution / trend-scrape ─────────────
log ""
log "  [META] Running meta_manager scheduling check..."
if ! python3 "$AGENTS_DIR/meta_manager.py" --schedule >> "$BLITZ_LOG" 2>&1; then
  log "  [META] ERROR: meta_manager.py --schedule failed (exit $?) — aborting blitz on stale parameters."
  exit 1
fi

# ── #13 Shadowban canary — run first to refresh canary_report.json ──────────
log ""
log "  [CANARY] Running shadowban canary check..."
python3 "$AGENTS_DIR/canary.py" >> "$BLITZ_LOG" 2>&1
CANARY_EXIT=$?
if [[ $CANARY_EXIT -eq 2 ]]; then
  log "  [CANARY] EXIT CODE 2 — shadowban detected by canary. Aborting blitz immediately."
  exit 1
elif [[ $CANARY_EXIT -ne 0 ]]; then
  log "  [CANARY] WARN: canary.py exited $CANARY_EXIT (non-zero, non-shadowban) — checking lock file."
fi

SHADOWBAN_LOCK="$SCRIPT_DIR/SHADOWBAN_LOCK"
if [[ -f "$SHADOWBAN_LOCK" ]]; then
  log "  [CANARY] SHADOWBAN_LOCK detected — aborting blitz."
  log "  [CANARY] Lock contents:"
  cat "$SHADOWBAN_LOCK" | while IFS= read -r line; do log "    $line"; done
  log "  [CANARY] Delete $SHADOWBAN_LOCK manually after verifying account health."
  exit 1
fi
log "  [CANARY] No shadowban lock — proceeding."

# ── #14 Hydra Optimizer — adjust NICHE_MAP from canary_report.json ──────────
log ""
log "  [HYDRA] Computing optimised niche distribution..."
HYDRA_TMP=$(mktemp)
if python3 "$AGENTS_DIR/canary.py" --niche-map > "$HYDRA_TMP" 2>>"$BLITZ_LOG"; then
  HYDRA_OUTPUT=$(cat "$HYDRA_TMP")
  read -ra HYDRA_SLOTS <<< "$HYDRA_OUTPUT"
  if [[ ${#HYDRA_SLOTS[@]} -eq 8 ]]; then
    NICHE_MAP=("" "${HYDRA_SLOTS[0]}" "${HYDRA_SLOTS[1]}" "${HYDRA_SLOTS[2]}" "${HYDRA_SLOTS[3]}" "${HYDRA_SLOTS[4]}" "${HYDRA_SLOTS[5]}" "${HYDRA_SLOTS[6]}" "${HYDRA_SLOTS[7]}")
    log "  [HYDRA] NICHE_MAP updated: ${HYDRA_SLOTS[*]}"
  else
    log "  [HYDRA] WARN: canary.py returned ${#HYDRA_SLOTS[@]} slots (expected 8) — using default NICHE_MAP"
  fi
else
  log "  [HYDRA] ERROR: canary.py --niche-map failed (exit $?) — stderr written to log above. Using default NICHE_MAP."
fi
rm -f "$HYDRA_TMP"

# ── #12 Pre-flight API failsafe ──────────────────────────────────────────────
log ""
log "  [PREFLIGHT] Running API health checks..."
if ! python3 "$AGENTS_DIR/preflight.py" >> "$BLITZ_LOG" 2>&1; then
  log "  [PREFLIGHT] FAIL — one or more API checks failed. Aborting blitz."
  exit 1
fi
log "  [PREFLIGHT] PASS — all systems go."
log ""

for i in {1..8}; do
  NICHE="${NICHE_MAP[$i]}"
  SLOT_START=$(date +%s)
  RS="FAIL"; RQ="SKIP"; RV="SKIP"

  log ""
  log "  Slot $i / 8  │  Niche: $NICHE  │  Sched: +$((i * 3))h from now"
  log "────────────────────────────────────────────────────────────"

  # ── Step A: Script generation ─────────────────────────────────
  log "[A] creative_synthesis.py --slot $i --niche $NICHE"
  if python3 "$AGENTS_DIR/creative_synthesis.py" --slot "$i" --niche "$NICHE" \
       >> "$BLITZ_LOG" 2>&1; then
    RS="PASS"
    log "[A] PASS — creative brief generated"
  else
    log "[A] FAIL — attempting self-heal on creative_synthesis.py"
    python3 "$AGENTS_DIR/meta_manager.py" --repair "$AGENTS_DIR/creative_synthesis.py" \
      >> "$BLITZ_LOG" 2>&1 || true
    # Retry once after attempted repair
    if python3 "$AGENTS_DIR/creative_synthesis.py" --slot "$i" --niche "$NICHE" \
         >> "$BLITZ_LOG" 2>&1; then
      RS="PASS"
      # Cross-validate brief niche against CLI niche — mismatch means wrong content was generated
      BRIEF_NICHE=$(python3 -c "
import json, glob, sys
briefs = sorted(glob.glob('$LOGS_DIR/creative_brief_*_slot${i}.json'))
if not briefs: sys.exit(1)
d = json.load(open(briefs[-1]))
print((d.get('script') or {}).get('niche') or d.get('niche') or '')
" 2>/dev/null || echo "")
      if [[ -n "$BRIEF_NICHE" && "$BRIEF_NICHE" != "$NICHE" ]]; then
        log "[A] WARN: brief niche '$BRIEF_NICHE' != CLI niche '$NICHE' — forcing correct niche via re-run"
        python3 "$AGENTS_DIR/creative_synthesis.py" --slot "$i" --niche "$NICHE" >> "$BLITZ_LOG" 2>&1 || true
      fi
      log "[A] PASS — creative brief generated (after self-heal)"
    else
      log "[A] FAIL — creative_synthesis.py failed after self-heal"
      RESULT_ROWS+=("$i|$NICHE|$RS|$RQ|$RV")
      (( FAIL++ )) || true
      continue
    fi
  fi

  # ── Step B: Quality gate ──────────────────────────────────────
  RQ="FAIL"  # step is now being attempted
  log "[B] quality_assessor.py --slot $i --niche $NICHE --max-retries 3"
  if python3 "$AGENTS_DIR/quality_assessor.py" --slot "$i" --niche "$NICHE" \
       --max-retries 3 >> "$BLITZ_LOG" 2>&1; then
    RQ="PASS"
    log "[B] PASS — script passed quality gate"
  else
    log "[B] FAIL — script rejected after max retries"
    RESULT_ROWS+=("$i|$NICHE|$RS|$RQ|$RV")
    (( FAIL++ )) || true
    continue
  fi

  # ── Step C: Render + auto-publish ────────────────────────────
  RV="FAIL"  # step is now being attempted
  log "[C] production_agent.py --slot $i --niche $NICHE"
  if python3 "$AGENTS_DIR/production_agent.py" --slot "$i" --niche "$NICHE" \
       >> "$BLITZ_LOG" 2>&1; then
    RV="PASS"
    log "[C] PASS — rendered and dispatched to Buffer API"
    (( PASS++ )) || true
  else
    log "[C] FAIL — attempting self-heal on production_agent.py"
    python3 "$AGENTS_DIR/meta_manager.py" --repair "$AGENTS_DIR/production_agent.py" \
      >> "$BLITZ_LOG" 2>&1 || true
    # Retry once after attempted repair
    if python3 "$AGENTS_DIR/production_agent.py" --slot "$i" --niche "$NICHE" \
         >> "$BLITZ_LOG" 2>&1; then
      RV="PASS"
      log "[C] PASS — rendered and dispatched (after self-heal)"
      (( PASS++ )) || true
    else
      log "[C] FAIL — production_agent.py failed after self-heal"
      (( FAIL++ )) || true
    fi
  fi

  SLOT_END=$(date +%s)
  log "  Duration: $(( SLOT_END - SLOT_START ))s"
  RESULT_ROWS+=("$i|$NICHE|$RS|$RQ|$RV")
done

# ── Summary ───────────────────────────────────────────────────
log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  RESULTS"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

printf "%-5s %-18s %-12s %-12s %-14s\n" "SLOT" "NICHE" "SCRIPT" "QA_GATE" "RENDER/PUB" \
  | tee -a "$BLITZ_LOG"
printf "%-5s %-18s %-12s %-12s %-14s\n" "----" "-----------------" "----------" "----------" "--------------" \
  | tee -a "$BLITZ_LOG"

for row in "${RESULT_ROWS[@]}"; do
  IFS='|' read -r i niche rs rq rv <<< "$row"
  # Strip any pipe characters that could corrupt the IFS-split (defensive sanitisation)
  niche="${niche//|/_}"
  printf "%-5s %-18s %-12s %-12s %-14s\n" "$i" "$niche" "$rs" "$rq" "$rv" \
    | tee -a "$BLITZ_LOG"
done

log ""
log "  Total:  ${PASS} PASS  /  ${FAIL} FAIL"
log ""
log "  Buffer API scheduling (UTC):"
for i in {1..8}; do
  NICHE="${NICHE_MAP[$i]}"
  log "    Slot $i  (${NICHE})  → +$((i * 3))h from run time"
done
log ""
log "  Log: $BLITZ_LOG"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Post-blitz garbage collection (safety sweep) ─────────────────────────────
# production_agent.py cleans up per-slot on success. This sweep catches any
# intermediates left by failed or aborted slots, or from previous runs.
# Rules:
#   - Delete audio intermediates: audio_*.mp3 mixed_audio_*.mp3 voice_*.mp3
#                                  bgm_*.mp3 ambience_*.mp3 bgm_norm_*.mp3
#   - Delete B-roll intermediates: footage_*.mp4 cache_fallback_*.mp4
#                                   vertical_footage_*.mp4 vertical_cache_fallback_*.mp4
#   - Delete caption files:        captions_*.ass
#   - Delete alignment JSON:        audio_alignment_*.json
#   - Delete assembled intermediates: assembled_*.mp4
#   - KEEP: post_*.mp4 (final renders), production_manifest_*.json, quarantine/
log ""
log "  [GC] Post-blitz output/ safety sweep..."

OUTPUT_DIR="$SCRIPT_DIR/output"
GC_COUNT=0
GC_BYTES=0

sweep_pattern() {
  local pattern="$1"
  for f in $OUTPUT_DIR/$pattern; do
    [[ -f "$f" ]] || continue
    bytes=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
    rm -f "$f" && (( GC_COUNT++ )) || true
    (( GC_BYTES += bytes )) || true
  done
}

sweep_pattern "audio_*.mp3"
sweep_pattern "mixed_audio_*.mp3"
sweep_pattern "voice_tempo_*.mp3"
sweep_pattern "voice_sfx_*.mp3"
sweep_pattern "bgm_*.mp3"
sweep_pattern "bgm_norm_*.mp3"
sweep_pattern "bgm_fallback_*.mp3"
sweep_pattern "ambience_*.mp3"
sweep_pattern "audio_alignment_*.json"
sweep_pattern "captions_*.ass"
sweep_pattern "footage_*.mp4"
sweep_pattern "cache_fallback_*.mp4"
sweep_pattern "vertical_footage_*.mp4"
sweep_pattern "vertical_cache_fallback_*.mp4"
sweep_pattern "assembled_*.mp4"

GC_MB=$(( GC_BYTES / 1048576 ))
log "  [GC] Swept ${GC_COUNT} files, freed ~${GC_MB} MB"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
