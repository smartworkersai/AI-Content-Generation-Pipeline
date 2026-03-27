#!/usr/bin/env python3
"""
pre_slot_intelligence.py — Pre-slot strategic intelligence
Runs 45 minutes before every harbinger_core --cycle N.

Three processes, in order:

  1. GAP ANALYSIS
     YouTube search: what angles dominate UK finance Shorts right now.
     Not to copy — to find the absence. The angle that 0 creators are taking
     is where this slot lives.

  2. ASYMMETRY HUNT (supplement)
     Reads today's asymmetry_brief.json (written by cultural_radar).
     Extracts the sharpest unfiltered signal — the thing the audience senses
     but cannot articulate. No new API calls. Deeper read of what's already there.

  3. CREATIVE RESEARCH
     BBC Business + Guardian Money RSS: live financial news.
     One cross-discipline ingredient: the unexpected angle that makes
     Harbinger's treatment feel inevitable, not assembled.

Claude synthesises all three into:
  - creative_directives.json → instructions (read by creative_synthesis.py Agent 2)
  - logs/experiments.json    → experiment log: one variable, one hypothesis per slot

Usage: python3 pre_slot_intelligence.py --slot <1-5>
"""

import os, sys, json, datetime, re, time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

INTEL_LOG      = LOGS_DIR / "pre_slot_intelligence.log"
EXPERIMENTS    = LOGS_DIR / "experiments.json"
DIRECTIVES     = LOGS_DIR / "creative_directives.json"
ASYMMETRY_BRIEF = LOGS_DIR / "asymmetry_brief.json"

NOW       = datetime.datetime.utcnow()
DATE_STR  = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")


def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(INTEL_LOG, "a") as f:
        f.write(line + "\n")


def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()


# ---------------------------------------------------------------------------
# 1. GAP ANALYSIS — YouTube: find the absent angle
# ---------------------------------------------------------------------------

# All recognisable angles in finance short-form content
ANGLE_PATTERNS = {
    "tutorial":    [r"how to", r"step by step", r"beginners guide", r"explained"],
    "comparison":  [r" vs ", r"versus", r"better than", r"or .*\?$"],
    "warning":     [r"stop ", r"don.t ", r"never ", r"avoid", r"mistake"],
    "revelation":  [r"i found", r"discovered", r"just realised", r"nobody told me", r"secret"],
    "outrage":     [r"they.re stealing", r"scam", r"robbing", r"they don.t want", r"hiding"],
    "data":        [r"\d+%", r"£\d+", r"\$\d+", r"billion", r"figures show", r"data"],
    "mechanism":   [r"here.s how", r"this is why", r"the reason", r"the system", r"mechanism"],
    "insider":     [r"what banks", r"what they", r"what your", r"what no one", r"what advisers"],
}


def classify_angle(title: str) -> str:
    t = title.lower()
    for angle, patterns in ANGLE_PATTERNS.items():
        if any(re.search(p, t) for p in patterns):
            return angle
    return "unclassified"


