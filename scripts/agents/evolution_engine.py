#!/usr/bin/env python3
"""
evolution_engine.py — Self-improvement module (Gaps #2 + #3: Prompt Evolution + Micro-Editing A/B).

Pipeline:
  1. Read logs/performance_memory.json
  2. Identify top 20% winners and bottom 20% losers by view count
  3. LLM rewrites SCRIPT_PROMPT_TEMPLATE in creative_synthesis.py to mirror winners
  4. Mutate FFmpeg micro-editing parameters (zoom_factor, ssml_break_secs) toward winners
  5. Write new baseline to logs/evolution_params.json
     → production_agent.py and creative_synthesis.py read this file each run

Requires: ≥8 audited entries in performance_memory.json before any evolution fires.

Usage:
  python3 evolution_engine.py
"""
import os, sys, json, datetime, re, ast, shutil, random
from pathlib import Path

BASE_DIR        = Path(__file__).parent.parent.parent
AGENTS_DIR      = Path(__file__).parent
SCRIPTS_DIR     = Path(__file__).parent.parent
LOGS_DIR        = BASE_DIR / "logs"
PERF_MEM        = LOGS_DIR / "performance_memory.json"
EVOLUTION_PARAMS= LOGS_DIR / "evolution_params.json"
EVOLUTION_LOG   = LOGS_DIR / "evolution_engine.log"

CREATIVE_SYNTHESIS = AGENTS_DIR / "creative_synthesis.py"

MIN_ENTRIES_TO_EVOLVE = 8     # need at least this many data points
WINNER_PERCENTILE     = 0.20  # top 20%
LOSER_PERCENTILE      = 0.20  # bottom 20%

# Param mutation bounds
ZOOM_MIN, ZOOM_MAX         = 0.05, 0.30   # zoom_factor (1+X at peak)
BREAK_MIN, BREAK_MAX       = 0.4,  1.5    # ssml_break_secs
MUTATION_SIGMA             = 0.03         # std-dev for Gaussian mutation


def log(msg: str):
    line = f"[{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] [evolution] {msg}"
    print(line)
    LOGS_DIR.mkdir(exist_ok=True)
    with open(EVOLUTION_LOG, "a") as f:
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


def load_perf_memory() -> dict:
    if PERF_MEM.exists():
        try:
            return json.loads(PERF_MEM.read_text())
        except Exception:
            pass
    return {"entries": []}


def load_evolution_params() -> dict:
    defaults = {
        "zoom_factor":          0.15,
        "ssml_break_secs":      0.8,
        "prompt_version":       1,
        "last_evolved_at":      None,
        "evolution_count":      0,
        "baseline_zoom":        0.15,
        "baseline_break":       0.8,
    }
    if EVOLUTION_PARAMS.exists():
        try:
            stored = json.loads(EVOLUTION_PARAMS.read_text())
            defaults.update(stored)
        except Exception:
            pass
    return defaults


