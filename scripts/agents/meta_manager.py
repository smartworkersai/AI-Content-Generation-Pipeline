#!/usr/bin/env python3
"""
meta_manager.py — Self-management orchestrator (Gap #5: Codebase Self-Healing + Scheduling).

Roles:
  --schedule  : Run auditor / evolution_engine / trend_scraper on their respective intervals.
                Reads/writes logs/meta_state.json for timing.

  --repair <file> : Extract the most recent Python traceback from logs/,
                    identify the broken function, ask the LLM to fix it,
                    syntax-validate, backup the original, patch the file.
                    Exit 0 = repaired, Exit 1 = could not repair.

Default (no flags): equivalent to --schedule.

Scheduling intervals (configurable via META_AUDIT_HOURS etc. in .env):
  auditor.py         — every 24 hours
  evolution_engine.py — every 48 hours
  trend_scraper.py   — every 168 hours (7 days)

Usage:
  python3 meta_manager.py                     # run scheduling check
  python3 meta_manager.py --schedule
  python3 meta_manager.py --repair scripts/agents/creative_synthesis.py
"""
import os, sys, json, datetime, re, ast, shutil, subprocess, traceback
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent.parent
AGENTS_DIR = Path(__file__).parent
LOGS_DIR   = BASE_DIR / "logs"
META_STATE = LOGS_DIR / "meta_state.json"
META_LOG   = LOGS_DIR / "meta_manager.log"

AUDIT_INTERVAL_H     = int(os.environ.get("META_AUDIT_HOURS",    "24"))
EVOLUTION_INTERVAL_H = int(os.environ.get("META_EVOLUTION_HOURS","48"))
TREND_INTERVAL_H     = int(os.environ.get("META_TREND_HOURS",    "168"))


def log(msg: str):
    line = f"[{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] [meta_manager] {msg}"
    print(line)
    LOGS_DIR.mkdir(exist_ok=True)
    with open(META_LOG, "a") as f:
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


def load_meta_state() -> dict:
    if META_STATE.exists():
        try:
            return json.loads(META_STATE.read_text())
        except Exception:
            pass
    return {"last_audit": None, "last_evolution": None, "last_trend_scrape": None, "repair_count": 0}


