#!/usr/bin/env python3
"""
canary.py — Shadowban canary + Hydra Optimizer for Harbinger blitz runs.

Reads the 5 most recent production manifests, queries Buffer API for impression
counts on posts older than 12 hours, and detects shadowban patterns.

Outputs:
  SHADOWBAN_LOCK            — written to BASE_DIR if all ≥3 recent posts have 0 views
  logs/canary_report.json   — per-niche view averages (used by Hydra Optimizer)

Exit codes:
  0 = normal (no shadowban)
  2 = shadowban lock written

Flags:
  --niche-map   Print space-separated Hydra-optimised niche list for 8 slots (for blitz_8.sh)

Usage:
  python3 canary.py
  python3 canary.py --niche-map
"""
import os, sys, json, datetime
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent.parent
LOGS_DIR    = BASE_DIR / "logs"
LOCK_FILE   = BASE_DIR / "SHADOWBAN_LOCK"
REPORT_FILE = LOGS_DIR / "canary_report.json"

SHADOWBAN_THRESHOLD_HOURS = 12   # only evaluate posts older than this
SHADOWBAN_MIN_POSTS       = 3    # need at least this many qualifying posts to declare shadowban


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


def load_recent_manifests(n: int = 5) -> list[dict]:
    """Load the N most recently modified distribution manifests (manifest_*.json).

    These are written by distribute.py after every successful publish cycle and
    contain api_results with per-platform post_ids.  Falls back to
    production_manifest_*.json (no post_ids, but has niche/timestamp) when no
    distribution manifests exist yet.
    """
    dist_manifests = sorted(
        LOGS_DIR.glob("manifest_[0-9]*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n]
    if dist_manifests:
        result = []
        for mp in dist_manifests:
            try:
                data = json.loads(mp.read_text())
                data["_manifest_path"] = str(mp)
                # Normalise: distribution manifests store post_id inside api_results.
                # Canary's main loop expects distribute_result.post_id — bridge the gap.
                api_results = data.get("api_results", {})
                for platform in ("youtube", "instagram", "tiktok"):
                    pid = (api_results.get(platform) or {}).get("post_id", "")
                    if pid:
                        data.setdefault("distribute_result", {})["post_id"] = pid
                        break
                result.append(data)
            except Exception:
                continue
        if result:
            return result

    # Fallback: production manifests (no post_ids, shadowban detection only)
    prod_manifests = sorted(
        LOGS_DIR.glob("production_manifest_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n]
    result = []
    for mp in prod_manifests:
        try:
            data = json.loads(mp.read_text())
            data["_manifest_path"] = str(mp)
            result.append(data)
        except Exception:
            continue
    return result


def query_buffer_impressions(buffer_token: str, post_id: str) -> int | None:
    """Query Buffer v1 REST API for post impressions. Returns count or None."""
    if not buffer_token or not post_id:
        return None
    try:
        import requests
        r = requests.get(
            f"https://api.bufferapp.com/1/updates/{post_id}.json",
            params={"access_token": buffer_token},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        stats = data.get("statistics") or {}
        return int(stats.get("impressions", 0) or 0)
    except Exception:
        return None


def compute_hydra_niche_map(report: dict) -> list[str]:
    """
    Compute optimal 8-slot niche distribution from per-niche view averages (#14).
    Default: tech=3, psych=3, mystery=2.
    If one niche has ≥3× the views of the worst niche, bias +2 slots toward the winner
    and -2 slots from the loser (minimum 1 slot each, total must stay at 8).
    """
    niches = report.get("niches", {})
    # Floor all values to 0.0 — corrupted JSON can produce negative floats which
    # invert max()/min() comparisons and produce incorrect slot weightings.
    tech  = max(0.0, float(niches.get("tech_ai")         or 0.0))
    psych = max(0.0, float(niches.get("dark_psychology") or 0.0))
    myst  = max(0.0, float(niches.get("micro_mystery")   or 0.0))

    t_slots, p_slots, m_slots = 3, 3, 2   # default

    best = max(tech, psych, myst)
    if best > 0:
        worst = min(tech, psych, myst)
        if best >= 3.0 * max(worst, 0.1):
            vals    = [tech, psych, myst]
            labels  = ["tech_ai", "dark_psychology", "micro_mystery"]
            best_n  = labels[vals.index(max(vals))]
            worst_n = labels[vals.index(min(vals))]

            if best_n == "tech_ai":         t_slots = 5
            elif best_n == "dark_psychology": p_slots = 5
            else:                             m_slots = 5

            if worst_n == "tech_ai":          t_slots = max(1, t_slots - 2)
            elif worst_n == "dark_psychology": p_slots = max(1, p_slots - 2)
            else:                              m_slots = max(1, m_slots - 2)

            # Guard: if total drifts from 8, revert to default
            if t_slots + p_slots + m_slots != 8:
                t_slots, p_slots, m_slots = 3, 3, 2

    slots = (
        ["tech_ai"]          * t_slots +
        ["dark_psychology"]  * p_slots +
        ["micro_mystery"]    * m_slots
    )
    return slots[:8]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--niche-map", action="store_true",
                        help="Output space-separated 8-slot niche list for blitz_8.sh and exit")
    args = parser.parse_args()

    load_env()

    # --niche-map mode: read existing report (if any) and output Hydra map
    if args.niche_map:
        report = {}
        if REPORT_FILE.exists():
            try:
                report = json.loads(REPORT_FILE.read_text())
            except Exception:
                pass
        slots = compute_hydra_niche_map(report)
        print(" ".join(slots))
        sys.exit(0)

    buffer_token = os.environ.get("BUFFER_API_TOKEN", "")
    manifests    = load_recent_manifests(5)
    now          = datetime.datetime.utcnow()

    if not manifests:
        print("[canary] No production manifests found — nothing to check")
        LOGS_DIR.mkdir(exist_ok=True)
        REPORT_FILE.write_text(json.dumps({
            "checked_at":      now.isoformat(),
            "posts_checked":   0,
            "zero_view_posts": 0,
            "niches":          {},
            "shadowban_detected": False,
        }, indent=2))
        sys.exit(0)

    zero_view_count = 0
    checked_count   = 0
    niche_views: dict[str, list[int]] = {
        "tech_ai":         [],
        "dark_psychology": [],
        "micro_mystery":   [],
    }

    for m in manifests:
        ts_str  = m.get("timestamp", "")
        # Niche can be in script.niche or top-level
        script = m.get("script", {})
        if isinstance(script, dict):
            niche = script.get("niche") or m.get("niche")
        else:
            niche = m.get("niche")
        if not niche or niche not in ("tech_ai", "dark_psychology", "micro_mystery"):
            print(f"[canary] SKIP manifest ts={ts_str[:19]} — corrupt/missing niche (got {niche!r}), not defaulting to tech_ai")
            continue
        post_id = (m.get("distribute_result") or {}).get("post_id", "")

        try:
            post_time = datetime.datetime.fromisoformat(ts_str)
            age_hours = (now - post_time).total_seconds() / 3600
        except Exception:
            age_hours = 0.0

        if age_hours < SHADOWBAN_THRESHOLD_HOURS:
            print(f"[canary] Post {(post_id or '?')[:8]} is {age_hours:.1f}h old — too recent, skipping")
            continue

        # Only count this post if we can actually query the API.
        # "No post_id" or "no token" means no data — not the same as 0 views.
        if not buffer_token:
            print(f"[canary] BUFFER_ACCESS_TOKEN not set — skipping analytics (not counting as 0)")
            continue
        if not post_id:
            print(f"[canary] niche={niche} | no post_id in manifest — skipping (not counting as 0)")
            continue

        result = query_buffer_impressions(buffer_token, post_id)
        if result is None:
            print(f"[canary] niche={niche} | age={age_hours:.1f}h | API query failed — skipping")
            continue

        views     = result
        niche_key = niche if niche in niche_views else "tech_ai"
        niche_views[niche_key].append(views)
        checked_count += 1
        if views == 0:
            zero_view_count += 1
        print(f"[canary] niche={niche} | age={age_hours:.1f}h | impressions={views}")

    # Per-niche averages
    niche_averages: dict[str, float | None] = {}
    for n, vlist in niche_views.items():
        niche_averages[n] = round(sum(vlist) / len(vlist), 1) if vlist else None

    # Shadowban detection: all qualifying posts have 0 views
    shadowban = (
        checked_count >= SHADOWBAN_MIN_POSTS and
        zero_view_count == checked_count
    )

    report = {
        "checked_at":        now.isoformat(),
        "posts_checked":     checked_count,
        "zero_view_posts":   zero_view_count,
        "niches":            niche_averages,
        "shadowban_detected": shadowban,
    }
    LOGS_DIR.mkdir(exist_ok=True)
    REPORT_FILE.write_text(json.dumps(report, indent=2))
    print(f"[canary] Report saved: {REPORT_FILE.name}")
    print(f"[canary] Niches: {niche_averages}")

    if shadowban:
        reason = (
            f"{zero_view_count}/{checked_count} recent posts have 0 impressions "
            f"after {SHADOWBAN_THRESHOLD_HOURS}h"
        )
        print(f"[canary] SHADOWBAN DETECTED: {reason}")
        LOCK_FILE.write_text(
            f"SHADOWBAN_LOCK written at {now.isoformat()}\n"
            f"Reason: {reason}\n"
            f"Delete this file manually after verifying account health to re-enable blitz runs.\n"
        )
        print(f"[canary] LOCK written: {LOCK_FILE}")
        sys.exit(2)
    else:
        print(f"[canary] No shadowban ({zero_view_count}/{checked_count} zero-view posts checked)")
        # Auto-remove stale lock if performance has recovered
        if LOCK_FILE.exists() and checked_count >= SHADOWBAN_MIN_POSTS and zero_view_count < checked_count:
            LOCK_FILE.unlink()
            print(f"[canary] Stale SHADOWBAN_LOCK removed — performance recovered")
        sys.exit(0)


if __name__ == "__main__":
    main()
