#!/usr/bin/env bash
# cleanup.sh — Harbinger Capital Engine nightly housekeeping
# Runs automatically via cron at 23:45 UTC.
# Deletes: stale logs (>7d), output intermediates (>1d), pycache, .DS_Store,
#          upload_* compressed copies, empty dirs.

HARBINGER="/Users/kolly/HARBINGERHQ"
LOG="$HARBINGER/logs/cleanup.log"
NOW=$(date -u '+%Y-%m-%d %H:%M:%S')

log() { echo "[$NOW UTC] $1" | tee -a "$LOG"; }

log "── CLEANUP START ──────────────────────────────────────"

# ── 1. Stale timestamped log files >7 days ────────────────────────────────────
# Covers: manifest_*, production_manifest_*, micro_delta_*, delta_report_*,
#         observed_behavior_*, asymmetry_brief_* — anything with a date stamp.
STALE_LOGS=$(find "$HARBINGER/logs" -maxdepth 1 -type f \
  \( -name "manifest_[0-9]*" \
     -o -name "production_manifest_[0-9]*" \
     -o -name "micro_delta_[0-9]*" \
     -o -name "delta_report_[0-9]*" \
     -o -name "observed_behavior_*" \
     -o -name "asymmetry_brief_*" \
     -o -name "blitz_8_[0-9]*.log" \
     -o -name "blitz_resume.log" \
  \) -mtime +7 2>/dev/null)

if [ -n "$STALE_LOGS" ]; then
  COUNT=$(echo "$STALE_LOGS" | wc -l | tr -d ' ')
  log "Deleting $COUNT stale log files (>7d)..."
  echo "$STALE_LOGS" | xargs rm -f
else
  log "No stale log files to remove"
fi

# ── 2. Output intermediates >1 day ────────────────────────────────────────────
# Keeps final post_*.mp4 renders. Deletes everything else:
# assembled_*, audio_*, mixed_audio_*, voice_*, bgm_*, captions_*, audio_alignment_*
# upload_* (our compress-for-cloudinary copies), *.part (failed yt-dlp downloads)
INTERMEDIATES=$(find "$HARBINGER/output" -maxdepth 1 -type f \
  \( -name "assembled_*" \
     -o -name "audio_*" \
     -o -name "mixed_audio_*" \
     -o -name "voice_*" \
     -o -name "bgm_*" \
     -o -name "captions_*" \
     -o -name "audio_alignment_*" \
     -o -name "upload_*" \
     -o -name "*.part" \
     -o -name "*.mp3" \
     -o -name "*.ass" \
     -o -name "*.json" \
  \) -mtime +1 2>/dev/null)

if [ -n "$INTERMEDIATES" ]; then
  COUNT=$(echo "$INTERMEDIATES" | wc -l | tr -d ' ')
  SIZE=$(echo "$INTERMEDIATES" | xargs du -sh 2>/dev/null | tail -1 | awk '{print $1}')
  log "Deleting $COUNT output intermediates (>1d)..."
  echo "$INTERMEDIATES" | xargs rm -f
else
  log "No output intermediates to remove"
fi

# ── 3. Final renders >14 days (already distributed, no longer needed) ─────────
OLD_RENDERS=$(find "$HARBINGER/output" -maxdepth 1 -type f -name "post_*.mp4" -mtime +14 2>/dev/null)
if [ -n "$OLD_RENDERS" ]; then
  COUNT=$(echo "$OLD_RENDERS" | wc -l | tr -d ' ')
  log "Deleting $COUNT old final renders (>14d)..."
  echo "$OLD_RENDERS" | xargs rm -f
else
  log "No old final renders to remove"
fi

# ── 4. scripts/logs stale files ───────────────────────────────────────────────
find "$HARBINGER/scripts/logs" -maxdepth 1 -type f -mtime +7 -delete 2>/dev/null && \
  log "scripts/logs: stale files cleared" || true

# ── 5. __pycache__ ────────────────────────────────────────────────────────────
find "$HARBINGER" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
log "__pycache__ cleared"

# ── 6. .DS_Store ─────────────────────────────────────────────────────────────
find "$HARBINGER" -name ".DS_Store" -delete 2>/dev/null
log ".DS_Store cleared"

# ── 7. Empty directories (except quarantine, which is intentional) ────────────
find "$HARBINGER/output" -mindepth 1 -maxdepth 1 -type d -empty \
  ! -name "quarantine" -delete 2>/dev/null
log "Empty dirs cleared"

# ── 8. Cleanup log self-rotation (keep last 200 lines) ───────────────────────
if [ -f "$LOG" ]; then
  tail -200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

log "── CLEANUP DONE ───────────────────────────────────────"