def save_meta_state(state: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    META_STATE.write_text(json.dumps(state, indent=2))


def _hours_since(iso_ts: str | None) -> float:
    """Return fractional hours since an ISO timestamp, or infinity if None."""
    if not iso_ts:
        return float("inf")
    try:
        dt   = datetime.datetime.fromisoformat(iso_ts)
        diff = datetime.datetime.utcnow() - dt
        return diff.total_seconds() / 3600
    except Exception:
        return float("inf")


def _run_agent(script_name: str) -> bool:
    """Run a Python agent script from AGENTS_DIR. Returns True on success."""
    script_path = AGENTS_DIR / script_name
    log(f"Running: {script_name}")
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=300,
            env={**os.environ},
        )
        for line in result.stdout.splitlines():
            log(f"  {line}")
        if result.returncode != 0:
            log(f"  FAILED (exit {result.returncode}): {result.stderr[-300:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT: {script_name} did not complete within 300s — skipping, continuing schedule")
        return False
    except Exception as e:
        log(f"  Error running {script_name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def run_schedule():
    """Run auditor / evolution_engine / trend_scraper based on elapsed time."""
    state = load_meta_state()
    ran_any = False

    # Auditor — every 24h
    if _hours_since(state.get("last_audit")) >= AUDIT_INTERVAL_H:
        log(f"Auditor due (last run: {state.get('last_audit', 'never')})")
        if _run_agent("auditor.py"):
            state["last_audit"] = datetime.datetime.utcnow().isoformat()
            ran_any = True
        save_meta_state(state)

    # Evolution engine — every 48h
    if _hours_since(state.get("last_evolution")) >= EVOLUTION_INTERVAL_H:
        log(f"Evolution engine due (last run: {state.get('last_evolution', 'never')})")
        if _run_agent("evolution_engine.py"):
            state["last_evolution"] = datetime.datetime.utcnow().isoformat()
            ran_any = True
        save_meta_state(state)

    # Trend scraper — every 7 days
    if _hours_since(state.get("last_trend_scrape")) >= TREND_INTERVAL_H:
        log(f"Trend scraper due (last run: {state.get('last_trend_scrape', 'never')})")
        if _run_agent("trend_scraper.py"):
            state["last_trend_scrape"] = datetime.datetime.utcnow().isoformat()
            ran_any = True
        save_meta_state(state)

    if not ran_any:
        log("All modules up to date — nothing scheduled this run")


# ---------------------------------------------------------------------------
# Codebase self-healing (#5)
# ---------------------------------------------------------------------------
def _extract_recent_traceback(log_file: Path | None = None) -> str | None:
    """
    Find the most recent Python traceback in the blitz log or production log.
    Returns the traceback string or None.
    """
    candidate_logs = []
    if log_file and log_file.exists():
        candidate_logs.append(log_file)
    # Fall back to most recent blitz log
    blitz_logs = sorted(LOGS_DIR.glob("blitz_8_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidate_logs.extend(blitz_logs[:3])
    candidate_logs.append(LOGS_DIR / "production_quality.log")

    for lf in candidate_logs:
        if not lf.exists():
            continue
        text = lf.read_text(errors="replace")
        # Find last Traceback block
        matches = list(re.finditer(
            r'Traceback \(most recent call last\):[\s\S]+?(?=\n\S|\Z)',
            text,
        ))
        if matches:
            return matches[-1].group().strip()
    return None


def _get_function_bounds(source: str, func_name: str) -> tuple[int, int] | None:
    """
    Return (start_line, end_line) — 0-indexed, end exclusive — for the given function.
    Only matches top-level module functions to avoid patching nested helpers with the same name.
    """
    try:
        tree = ast.parse(source)
        for node in tree.body:  # top-level only — ast.walk() descends into nested scopes
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == func_name:
                    return (node.lineno - 1, node.end_lineno)   # end_lineno is 1-indexed
    except Exception:
        pass
    return None


def _call_llm_repair(broken_source: str, traceback_text: str, replicate_token: str) -> str | None:
    """Ask the LLM to fix a broken Python module. Returns corrected source code or None."""
    # Try Anthropic Claude first (better at code), then Replicate
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            prompt = (
                f"The following Python module has a fatal error.\n\n"
                f"TRACEBACK:\n{traceback_text[-1500:]}\n\n"
                f"CURRENT SOURCE:\n```python\n{broken_source[:4000]}\n```\n\n"
                f"Fix ONLY the broken function(s) indicated in the traceback. "
                f"Return the COMPLETE corrected Python source file. "
                f"Preserve all imports, constants, and other functions exactly. "
                f"Return ONLY the Python code block — no explanation."
            )
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            code_match = re.search(r'```python\n([\s\S]+?)\n```', raw)
            if not code_match:
                log("  Anthropic repair: no ```python``` block in response — aborting repair (refusing to parse raw prose)")
                return None
            return code_match.group(1).strip()
        except Exception as e:
            log(f"  Anthropic repair attempt failed: {e}")

    if replicate_token:
        try:
            import replicate
            prompt = (
                f"Fix this broken Python module. Return ONLY the corrected Python code, no explanation.\n\n"
                f"TRACEBACK:\n{traceback_text[-800:]}\n\n"
                f"MODULE:\n```python\n{broken_source[:3000]}\n```"
            )
            output = replicate.run(
                "meta/meta-llama-3.1-405b-instruct",
                input={"prompt": prompt, "max_tokens": 3000, "temperature": 0.2},
            )
            raw = "".join(output).strip()
            code_match = re.search(r'```python\n([\s\S]+?)\n```', raw)
            if not code_match:
                log("  Replicate repair: no ```python``` block in response — aborting repair (refusing to parse raw prose)")
                return None
            return code_match.group(1).strip()
        except Exception as e:
            log(f"  Replicate repair attempt failed: {e}")

    return None


def run_repair(target_file: str) -> bool:
    """
    Self-healing: identify the traceback, ask LLM to repair the target file,
    validate syntax, backup original, apply patch.
    Returns True on successful repair, False otherwise.
    """
    load_env()
    target_path = Path(target_file)
    if not target_path.is_absolute():
        # Try relative to BASE_DIR
        target_path = BASE_DIR / target_file
    if not target_path.exists():
        log(f"REPAIR: target file not found: {target_file}")
        return False

    log(f"REPAIR: starting self-healing for {target_path.name}")

    # Step 1: Extract traceback
    tb = _extract_recent_traceback()
    if not tb:
        log("REPAIR: no recent traceback found in logs — cannot proceed")
        return False
    log(f"REPAIR: traceback extracted ({len(tb)} chars)")
    log(f"  {tb[:300].replace(chr(10), ' | ')}")

    # Step 2: Confirm the traceback implicates this file
    if target_path.name not in tb and str(target_path) not in tb:
        log(f"REPAIR: traceback does not reference {target_path.name} — skipping")
        return False

    # Step 3: Read broken source
    broken_source = target_path.read_text()

    # Step 4: Ask LLM for repair
    replicate_tok = os.environ.get("REPLICATE_API_TOKEN", "")
    repaired_source = _call_llm_repair(broken_source, tb, replicate_tok)

    if not repaired_source:
        log("REPAIR: LLM could not produce a patch (no API keys configured or all attempts failed)")
        return False

    # Step 5: Validate syntax
    try:
        ast.parse(repaired_source)
    except SyntaxError as e:
        log(f"REPAIR: LLM patch has syntax error: {e} — rejecting")
        return False

    # Step 6: Backup and apply
    state        = load_meta_state()
    repair_count = state.get("repair_count", 0) + 1
    backup_path  = target_path.with_suffix(f".py.repair_bak_{repair_count}")
    shutil.copy(str(target_path), str(backup_path))
    log(f"REPAIR: backup saved → {backup_path.name}")

    target_path.write_text(repaired_source)
    log(f"REPAIR: patch applied to {target_path.name}")

    state["repair_count"] = repair_count
    state[f"last_repair_{target_path.stem}"] = datetime.datetime.utcnow().isoformat()
    save_meta_state(state)

    log(f"REPAIR: {target_path.name} successfully self-healed (repair #{repair_count})")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--schedule", action="store_true", help="Run scheduled meta-tasks")
    parser.add_argument("--repair",   type=str, default=None, metavar="FILE",
                        help="Self-heal: repair the named Python file using LLM")
    args = parser.parse_args()

    load_env()

    if args.repair:
        success = run_repair(args.repair)
        sys.exit(0 if success else 1)
    else:
        # Default: run schedule (works for both --schedule and bare invocation)
        run_schedule()


if __name__ == "__main__":
    main()
