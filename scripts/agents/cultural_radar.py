#!/usr/bin/env python3
"""
cultural_radar.py — Agent 1: Cultural Radar (v4.0 — Viral/Outrage Edition)

Finds what people are genuinely angry, shocked, or outraged about RIGHT NOW.
Not finance theory. Not information asymmetry. Real emotional temperature.

The question this agent answers:
  "What happened in the last 24 hours that made ordinary UK people say
   'how is this legal', 'are you serious', or 'share this now'?"

Output: logs/asymmetry_brief.json

Usage: python3 cultural_radar.py [--slot N] [--force]
"""
from __future__ import annotations
import os, sys, json, datetime, time, re, traceback
from pathlib import Path

BASE_DIR              = Path(__file__).parent.parent.parent
LOGS_DIR              = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
RADAR_LOG             = LOGS_DIR / "cultural_radar.log"
VIRAL_FRAMEWORKS_FILE = LOGS_DIR / "viral_frameworks.json"
NOW        = datetime.datetime.utcnow()
DATE_STR   = NOW.strftime("%Y-%m-%d")
TIMESTAMP  = NOW.strftime("%Y%m%d_%H%M%S")


def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(RADAR_LOG, "a") as f:
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


# ---------------------------------------------------------------------------
# Outrage signal detection
# Measures: genuine anger, disbelief, sharing intent, economic injustice
# ---------------------------------------------------------------------------
OUTRAGE_SIGNALS = [
    # Anger
    r"\bfuming\b", r"\boutrageous\b", r"\bdisgusting\b", r"\bscandal\b",
    r"\bscam\b", r"\bstealing\b", r"how is this legal", r"are you serious",
    r"\bshocking\b", r"\bunbelievable\b", r"\bcriminal\b", r"\bexposed\b",
    # Disbelief  
    r"can't believe", r"cannot believe", r"didn't know", r"just found out",
    r"wait.{0,20}what", r"no way", r"seriously\?", r"is this real",
    # Economic injustice
    r"\bprofit\b.{0,30}\b(billion|million)\b",
    r"\bboss\b.{0,30}\bpay\b", r"\bceo\b.{0,30}\b(salary|bonus|pay)\b",
    r"\blandlord\b", r"\brent.{0,20}(rise|hike|increase)\b",
    r"\benergy.{0,20}(bill|price|profit)\b",
    r"\bsupermarket.{0,20}(profit|price|greed)\b",
    # Sharing intent
    r"share this", r"everyone needs to know", r"tell your friends",
    r"pass this on", r"spread the word", r"\bviral\b",
    # UK-specific
    r"\bnhs\b", r"\btory\b", r"\bcost of living\b", r"\bmortgage\b",
    r"\bcouncil.{0,20}tax\b", r"\bstudent loan\b",
]

# Categories that get algorithmic push for new accounts
VIRAL_CATEGORIES = [
    "corporate_greed",     # companies making billions while workers struggle
    "housing_crisis",      # landlords, rent, mortgages
    "government_failure",  # policy failures, broken promises
    "hidden_charges",      # fees people didn't know existed
    "wage_theft",          # employers underpaying, tip theft
    "energy_scandal",      # utility company profits vs bills
    "food_poverty",        # foodbanks vs supermarket profits
    "banking_ripoff",      # bank fees, savings rate vs mortgage rate gap
]


def outrage_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    for pattern in OUTRAGE_SIGNALS:
        if re.search(pattern, text_lower):
            score += 12
    return min(score, 72)


def viral_score(post: dict) -> int:
    """Full score: recency + engagement velocity + outrage signals."""
    recency = 0
    if "created_utc" in post:
        age_h = (time.time() - post["created_utc"]) / 3600
        if age_h < 3:
            recency = 45
        elif age_h < 12:
            recency = 30
        elif age_h < 24:
            recency = 20
        elif age_h < 48:
            recency = 8

    # Comment velocity — comments = emotional reaction = algorithm signal
    # Direct: each comment scores 5 points, capped at 100
    comment_velocity = post.get("num_comments", 0) * 5
    eng_score = min(comment_velocity, 100)

    text = f"{post.get('title', '')} {post.get('selftext', '')}"
    outrage = outrage_score(text)

    return recency + eng_score + outrage


