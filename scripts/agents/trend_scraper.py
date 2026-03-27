#!/usr/bin/env python3
"""
trend_scraper.py — Weekly self-research module (Gap #1: Dynamic Trend Sourcing).

Pipeline:
  1. Scrape fast-rising topics from Reddit public JSON API + Google Trends RSS
  2. Check per-niche performance from performance_memory.json
  3. If a niche consistently underperforms (avg views < 30% of best niche):
       — Replace its hooks with trend-derived alternatives
       — Update its B-roll search queries
  4. Write logs/niche_overrides.json
     → creative_synthesis.py and footage_sourcer.py load this on each run

Usage:
  python3 trend_scraper.py
"""
import os, sys, json, datetime, re, time
from pathlib import Path

BASE_DIR         = Path(__file__).parent.parent.parent
LOGS_DIR         = BASE_DIR / "logs"
NICHE_OVERRIDES  = LOGS_DIR / "niche_overrides.json"
PERF_MEM         = LOGS_DIR / "performance_memory.json"
SCRAPER_LOG      = LOGS_DIR / "trend_scraper.log"

VALID_NICHES = ["tech_ai", "dark_psychology", "micro_mystery"]

# Fallback niche if a current niche underperforms — keyed by underperforming niche
TREND_REPLACEMENT_CANDIDATES = {
    "tech_ai":         "stoic_philosophy",
    "dark_psychology": "conspiracy_facts",
    "micro_mystery":   "true_crime_unsolved",
}

# Default replacement hooks and queries when trend scraping yields nothing novel
REPLACEMENT_HOOKS = {
    "stoic_philosophy": [
        "Marcus Aurelius said one thing 2000 years ago that fixes modern anxiety.",
        "Stoics knew about dopamine long before scientists did.",
        "This Stoic technique silences your inner critic in 10 seconds.",
    ],
    "conspiracy_facts": [
        "This government document was declassified last year and nobody noticed.",
        "The conspiracy theory that turned out to be 100 percent true.",
        "They lied about this for 50 years and now the truth is public record.",
    ],
    "true_crime_unsolved": [
        "This case was closed but the evidence does not add up.",
        "The most unsettling cold case that was solved by a TikTok comment.",
        "Police sealed this file for 30 years. Here is what was inside.",
    ],
}

REPLACEMENT_VIDEO_QUERIES = {
    "stoic_philosophy": [
        "ancient rome aesthetic cinematic 4k",
        "stone carved ancient text close up 4k",
    ],
    "conspiracy_facts": [
        "declassified document close up dramatic lighting 4k",
        "government archive hallway cinematic 4k",
    ],
    "true_crime_unsolved": [
        "crime scene police tape night dramatic 4k",
        "missing poster close up moody lighting 4k",
    ],
}


def log(msg: str):
    line = f"[{datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] [trend_scraper] {msg}"
    print(line)
    LOGS_DIR.mkdir(exist_ok=True)
    with open(SCRAPER_LOG, "a") as f:
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


def load_niche_overrides() -> dict:
    if NICHE_OVERRIDES.exists():
        try:
            return json.loads(NICHE_OVERRIDES.read_text())
        except Exception:
            pass
    return {"hooks": {}, "video_queries": {}, "active_niches": list(VALID_NICHES), "last_scraped": None}


