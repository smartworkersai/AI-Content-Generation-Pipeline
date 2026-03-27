#!/usr/bin/env python3
"""
timing_analyser.py — Character-level audio timing analysis
Converts ElevenLabs character-level alignment data into word-level timing,
delivery pace scores, and structural pause maps.

Used by:
  - quality_mirror.py --micro (after every render, measures actual pacing)
  - pre_slot_intelligence.py (reads historical pacing for experiment design)
  - Loop 2 (Silence Geometry) — replaces estimated pause durations with measured ones

Output: logs/timing_report_[timestamp]_slot[n].json
"""

import json
import re
import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"


# ---------------------------------------------------------------------------
# Core: characters → words
# ---------------------------------------------------------------------------
def extract_word_timings(alignment: dict) -> list[dict]:
    """Convert character-level ElevenLabs alignment into word-level timing dicts.

    Each word dict: {word, start_s, end_s, duration_ms, gap_before_ms}
    gap_before_ms = silence between previous word end and this word start.
    """
    chars   = alignment.get("characters", [])
    starts  = alignment.get("character_start_times_seconds", [])
    ends    = alignment.get("character_end_times_seconds", [])

    if not chars or len(chars) != len(starts):
        return []

    # Group into words — split on space/newline, carry timing
    words = []
    current_chars   = []
    current_starts  = []
    current_ends    = []

    for ch, s, e in zip(chars, starts, ends):
        if ch in (" ", "\n", "\t"):
            if current_chars:
                words.append((current_chars, current_starts, current_ends))
                current_chars, current_starts, current_ends = [], [], []
        else:
            current_chars.append(ch)
            current_starts.append(s)
            current_ends.append(e)

    if current_chars:
        words.append((current_chars, current_starts, current_ends))

    # Build result list, stripping ElevenLabs voice-tag tokens [serious tone] etc.
    result = []
    prev_end_s = 0.0
    tag_pattern = re.compile(r"^\[.*\]$")

    for w_chars, w_starts, w_ends in words:
        word_str = "".join(w_chars)
        if tag_pattern.match(word_str):
            prev_end_s = w_ends[-1] if w_ends else prev_end_s
            continue
        if not word_str.strip():
            continue

        word_start = w_starts[0]
        word_end   = w_ends[-1]
        gap_ms     = round((word_start - prev_end_s) * 1000)

        result.append({
            "word":         word_str,
            "start_s":      round(word_start, 3),
            "end_s":        round(word_end, 3),
            "duration_ms":  round((word_end - word_start) * 1000),
            "gap_before_ms": max(gap_ms, 0),
        })
        prev_end_s = word_end

    return result


# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------
def analyse_hook_pace(word_timings: list[dict], hook_window_s: float = 3.0) -> dict:
    """Words per second in the hook window (first N seconds of actual speech).

    The hook window starts at first word start, not at 0.0.
    """
    if not word_timings:
        return {"hook_wps": 0, "hook_word_count": 0, "hook_window_s": hook_window_s}

    hook_start = word_timings[0]["start_s"]
    hook_end   = hook_start + hook_window_s
    hook_words = [w for w in word_timings if w["start_s"] < hook_end]

    wps = round(len(hook_words) / hook_window_s, 2) if hook_window_s > 0 else 0
    return {
        "hook_wps":         wps,
        "hook_word_count":  len(hook_words),
        "hook_window_s":    hook_window_s,
        "hook_words":       " ".join(w["word"] for w in hook_words),
    }


def find_pause_points(word_timings: list[dict], threshold_ms: int = 200) -> list[dict]:
    """Identify natural pause points where gap_before_ms >= threshold.

    Returns list of pause dicts: {after_word, pause_ms, position_s, position_pct}
    position_pct is where in the total narration (0.0–1.0) the pause falls.
    """
    if not word_timings:
        return []

    total_duration = word_timings[-1]["end_s"] - word_timings[0]["start_s"]
    pauses = []

    for i, word in enumerate(word_timings):
        if i == 0:
            continue
        if word["gap_before_ms"] >= threshold_ms:
            prev_word = word_timings[i - 1]
            position_s = prev_word["end_s"]
            pct = (position_s - word_timings[0]["start_s"]) / total_duration if total_duration > 0 else 0
            pauses.append({
                "after_word":  prev_word["word"],
                "before_word": word["word"],
                "pause_ms":    word["gap_before_ms"],
                "position_s":  round(position_s, 3),
                "position_pct": round(pct, 3),
            })

    return pauses