# ---------------------------------------------------------------------------
# Data sources
# ---------------------------------------------------------------------------
def scrape_reddit(user_agent: str = "harbinger-radar/2.0") -> list[dict]:
    """
    Scrape outrage/viral posts from UK subreddits.
    Focus: real anger, real events, recent.
    """
    import requests

    # Expanded to outrage/viral subreddits, not just finance
    subreddits = [
        # UK anger and news
        "CasualUK",
        "AskUK",
        "unitedkingdom",
        "ukpolitics",
        "UKPersonalFinance",
        # Economic injustice
        "LegalAdviceUK",
        "HousingUK",
        "TenantsInEngland",
        # Viral/outrage
        "antiwork",
        "MildlyInfuriating",
        "mildlyinfuriating",
    ]

    findings = []
    headers  = {"User-Agent": user_agent}
    session  = requests.Session()
    session.headers.update(headers)

    for sub in subreddits:
        for sort in ["hot", "new"]:
            try:
                url = f"https://www.reddit.com/r/{sub}/{sort}.json?limit=25"
                backoff = 2
                for _attempt in range(3):
                    r = session.get(url, timeout=15)
                    if r.status_code == 429:
                        log(f"Reddit {sub}/{sort} rate-limited — waiting {backoff}s before retry")
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    break
                if r.status_code != 200:
                    continue
                data = r.json()
                posts = [p["data"] for p in data["data"]["children"] if p["kind"] == "t3"]
                # Filter: must have some engagement
                posts = [p for p in posts if p.get("score", 0) > 50 or p.get("num_comments", 0) > 10]
                for p in posts:
                    p["_subreddit"] = sub
                    p["_sort"]      = sort
                findings.extend(posts)
                time.sleep(0.3)
            except Exception as e:
                log(f"Reddit {sub}/{sort} failed: {e}")
                continue

    log(f"Reddit: {len(findings)} posts collected")
    return findings


def scrape_google_news(serp_api_key: str = "") -> list[dict]:
    """
    Pull trending UK news via SerpAPI or fallback to BBC RSS.
    Looks for stories with economic injustice / outrage angle.
    """
    import requests

    results = []

    # Outrage-oriented search queries
    queries = [
        "UK company profit workers pay 2026",
        "UK landlord rent increase scandal 2026",
        "UK energy bills profit 2026",
        "UK cost of living crisis March 2026",
        "UK corporation tax avoidance 2026",
        "UK CEO bonus salary 2026",
        "UK bank charges fees 2026",
        "UK supermarket price gouging 2026",
    ]

    if serp_api_key:
        for q in queries[:4]:  # budget-conscious
            try:
                r = requests.get(
                    "https://serpapi.com/search",
                    params={"q": q, "tbm": "nws", "api_key": serp_api_key,
                            "num": 5, "gl": "uk", "hl": "en"},
                    timeout=20,
                )
                if r.status_code == 200:
                    data = r.json()
                    for item in data.get("news_results", []):
                        results.append({
                            "title": item.get("title", ""),
                            "snippet": item.get("snippet", ""),
                            "source": item.get("source", ""),
                            "date": item.get("date", ""),
                            "_type": "news",
                            "score": 100,
                            "num_comments": 0,
                        })
                time.sleep(0.5)
            except Exception as e:
                log(f"SerpAPI query '{q[:40]}' failed: {e}")
    else:
        # Free fallback: BBC News RSS feeds
        rss_feeds = [
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://feeds.bbci.co.uk/news/uk/rss.xml",
            "https://feeds.bbci.co.uk/news/rss.xml",
        ]
        try:
            import xml.etree.ElementTree as ET
            for feed_url in rss_feeds:
                r = requests.get(feed_url, timeout=15)
                if r.status_code != 200:
                    continue
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title_el = item.find("title")
                    title    = (title_el.text or "").strip() if title_el is not None else ""
                    if not title:
                        continue  # skip — no fallback to raw XML element dump
                    desc_el  = item.find("description")
                    desc     = (desc_el.text or "").strip() if desc_el is not None else ""
                    date_el  = item.find("pubDate")
                    pubdate  = (date_el.text or "").strip() if date_el is not None else ""
                    results.append({
                        "title": title,
                        "snippet": desc,
                        "source": "BBC News",
                        "date": pubdate,
                        "_type": "news",
                        "score": 80,
                        "num_comments": 0,
                    })
        except Exception as e:
            log(f"BBC RSS fallback failed: {e}")

    log(f"News: {len(results)} stories collected")
    return results


