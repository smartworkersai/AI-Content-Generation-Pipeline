#!/usr/bin/env python3
"""
quality_assessor.py — Pre-flight script quality gate.

Runs after creative_synthesis.py, before production_agent.py.
Checks every generated brief for:
  1. Word count ≤ 70 words
  2. Verbatim hook present in full_script_text
  3. A recognised CTA phrase present

On any failure: re-runs creative_synthesis.py --slot N --niche NICHE and
re-assesses. Retries up to --max-retries times before giving up and exiting 1.

Exit codes:
  0 — brief passed (or was fixed within retry budget)
  1 — brief failed all retries, do not proceed to render

Usage:
  python3 quality_assessor.py --slot 1 [--niche tech_ai] [--max-retries 3]
"""
from __future__ import annotations
import os, sys, json, re, datetime, subprocess
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent.parent
LOGS_DIR  = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
QA_LOG    = LOGS_DIR / "quality_assessor.log"
NOW       = datetime.datetime.utcnow()
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

VALID_NICHES   = ["tech_ai", "dark_psychology", "micro_mystery"]
MAX_WORDS      = 70
# Each entry is a regex pattern matched with word boundaries against lowercased script text.
# Use full phrases before single words to avoid ambiguous partial matches.
CTA_PATTERNS = [
    r"\bfollow for more\b",
    r"\blink in bio\b",
    r"\blet me know in the comments\b",
    r"\bsave this\b",
    r"\btag someone\b",
    r"\bcomment below\b",
    r"\bwhat do you think\b",
    r"\bfollow me\b",
    r"\bfollow\b",
]


def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] [quality_assessor] {msg}"
    print(line)
    with open(QA_LOG, "a") as f:
        f.write(line + "\n")


def load_env():
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() and k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip()


def load_latest_brief(slot: int) -> tuple[Path | None, dict]:
    """Return (path, brief_dict) for the most recent brief for this slot.
    Sorts by the YYYYMMDD_HHMMSS timestamp embedded in the filename — deterministic
    even when multiple files are created within the same filesystem mtime resolution."""
    def _filename_ts(p: Path) -> str:
        # Filename: creative_brief_YYYYMMDD_HHMMSS_slot{n}.json
        # Extract the timestamp portion; fall back to empty string (sorts last)
        m = re.search(r'creative_brief_(\d{8}_\d{6})_slot', p.name)
        return m.group(1) if m else ""

    briefs = sorted(
        LOGS_DIR.glob(f"creative_brief_*_slot{slot}.json"),
        key=_filename_ts, reverse=True,
    )
    if not briefs:
        return None, {}
    try:
        return briefs[0], json.loads(briefs[0].read_text())
    except Exception as e:
        log(f"Failed to load brief: {e}")
        return None, {}


def _word_count(text: str) -> int:
    return len(text.split())


def assess(brief: dict) -> list[str]:
    """
    Run quality checks. Returns list of failure reason strings (empty = pass).
    """
    failures = []
    full_text = (brief.get("full_script_text") or "").strip()
    script    = brief.get("script", {})
    hook      = (script.get("hook") or brief.get("topic") or "").strip()

    # Check 1: word count
    wc = _word_count(full_text)
    if wc > MAX_WORDS:
        failures.append(f"word_count={wc} exceeds {MAX_WORDS}")

    # Check 2: verbatim hook present
    if hook and hook.lower() not in full_text.lower():
        failures.append(f"hook not found verbatim: '{hook[:60]}'")

    # Check 3: CTA present — word-boundary regex prevents "following" matching "follow" etc.
    text_lower = full_text.lower()
    if not any(re.search(pattern, text_lower) for pattern in CTA_PATTERNS):
        failures.append("no recognised CTA phrase found")

    return failures


def regenerate(slot: int, niche: str) -> bool:
    """Re-run creative_synthesis.py for this slot. Returns True on success."""
    synthesis_script = Path(__file__).parent / "creative_synthesis.py"
    cmd = [sys.executable, str(synthesis_script), "--slot", str(slot), "--niche", niche]
    log(f"Regenerating: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            log("Regeneration succeeded")
            return True
        log(f"Regeneration failed (exit {result.returncode}): {result.stderr[-200:]}")
        return False
    except subprocess.TimeoutExpired:
        log("Regeneration timed out (120s)")
        return False
    except Exception as e:
        log(f"Regeneration error: {e}")
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",        type=int, required=True, choices=list(range(1, 11)))
    parser.add_argument("--niche",       type=str, default=None, choices=VALID_NICHES)
    parser.add_argument("--max-retries", type=int, default=3)
    args = parser.parse_args()

    load_env()

    slot       = args.slot
    niche      = args.niche
    max_retries = args.max_retries

    log("=" * 60)
    log(f"QUALITY ASSESSOR — SLOT {slot}  (max retries: {max_retries})")
    log("=" * 60)

    for attempt in range(max_retries + 1):
        brief_path, brief = load_latest_brief(slot)

        if not brief:
            log(f"No brief found for slot {slot}")
            if attempt < max_retries:
                _niche = niche or "tech_ai"
                log(f"Attempting regeneration ({attempt + 1}/{max_retries})...")
                if not regenerate(slot, _niche):
                    log(f"Regeneration failed (attempt {attempt + 1}/{max_retries}) — will retry")
                continue
            sys.exit(1)

        # Auto-detect niche from brief if not provided via CLI
        _niche = niche or brief.get("niche", "tech_ai")

        failures = assess(brief)

        if not failures:
            log(f"PASS — brief '{brief_path.name}' passed all checks")
            log(f"  Words:   {_word_count(brief.get('full_script_text', ''))}")
            log(f"  Hook:    {brief.get('script', {}).get('hook', '')[:60]}")
            log(f"  Niche:   {brief.get('niche_label', _niche)}")
            log("=" * 60)
            sys.exit(0)

        log(f"FAIL (attempt {attempt + 1}/{max_retries + 1}): {'; '.join(failures)}")

        if attempt < max_retries:
            log(f"Triggering regeneration for slot {slot} niche={_niche}...")
            regenerate(slot, _niche)
        else:
            log(f"ABORT — brief failed quality gate after {max_retries + 1} assessments")
            log("=" * 60)
            sys.exit(1)

    sys.exit(1)


if __name__ == "__main__":
    main()
