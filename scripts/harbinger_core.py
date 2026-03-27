#!/usr/bin/env python3
"""
harbinger_core.py — Harbinger Capital Engine Orchestrator
Manages agent execution, passes outputs, handles failures, escalates via Telegram.

Usage:
  python3 harbinger_core.py --cycle <slot>        # Run full cycle for slot 1/2/3
  python3 harbinger_core.py --agent <name>         # Run single agent
  python3 harbinger_core.py --health               # Health check
  python3 harbinger_core.py --manual-cycle <slot>  # Run cycle, halt before distribute for review
"""
import os, sys, json, time, argparse, datetime, subprocess, traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
SCRIPTS_DIR = BASE_DIR / "scripts"
AGENTS_DIR = SCRIPTS_DIR / "agents"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

CORE_LOG = LOGS_DIR / "harbinger_core.log"
NOW = datetime.datetime.utcnow()
DATE_STR = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

SLOT_TIMES = {1: "06:00", 2: "08:30", 3: "11:00", 4: "13:30", 5: "16:00", 6: "18:30", 7: "21:00"}

def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(CORE_LOG, "a") as f:
        f.write(line + "\n")

def send_telegram(msg: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram send failed: {e}")

def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()

def run_agent(script_path: Path, args: list[str] = [], timeout: int = 600) -> tuple[bool, str]:
    """Run an agent script, return (success, output)."""
    cmd = [sys.executable, str(script_path)] + args
    log(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(BASE_DIR))
        output = result.stdout + result.stderr
        combined = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, result.stdout.strip()
        else:
            log(f"Agent failed (rc={result.returncode}): {combined[-500:]}")
            return False, combined
    except subprocess.TimeoutExpired:
        log(f"Agent timed out after {timeout}s: {script_path.name}")
        return False, "timeout"
    except Exception as e:
        log(f"Agent execution error: {e}")
        return False, str(e)

def handle_agent_failure(agent_name: str, error: str, slot: int, timeout: int = 600) -> bool:
    """Retry twice, then apply agent-specific fallback. Returns True if recovered.
    - production_agent: Kling timeout — retries at same timeout, sends render alert (not 'fallback brief')
    - creative_synthesis: retries, then falls back to previous day's brief
    - others: generic retry + unrecoverable alert
    """
    log(f"FAILURE: {agent_name} — {error[:200]}")

    if agent_name == "production_agent" and "timeout" in error.lower():
        send_telegram(f"⚠️ *Harbinger Slot {slot}*: Production timeout — Kling render stalled. Retrying.")
    else:
        send_telegram(f"⚠️ *Harbinger*: {agent_name} failed\n`{error[:300]}`\nRetrying...")

    script_map = {
        "cultural_radar":         AGENTS_DIR / "cultural_radar.py",
        "creative_synthesis":     AGENTS_DIR / "creative_synthesis.py",
        "production_agent":       AGENTS_DIR / "production_agent.py",
        "quality_mirror":         AGENTS_DIR / "quality_mirror.py",
        "algorithm_intelligence": AGENTS_DIR / "algorithm_intelligence.py",
    }
    script = script_map.get(agent_name)
    if not script:
        return False

    for attempt in range(1, 3):
        log(f"Retry {attempt}/2 for {agent_name} (timeout={timeout}s)...")
        time.sleep(30 * attempt)
        success, _ = run_agent(script, ["--slot", str(slot)] if slot else [], timeout=timeout)
        if success:
            log(f"{agent_name} recovered on retry {attempt}")
            send_telegram(f"✅ *Harbinger*: {agent_name} recovered on retry {attempt}")
            return True

    # Agent-specific exhaustion handling
    if agent_name == "creative_synthesis":
        # Intelligence failure — fall back to previous day's brief
        log(f"{agent_name} failed all retries — attempting fallback to previous brief")
        prev_briefs = sorted(LOGS_DIR.glob("creative_brief_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if len(prev_briefs) > 1:
            fallback = prev_briefs[1]  # second most recent = yesterday's
            log(f"Fallback brief: {fallback.name}")
            send_telegram(f"🔄 *Harbinger*: Intelligence fallback — using previous brief `{fallback.name}`")
            return True
    elif agent_name == "production_agent":
        # Render failure — brief is fine, Kling is stuck
        send_telegram(
            f"🚨 *Harbinger Slot {slot}*: Production Agent unrecoverable — "
            f"Kling stalled after all retries. Manual run required."
        )
        return False

    send_telegram(f"🚨 *Harbinger*: {agent_name} UNRECOVERABLE — manual intervention required")
    return False

def read_delta_report() -> dict:
    """Load Quality Mirror's overnight updates."""
    reports = sorted(LOGS_DIR.glob("delta_report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return {}
    try:
        return json.loads(reports[0].read_text())
    except Exception:
        return {}

def apply_directives():
    """Inject Agent 4's updated weights into scoring/creative files."""
    weights_file = LOGS_DIR / "scoring_weights.json"
    directives_file = LOGS_DIR / "creative_directives.json"
    if weights_file.exists():
        log(f"Scoring weights loaded: {weights_file.name}")
    if directives_file.exists():
        log(f"Creative directives loaded: {directives_file.name}")

def health_check() -> dict:
    """Verify all API keys active, services reachable, and alert on critical thresholds."""
    log("=" * 60)
    log("HARBINGER HEALTH CHECK")
    log("=" * 60)
    results = {}

    # Run gated alert checks (fires Telegram only for the 7 critical conditions)
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import alert_manager
        alert_manager.run_all_checks()
    except Exception as e:
        log(f"Alert manager error: {e}")

    checks = {
        "ELEVENLABS_API_KEY": "ElevenLabs",
        "REPLICATE_API_TOKEN": "Replicate",
        "BUFFER_API_TOKEN": "Buffer",
        "TELEGRAM_BOT_TOKEN": "Telegram",
        "FAL_KEY": "fal.ai (Kling)",
        "REDDIT_CLIENT_ID": "Reddit API",
        "TWITTER_BEARER_TOKEN": "Twitter/X API",
        "YOUTUBE_API_KEY": "YouTube API",
        "CLOUDINARY_API_KEY": "Cloudinary",
    }

    for key, name in checks.items():
        val = os.environ.get(key, "")
        status = "✅ SET" if val else "❌ MISSING"
        log(f"  {name:<20} {status}")
        results[key] = bool(val)

    # Check fal-client installed
    try:
        import importlib
        importlib.import_module("fal_client")
        log("  fal-client           ✅ INSTALLED")
        results["fal_client"] = True
    except ImportError:
        log("  fal-client           ❌ NOT INSTALLED (run: pip install fal-client)")
        results["fal_client"] = False

    log("=" * 60)
    return results

def log_cycle_complete(slot: int, metrics: dict):
    cycle_log = LOGS_DIR / "cycle_metrics.json"
    history = []
    if cycle_log.exists():
        try:
            history = json.loads(cycle_log.read_text())
        except Exception:
            history = []
    history.append({"timestamp": NOW.isoformat(), "slot": slot, **metrics})
    history = history[-100:]  # keep last 100
    cycle_log.write_text(json.dumps(history, indent=2))

def get_production_timeout(slot: int) -> int:
    """Derive production_agent timeout from shot count in most recent prompt_metadata for this slot.
    Formula: max(900, shot_count × 150). Default shot_count=7 → 1050s."""
    meta_files = sorted(
        LOGS_DIR.glob(f"prompt_metadata_*_slot{slot}.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    shot_count = 3  # default — matches prompt_engine's 3-shot structure
    if meta_files:
        try:
            meta = json.loads(meta_files[0].read_text())
            shot_count = len(meta.get("shot_list", []) or []) or 7
        except Exception:
            pass
    return max(900, shot_count * 150)


def run_full_cycle(slot: int, manual: bool = False):
    log("=" * 60)
    log(f"HARBINGER CORE — FULL CYCLE (SLOT {slot}){' [MANUAL]' if manual else ''}")
    log("=" * 60)

    apply_directives()
    metrics = {"slot": slot, "agents": {}}
    start = time.time()

    # Agent 5: Algorithm Intelligence — runs daily on slot 1 cycle only.
    # Updates signal model, refreshes algorithm_directives.json before Agent 2 reads it.
    if slot == 1:
        algo_file = LOGS_DIR / "algorithm_signals.json"
        algo_run_today = False
        if algo_file.exists():
            mtime = datetime.datetime.utcfromtimestamp(algo_file.stat().st_mtime)
            algo_run_today = mtime.date() == NOW.date()
        if not algo_run_today:
            log("Running Algorithm Intelligence (daily pre-step)...")
            ok, _ = run_agent(AGENTS_DIR / "algorithm_intelligence.py", timeout=120)
            metrics["agents"]["algorithm_intelligence"] = "ok" if ok else "failed"
            if not ok:
                log("Algorithm Intelligence failed — continuing with existing directives")
        else:
            log("Algorithm Intelligence already ran today — skipping")
            metrics["agents"]["algorithm_intelligence"] = "skipped_cached"

    # Agent 1: Cultural Radar — runs per slot for fresh SerpAPI trends data.
    # SERP API call budget enforced by 2-hour cache inside cultural_radar.py
    # (max 6 calls/day: morning brief + 5 production slots).
    ok, out = run_agent(AGENTS_DIR / "cultural_radar.py", ["--slot", str(slot)])
    metrics["agents"]["cultural_radar"] = "ok" if ok else "failed"
    if not ok:
        handle_agent_failure("cultural_radar", out, slot)

    # Agent 2: Creative Synthesis
    ok, out = run_agent(AGENTS_DIR / "creative_synthesis.py", ["--slot", str(slot)])
    metrics["agents"]["creative_synthesis"] = "ok" if ok else "failed"
    if not ok:
        recovered = handle_agent_failure("creative_synthesis", out, slot)
        if not recovered:
            log("ABORT: Creative Synthesis unrecoverable")
            return

    # Loop B: £100k Chain Evaluator — runs after Agent 2, before Agent 3.
    # Evaluates visual identity → mechanism → emotional arc → CTA → affiliate → comment trigger.
    # If chain score < 6.5 (rewrite_required), re-runs Agent 2 with Loop B directives injected.
    log("Running Loop B — £100k chain evaluation...")
    loop_b_ok, _ = run_agent(
        AGENTS_DIR / "loop_b_evaluator.py", ["--slot", str(slot)], timeout=90
    )
    if not loop_b_ok:
        # loop_b_evaluator exits 1 when rewrite is required
        log("Loop B: chain score below threshold — re-running Agent 2 with corrective directives")
        ok2, out2 = run_agent(AGENTS_DIR / "creative_synthesis.py", ["--slot", str(slot)])
        metrics["agents"]["creative_synthesis_rewrite"] = "ok" if ok2 else "failed"
        if not ok2:
            log("Agent 2 rewrite failed — continuing with original brief (non-fatal)")
    else:
        log("Loop B: chain score acceptable — proceeding to production")
    metrics["agents"]["loop_b"] = "pass" if loop_b_ok else "rewrite"

    # Agent 3: Production
    prod_timeout = get_production_timeout(slot)
    log(f"Production timeout: {prod_timeout}s ({prod_timeout // 150} shots × 150s, min 900)")
    ok, out = run_agent(AGENTS_DIR / "production_agent.py", ["--slot", str(slot)], timeout=prod_timeout)
    metrics["agents"]["production_agent"] = "ok" if ok else "failed"
    if not ok:
        recovered = handle_agent_failure("production_agent", out, slot, timeout=prod_timeout)
        if not recovered:
            log("ABORT: Production Agent unrecoverable")
            return

    # Agent 4 (micro): Per-render self-improvement — genome + caption AB, no API calls, <60s
    log("Running Quality Mirror micro-loop (per-render)...")
    ok_micro, _ = run_agent(AGENTS_DIR / "quality_mirror.py",
                             ["--micro", "--slot", str(slot)], timeout=120)
    metrics["agents"]["quality_mirror_micro"] = "ok" if ok_micro else "skipped"
    if not ok_micro:
        log("Quality Mirror micro-loop skipped (non-fatal)")

    metrics["duration_s"] = round(time.time() - start, 1)
    log_cycle_complete(slot, metrics)

    # ── Quality gate — check output before distribution ───────────────────────
    # Reads the latest production manifest to get the video path, then verifies:
    #   (a) file size > 5MB  (b) duration 25-90s  (c) resolution 1080x1920
    # If any check fails: Telegram alert + abort before distribute.
    def _quality_gate(slot: int) -> bool:
        """Return True if the render passes all quality checks."""
        import json as _json, subprocess as _sp
        manifests = sorted(
            LOGS_DIR.glob(f"production_manifest_*_slot{slot}.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not manifests:
            log("Quality gate: no production manifest found — skipping gate")
            return True  # can't check, let it through and trust production_agent's own check

        try:
            m = _json.loads(manifests[0].read_text())
        except Exception:
            return True

        video_path_str = m.get("video", "")
        if not video_path_str:
            send_telegram(f"🚨 *Slot {slot} quality gate FAIL*: no video path in manifest")
            return False

        vp = Path(video_path_str)
        if not vp.exists():
            send_telegram(f"🚨 *Slot {slot} quality gate FAIL*: video file missing\n`{vp.name}`")
            return False

        failures = []

        # (a) File size > 5MB
        size_mb = vp.stat().st_size / 1024 / 1024
        if size_mb <= 5:
            failures.append(f"size {size_mb:.1f}MB ≤ 5MB (likely black screen)")

        # (b) Duration 25-90s and (c) Resolution 1080x1920
        try:
            probe = _sp.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_streams", "-show_format", str(vp)],
                capture_output=True, text=True, timeout=30,
            )
            data = _json.loads(probe.stdout)
            vs   = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
            w    = vs.get("width", 0)
            h    = vs.get("height", 0)
            dur  = float(data.get("format", {}).get("duration", 0))

            if not (25 <= dur <= 90):
                failures.append(f"duration {dur:.1f}s not in 25-90s range")
            if not (w == 1080 and h == 1920):
                failures.append(f"resolution {w}x{h} ≠ 1080x1920")
        except Exception as e:
            log(f"Quality gate probe error: {e}")

        if failures:
            reason = " | ".join(failures)
            log(f"QUALITY GATE FAILED (slot {slot}): {reason}")
            send_telegram(
                f"🚨 *Slot {slot} quality gate FAIL — NOT distributing*\n\n"
                f"File: `{vp.name}`\n"
                f"Reason: {reason}"
            )
            return False

        log(f"Quality gate PASSED (slot {slot}): {size_mb:.1f}MB, {dur:.1f}s, {w}x{h}")
        return True

    if not _quality_gate(slot):
        log("ABORT: Quality gate failed — refusing to distribute broken render")
        metrics["agents"]["quality_gate"] = "failed"
        return
    metrics["agents"]["quality_gate"] = "passed"

    if manual:
        log("\n" + "=" * 60)
        log("MANUAL CYCLE COMPLETE — HALTED BEFORE DISTRIBUTE")
        log("Review outputs in output/ and logs/ before approving.")
        log("Run distribute.sh manually when ready.")
        log("=" * 60)
        send_telegram(
            f"🔍 *Harbinger Manual Cycle — Slot {slot} Complete*\n"
            f"Agents: {metrics['agents']}\n"
            f"Duration: {metrics['duration_s']}s\n"
            f"Review outputs before distributing."
        )
    else:
        # Run distribute — call Python directly, not the shell wrapper
        distribute_py = SCRIPTS_DIR / "distribute.py"
        ok, _ = run_agent(distribute_py, ["--slot", str(slot)])
        metrics["agents"]["distribute"] = "ok" if ok else "failed"

        # ── Post-distribution verification ──────────────────────────────────
        # Read the manifest written by distribute.py and check per-platform results.
        # If any platform failed, wait 30s and retry once with --retry-failed.
        def _read_dist_manifest(slot: int) -> dict:
            from pathlib import Path as _P
            import json as _json
            date_str = datetime.datetime.utcnow().strftime("%Y%m%d")
            manifests = sorted((_P(str(LOGS_DIR))).glob(f"manifest_{date_str}*_slot{slot}.json"))
            if manifests:
                try:
                    return _json.loads(manifests[-1].read_text())
                except Exception:
                    pass
            return {}

        def _platform_status_line(results: dict) -> str:
            icons = {"posted": "✅", "failed": "❌", "no_channel": "⚠️"}
            lines = []
            for p in ["tiktok", "youtube", "instagram"]:
                r = results.get(p, {})
                st = r.get("status", "unknown")
                icon = icons.get(st, "❓")
                post_id = r.get("post_id", "")
                lines.append(f"{icon} {p.capitalize()}: {st}" + (f" ({post_id[:12]})" if post_id else ""))
            return "\n".join(lines)

        dist_manifest = _read_dist_manifest(slot)
        api_results   = dist_manifest.get("api_results", {})
        failed_platforms = [p for p, r in api_results.items() if r.get("status") == "failed"]

        if failed_platforms:
            log(f"Distribution: {failed_platforms} failed — waiting 30s then retrying...")
            import time as _time
            _time.sleep(30)
            ok_retry, _ = run_agent(distribute_py, ["--slot", str(slot), "--retry-failed"])
            metrics["agents"]["distribute_retry"] = "ok" if ok_retry else "failed"
            dist_manifest = _read_dist_manifest(slot)
            api_results   = dist_manifest.get("api_results", {})

        status_line  = _platform_status_line(api_results)
        all_posted   = all(r.get("status") == "posted" for r in api_results.values() if r)
        hook_preview = (dist_manifest.get("hook") or "")[:80]

        send_telegram(
            f"{'✅' if all_posted else '⚠️'} *Slot {slot} distributed*\n\n"
            f"{status_line}\n\n"
            f"Hook: _{hook_preview}_\n"
            f"Sched: {dist_manifest.get('scheduled_at','?')}"
        )

        # Platform metrics scrape — runs after every distribution so Quality Mirror
        # loops have real views/likes/comments data instead of zeros.
        platform_metrics_py = SCRIPTS_DIR / "platform_metrics.py"
        if platform_metrics_py.exists():
            log("Running platform metrics scraper...")
            ok_pm, _ = run_agent(platform_metrics_py, [], timeout=120)
            metrics["agents"]["platform_metrics"] = "ok" if ok_pm else "failed"
            if not ok_pm:
                log("Platform metrics scraper failed (non-fatal)")

        # CEO audit — fires after every distribution (success or failure)
        # Sends one additional Telegram message: brutal, specific, no padding.
        ceo_audit_py = AGENTS_DIR / "ceo_audit.py"
        if ceo_audit_py.exists():
            log("Running CEO audit...")
            ok_audit, _ = run_agent(ceo_audit_py, ["--slot", str(slot)], timeout=60)
            metrics["agents"]["ceo_audit"] = "ok" if ok_audit else "skipped"
            if not ok_audit:
                log("CEO audit skipped (non-fatal)")

def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycle", type=int, choices=list(range(1, 8)), help="Run full cycle for slot")
    parser.add_argument("--manual-cycle", type=int, choices=list(range(1, 8)), dest="manual_cycle",
                        help="Run cycle, halt before distribute for review")
    parser.add_argument("--agent", choices=["cultural_radar", "creative_synthesis", "production_agent", "quality_mirror", "algorithm_intelligence"])
    parser.add_argument("--slot", type=int, default=1, choices=list(range(1, 8)))
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    if args.health:
        health_check()
    elif args.cycle:
        run_full_cycle(args.cycle)
    elif args.manual_cycle:
        run_full_cycle(args.manual_cycle, manual=True)
    elif args.agent:
        script = AGENTS_DIR / f"{args.agent}.py"
        ok, out = run_agent(script, ["--slot", str(args.slot)])
        if not ok:
            log(f"Agent failed: {out}")
            sys.exit(1)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