def classify_category(text: str) -> str:
    """Classify a finding into a viral category."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["ceo", "boss", "executive", "director", "profit", "bonus"]):
        return "corporate_greed"
    if any(w in text_lower for w in ["rent", "landlord", "evict", "housing", "property"]):
        return "housing_crisis"
    if any(w in text_lower for w in ["energy", "electricity", "gas bill", "utility"]):
        return "energy_scandal"
    if any(w in text_lower for w in ["supermarket", "food bank", "grocery", "tesco", "sainsbury"]):
        return "food_poverty"
    if any(w in text_lower for w in ["bank", "interest rate", "savings", "mortgage rate"]):
        return "banking_ripoff"
    if any(w in text_lower for w in ["wage", "pay", "salary", "minimum wage", "tips"]):
        return "wage_theft"
    if any(w in text_lower for w in ["fee", "charge", "hidden", "small print", "subscription"]):
        return "hidden_charges"
    return "government_failure"


def synthesise_brief_with_claude(top_findings: list[dict]) -> dict | None:
    """
    Pass top findings to Claude to synthesise into a content brief.
    The brief targets: what will make someone stop scrolling and share this.
    NOT finance education. Outrage + insight + share trigger.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    except ImportError:
        return None

    findings_text = json.dumps([
        {
            "title": f.get("title", ""),
            "snippet": f.get("snippet", f.get("selftext", ""))[:200],
            "source": f.get("_subreddit", f.get("source", "")),
            "score": f.get("_viral_score", 0),
            "category": f.get("_category", ""),
        }
        for f in top_findings[:8]
    ], indent=2)

    prompt = f"""You are finding the single best piece of content to create right now for a UK faceless short-form video account with zero followers.

The account needs to grow from zero. That means the content must be immediately shareable by cold audiences who have never heard of the account.

Here are today's top trending stories and posts from UK sources:

{findings_text}

Select ONE story to build content around. Choose based on:
1. Genuine outrage potential — would a normal UK person stop and say "wait, what?"
2. Shareable — would they send it to a friend without any context?
3. Has a clear villain or injustice
4. Can be explained in 30-45 seconds

Output as JSON:
{{
  "asymmetry": "The core injustice or shocking fact in one sentence",
  "category": "One of: corporate_greed / housing_crisis / energy_scandal / food_poverty / banking_ripoff / wage_theft / hidden_charges / government_failure",
  "hook": "The opening line that stops the scroll — 10 words max, no fluff",
  "core_fact": "The specific number, statistic, or fact that makes this outrageous",
  "villain": "The specific company, person, or institution at fault",
  "share_trigger": "What makes someone send this to their mate — complete this: 'Did you see this? [reason]'",
  "search_query": "Best YouTube search query to find real footage for this story (be specific)",
  "keywords": ["list", "of", "5-8", "keywords", "for", "footage", "sourcing"],
  "estimated_anger_score": 0
}}

Be specific. Real numbers. Real villains. No hedging."""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        log(f"Claude synthesis failed: {e}")
        return None


def heuristic_brief(top_findings: list[dict]) -> dict:
    """Fallback brief if Claude unavailable."""
    if not top_findings:
        return {
            "asymmetry": "UK energy companies made record profits while household bills doubled",
            "category": "energy_scandal",
            "hook": "Energy companies made £9 billion profit last year",
            "core_fact": "UK energy firms posted record profits while 6.5 million households were in fuel poverty",
            "villain": "UK energy companies",
            "share_trigger": "They're making billions while you can't heat your home",
            "search_query": "UK energy company profit scandal 2025 2026",
            "keywords": ["energy", "profit", "bills", "UK", "scandal"],
            "estimated_anger_score": 85,
        }

    top = top_findings[0]
    title = top.get("title", "")
    snippet = top.get("snippet", top.get("selftext", ""))

    return {
        "asymmetry": title,
        "category": top.get("_category", "corporate_greed"),
        "hook": title[:60] + "..." if len(title) > 60 else title,
        "core_fact": snippet[:200] if snippet else title,
        "villain": "UK corporations",
        "share_trigger": f"Did you see this? {title[:80]}",
        "search_query": f"{title[:50]} UK news 2026",
        "keywords": re.findall(r'\b[A-Za-z]{4,}\b', title)[:8],
        "estimated_anger_score": top.get("_viral_score", 50),
    }