def save_evolution_params(params: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    EVOLUTION_PARAMS.write_text(json.dumps(params, indent=2))


# ---------------------------------------------------------------------------
# Prompt evolution (#2)
# ---------------------------------------------------------------------------
def _call_llm(prompt: str, replicate_token: str, max_tokens: int = 800) -> str | None:
    """Call Replicate LLM. Returns raw text or None."""
    if not replicate_token:
        return None
    try:
        import replicate
        output = replicate.run(
            "meta/meta-llama-3.1-405b-instruct",
            input={"prompt": prompt, "max_tokens": max_tokens, "temperature": 0.7},
        )
        return "".join(output).strip()
    except Exception as e:
        log(f"LLM error: {e}")
        return None


def evolve_prompt_template(winners: list[dict], losers: list[dict], replicate_token: str) -> str | None:
    """
    Ask the LLM to rewrite the SCRIPT_PROMPT_TEMPLATE based on winner vs loser script analysis.
    Returns a new template string, or None if evolution fails validation.

    REQUIRED placeholders that must survive: {hook}, {niche_label}, {framework}, {word_target}
    JSON schema must use {{ and }} for literal braces.
    """
    winner_scripts = "\n---\n".join(
        f"[{e['niche']} | {e['views']} views | {e['sentiment']}]\n{e.get('script_text','')[:300]}"
        for e in winners[:4]
    )
    loser_scripts = "\n---\n".join(
        f"[{e['niche']} | {e['views']} views | {e['sentiment']}]\n{e.get('script_text','')[:300]}"
        for e in losers[:4]
    )

    # Read current template from file
    current_source = CREATIVE_SYNTHESIS.read_text()
    tmpl_match = re.search(
        r'SCRIPT_PROMPT_TEMPLATE\s*=\s*"""([\s\S]+?)"""',
        current_source,
    )
    if not tmpl_match:
        log("Cannot find SCRIPT_PROMPT_TEMPLATE in creative_synthesis.py — skipping prompt evolution")
        return None
    current_template = tmpl_match.group(1)

    prompt = f"""You are an expert viral video scriptwriter and prompt engineer.

You are analyzing the performance of TikTok/Reels short-form video scripts.
Your task: rewrite the SCRIPT_PROMPT_TEMPLATE to maximize views.

CURRENT TEMPLATE:
\"\"\"
{current_template[:1200]}
\"\"\"

HIGH-PERFORMING SCRIPTS (winners — mimic their psychological structure):
{winner_scripts}

LOW-PERFORMING SCRIPTS (losers — discard their traits):
{loser_scripts}

Rules:
- Keep these EXACT placeholders: {{hook}}, {{niche_label}}, {{framework}}, {{word_target}}
- Keep the JSON output schema with double-braces: {{{{ and }}}} for literal braces
- Make the new prompt more psychologically potent based on winners
- Keep it under 600 words
- The output format section must remain: Output ONLY valid JSON...

Return ONLY the new template content between triple quotes. No other text."""

    raw = _call_llm(prompt, replicate_token, max_tokens=1000)
    if not raw:
        return None

    # Extract the template — remove any triple-quote wrapping
    raw = re.sub(r'^"""', '', raw.strip())
    raw = re.sub(r'"""$', '', raw.strip())
    new_template = raw.strip()

    # Validate required placeholders
    required = ["{hook}", "{niche_label}", "{framework}", "{word_target}"]
    for ph in required:
        if ph not in new_template:
            log(f"  Evolved template missing placeholder {ph} — rejecting")
            return None

    # Ensure JSON schema uses double-braces (LLM may have used single)
    # Check that the JSON schema block has {{ and }}
    if '"script": {' in new_template and '"script": {{' not in new_template:
        log("  Evolved template has unescaped braces in JSON schema — auto-fixing")
        # Replace the JSON schema block carefully
        new_template = re.sub(
            r'(\{)\s*\n\s*("script")',
            r'{{\n  \2',
            new_template,
        )
        new_template = re.sub(r'(?<!\{)\{(?!\{)(\s*"(?:hook|body|cta|full_script_text)")',
                               r'{{\1', new_template)
        new_template = re.sub(r'(?<!\})(?!\})\}(\s*(?:,|\n\s*\}))',
                               r'}}\1', new_template)

    log(f"  New template: {len(new_template)} chars")
    return new_template


def patch_prompt_template(new_template: str, version: int) -> bool:
    """Overwrite SCRIPT_PROMPT_TEMPLATE in creative_synthesis.py. Backs up original first."""
    source = CREATIVE_SYNTHESIS.read_text()
    pattern = r'(SCRIPT_PROMPT_TEMPLATE\s*=\s*""")[\s\S]+?(""")'
    replacement = f'SCRIPT_PROMPT_TEMPLATE = """\n{new_template}\n"""'
    new_source, count = re.subn(pattern, replacement, source, count=1)
    if count == 0:
        log("  Patch failed: SCRIPT_PROMPT_TEMPLATE not found")
        return False

    # Validate Python syntax before writing
    try:
        ast.parse(new_source)
    except SyntaxError as e:
        log(f"  Patched source has syntax error: {e} — aborting")
        return False

    # Backup
    backup = CREATIVE_SYNTHESIS.with_suffix(f".py.bak_v{version}")
    shutil.copy(str(CREATIVE_SYNTHESIS), str(backup))
    log(f"  Backup: {backup.name}")

    CREATIVE_SYNTHESIS.write_text(new_source)
    log(f"  SCRIPT_PROMPT_TEMPLATE patched (v{version})")
    return True


# ---------------------------------------------------------------------------
# Micro-editing parameter evolution (#3)
# ---------------------------------------------------------------------------
def _mutate(value: float, lo: float, hi: float, sigma: float = MUTATION_SIGMA) -> float:
    """Gaussian mutation clamped to [lo, hi], rounded to 2dp."""
    import random as _r
    new_val = value + _r.gauss(0, sigma)
    return round(max(lo, min(hi, new_val)), 2)


def evolve_ffmpeg_params(entries: list[dict], current_params: dict) -> dict:
    """
    Correlate zoom_factor and ssml_break_secs with view counts.
    Shift baselines toward the winning parameter values.
    Also generate an A/B test mutation for the next batch.

    Returns updated params dict.
    """
    # Filter entries that have both ffmpeg_params and view counts
    valid = [
        e for e in entries
        if e.get("views") is not None and e.get("ffmpeg_params")
    ]
    if len(valid) < 4:
        log(f"  FFmpeg evolution: insufficient data ({len(valid)} entries with params) — skipping")
        return current_params

    # Sort by views
    valid.sort(key=lambda e: e["views"] or 0, reverse=True)
    n_win = max(1, len(valid) // 5)
    n_los = max(1, len(valid) // 5)
    winners = valid[:n_win]
    losers  = valid[-n_los:]

    def avg_param(group: list[dict], key: str) -> float | None:
        vals = [e["ffmpeg_params"].get(key) for e in group if e["ffmpeg_params"].get(key) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    win_zoom  = avg_param(winners, "zoom_factor")
    win_break = avg_param(winners, "ssml_break_secs")
    los_zoom  = avg_param(losers,  "zoom_factor")
    los_break = avg_param(losers,  "ssml_break_secs")

    log(f"  Param analysis: winner zoom={win_zoom} break={win_break} | loser zoom={los_zoom} break={los_break}")

    # Move baseline toward winner average (50% blend), then mutate for A/B test
    params = current_params.copy()

    if win_zoom is not None:
        new_baseline_zoom  = round((params["baseline_zoom"] + win_zoom) / 2, 3)
        params["baseline_zoom"]  = new_baseline_zoom
        params["zoom_factor"]    = _mutate(new_baseline_zoom, ZOOM_MIN, ZOOM_MAX)
        log(f"  zoom_factor: {current_params['zoom_factor']} → baseline={new_baseline_zoom} next={params['zoom_factor']}")

    if win_break is not None:
        new_baseline_break = round((params["baseline_break"] + win_break) / 2, 3)
        params["baseline_break"] = new_baseline_break
        params["ssml_break_secs"]= _mutate(new_baseline_break, BREAK_MIN, BREAK_MAX)
        log(f"  ssml_break: {current_params['ssml_break_secs']} → baseline={new_baseline_break} next={params['ssml_break_secs']}")

    return params


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_evolution() -> bool:
    load_env()
    log("=" * 60)
    log("EVOLUTION ENGINE — self-improvement run")
    log("=" * 60)

    perf_mem      = load_perf_memory()
    entries       = perf_mem.get("entries", [])
    replicate_tok = os.environ.get("REPLICATE_API_TOKEN", "")

    if len(entries) < MIN_ENTRIES_TO_EVOLVE:
        log(f"Insufficient data: {len(entries)} entries (need ≥{MIN_ENTRIES_TO_EVOLVE}) — skipping")
        return False

    # Filter entries with view data; sort descending
    with_views = [e for e in entries if e.get("views") is not None]
    if len(with_views) < MIN_ENTRIES_TO_EVOLVE:
        log(f"Insufficient view data: {len(with_views)} entries with views — skipping")
        return False

    with_views.sort(key=lambda e: e["views"] or 0, reverse=True)
    n_win = max(1, int(len(with_views) * WINNER_PERCENTILE))
    n_los = max(1, int(len(with_views) * LOSER_PERCENTILE))
    winners = with_views[:n_win]
    losers  = with_views[-n_los:]

    log(f"Data: {len(with_views)} entries | winners={n_win} (≥{winners[-1]['views']} views) | losers={n_los}")

    params  = load_evolution_params()
    evolved = False

    # ── Prompt evolution ─────────────────────────────────────────────────────
    log("Phase 1: Prompt evolution")
    new_template = evolve_prompt_template(winners, losers, replicate_tok)
    if new_template:
        ver = params.get("prompt_version", 1) + 1
        if patch_prompt_template(new_template, ver):
            params["prompt_version"] = ver
            evolved = True
    else:
        log("  Prompt evolution skipped (LLM unavailable or validation failed)")

    # ── FFmpeg param evolution ────────────────────────────────────────────────
    log("Phase 2: FFmpeg parameter evolution")
    params = evolve_ffmpeg_params(entries, params)

    # Randomise for A/B test this batch (already done inside evolve_ffmpeg_params)
    log(f"  Next batch params: zoom_factor={params['zoom_factor']} ssml_break={params['ssml_break_secs']}s")

    params["last_evolved_at"] = datetime.datetime.utcnow().isoformat()
    params["evolution_count"] = params.get("evolution_count", 0) + 1
    save_evolution_params(params)
    log(f"Evolution params saved (run #{params['evolution_count']})")

    return evolved


def main():
    run_evolution()


if __name__ == "__main__":
    main()