def save_niche_overrides(data: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    NICHE_OVERRIDES.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Trend sources
# ---------------------------------------------------------------------------
def scrape_reddit_trending(subreddit: str, time_filter: str = "week", limit: int = 10) -> list[str]:
    """
    Fetch top post titles from a subreddit using the public JSON API.
    No authentication required.
    """
    try:
        import urllib.request
        url = f"https://www.reddit.com/r/{subreddit}/top.json?t={time_filter}&limit={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "HarbingerTrendScraper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        posts = (data.get("data") or {}).get("children", [])
        titles = [p.get("data", {}).get("title", "") for p in posts if p.get("data")]
        return [t for t in titles if len(t) > 10]
    except Exception as e:
        log(f"  Reddit scrape error ({subreddit}): {e}")
        return []


def scrape_google_trends_rss(geo: str = "US") -> list[str]:
    """
    Fetch Google Trends daily trending searches via public RSS feed.
    """
    try:
        import urllib.request, xml.etree.ElementTree as ET
        url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
        req = urllib.request.Request(url, headers={"User-Agent": "HarbingerTrendScraper/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml_data = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(xml_data)
        titles = []
        for item in root.iter("item"):
            title_el = item.find("title")
            if title_el is not None and title_el.text:
                titles.append(title_el.text.strip())
        return titles[:20]
    except Exception as e:
        log(f"  Google Trends RSS error: {e}")
        return []


def scrape_all_trends() -> dict[str, list[str]]:
    """
    Aggregate trending signals across platforms.
    Returns dict of category → list of trending titles/topics.
    """
    log("Scraping trends...")
    results: dict[str, list[str]] = {}

    # Tech/AI trends
    tech_titles = (
        scrape_reddit_trending("technology",  "week", 10) +
        scrape_reddit_trending("artificial",  "week", 10) +
        scrape_reddit_trending("ChatGPT",     "week",  5)
    )
    results["tech_ai"] = tech_titles[:15]
    log(f"  tech_ai: {len(results['tech_ai'])} titles")
    time.sleep(1)

    # Psychology/mindset trends
    psych_titles = (
        scrape_reddit_trending("psychology",  "week", 10) +
        scrape_reddit_trending("stoicism",    "week",  5) +
        scrape_reddit_trending("selfimprovement", "week", 5)
    )
    results["dark_psychology"] = psych_titles[:15]
    log(f"  dark_psychology: {len(results['dark_psychology'])} titles")
    time.sleep(1)

    # Mystery/unsolved trends
    mystery_titles = (
        scrape_reddit_trending("UnresolvedMysteries", "week", 10) +
        scrape_reddit_trending("conspiracy",          "week",  5) +
        scrape_reddit_trending("TrueCrime",           "week",  5)
    )
    results["micro_mystery"] = mystery_titles[:15]
    log(f"  micro_mystery: {len(results['micro_mystery'])} titles")
    time.sleep(1)

    # Google Trends (broad)
    google_trending = scrape_google_trends_rss("US")
    results["_google_trending"] = google_trending
    log(f"  Google Trends: {len(google_trending)} entries")

    return results


def derive_hooks_from_trends(
    niche: str,
    trending_titles: list[str],
    replicate_token: str,
) -> list[str] | None:
    """
    Use LLM to turn scraped trending titles into viral hooks for the given niche.
    Returns list of 3-5 new hooks, or None if LLM unavailable.
    """
    if not replicate_token or not trending_titles:
        return None

    titles_block = "\n".join(f"- {t}" for t in trending_titles[:10])
    niche_label = {
        "tech_ai":         "Tech / AI Hacks",
        "dark_psychology": "Dark Psychology",
        "micro_mystery":   "Micro-Mysteries",
    }.get(niche, niche)

    prompt = f"""You are a viral short-form video scriptwriter.

These are today's TRENDING topics on Reddit and Google (for {niche_label}):
{titles_block}

Write 5 SHORT viral video hooks (1 sentence each, max 15 words) inspired by these trends.
Each hook must:
- Be in the niche: {niche_label}
- Start with a fear/loss/threat framing ("This", "Why", "How", "Stop", "The", "If")
- Not mention specific people by name
- Be platform-safe (no illegal activity, no medical claims)

Output ONLY a JSON array of 5 strings. No markdown, no explanation.
["hook 1", "hook 2", "hook 3", "hook 4", "hook 5"]"""

    try:
        import replicate
        output = replicate.run(
            "meta/meta-llama-3.1-405b-instruct",
            input={"prompt": prompt, "max_tokens": 300, "temperature": 0.85},
        )
        raw = "".join(output).strip()
        match = re.search(r'\[[\s\S]+?\]', raw)
        if match:
            hooks = json.loads(match.group())
            if isinstance(hooks, list) and len(hooks) >= 3:
                return [str(h).strip() for h in hooks if len(str(h).strip()) > 10]
    except Exception as e:
        log(f"  LLM hook derivation failed: {e}")
    return None


# ---------------------------------------------------------------------------
# Niche health check
# ---------------------------------------------------------------------------
def check_niche_health(entries: list[dict]) -> dict[str, float | None]:
    """
    Compute average views per niche from performance_memory entries.
    Returns dict niche → avg_views (or None if no data).
    """
    views_by_niche: dict[str, list[int]] = {n: [] for n in VALID_NICHES}
    for e in entries:
        n = e.get("niche")
        v = e.get("views")
        if n in views_by_niche and v is not None:
            views_by_niche[n].append(v)

    return {
        n: (round(sum(vl) / len(vl), 1) if vl else None)
        for n, vl in views_by_niche.items()
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_trend_scrape() -> dict:
    load_env()
    log("=" * 60)
    log("TREND SCRAPER — weekly self-research run")
    log("=" * 60)

    replicate_tok = os.environ.get("REPLICATE_API_TOKEN", "")
    perf_mem      = load_perf_memory()
    entries       = perf_mem.get("entries", [])
    overrides     = load_niche_overrides()

    # ── Check niche health ──────────────────────────────────────────────────
    health = check_niche_health(entries)
    log(f"Niche health (avg views): {health}")

    valid_views = {n: v for n, v in health.items() if v is not None}
    best_views  = max(valid_views.values()) if valid_views else None

    niches_to_replace: list[str] = []
    if best_views and len(valid_views) >= 2:
        for niche, avg in valid_views.items():
            if avg < best_views * 0.30:
                log(f"  {niche}: {avg} avg views < 30% of best ({best_views}) — flagged for replacement")
                niches_to_replace.append(niche)

    # ── Scrape trends ───────────────────────────────────────────────────────
    trends = scrape_all_trends()

    # ── Derive new hooks per niche ──────────────────────────────────────────
    new_hooks: dict[str, list[str]] = {}
    for niche in VALID_NICHES:
        niche_titles = trends.get(niche, [])
        if not niche_titles:
            log(f"  No trend data for {niche} — keeping existing hooks")
            continue
        derived = derive_hooks_from_trends(niche, niche_titles, replicate_tok)
        if derived:
            new_hooks[niche] = derived
            log(f"  {niche}: {len(derived)} new hooks derived from trends")
        else:
            log(f"  {niche}: LLM unavailable — using trend keywords as hooks")
            # Lightweight fallback: use top Reddit titles as raw hook material
            new_hooks[niche] = [
                f"Nobody is talking about this: {t[:60]}."
                for t in niche_titles[:3]
                if len(t) > 20
            ]
        time.sleep(2)

    # ── Update active_niches for underperformers ────────────────────────────
    active_niches = list(overrides.get("active_niches", VALID_NICHES))
    for bad_niche in niches_to_replace:
        replacement = TREND_REPLACEMENT_CANDIDATES.get(bad_niche)
        if replacement and replacement not in active_niches:
            log(f"  Replacing {bad_niche} with {replacement} in active_niches")
            active_niches = [replacement if n == bad_niche else n for n in active_niches]
            # Seed default hooks + queries for the new niche
            if replacement not in new_hooks:
                new_hooks[replacement] = REPLACEMENT_HOOKS.get(replacement, [])
            overrides["video_queries"][replacement] = REPLACEMENT_VIDEO_QUERIES.get(replacement, [])

    # ── Write overrides ─────────────────────────────────────────────────────
    overrides["hooks"].update(new_hooks)
    overrides["active_niches"] = active_niches
    overrides["last_scraped"]  = datetime.datetime.utcnow().isoformat()
    save_niche_overrides(overrides)

    log(f"Overrides saved: {len(new_hooks)} niches updated | active={active_niches}")
    log(f"niche_overrides.json → {NICHE_OVERRIDES}")
    return overrides


def main():
    run_trend_scrape()


if __name__ == "__main__":
    main()