# ---------------------------------------------------------------------------
# Viral framework intelligence
# ---------------------------------------------------------------------------
FALLBACK_FRAMEWORKS = {
    "tech_ai": [
        {
            "structure": "[Familiar Tool/Habit] + ['is secretly/actually'] + [Hidden Cost or Danger you didn't know about]",
            "trigger": "Loss aversion + status threat — viewer is already being harmed without knowing it",
            "example": "That iPhone feature everyone uses is secretly selling your location to 47 data brokers.",
        },
        {
            "structure": "[Number] + [Category of Thing] + ['that feel illegal to know'] + [Implied restricted access]",
            "trigger": "Information asymmetry — viewer gains access to knowledge others don't have",
            "example": "4 websites that feel illegal to know about in 2026.",
        },
        {
            "structure": "['How to'] + [Aspirational Outcome] + ['using'] + [Unexpected Free or Unknown Method]",
            "trigger": "Effort gap — same result with a fraction of the work, and almost nobody knows this exists",
            "example": "How to automate your entire Monday morning using one free AI tool nobody is using.",
        },
    ],
    "dark_psychology": [
        {
            "structure": "['If someone does'] + [Very Specific Observable Behaviour] + ['they are'] + [Disturbing Hidden Intent]",
            "trigger": "Social threat detection — protecting the viewer from invisible manipulation",
            "example": "If someone mirrors your body language in the first 30 seconds, they are running a dominance test on you.",
        },
        {
            "structure": "['The reason you'] + [Universal Relatable Behaviour] + ['is not'] + [Assumed Reason] + ['it is'] + [Dark Psychological Mechanism]",
            "trigger": "Self-revelation — reframes a familiar experience with a disturbing explanation the viewer cannot unsee",
            "example": "The reason you can't stop scrolling is not boredom. It is a dopamine loop engineered by behavioural scientists.",
        },
        {
            "structure": "[Authority] + ['has known this for decades'] + [Why it was suppressed] + [What it means for you now]",
            "trigger": "Conspiracy of suppression — powerful people deliberately kept this from you",
            "example": "Psychologists have known this negotiation trick for 40 years. It was never taught in schools because it is too effective.",
        },
    ],
    "micro_mystery": [
        {
            "structure": "['What if'] + [Familiar Safe Phenomenon] + ['is actually'] + [Terrifying Alternative Explanation left unresolved]",
            "trigger": "Existential reframe — takes a safe everyday experience and leaves it permanently unsettling",
            "example": "What if déjà vu is not a memory glitch. What if it is your brain catching a bleed from a parallel timeline.",
        },
        {
            "structure": "[Credible Authority] + ['can't explain'] + [Specific Documented Anomaly with real detail] + [Open implication, never resolved]",
            "trigger": "Authority gap — if the experts cannot explain it, the mystery is real and the viewer is helpless",
            "example": "NASA has documented 47 radio signals from deep space that repeat on a 16-day cycle. They have no explanation.",
        },
        {
            "structure": "['The [specific place/object/event] that'] + [Mundane description] + ['is actually hiding'] + [Forbidden or suppressed truth]",
            "trigger": "Hidden reality — the world has a concealed layer that almost nobody sees",
            "example": "The town in Norway where it is illegal to die is not a tourist quirk. It is covering up something much stranger.",
        },
    ],
}