def score_delivery_pacing(word_timings: list[dict]) -> dict:
    """Overall pacing score: average WPS, peak density zone, tempo variance."""
    if len(word_timings) < 3:
        return {"overall_wps": 0, "peak_zone": "unknown", "tempo_variance": 0}

    total_words    = len(word_timings)
    total_duration = word_timings[-1]["end_s"] - word_timings[0]["start_s"]
    overall_wps    = round(total_words / total_duration, 2) if total_duration > 0 else 0

    # Split into thirds: HOOK / MECHANISM / MOVE
    third = total_duration / 3
    origin = word_timings[0]["start_s"]
    zones  = {"hook": [], "mechanism": [], "move": []}

    for w in word_timings:
        rel = w["start_s"] - origin
        if rel < third:
            zones["hook"].append(w)
        elif rel < third * 2:
            zones["mechanism"].append(w)
        else:
            zones["move"].append(w)

    zone_wps = {}
    for name, words in zones.items():
        if words:
            span = (words[-1]["end_s"] - words[0]["start_s"]) or 1
            zone_wps[name] = round(len(words) / span, 2)
        else:
            zone_wps[name] = 0

    peak_zone = max(zone_wps, key=zone_wps.get)

    # Tempo variance: std dev of inter-word gaps (excluding pauses >500ms)
    gaps = [w["gap_before_ms"] for w in word_timings[1:] if w["gap_before_ms"] < 500]
    if gaps:
        mean_gap = sum(gaps) / len(gaps)
        variance = sum((g - mean_gap) ** 2 for g in gaps) / len(gaps)
        tempo_variance = round(variance ** 0.5, 1)
    else:
        tempo_variance = 0

    return {
        "overall_wps":    overall_wps,
        "zone_wps":       zone_wps,
        "peak_zone":      peak_zone,
        "tempo_variance": tempo_variance,
        "total_words":    total_words,
        "duration_s":     round(total_duration, 2),
    }


def measure_pre_hook_silence(alignment: dict) -> float:
    """Milliseconds of silence before the first real word (post voice-tag)."""
    starts = alignment.get("character_start_times_seconds", [])
    chars  = alignment.get("characters", [])
    tag_pattern = re.compile(r"\[.*?\]")
    raw_text = "".join(chars)
    clean_start_idx = len(re.match(r"(\[.*?\]\s*)*", raw_text).group(0)) if raw_text else 0

    if clean_start_idx < len(starts):
        return round(starts[clean_start_idx] * 1000)
    return 0


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------
def analyse_slot(alignment_path: Path) -> dict:
    """Run full analysis on one audio_alignment file. Returns timing report dict."""
    alignment = json.loads(alignment_path.read_text())
    word_timings = extract_word_timings(alignment)

    hook_pace        = analyse_hook_pace(word_timings)
    pauses           = find_pause_points(word_timings, threshold_ms=200)
    delivery         = score_delivery_pacing(word_timings)
    pre_hook_silence = measure_pre_hook_silence(alignment)

    # Structural pause summary (named by position)
    structural_pauses = {
        "after_hook_ms":      next((p["pause_ms"] for p in pauses if p["position_pct"] < 0.25), 0),
        "after_mechanism_ms": next((p["pause_ms"] for p in pauses if 0.25 <= p["position_pct"] < 0.65), 0),
        "before_move_ms":     next((p["pause_ms"] for p in pauses if p["position_pct"] >= 0.65), 0),
    }

    return {
        "source_file":       alignment_path.name,
        "pre_hook_silence_ms": pre_hook_silence,
        "hook_pace":         hook_pace,
        "delivery":          delivery,
        "pauses_detected":   len(pauses),
        "pause_map":         pauses,
        "structural_pauses": structural_pauses,
        "word_count":        len(word_timings),
    }


def save_timing_report(report: dict, timestamp: str, slot: int) -> Path:
    out = LOGS_DIR / f"timing_report_{timestamp}_slot{slot}.json"
    out.write_text(json.dumps(report, indent=2))
    return out


def load_latest_timing_reports(n: int = 10) -> list[dict]:
    """Load last N timing reports across all slots for trend analysis."""
    files = sorted(LOGS_DIR.glob("timing_report_*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:n]
    reports = []
    for f in files:
        try:
            reports.append(json.loads(f.read_text()))
        except Exception:
            pass
    return reports


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, required=True)
    parser.add_argument("--timestamp", type=str, default=None,
                        help="Match alignment file by timestamp prefix")
    args = parser.parse_args()

    OUTPUT_DIR = BASE_DIR / "output"
    pattern = f"audio_alignment_{args.timestamp}_slot{args.slot}.json" \
        if args.timestamp else f"audio_alignment_*_slot{args.slot}.json"

    candidates = sorted(OUTPUT_DIR.glob(pattern),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        print(f"No alignment file found for slot {args.slot}", file=sys.stderr)
        sys.exit(1)

    alignment_path = candidates[0]
    print(f"Analysing: {alignment_path.name}", file=sys.stderr)
    report = analyse_slot(alignment_path)

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    saved = save_timing_report(report, ts, args.slot)
    print(f"Saved: {saved}", file=sys.stderr)
    print(json.dumps(report, indent=2))