def fetch_youtube_competitor_titles(yt_key: str) -> list[dict]:
    """Search YouTube for UK personal finance Shorts published in last 48h.
    Returns list of {title, view_count, angle} for top 20 results."""
    if not yt_key:
        log("GAP ANALYSIS: no YOUTUBE_API_KEY — skipping YouTube scan")
        return []

    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=yt_key)
        published_after = (NOW - datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")

        queries = [
            "UK personal finance 2026",
            "ISA savings UK",
            "UK mortgage rates",
            "UK investing beginners",
        ]
        all_items = []
        seen_ids = set()

        for query in queries:
            try:
                resp = youtube.search().list(
                    part="snippet",
                    q=query,
                    type="video",
                    videoDuration="short",
                    publishedAfter=published_after,
                    regionCode="GB",
                    relevanceLanguage="en",
                    order="viewCount",
                    maxResults=5,
                ).execute()
                for item in resp.get("items", []):
                    vid_id = item["id"]["videoId"]
                    if vid_id not in seen_ids:
                        seen_ids.add(vid_id)
                        title = item["snippet"]["title"]
                        all_items.append({
                            "title": title,
                            "angle": classify_angle(title),
                            "channel": item["snippet"]["channelTitle"],
                        })
                time.sleep(0.5)
            except Exception as e:
                log(f"GAP ANALYSIS: YouTube query '{query}': {e}")

        log(f"GAP ANALYSIS: {len(all_items)} competitor titles fetched")
        return all_items

    except Exception as e:
        log(f"GAP ANALYSIS: YouTube build failed: {e}")
        return []


def identify_gap(competitor_titles: list[dict]) -> dict:
    """Count angles across competitor titles. Find what's absent or rare."""
    angle_counts = {a: 0 for a in ANGLE_PATTERNS}
    for item in competitor_titles:
        angle = item.get("angle", "unclassified")
        if angle in angle_counts:
            angle_counts[angle] += 1

    total = len(competitor_titles) or 1
    angle_saturation = {a: round(c / total, 2) for a, c in angle_counts.items()}

    # Absent = 0 uses. Rare = <=1 use. Dominant = highest count.
    absent  = [a for a, c in angle_counts.items() if c == 0]
    rare    = [a for a, c in angle_counts.items() if c == 1]
    dominant = max(angle_counts, key=angle_counts.get)

    # Gap = first absent angle (priority order: mechanism > insider > data > revelation)
    priority = ["mechanism", "insider", "data", "revelation", "outrage",
                "comparison", "warning", "tutorial"]
    gap_angle = next((a for a in priority if a in absent), None) \
             or next((a for a in priority if a in rare), None) \
             or "mechanism"  # fallback: mechanism is always underused

    log(f"GAP ANALYSIS: dominant={dominant} ({angle_counts[dominant]} uses), "
        f"gap={gap_angle} ({angle_counts.get(gap_angle, 0)} uses)")

    return {
        "angle_counts":     angle_counts,
        "angle_saturation": angle_saturation,
        "dominant_angle":   dominant,
        "gap_angle":        gap_angle,
        "absent_angles":    absent,
        "competitor_sample": [t["title"] for t in competitor_titles[:8]],
    }


# ---------------------------------------------------------------------------
# 2. ASYMMETRY HUNT — deeper read of what cultural_radar found
# ---------------------------------------------------------------------------

def extract_sharpest_signal(brief: dict) -> dict:
    """Pull the most precise signal from today's asymmetry brief.
    Returns {asymmetry, strongest_finding, source, verbatim}"""
    asymmetry = brief.get("asymmetry", "")
    findings  = brief.get("top_findings", [])

    if not findings:
        return {"asymmetry": asymmetry, "strongest_finding": "", "source": "", "verbatim": ""}

    # Strongest = highest urgency_score with comment or text content
    for f in findings[:5]:
        verbatim = (
            f.get("text", "")
            or (f.get("top_comments") or [""])[0]
            or f.get("comment", "")
            or f.get("term", "")
        )
        if verbatim and len(verbatim) > 20:
            return {
                "asymmetry":        asymmetry,
                "strongest_finding": f.get("title", f.get("term", "")),
                "source":           f.get("source", ""),
                "verbatim":         verbatim[:300],
                "urgency_score":    f.get("urgency_score", 0),
            }

    return {"asymmetry": asymmetry, "strongest_finding": "", "source": "", "verbatim": ""}


# ---------------------------------------------------------------------------
# 3. CREATIVE RESEARCH — RSS feeds + cross-discipline ingredient
# ---------------------------------------------------------------------------

RSS_FEEDS = [
    ("BBC Business",     "http://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Guardian Money",   "https://www.theguardian.com/money/rss"),
]


def fetch_rss_headlines(max_per_feed: int = 6) -> list[dict]:
    """Fetch live headlines from BBC Business + Guardian Money RSS."""
    headlines = []
    for source_name, url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "harbinger-intel/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            ns   = {"atom": "http://www.w3.org/2005/Atom"}
            items = root.findall(".//item") or root.findall(".//atom:entry", ns)
            for item in items[:max_per_feed]:
                title = (item.findtext("title") or
                         item.findtext("atom:title", namespaces=ns) or "")
                desc  = (item.findtext("description") or
                         item.findtext("atom:summary", namespaces=ns) or "")
                if title.strip():
                    headlines.append({
                        "source":      source_name,
                        "title":       title.strip(),
                        "description": desc.strip()[:200],
                    })
        except Exception as e:
            log(f"RSS {source_name}: {e}")

    log(f"CREATIVE RESEARCH: {len(headlines)} headlines from {len(RSS_FEEDS)} feeds")
    return headlines


# ---------------------------------------------------------------------------
# Performance context — what's working and what isn't
# ---------------------------------------------------------------------------

def load_performance_context() -> dict:
    """Load Loop B findings + timing reports to tell Claude what's weak."""
    context = {"loop_b_weakest": "", "avg_chain_score": 0, "timing_insights": []}

    lb_path = LOGS_DIR / "loop_b_findings.json"
    if lb_path.exists():
        try:
            findings = json.loads(lb_path.read_text())
            if findings:
                recent = findings[-10:]
                scores = [f.get("overall_chain_score", 0) for f in recent]
                context["avg_chain_score"] = round(sum(scores) / len(scores), 1)
                # Most common weakest link
                weak_counts: dict = {}
                for f in recent:
                    w = f.get("weakest_link", "")
                    weak_counts[w] = weak_counts.get(w, 0) + 1
                context["loop_b_weakest"] = max(weak_counts, key=weak_counts.get) if weak_counts else ""
        except Exception:
            pass

    # Load recent timing reports for pacing insight
    timing_files = sorted(LOGS_DIR.glob("timing_report_*.json"),
                          key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    for tf in timing_files:
        try:
            tr = json.loads(tf.read_text())
            context["timing_insights"].append({
                "hook_wps":          tr.get("hook_pace", {}).get("hook_wps", 0),
                "overall_wps":       tr.get("delivery", {}).get("overall_wps", 0),
                "pre_hook_silence_ms": tr.get("pre_hook_silence_ms", 0),
                "structural_pauses": tr.get("structural_pauses", {}),
            })
        except Exception:
            pass

    return context


# ---------------------------------------------------------------------------
# Experiment system — what variable does this slot test?
# ---------------------------------------------------------------------------

EXPERIMENT_VARIABLES = [
    "hook_delivery_pace",     # fast/clipped vs deliberate/weighted
    "mechanism_specificity",  # named UK source vs unnamed mechanism
    "visual_identity_source", # what real-world image the visual derives from
    "pre_hook_silence",       # ms of silence before first word
    "cta_architecture",       # question / instruction / challenge
]


def select_experiment_variable(slot: int, perf: dict) -> str:
    """Choose experiment variable based on performance gaps and slot number.

    Priority: fix the chronically weakest link first. Then rotate remaining vars."""
    weakest = perf.get("loop_b_weakest", "")
    if weakest == "visual_identity_precondition":
        return "visual_identity_source"
    if weakest == "mechanism_specificity":
        return "mechanism_specificity"
    if weakest == "comment_trigger_structural":
        return "cta_architecture"

    # Rotate by slot
    return EXPERIMENT_VARIABLES[(slot - 1) % len(EXPERIMENT_VARIABLES)]


def load_experiments() -> list[dict]:
    if not EXPERIMENTS.exists():
        return []
    try:
        return json.loads(EXPERIMENTS.read_text())
    except Exception:
        return []


def save_experiment(experiment: dict):
    existing = load_experiments()
    # Avoid duplicate slot+date entries
    existing = [e for e in existing
                if not (e.get("date") == DATE_STR and e.get("slot") == experiment["slot"])]
    existing.append(experiment)
    EXPERIMENTS.write_text(json.dumps(existing, indent=2))


# ---------------------------------------------------------------------------
# Claude synthesis — the intelligence that ties it together
# ---------------------------------------------------------------------------

def synthesise_with_claude(
    gap:      dict,
    signal:   dict,
    headlines: list[dict],
    perf:     dict,
    slot:     int,
    variable: str,
) -> dict:
    """Use Claude to synthesise GAP + ASYMMETRY + CREATIVE RESEARCH into:
    - instructions: the creative directive for Agent 2 (creative_synthesis.py)
    - creative_angle: Harbinger's specific angle for this slot
    - experiment_hypothesis: what this slot is testing and what success looks like
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log("Claude: no ANTHROPIC_API_KEY — using structured fallback")
        return _fallback_synthesis(gap, signal, variable, slot)

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        competitor_block = "\n".join(f"  - {t}" for t in gap.get("competitor_sample", []))
        headline_block   = "\n".join(
            f"  [{h['source']}] {h['title']}" for h in headlines[:8]
        )
        timing_block = ""
        if perf.get("timing_insights"):
            t = perf["timing_insights"][0]
            timing_block = (
                f"Recent delivery data: hook_wps={t['hook_wps']} words/sec, "
                f"pre_hook_silence={t['pre_hook_silence_ms']}ms, "
                f"overall_wps={t['overall_wps']} words/sec"
            )

        prompt = f"""You are the creative intelligence of Harbinger — an AI-native system that uses finance as its highest-paying wrapper. Your job: turn three data inputs into one precise creative directive for the next production slot.

The standard: a viewer watches one Harbinger video and feels they were told something they were not supposed to know, by someone who already knows how it ends. Not generated. Not automated. Inevitable.

---

INPUT 1 — MARKET GAP (what the competition is NOT doing)
Dominant angle: {gap.get('dominant_angle')} (saturated — {gap.get('angle_counts', {}).get(gap.get('dominant_angle', ''), 0)} videos)
Absent angle:   {gap.get('gap_angle')} (0–1 creators using this)

Competitor titles currently in market:
{competitor_block}

---

INPUT 2 — ASYMMETRY SIGNAL (what the audience senses but cannot say)
Core asymmetry: {signal.get('asymmetry')}
Strongest finding: {signal.get('strongest_finding')}
Verbatim audience voice: "{signal.get('verbatim')}"
Urgency score: {signal.get('urgency_score', 0)}/80

---

INPUT 3 — CREATIVE RESEARCH (live financial news + cross-discipline ingredient)
{headline_block}

---

PERFORMANCE CONTEXT
Loop B average chain score (last 10 briefs): {perf.get('avg_chain_score')}/10
Chronically weakest link: {perf.get('loop_b_weakest')}
{timing_block}

---

THIS SLOT'S EXPERIMENT
Variable being tested: {variable}
(Keep everything else as constant as possible. Change ONLY this one variable.)

---

Produce a JSON object with exactly these fields:

{{
  "gap_identified": "one sentence — the specific absence in the market right now",
  "creative_angle": "one sentence — exactly what angle Harbinger takes in this slot and why it is the gap",
  "cross_discipline_ingredient": "one headline or insight that adds the unexpected element. Explain why it belongs.",
  "instructions": "The full creative directive for Agent 2. Written in second person. 200-300 words. Include: (1) the specific angle to take, (2) what the audience is feeling and how to name it precisely, (3) the cross-discipline ingredient and how to use it, (4) what the video should make the viewer feel in the first 3 seconds, (5) the single experiment variable and how to execute it differently from the last slot. British English. Specific. No generalities.",
  "experiment_hypothesis": "If [specific change to {variable}], then [specific measurable outcome], because [mechanism]. One sentence."
}}

Return ONLY the JSON. No preamble.
"""
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        log(f"Claude synthesis: gap='{result.get('gap_identified', '')[:80]}'")
        log(f"Claude synthesis: angle='{result.get('creative_angle', '')[:80]}'")
        return result

    except Exception as e:
        log(f"Claude synthesis failed: {e} — using fallback")
        return _fallback_synthesis(gap, signal, variable, slot)


def _fallback_synthesis(gap: dict, signal: dict, variable: str, slot: int) -> dict:
    """Structured fallback when Claude is unavailable."""
    gap_angle  = gap.get("gap_angle", "mechanism")
    asymmetry  = signal.get("asymmetry", "")
    dominant   = gap.get("dominant_angle", "tutorial")

    angle_descriptions = {
        "mechanism":  "expose the exact mechanism — name the system, name the institution, name the rate",
        "insider":    "speak as someone already inside the system — the viewer is being briefed, not taught",
        "data":       "lead with a specific UK number that makes the mechanism legible — no vague claims",
        "revelation": "structure as discovery — what you just found, exactly, and what it means right now",
        "outrage":    "name the transfer — who benefits, from whom, by what mechanism, documented",
    }

    angle_desc = angle_descriptions.get(gap_angle, angle_descriptions["mechanism"])

    instructions = (
        f"SLOT {slot} DIRECTIVE — {gap_angle.upper()} ANGLE\n\n"
        f"The market is saturated with {dominant} content. Take the {gap_angle} angle instead: "
        f"{angle_desc}.\n\n"
        f"The audience is feeling: '{asymmetry}' — they sense this but have no words for it. "
        f"Give them the words. Be exact. Name the mechanism, the institution, the number.\n\n"
        f"EXPERIMENT ({variable}): change only this one variable from the previous slot. "
        f"Everything else stays constant. Document in experiments.json."
    )

    return {
        "gap_identified":              f"No UK finance Shorts are using the {gap_angle} angle (0 competitors)",
        "creative_angle":              f"Harbinger takes the {gap_angle} angle — {angle_desc}",
        "cross_discipline_ingredient": "Live market data as primary source — present as intelligence briefing",
        "instructions":                instructions,
        "experiment_hypothesis":       f"If the hook uses the {gap_angle} angle, completion rate increases because it names the mechanism rather than describing it.",
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def update_creative_directives(instructions: str):
    """Merge new instructions into creative_directives.json.
    Preserves all existing keys (visual, audio settings, kling prompts)."""
    existing = {}
    if DIRECTIVES.exists():
        try:
            existing = json.loads(DIRECTIVES.read_text())
        except Exception:
            pass

    existing["instructions"]            = instructions
    existing["instructions_updated_at"] = NOW.isoformat()
    existing["instructions_slot"]       = NOW.strftime("%Y%m%d_%H%M")

    DIRECTIVES.write_text(json.dumps(existing, indent=2))
    log(f"creative_directives.json updated ({len(instructions)} chars)")


def write_experiment(slot: int, synthesis: dict, variable: str, gap: dict, perf: dict):
    experiment = {
        "id":                    f"{DATE_STR.replace('-', '')}_slot{slot}",
        "date":                  DATE_STR,
        "slot":                  slot,
        "timestamp":             NOW.isoformat(),
        "variable":              variable,
        "gap_identified":        synthesis.get("gap_identified", ""),
        "creative_angle":        synthesis.get("creative_angle", ""),
        "cross_discipline":      synthesis.get("cross_discipline_ingredient", ""),
        "experiment_hypothesis": synthesis.get("experiment_hypothesis", ""),
        "loop_b_chain_avg":      perf.get("avg_chain_score", 0),
        "loop_b_weakest":        perf.get("loop_b_weakest", ""),
        "gap_angle":             gap.get("gap_angle", ""),
        "angle_saturation":      gap.get("angle_saturation", {}),
        "status":                "pending",
        "metrics":               None,
        "result":                None,
    }
    save_experiment(experiment)
    log(f"Experiment logged: {experiment['id']} — variable={variable}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, required=True, choices=range(1, 6))
    args = parser.parse_args()
    slot = args.slot

    load_env()

    log("=" * 60)
    log(f"PRE-SLOT INTELLIGENCE — SLOT {slot}")
    log("=" * 60)

    yt_key = os.environ.get("YOUTUBE_API_KEY", "")

    # ── 1. GAP ANALYSIS ──────────────────────────────────────────────────────
    log("--- 1. GAP ANALYSIS ---")
    competitor_titles = fetch_youtube_competitor_titles(yt_key)
    gap = identify_gap(competitor_titles)

    # ── 2. ASYMMETRY HUNT ─────────────────────────────────────────────────────
    log("--- 2. ASYMMETRY HUNT ---")
    if not ASYMMETRY_BRIEF.exists():
        log("WARNING: asymmetry_brief.json not found — cultural_radar.py must run first")
        asymmetry_brief = {}
    else:
        asymmetry_brief = json.loads(ASYMMETRY_BRIEF.read_text())
    signal = extract_sharpest_signal(asymmetry_brief)
    log(f"Signal: urgency={signal.get('urgency_score', 0)}, source={signal.get('source', 'none')}")
    log(f"Asymmetry: {signal.get('asymmetry', '')[:100]}")

    # ── 3. CREATIVE RESEARCH ──────────────────────────────────────────────────
    log("--- 3. CREATIVE RESEARCH ---")
    headlines = fetch_rss_headlines()

    # ── PERFORMANCE CONTEXT ───────────────────────────────────────────────────
    perf     = load_performance_context()
    variable = select_experiment_variable(slot, perf)
    log(f"Experiment variable: {variable} (loop_b weakest: {perf.get('loop_b_weakest', 'none')})")

    # ── SYNTHESIS ─────────────────────────────────────────────────────────────
    log("--- SYNTHESIS ---")
    synthesis = synthesise_with_claude(gap, signal, headlines, perf, slot, variable)

    # ── OUTPUTS ───────────────────────────────────────────────────────────────
    update_creative_directives(synthesis.get("instructions", ""))
    write_experiment(slot, synthesis, variable, gap, perf)

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    log("=" * 60)
    log(f"GAP:        {synthesis.get('gap_identified', '')[:90]}")
    log(f"ANGLE:      {synthesis.get('creative_angle', '')[:90]}")
    log(f"EXPERIMENT: {synthesis.get('experiment_hypothesis', '')[:90]}")
    log("=" * 60)

    print(json.dumps({
        "slot":      slot,
        "gap":       synthesis.get("gap_identified"),
        "angle":     synthesis.get("creative_angle"),
        "variable":  variable,
        "hypothesis": synthesis.get("experiment_hypothesis"),
    }, indent=2))


if __name__ == "__main__":
    main()
