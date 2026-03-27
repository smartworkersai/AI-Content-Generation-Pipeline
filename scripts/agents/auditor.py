#!/usr/bin/env python3
"""
auditor.py — 24-hour self-auditing module (Gap #4: Qualitative Sentiment Analysis).

Pipeline:
  1. Load last 8 production manifests
  2. Query Buffer API for per-post impressions
  3. Feed comment context + performance data into LLM for sentiment classification
     (Hooked / Entertained / Confused / Bored / Angry)
  4. Append structured results to logs/performance_memory.json

Usage:
  python3 auditor.py
"""
import os, sys, json, datetime, time, re
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent.parent
LOGS_DIR  = BASE_DIR / "logs"
PERF_MEM  = LOGS_DIR / "performance_memory.json"
AUDIT_LOG = LOGS_DIR / "auditor.log"

SENTIMENT_LABELS = ["Hooked", "Entertained", "Confused", "Bored", "Angry"]


def log(msg: str):
    line = f"[{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] [auditor] {msg}"
    print(line)
    LOGS_DIR.mkdir(exist_ok=True)
    with open(AUDIT_LOG, "a") as f:
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
    return {"entries": [], "last_audit": None}


def save_perf_memory(data: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    PERF_MEM.write_text(json.dumps(data, indent=2))


def load_recent_manifests(n: int = 8) -> list[dict]:
    manifests = sorted(
        LOGS_DIR.glob("production_manifest_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[:n]
    result = []
    for mp in manifests:
        try:
            data = json.loads(mp.read_text())
            data["_manifest_file"] = mp.name
            result.append(data)
        except Exception:
            continue
    return result


def get_buffer_impressions(token: str, post_id: str) -> int | None:
    """Query Buffer GraphQL for post impressions. Returns count or None."""
    if not token or not post_id:
        return None
    try:
        import requests
        r = requests.post(
            "https://api.bufferapp.com/graphql",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"query": f'{{ post(id: "{post_id}") {{ statistics {{ impressions }} }} }}'},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data  = r.json()
        post  = (data.get("data") or {}).get("post", {})
        stats = post.get("statistics") or {}
        val   = stats.get("impressions")
        return int(val) if val is not None else None
    except Exception as e:
        log(f"Buffer impressions error [{post_id[:8]}]: {e}")
        return None


def classify_sentiment(
    script_text: str,
    views: int | None,
    niche: str,
    replicate_token: str,
) -> dict:
    """
    Use LLM to classify audience sentiment for a video given its script + view count.
    Falls back to a heuristic when LLM is unavailable.
    Returns {"label": str, "summary": str, "action": str}
    """
    if not replicate_token:
        # Heuristic fallback
        if views is None:
            return {"label": "Unknown", "summary": "No analytics available.", "action": "none"}
        if views >= 50_000:
            return {"label": "Hooked",       "summary": "High views suggest strong hook performance.", "action": "replicate"}
        if views >= 10_000:
            return {"label": "Entertained",  "summary": "Solid mid-tier performance.", "action": "replicate"}
        if views >= 1_000:
            return {"label": "Bored",        "summary": "Below-average — hook or pacing may be weak.", "action": "rewrite_body"}
        return {"label": "Confused",     "summary": "Very low views — hook clarity likely the issue.", "action": "rewrite_hook"}

    prompt = f"""You are a viral content analyst reviewing a short-form video performance.

Niche: {niche}
Views: {views if views is not None else "unknown"}
Script (excerpt): {script_text[:400]}

Based on the view count and script quality, classify audience sentiment as exactly one of:
  Hooked, Entertained, Confused, Bored, Angry

Then:
1. Write 1-2 sentences explaining WHY the video performed this way.
2. Suggest ONE concrete action: "replicate", "rewrite_hook", "rewrite_body", "change_niche", or "none".

Output ONLY valid JSON (no markdown):
{{"label": "...", "summary": "...", "action": "..."}}"""

    try:
        import replicate
        output = replicate.run(
            "meta/meta-llama-3.1-405b-instruct",
            input={"prompt": prompt, "max_tokens": 250, "temperature": 0.3},
        )
        raw   = "".join(output).strip()
        match = re.search(r'\{[\s\S]+?\}', raw)
        if match:
            result = json.loads(match.group())
            label  = result.get("label", "Unknown")
            if label not in SENTIMENT_LABELS:
                label = "Unknown"
            return {
                "label":   label,
                "summary": result.get("summary", ""),
                "action":  result.get("action", "none"),
            }
    except Exception as e:
        log(f"LLM sentiment error: {e}")

    return {"label": "Unknown", "summary": "LLM unavailable.", "action": "none"}


def run_audit() -> dict:
    load_env()
    log("=" * 60)
    log("AUDITOR — 24-hour performance audit")
    log("=" * 60)

    buffer_token  = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    replicate_tok = os.environ.get("REPLICATE_API_TOKEN", "")
    manifests     = load_recent_manifests(8)
    perf_mem      = load_perf_memory()
    existing      = {e.get("manifest_file") for e in perf_mem.get("entries", [])}

    new_entries = 0
    for m in manifests:
        mfile = m.get("_manifest_file", "")
        if mfile in existing:
            log(f"  Already audited: {mfile}")
            continue

        slot    = m.get("slot", 0)
        script  = m.get("script") or {}
        niche   = script.get("niche", "tech_ai") if isinstance(script, dict) else "tech_ai"
        hook    = script.get("hook", "")        if isinstance(script, dict) else ""
        ts      = m.get("timestamp", "")
        post_id = (m.get("distribute_result") or {}).get("post_id", "")
        ffp     = m.get("ffmpeg_evolution_params", {})

        log(f"  Auditing slot={slot} niche={niche} post={post_id[:8] if post_id else '?'}")

        views = get_buffer_impressions(buffer_token, post_id)

        script_text = " ".join(filter(None, [
            script.get("hook", ""),
            script.get("body", ""),
            script.get("cta", ""),
        ])) if isinstance(script, dict) else ""

        sentiment = classify_sentiment(script_text, views, niche, replicate_tok)
        log(f"  → views={views} | {sentiment['label']} | action={sentiment['action']}")
        log(f"     {sentiment['summary'][:100]}")

        perf_mem["entries"].append({
            "manifest_file":     mfile,
            "timestamp":         ts,
            "slot":              slot,
            "niche":             niche,
            "hook":              hook,
            "script_text":       script_text[:500],
            "script_variant":    m.get("script_variant", "unknown"),
            "views":             views,
            "sentiment":         sentiment["label"],
            "sentiment_summary": sentiment["summary"],
            "sentiment_action":  sentiment["action"],
            "ffmpeg_params":     ffp,
            "audited_at":        datetime.datetime.utcnow().isoformat(),
        })
        new_entries += 1
        time.sleep(1)

    perf_mem["last_audit"] = datetime.datetime.utcnow().isoformat()
    save_perf_memory(perf_mem)
    log(f"Audit complete: {new_entries} new | {len(perf_mem['entries'])} total in memory")
    return perf_mem


def main():
    run_audit()


if __name__ == "__main__":
    main()