def scrape_viral_highvelocity(user_agent: str = "harbinger-radar/2.0") -> list[str]:
    """
    Scrape high-velocity viral content from broad viral subreddits.
    Returns a list of hook strings (post titles) with >10k upvotes in the last 24h
    or top posts by engagement velocity from broad viral communities.
    """
    import requests

    viral_subreddits = [
        "todayilearned",
        "interestingasfuck",
        "Damnthatsinteresting",
        "Showerthoughts",
        "LifeProTips",
        "YouShouldKnow",
        "mindblowing",
        "Unexpected",
        "nextfuckinglevel",
        "HumansBeingBros",
    ]

    hooks = []
    headers = {"User-Agent": user_agent}
    session = requests.Session()
    session.headers.update(headers)

    for sub in viral_subreddits:
        for sort in ["hot", "top"]:
            params = "?limit=25&t=day" if sort == "top" else "?limit=25"
            try:
                url = f"https://www.reddit.com/r/{sub}/{sort}.json{params}"
                backoff = 2
                for _attempt in range(3):
                    r = session.get(url, timeout=15)
                    if r.status_code == 429:
                        log(f"Viral scrape {sub}/{sort} rate-limited — waiting {backoff}s before retry")
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    break
                if r.status_code != 200:
                    continue
                data = r.json()
                posts = [p["data"] for p in data["data"]["children"] if p["kind"] == "t3"]
                # High-velocity filter: >10k upvotes OR >500 comments (proxy for 10k on smaller subs)
                hot_posts = [
                    p for p in posts
                    if p.get("score", 0) > 10000 or p.get("num_comments", 0) > 500
                ]
                # Also grab top 3 from each sub regardless of threshold
                if not hot_posts:
                    hot_posts = sorted(posts, key=lambda x: x.get("score", 0), reverse=True)[:3]
                for p in hot_posts:
                    title = p.get("title", "").strip()
                    if title and len(title) > 15:
                        hooks.append(title)
                time.sleep(0.3)
            except Exception as e:
                log(f"Viral scrape {sub}/{sort} failed: {e}")
                continue

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for h in hooks:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    log(f"High-velocity hooks scraped: {len(unique)} unique titles")
    return unique[:60]  # cap at 60 to keep Claude prompt manageable


