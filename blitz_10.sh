#!/usr/bin/env bash
# blitz_10.sh — Niche-routed batch orchestrator: 10 slots across 3 niches.
#
# Niche split:
#   Slots 1, 4, 7, 10  → tech_ai
#   Slots 2, 5, 8      → dark_psychology
#   Slots 3, 6, 9      → micro_mystery
#
# Each slot calls creative_synthesis.py then production_agent.py with
# --slot N --niche <niche>. Scheduling in distribute.py uses 2.5-hour
# intervals starting at 07:00 UTC (slot 1 = 07:00, slot 2 = 09:30, ...).
#
# Usage:
#   ./blitz_10.sh
#
# Output: ads_ready_for_review/ — each slot auto-publishes via distribute.py.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$SCRIPT_DIR/scripts/agents"
ADS_READY="$SCRIPT_DIR/ads_ready_for_review"

mkdir -p "$ADS_READY"

# Load .env
if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a; source "$SCRIPT_DIR/.env"; set +a
fi

# Niche map indexed by slot (1-based, element 0 unused)
NICHE_MAP=("" "tech_ai" "dark_psychology" "micro_mystery" "tech_ai" "dark_psychology" "micro_mystery" "tech_ai" "dark_psychology" "micro_mystery" "tech_ai")

PASS=0
FAIL=0

for i in {1..10}; do
  NICHE="${NICHE_MAP[$i]}"

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Slot $i / 10  │  Niche: $NICHE"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Step 1: Script generation
  echo "  [A] creative_synthesis.py --slot $i --niche $NICHE"
  if ! python3 "$AGENTS_DIR/creative_synthesis.py" --slot "$i" --niche "$NICHE"; then
    echo "  [FAIL] creative_synthesis.py exited non-zero — skipping slot $i"
    (( FAIL++ )) || true
    continue
  fi
  echo "  [A] PASS — creative brief generated"

  # Step 2: Audio + video render + auto-publish
  echo "  [B] production_agent.py --slot $i --niche $NICHE"
  if python3 "$AGENTS_DIR/production_agent.py" --slot "$i" --niche "$NICHE"; then
    echo ""
    echo "  ✓ Slot $i complete  │  Niche: $NICHE  │  Scheduled via Buffer API"
    (( PASS++ )) || true
  else
    echo ""
    echo "  ✗ Slot $i FAILED  │  production_agent.py exited non-zero"
    (( FAIL++ )) || true
  fi
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  BLITZ 10 COMPLETE  │  ${PASS} PASS  /  ${FAIL} FAIL"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if ls "$ADS_READY"/post_*.mp4 &>/dev/null; then
  echo "  Archived renders in ads_ready_for_review/:"
  echo ""
  ls -lht "$ADS_READY"/post_*.mp4 2>/dev/null \
    | awk '{printf "  %-10s  %s\n", $5, $NF}'
else
  echo "  (no output files in ads_ready_for_review/)"
fi

echo ""
echo "  Scheduling summary (Buffer API, UTC):"
echo "    Slot  1 (Tech)       → 07:00"
echo "    Slot  2 (Psychology) → 09:30"
echo "    Slot  3 (Mystery)    → 12:00"
echo "    Slot  4 (Tech)       → 14:30"
echo "    Slot  5 (Psychology) → 17:00"
echo "    Slot  6 (Mystery)    → 19:30"
echo "    Slot  7 (Tech)       → 22:00"
echo "    Slot  8 (Psychology) → 00:30 +1d"
echo "    Slot  9 (Mystery)    → 03:00 +1d"
echo "    Slot 10 (Tech)       → 05:30 +1d"
echo ""