def deconstruct_viral_hooks_with_claude(hooks: list[str]) -> dict | None:
    """
    Pass scraped viral hooks to Claude for PhD-level structural deconstruction.
    Returns {niche: [framework_dicts]} or None on failure.
    """
    if not hooks:
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        return None

    hooks_text = "\n".join(f"- {h}" for h in hooks[:50])

    prompt = f"""You are a PhD-level internet sociologist specialising in viral content mechanics on TikTok, Instagram Reels, and YouTube Shorts.

Below are highly viral hooks — post titles and opening lines that generated massive engagement on social media:

{hooks_text}

Your task: Deconstruct the UNDERLYING PSYCHOLOGICAL FRAMEWORKS and GRAMMATICAL SYNTAX TEMPLATES that make these hooks go viral.

Do NOT copy the hooks. Extract the structural pattern behind each one.

Generate exactly 9 frameworks — 3 for each of these niches:
- tech_ai: Technology, AI tools, digital life hacks, hidden digital features
- dark_psychology: Human behaviour, manipulation, body language, social dynamics
- micro_mystery: Unexplained phenomena, space, simulation theory, hidden realities

Each framework must have:
- structure: The reusable grammatical template using [Placeholder] notation (e.g., "[Familiar Concept] + [Pattern Interrupt] + [Negative Twist]")
- trigger: The specific psychological mechanism activated (be precise — name the cognitive bias or emotional response)
- example: A fresh, original example using this template for its assigned niche. Do NOT reuse any hook from the input list.

Output ONLY valid JSON. No markdown, no preamble.
{{
  "tech_ai": [
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}}
  ],
  "dark_psychology": [
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}}
  ],
  "micro_mystery": [
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}},
    {{"structure": "...", "trigger": "...", "example": "..."}}
  ]
}}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r'^```json\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        frameworks = json.loads(raw)
        # Validate structure
        for niche in ["tech_ai", "dark_psychology", "micro_mystery"]:
            if niche not in frameworks or not isinstance(frameworks[niche], list):
                raise ValueError(f"Missing or invalid niche: {niche}")
        log(f"Claude deconstruction: {sum(len(v) for v in frameworks.values())} frameworks generated")
        return frameworks
    except Exception as e:
        log(f"Claude deconstruction failed: {e}")
        return None


def update_viral_frameworks(force: bool = False) -> dict:
    """
    Check if viral_frameworks.json is < 24h old. If fresh, return cached.
    If stale or missing, scrape high-velocity hooks, run Claude deconstruction,
    and save to viral_frameworks.json. Falls back to FALLBACK_FRAMEWORKS on error.
    """
    cache_age_h = float("inf")
    if VIRAL_FRAMEWORKS_FILE.exists():
        cache_age_h = (time.time() - VIRAL_FRAMEWORKS_FILE.stat().st_mtime) / 3600

    if not force and cache_age_h < 24:
        log(f"Viral frameworks cache fresh ({cache_age_h:.1f}h old) — skipping update")
        try:
            data = json.loads(VIRAL_FRAMEWORKS_FILE.read_text())
            return data.get("frameworks", FALLBACK_FRAMEWORKS)
        except Exception as e:
            log(f"WARNING: viral_frameworks.json is corrupt and could not be parsed ({e})")
            log("WARNING: FALLING BACK TO COLD-START FRAMEWORKS — output quality will be reduced until cache is rebuilt")

    log("Updating viral frameworks — scraping high-velocity hooks...")
    hooks = scrape_viral_highvelocity()
    frameworks = None

    if hooks:
        frameworks = deconstruct_viral_hooks_with_claude(hooks)

    if not frameworks:
        log("Framework deconstruction unavailable — using fallback frameworks")
        frameworks = FALLBACK_FRAMEWORKS

    payload = {
        "timestamp": NOW.isoformat(),
        "source_hooks_count": len(hooks),
        "frameworks": frameworks,
    }
    try:
        VIRAL_FRAMEWORKS_FILE.write_text(json.dumps(payload, indent=2))
        log(f"Viral frameworks saved to {VIRAL_FRAMEWORKS_FILE.name}")
    except Exception as e:
        log(f"Failed to save viral frameworks: {e}")

    return frameworks


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",  type=int, default=1)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    load_env()

    log("=" * 60)
    log(f"AGENT 1: CULTURAL RADAR v4.0 — VIRAL/OUTRAGE EDITION (SLOT {args.slot})")
    log("=" * 60)

    # Check cache (don't re-run within 90 minutes unless forced)
    cache_file = LOGS_DIR / "asymmetry_brief.json"
    if not args.force and cache_file.exists():
        age_min = (time.time() - cache_file.stat().st_mtime) / 60
        if age_min < 90:
            log(f"Cache fresh ({age_min:.0f}min old) — using cached brief")
            return

    # Collect findings
    serp_key = os.environ.get("SERPAPI_KEY", "")
    reddit_findings = scrape_reddit()
    news_findings   = scrape_google_news(serp_key)
    all_findings    = reddit_findings + news_findings

    if not all_findings:
        log("WARNING: No findings collected — using fallback brief")
        brief = heuristic_brief([])
    else:
        # Score everything
        for f in all_findings:
            text = f"{f.get('title', '')} {f.get('selftext', f.get('snippet', ''))}"
            f["_viral_score"] = outrage_score(text) + (
                viral_score(f) if "created_utc" in f else 0
            )
            f["_category"] = classify_category(text)

        # Top 10 by viral score
        top = sorted(all_findings, key=lambda x: x["_viral_score"], reverse=True)[:10]

        log("Top findings:")
        for i, f in enumerate(top[:5], 1):
            log(f"  {i}. [{f['_viral_score']}] {f.get('title', '')[:80]} ({f.get('_category', '')})")

        # Synthesise brief
        brief = None
        if os.environ.get("ANTHROPIC_API_KEY"):
            brief = synthesise_brief_with_claude(top)

        if not brief:
            brief = heuristic_brief(top)

    # Write output
    brief["timestamp"]   = NOW.isoformat()
    brief["slot"]        = args.slot
    brief["findings_count"] = len(all_findings)

    cache_file.write_text(json.dumps(brief, indent=2))

    slot_file = LOGS_DIR / f"asymmetry_brief_slot{args.slot}_{TIMESTAMP}.json"
    slot_file.write_text(json.dumps(brief, indent=2))

    log(f"\nBRIEF WRITTEN: {cache_file.name}")
    log(f"Category:    {brief.get('category', '')}")
    log(f"Hook:        {brief.get('hook', '')}")
    log(f"Villain:     {brief.get('villain', '')}")
    log(f"Anger score: {brief.get('estimated_anger_score', 0)}/100")
    log(f"Search:      {brief.get('search_query', '')}")

    # Update viral frameworks (24h cache — skips if fresh)
    log("\nChecking viral frameworks cache...")
    update_viral_frameworks(force=args.force)

    print(json.dumps(brief, indent=2))


if __name__ == "__main__":
    main()
