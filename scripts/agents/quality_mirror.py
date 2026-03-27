#!/usr/bin/env python3
"""
quality_mirror.py — Agent 4: Quality Mirror
Eight self-improving learning loops that run nightly and write back into
the system's creative and production directives.

Loops:
  1. First Frame      — thumbnail visual scoring via Claude Vision
  2. Silence Geometry — silence duration vs completion rate correlation
  3. Sound Design A/B — slot-based sound profile testing
  4. Visual Language  — captured vs generated scoring via Claude Vision
  5. Acoustic Space   — reverb profile vs share rate correlation
  6. Caption AB       — CONTRAST STACK variable testing
  7. Prompt Genome    — fitness scoring + evolution
  8. Trust Signal     — source credibility, UK relevance, affiliate framing, claim specificity

Output: logs/delta_report_[date].json + updates to logs/creative_directives.json
        logs/trust_signal_state.json — per-brief trust scores and retirement tracking

Usage: python3 quality_mirror.py [--dry-run]
"""
from __future__ import annotations
import os, sys, json, datetime, re, time, base64, traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR.mkdir(exist_ok=True)
MIRROR_LOG = LOGS_DIR / "quality_mirror.log"
NOW = datetime.datetime.utcnow()
DATE_STR = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

# Canonical creative directives structure — defaults on first run
DEFAULT_DIRECTIVES = {
    "visual_frame_rules": [],
    "silence_geometry": {
        "after_intrusion": 400,
        "after_mechanism": 300,
        "before_move": 200,
    },
    "sound_profile": {
        "sub_bass_db": -18,
        "sub_bass_hz": 40,
        "ambience_type": "server_hum",
    },
    "kling_base_prompt_additions": [],
    "kling_negative_prompt_additions": [],
    "acoustic_profile": {
        "reverb_tail_ms": 400,
        "pre_delay_ms": 25,
        "room_size": "medium",
    },
}

# Sound A/B test slot profiles
SLOT_SOUND_PROFILES = {
    1: {"sub_bass_db": -18, "ambience_type": "server_hum"},
    2: {"sub_bass_db": -22, "ambience_type": "rain_glass"},
    3: {"sub_bass_db": -20, "ambience_type": "distant_traffic"},
}

AB_TEST_STAGES = ["sub_bass_db", "ambience_type", "sub_bass_hz"]
SUB_BASS_HZ_VARIANTS = [40, 60, 80]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------
def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(MIRROR_LOG, "a") as f:
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
        log(f"Telegram failed: {e}")

def load_directives() -> dict:
    f = LOGS_DIR / "creative_directives.json"
    if not f.exists():
        log("creative_directives.json not found — creating with defaults")
        f.write_text(json.dumps(DEFAULT_DIRECTIVES, indent=2))
        return dict(DEFAULT_DIRECTIVES)
    try:
        d = json.loads(f.read_text())
        # Merge with defaults to fill any missing keys
        for k, v in DEFAULT_DIRECTIVES.items():
            if k not in d:
                d[k] = v
        return d
    except Exception:
        return dict(DEFAULT_DIRECTIVES)

def save_directives(directives: dict, changed_keys: list[str]) -> list[str]:
    f = LOGS_DIR / "creative_directives.json"
    f.write_text(json.dumps(directives, indent=2))
    log(f"creative_directives.json updated: {changed_keys}")
    return changed_keys

def load_production_manifests(days: int = 2) -> list[dict]:
    cutoff = NOW - datetime.timedelta(days=days)
    manifests = []
    for p in sorted(LOGS_DIR.glob("production_manifest_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if datetime.datetime.utcfromtimestamp(p.stat().st_mtime) >= cutoff:
            try:
                manifests.append(json.loads(p.read_text()))
            except Exception:
                pass
    log(f"Loaded {len(manifests)} production manifests (last {days}d)")
    return manifests

def load_manifests_days(days: int = 14) -> list[dict]:
    return load_production_manifests(days=days)

def _vision_request(image_b64: str, prompt: str, media_type: str = "image/jpeg") -> dict:
    """Call Claude claude-opus-4-5 Vision with a base64 image. Returns parsed JSON or {}."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    try:
        message = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        raw = message.content[0].text.strip()
        m = re.search(r'\{[\s\S]+\}', raw)
        return json.loads(m.group()) if m else {}
    except Exception as e:
        log(f"  Vision request failed: {e}")
        return {}

def _download_image_b64(url: str) -> tuple[str, str]:
    """Download image URL, return (base64_string, media_type). Returns ('','') on error."""
    import requests
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return "", ""
        ct = r.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        return base64.b64encode(r.content).decode(), ct
    except Exception as e:
        log(f"  Image download failed ({url[:60]}): {e}")
        return "", ""

def _get_youtube_client():
    from googleapiclient.discovery import build
    return build("youtube", "v3", developerKey=os.environ.get("YOUTUBE_API_KEY", ""))


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------
def gather_viral_benchmarks() -> list[dict]:
    """Pull top 10 finance videos from YouTube published in last 24h."""
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not yt_key:
        log("YouTube: SKIP — YOUTUBE_API_KEY not set")
        return []
    results = []
    try:
        yt = _get_youtube_client()
        published_after = (NOW - datetime.timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for query in ["personal finance UK", "uk finance money"]:
            resp = yt.search().list(
                part="snippet",
                q=query,
                type="video",
                publishedAfter=published_after,
                order="viewCount",
                maxResults=5,
            ).execute()
            for item in resp.get("items", []):
                vid = item["id"]["videoId"]
                snip = item["snippet"]
                stats_resp = yt.videos().list(part="statistics", id=vid).execute()
                stats = stats_resp.get("items", [{}])[0].get("statistics", {})
                thumbnail_url = snip.get("thumbnails", {}).get("maxres", snip.get("thumbnails", {}).get("high", {})).get("url", "")
                results.append({
                    "video_id": vid,
                    "title": snip["title"],
                    "description": snip.get("description", "")[:300],
                    "thumbnail_url": thumbnail_url,
                    "view_count": int(stats.get("viewCount", 0)),
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "channel": snip.get("channelTitle", ""),
                    "url": f"https://youtube.com/watch?v={vid}",
                })
        results.sort(key=lambda x: x["view_count"], reverse=True)
        log(f"Viral benchmarks: {len(results)} videos")
    except Exception as e:
        log(f"Viral benchmark scrape failed: {e}")
    return results[:10]

def gather_harbinger_analytics(manifests: list[dict]) -> list[dict]:
    """
    Load real platform metrics from platform_metrics.json (written by
    scripts/platform_metrics.py after each distribution cycle).

    Falls back to direct YouTube Data API lookup if the metrics file is
    absent or stale (older than 12 hours).

    Each returned record contains:
      post_id, slot, scheduled, views, likes, comments,
      yt_views, ig_views, tt_views, shares,
      completion_rate (None — not available from public APIs)
    """
    metrics_file = LOGS_DIR / "platform_metrics.json"

    # Primary: read from platform_metrics.json
    if metrics_file.exists():
        try:
            age_hours = (NOW - datetime.datetime.utcfromtimestamp(
                metrics_file.stat().st_mtime)).total_seconds() / 3600
            if age_hours < 12:
                data   = json.loads(metrics_file.read_text())
                posts  = data.get("posts", [])
                result = [
                    p for p in posts
                    if p.get("post_id")   # must have a YouTube video ID
                ]
                log(f"Analytics: loaded {len(result)} posts from platform_metrics.json "
                    f"(age {age_hours:.1f}h)")
                return result
            else:
                log(f"Analytics: platform_metrics.json is {age_hours:.1f}h old — refreshing")
        except Exception as e:
            log(f"Analytics: platform_metrics.json read error: {e}")

    # Fallback: run the scraper inline so we always have data
    log("Analytics: running platform_metrics scraper inline...")
    try:
        import sys
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        import platform_metrics as pm
        pm.NOW = NOW
        pm.load_env()
        dist_manifests = pm.load_distribution_manifests(days=14)
        yt_data = pm.scrape_youtube(dist_manifests)
        ig_data = pm.scrape_instagram(dist_manifests)
        tt_data = pm.scrape_tiktok(dist_manifests)
        metrics = pm.build_metrics(dist_manifests, yt_data, ig_data, tt_data)
        metrics_file.write_text(json.dumps(metrics, indent=2))
        result = [p for p in metrics.get("posts", []) if p.get("post_id")]
        log(f"Analytics: scraper returned {len(result)} matched posts")
        return result
    except Exception as e:
        log(f"Analytics: inline scraper failed: {traceback.format_exc()[:300]}")
        return []


# ---------------------------------------------------------------------------
# LOOP 1 — First Frame
# ---------------------------------------------------------------------------
def loop_first_frame(viral_benchmarks: list[dict], manifests: list[dict], directives: dict) -> dict:
    """Score thumbnail visual quality via Claude Vision. Update visual_frame_rules."""
    log("--- LOOP 1: First Frame ---")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        log("  SKIP — ANTHROPIC_API_KEY not set")
        return {"skipped": True, "reason": "no ANTHROPIC_API_KEY"}

    THUMB_PROMPT = (
        "Analyse this video thumbnail. Score it 0-100 on scroll-stopping power. "
        "Identify: shadow_ratio_estimate (0.0-1.0), dominant_colour_temp_kelvin (integer), "
        "subject_position (string), what creates unease or curiosity (string), "
        "what would make someone stop scrolling (string). "
        "Return JSON only, no preamble: "
        "{\"score\": 0, \"shadow_ratio\": 0.0, \"colour_temp_kelvin\": 0, "
        "\"subject_position\": \"\", \"unease_source\": \"\", \"stop_scroll_element\": \"\"}"
    )

    viral_scores = []
    viral_patterns = []

    log(f"  Scoring {min(len(viral_benchmarks), 5)} viral thumbnails...")
    for bench in viral_benchmarks[:5]:
        url = bench.get("thumbnail_url", "")
        if not url:
            continue
        b64, mt = _download_image_b64(url)
        if not b64:
            continue
        result = _vision_request(b64, THUMB_PROMPT, mt)
        if result and "score" in result:
            result["title"] = bench["title"]
            result["view_count"] = bench["view_count"]
            viral_scores.append(result)
            log(f"    Viral '{bench['title'][:40]}': score={result.get('score')}, shadow={result.get('shadow_ratio')}")
            viral_patterns.append({
                "stop_scroll_element": result.get("stop_scroll_element", ""),
                "unease_source": result.get("unease_source", ""),
                "shadow_ratio": result.get("shadow_ratio", 0),
                "colour_temp_kelvin": result.get("colour_temp_kelvin", 5000),
            })
        time.sleep(1)

    # Score Harbinger frames (visual_*.jpg from output/)
    harbinger_scores = []
    harbinger_frames = sorted(OUTPUT_DIR.glob("visual_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    log(f"  Scoring {len(harbinger_frames)} Harbinger frames...")
    for frame_path in harbinger_frames:
        try:
            b64 = base64.b64encode(frame_path.read_bytes()).decode()
            result = _vision_request(b64, THUMB_PROMPT)
            if result and "score" in result:
                result["file"] = frame_path.name
                harbinger_scores.append(result)
                log(f"    Harbinger '{frame_path.name}': score={result.get('score')}")
        except Exception as e:
            log(f"  Frame scoring error: {e}")
        time.sleep(1)

    viral_avg = sum(s.get("score", 0) for s in viral_scores) / max(len(viral_scores), 1)
    harbinger_avg = sum(s.get("score", 0) for s in harbinger_scores) / max(len(harbinger_scores), 1)
    gap = round(viral_avg - harbinger_avg, 1)

    # Extract top 3 patterns viral has that Harbinger lacks
    top_patterns = []
    if viral_patterns:
        stop_elements = [p["stop_scroll_element"] for p in viral_patterns if p.get("stop_scroll_element")]
        unease_sources = [p["unease_source"] for p in viral_patterns if p.get("unease_source")]
        high_shadow = [p for p in viral_patterns if p.get("shadow_ratio", 0) > 0.6]
        if stop_elements:
            top_patterns.append(f"Stop-scroll elements: {'; '.join(stop_elements[:2])}")
        if unease_sources:
            top_patterns.append(f"Unease sources: {'; '.join(unease_sources[:2])}")
        if high_shadow:
            top_patterns.append(f"High shadow ratio (>{0.6}) used in {len(high_shadow)}/{len(viral_patterns)} viral thumbnails")
    top_patterns = top_patterns[:3]

    # Write visual learnings
    learnings = {
        "date": DATE_STR,
        "viral_avg_score": round(viral_avg, 1),
        "harbinger_avg_score": round(harbinger_avg, 1),
        "gap_score": gap,
        "top_patterns_viral": top_patterns,
        "viral_scores": viral_scores,
        "harbinger_scores": harbinger_scores,
    }
    (LOGS_DIR / "visual_learnings.json").write_text(json.dumps(learnings, indent=2))

    # Inject top patterns into directives
    if top_patterns:
        directives["visual_frame_rules"] = top_patterns
        log(f"  visual_frame_rules updated: {top_patterns}")

    log(f"  Loop 1 complete — viral avg: {viral_avg:.1f}, Harbinger avg: {harbinger_avg:.1f}, gap: {gap}")
    return {
        "viral_avg_score": round(viral_avg, 1),
        "harbinger_avg_score": round(harbinger_avg, 1),
        "gap_score": gap,
        "top_patterns": top_patterns,
    }


# ---------------------------------------------------------------------------
# LOOP 2 — Silence Geometry
# ---------------------------------------------------------------------------
def loop_silence_geometry(manifests: list[dict], analytics: list[dict], directives: dict) -> dict:
    """Correlate silence durations with completion rates. Update silence_geometry."""
    log("--- LOOP 2: Silence Geometry ---")

    # Load manifests from last 14 days
    all_manifests = load_manifests_days(14)
    if len(all_manifests) < 3:
        log(f"  SKIP — only {len(all_manifests)} manifests, need >=3 for correlation")
        return {"skipped": True, "reason": "insufficient_data"}

    # Map post_id -> analytics
    analytics_map = {a["post_id"]: a for a in analytics}

    # Group by silence duration ranges
    RANGES = [
        ("<200", lambda ms: ms < 200),
        ("200-300", lambda ms: 200 <= ms < 300),
        ("300-400", lambda ms: 300 <= ms < 400),
        ("400-500", lambda ms: 400 <= ms < 500),
        (">500", lambda ms: ms >= 500),
    ]

    # Build data points: (silence_ms, views) per section
    section_data = {"after_intrusion": [], "after_mechanism": [], "before_move": []}

    for m in all_manifests:
        vs = m.get("voice_settings", {})
        silence_intrusion = vs.get("pre_script_silence_ms", 400)
        silence_mechanism = vs.get("post_weight_silence_ms", 200)
        silence_move = vs.get("post_proof_silence_ms", 300)

        # Try to find matching analytics
        vid_path = m.get("video")
        views = 0
        for a in analytics:
            views = max(views, a.get("views", 0))

        section_data["after_intrusion"].append((silence_intrusion, views))
        section_data["after_mechanism"].append((silence_mechanism, views))
        section_data["before_move"].append((silence_move, views))

    # Find best silence range per section
    winners = {}
    for section, data_points in section_data.items():
        if not data_points:
            continue
        range_scores = {}
        for label, test_fn in RANGES:
            group = [views for ms, views in data_points if test_fn(ms)]
            if group:
                range_scores[label] = sum(group) / len(group)
        if range_scores:
            winner_label = max(range_scores, key=range_scores.get)
            # Convert label to a concrete ms value
            winner_ms_map = {"<200": 150, "200-300": 250, "300-400": 350, "400-500": 450, ">500": 550}
            winners[section] = {"label": winner_label, "ms": winner_ms_map[winner_label], "mean_views": round(range_scores[winner_label], 0)}

    learnings = {"date": DATE_STR, "data_points": len(all_manifests), "winners": winners}
    (LOGS_DIR / "silence_learnings.json").write_text(json.dumps(learnings, indent=2))

    # Update directives if we have winners
    if winners:
        sg = directives.setdefault("silence_geometry", {})
        if "after_intrusion" in winners:
            sg["after_intrusion"] = winners["after_intrusion"]["ms"]
        if "after_mechanism" in winners:
            sg["after_mechanism"] = winners["after_mechanism"]["ms"]
        if "before_move" in winners:
            sg["before_move"] = winners["before_move"]["ms"]
        log(f"  silence_geometry updated: {sg}")

    log(f"  Loop 2 complete — {len(all_manifests)} manifests analysed, winners: {list(winners.keys())}")
    return {"data_points": len(all_manifests), "winners": winners}


# ---------------------------------------------------------------------------
# LOOP 3 — Sound Design A/B
# ---------------------------------------------------------------------------
def loop_sound_ab(manifests: list[dict], analytics: list[dict], directives: dict) -> dict:
    """Slot-based sound A/B testing. One variable at a time."""
    log("--- LOOP 3: Sound Design A/B ---")

    state_file = LOGS_DIR / "sound_ab_state.json"
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {}
    else:
        state = {}

    # Defaults
    current_stage = state.get("current_stage", "sub_bass_db")
    stage_start_date = state.get("stage_start_date", DATE_STR)
    locked_variables = state.get("locked_variables", {})
    observations = state.get("observations", [])

    # Log today's slot profiles
    all_14d = load_manifests_days(14)
    for m in all_14d:
        slot = m.get("slot", 0)
        if not slot:
            continue
        profile = SLOT_SOUND_PROFILES.get(slot, {})
        ts = m.get("timestamp", "")[:10]
        obs_key = f"{ts}_slot{slot}"
        # Find analytics for this slot
        views = 0
        for a in analytics:
            if a.get("slot") == slot:
                views = a.get("views", 0)
        if not any(o.get("key") == obs_key for o in observations):
            observations.append({
                "key": obs_key,
                "date": ts,
                "slot": slot,
                "sub_bass_db": profile.get("sub_bass_db"),
                "ambience_type": profile.get("ambience_type"),
                "views": views,
            })

    # Check if we have 10+ days of data to evaluate current stage
    result = {
        "current_stage": current_stage,
        "stage_start_date": stage_start_date,
        "total_observations": len(observations),
        "locked_variables": locked_variables,
    }

    stage_observations = [o for o in observations if o.get("date", "") >= stage_start_date]
    stage_days = (NOW - datetime.datetime.strptime(stage_start_date, "%Y-%m-%d")).days

    if stage_days >= 10 and len(stage_observations) >= 10:
        log(f"  Evaluating {current_stage} after {stage_days} days...")

        if current_stage == "sub_bass_db":
            # Group by sub_bass_db value
            groups = {}
            for o in stage_observations:
                k = str(o.get("sub_bass_db"))
                groups.setdefault(k, []).append(o.get("views", 0))
            winner_db = max(groups, key=lambda k: sum(groups[k]) / max(len(groups[k]), 1))
            locked_variables["sub_bass_db"] = float(winner_db)
            current_stage = "ambience_type"
            stage_start_date = DATE_STR
            log(f"  sub_bass_db winner: {winner_db}dB -> moving to ambience_type test")
            result["stage_winner"] = f"sub_bass_db={winner_db}"

        elif current_stage == "ambience_type":
            groups = {}
            for o in stage_observations:
                k = str(o.get("ambience_type"))
                groups.setdefault(k, []).append(o.get("views", 0))
            winner_amb = max(groups, key=lambda k: sum(groups[k]) / max(len(groups[k]), 1))
            locked_variables["ambience_type"] = winner_amb
            current_stage = "sub_bass_hz"
            stage_start_date = DATE_STR
            log(f"  ambience_type winner: {winner_amb} -> moving to sub_bass_hz test")
            result["stage_winner"] = f"ambience_type={winner_amb}"

        elif current_stage == "sub_bass_hz":
            groups = {}
            for o in stage_observations:
                k = str(o.get("sub_bass_hz", 40))
                groups.setdefault(k, []).append(o.get("views", 0))
            if groups:
                winner_hz = max(groups, key=lambda k: sum(groups[k]) / max(len(groups[k]), 1))
                locked_variables["sub_bass_hz"] = int(winner_hz)
                # Cycle back to sub_bass_db for next round
                current_stage = "sub_bass_db"
                stage_start_date = DATE_STR
                log(f"  sub_bass_hz winner: {winner_hz}Hz -> cycling back to sub_bass_db")
                result["stage_winner"] = f"sub_bass_hz={winner_hz}"

    # Apply locked variables to directives sound_profile
    sp = directives.setdefault("sound_profile", {})
    if "sub_bass_db" in locked_variables:
        sp["sub_bass_db"] = locked_variables["sub_bass_db"]
    if "ambience_type" in locked_variables:
        sp["ambience_type"] = locked_variables["ambience_type"]
    if "sub_bass_hz" in locked_variables:
        sp["sub_bass_hz"] = locked_variables["sub_bass_hz"]

    # Save updated state
    new_state = {
        "current_stage": current_stage,
        "stage_start_date": stage_start_date,
        "locked_variables": locked_variables,
        "observations": observations[-200:],  # keep last 200
        "last_updated": NOW.isoformat(),
    }
    state_file.write_text(json.dumps(new_state, indent=2))
    (LOGS_DIR / "sound_learnings.json").write_text(json.dumps({
        "date": DATE_STR,
        "current_stage": current_stage,
        "stage_days": stage_days,
        "total_observations": len(observations),
        "locked_variables": locked_variables,
    }, indent=2))

    log(f"  Loop 3 complete — testing: {current_stage}, days in stage: {stage_days}, locked: {locked_variables}")
    return result


# ---------------------------------------------------------------------------
# LOOP 4 — Visual Language (Captured vs Generated)
# ---------------------------------------------------------------------------
def loop_visual_language(viral_benchmarks: list[dict], directives: dict) -> dict:
    """Score captured vs generated. Update Kling prompts."""
    log("--- LOOP 4: Visual Language ---")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not anthropic_key:
        log("  SKIP — ANTHROPIC_API_KEY not set")
        return {"skipped": True, "reason": "no ANTHROPIC_API_KEY"}

    CAPTURE_PROMPT = (
        "Score this image 0-100: 100 = looks exactly like real captured footage, "
        "0 = obviously AI generated. "
        "If score < 70, identify the 3 specific visual elements making it look generated rather than captured. "
        "If score >= 70, identify 2 elements that make it look real and captured. "
        "Return JSON only: "
        "{\"captured_score\": 0, \"failing_elements\": [], \"captured_elements\": []}"
    )

    # Score Harbinger Kling frames
    harbinger_frames = sorted(OUTPUT_DIR.glob("visual_*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    harbinger_scores = []
    failing_elements_all = []
    log(f"  Scoring {len(harbinger_frames)} Harbinger Kling frames...")
    for fp in harbinger_frames:
        try:
            b64 = base64.b64encode(fp.read_bytes()).decode()
            result = _vision_request(b64, CAPTURE_PROMPT)
            if result:
                score = result.get("captured_score", 0)
                harbinger_scores.append(score)
                failing = result.get("failing_elements", [])
                failing_elements_all.extend(failing)
                log(f"    {fp.name}: captured_score={score}, failing={failing}")
        except Exception as e:
            log(f"  Frame error: {e}")
        time.sleep(1)

    # Score viral thumbnail frames for captured elements to learn from
    viral_captured_elements = []
    log(f"  Scoring {min(len(viral_benchmarks), 3)} viral frames...")
    for bench in viral_benchmarks[:3]:
        url = bench.get("thumbnail_url", "")
        if not url:
            continue
        b64, mt = _download_image_b64(url)
        if not b64:
            continue
        result = _vision_request(b64, CAPTURE_PROMPT, mt)
        if result and result.get("captured_score", 0) >= 70:
            viral_captured_elements.extend(result.get("captured_elements", []))
            log(f"    Viral '{bench['title'][:40]}': score={result.get('captured_score')}")
        time.sleep(1)

    mean_score = round(sum(harbinger_scores) / max(len(harbinger_scores), 1), 1)

    # Deduplicate failing elements
    failing_unique = list(dict.fromkeys(failing_elements_all))[:5]
    captured_unique = list(dict.fromkeys(viral_captured_elements))[:5]

    # Update Kling prompts in directives
    changed = False
    if failing_unique:
        existing_neg = set(directives.get("kling_negative_prompt_additions", []))
        new_neg = existing_neg | set(failing_unique)
        directives["kling_negative_prompt_additions"] = list(new_neg)
        changed = True
        log(f"  Kling negative additions: {failing_unique}")

    if captured_unique:
        existing_base = set(directives.get("kling_base_prompt_additions", []))
        new_base = existing_base | set(captured_unique)
        directives["kling_base_prompt_additions"] = list(new_base)
        changed = True
        log(f"  Kling base additions: {captured_unique}")

    learnings = {
        "date": DATE_STR,
        "mean_harbinger_captured_score": mean_score,
        "harbinger_scores": harbinger_scores,
        "failing_elements": failing_unique,
        "captured_elements_to_learn": captured_unique,
    }
    (LOGS_DIR / "visual_language_learnings.json").write_text(json.dumps(learnings, indent=2))

    log(f"  Loop 4 complete — mean captured score: {mean_score}, failing elements: {len(failing_unique)}")
    return {"mean_captured_score": mean_score, "failing_elements": failing_unique, "captured_elements": captured_unique}


# ---------------------------------------------------------------------------
# LOOP 5 — Acoustic Space
# ---------------------------------------------------------------------------
def loop_acoustic_space(manifests: list[dict], analytics: list[dict], directives: dict) -> dict:
    """Correlate reverb profiles with share rates. Update acoustic_profile."""
    log("--- LOOP 5: Acoustic Space ---")

    all_14d = load_manifests_days(14)
    if len(all_14d) < 5:
        log(f"  SKIP — only {len(all_14d)} manifests, need >=5")
        return {"skipped": True, "reason": "insufficient_data"}

    # Build dataset: reverb profile -> shares
    REVERB_PROFILES = [
        {"reverb_tail_ms": 200, "pre_delay_ms": 10, "room_size": "small"},
        {"reverb_tail_ms": 400, "pre_delay_ms": 25, "room_size": "medium"},
        {"reverb_tail_ms": 600, "pre_delay_ms": 40, "room_size": "large"},
    ]

    analytics_map = {}
    for a in analytics:
        key = f"slot{a.get('slot', 0)}"
        analytics_map[key] = a.get("views", 0)

    # Group manifests by reverb profile used
    profile_groups: dict[str, list[int]] = {}
    for m in all_14d:
        # Read reverb from sound_profile or directives default
        sp = m.get("sound_profile", directives.get("acoustic_profile", REVERB_PROFILES[1]))
        tail = sp.get("reverb_tail_ms", 400)
        if tail < 300:
            profile_key = "small"
        elif tail < 500:
            profile_key = "medium"
        else:
            profile_key = "large"

        slot = m.get("slot", 0)
        views = analytics_map.get(f"slot{slot}", 0)
        profile_groups.setdefault(profile_key, []).append(views)

    # Calculate mean views per profile
    profile_means = {k: round(sum(v) / max(len(v), 1), 0) for k, v in profile_groups.items()}
    log(f"  Profile means: {profile_means}")

    winner_profile_key = None
    winner_config = REVERB_PROFILES[1]  # default medium
    if len(profile_means) >= 2:
        winner_profile_key = max(profile_means, key=profile_means.get)
        key_to_config = {"small": REVERB_PROFILES[0], "medium": REVERB_PROFILES[1], "large": REVERB_PROFILES[2]}
        winner_config = key_to_config.get(winner_profile_key, REVERB_PROFILES[1])
        log(f"  Winning reverb profile: {winner_profile_key} — {winner_config}")
        directives["acoustic_profile"] = winner_config

    learnings = {
        "date": DATE_STR,
        "data_points": len(all_14d),
        "profile_means": profile_means,
        "winner_profile": winner_profile_key,
        "winner_config": winner_config,
    }
    (LOGS_DIR / "acoustic_learnings.json").write_text(json.dumps(learnings, indent=2))

    log(f"  Loop 5 complete — winner: {winner_profile_key}, profiles analysed: {list(profile_groups.keys())}")
    return {"winner_profile": winner_profile_key, "config": winner_config, "profile_means": profile_means}


# ---------------------------------------------------------------------------
# LOOP 6 — Caption AB
# ---------------------------------------------------------------------------
def loop_caption_ab(manifests: list[dict], analytics: list[dict]) -> dict:
    """
    Evaluate IMPACT STACK caption AB test results.
    Delegates to caption_engine.run_ab_update() which owns the AB state machine.
    Enriches manifests with completion_rate (primary) and views (secondary) from
    analytics — completion_rate is the algorithm-weighted metric that drives reach.
    """
    log("--- LOOP 6: Caption AB ---")
    import sys
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    try:
        import caption_engine
    except ImportError:
        log("  caption_engine not found — skipping")
        return {"skipped": True}

    # Enrich manifests with completion_rate + views from analytics
    analytics_by_id = {a.get("post_id"): a for a in analytics if a.get("post_id")}
    enriched = []
    for m in manifests:
        if not m.get("caption_metadata"):
            continue
        em = dict(m)
        post_id = m.get("post_id") or m.get("youtube_post_id")
        if post_id and post_id in analytics_by_id:
            a = analytics_by_id[post_id]
            em["views"]           = a.get("views", 0)
            # completion_rate: 0.0–1.0 fraction of viewers reaching 70% of video
            em["completion_rate"] = a.get("completion_rate") or a.get("watch_time_pct")
            # Write back into caption_metadata so run_ab_update can read it
            if em.get("caption_metadata"):
                em["caption_metadata"]["completion_rate"] = em["completion_rate"]
                em["caption_metadata"]["watch_time_pct"]  = a.get("watch_time_pct")
        enriched.append(em)

    log(f"  Caption manifests with metadata: {len(enriched)}, "
        f"with completion data: {sum(1 for e in enriched if e.get('completion_rate'))}")

    log(f"  Caption manifests with metadata: {len(enriched)}")
    if not enriched:
        return {"status": "no_data", "message": "No caption metadata in manifests yet"}

    result = caption_engine.run_ab_update(enriched)
    if result is None:
        caps = caption_engine.load_directives()
        ab   = caps.get("ab_test", {})
        return {
            "status":           "waiting",
            "current_variable": ab.get("current_variable"),
            "current_index":    ab.get("current_index"),
            "samples_needed":   ab.get("min_samples", 10),
            "samples_found":    len(enriched),
        }

    return {
        "status":  "updated",
        "history": result.get("ab_test", {}).get("history", [])[-1],
    }


def loop_prompt_genome(manifests: list[dict], analytics: list[dict]) -> dict:
    """
    Loop 7: Prompt Genome Evolution.
    Scores each video's prompt components via visual_score from analytics,
    calls prompt_engine.self_improve() to update genome fitness and evolve.
    Runs nightly — components below fitness 25 retired, above 80 mutated.
    """
    log("--- LOOP 7: Prompt Genome ---")
    sys.path.insert(0, str(BASE_DIR / "scripts"))
    try:
        import prompt_engine
    except ImportError:
        log("  prompt_engine not found — skipping")
        return {"skipped": True}

    # Enrich manifests: attach visual_score from analytics (completion proxy)
    analytics_by_id = {a.get("post_id"): a for a in analytics if a.get("post_id")}
    scored = []
    for m in manifests:
        if not m.get("prompt_metadata"):
            continue
        em = dict(m)
        post_id = m.get("post_id") or m.get("youtube_post_id")
        if post_id and post_id in analytics_by_id:
            a = analytics_by_id[post_id]
            # visual_score: completion_rate * 100 weighted 0.6 + view_normalised 0.4
            completion = a.get("completion_rate", 0.5)
            views = a.get("views", 0)
            view_score = min(100, views / 1000)  # 1M views = 100
            em["visual_score"] = round(completion * 60 + view_score * 0.4, 1)
        else:
            em["visual_score"] = 50  # neutral if no analytics yet
        scored.append(em)

    log(f"  Manifests with prompt_metadata: {len(scored)}")
    if not scored:
        genome_path = BASE_DIR / "logs" / "prompt_genome.json"
        if genome_path.exists():
            g = json.loads(genome_path.read_text())
            return {"status": "no_new_data", "genome_generation": g.get("generation", 1)}
        return {"status": "no_data"}

    result = prompt_engine.self_improve(scored)
    return {
        "status": "evolved" if result.get("evolved") else "scored",
        "videos_scored": result.get("videos_scored", 0),
        "genome_generation": result.get("generation", 1),
        "components_retired": result.get("retired", 0),
        "mutations_added": result.get("mutations", 0),
    }


# ---------------------------------------------------------------------------
# LOOP 8 — Trust Signal Scorer
# ---------------------------------------------------------------------------
def loop_trust_signal_scorer(manifests: list[dict], analytics: list[dict]) -> dict:
    """
    Loop 8: Trust Signal Scorer.

    For each creative brief in the manifest window, scores four trust dimensions:
      - source_credibility (0-10): named, verifiable UK source vs vague claim
      - uk_relevance (0-10): FCA/FSCS/HMRC/FOS vs generic or US-framing
      - affiliate_framing (0-10): empowerment framing vs promotional framing
      - claim_specificity (0-10): specific figure + named source vs "studies show"

    trust_score = weighted average (credibility 0.30, uk 0.25, framing 0.25, specificity 0.20)

    Thresholds:
      < 4.0 — flag auto_retry: creative_synthesis should re-run with trust corrections
      < 6.0 — flag review: manual check before distribution
      >= 6.0 — pass

    Nightly: correlates trust_score against completion_rate and share_velocity.
    Retiring scripts with trust_score < 5.0 after accumulating 3 posts.
    """
    log("--- LOOP 8: Trust Signal Scorer ---")

    TRUST_STATE_FILE = LOGS_DIR / "trust_signal_state.json"

    def _score_source_credibility(brief: dict) -> float:
        """Score whether the proof/weight cite a named, verifiable UK source."""
        trust_anchors = brief.get("trust_anchors", {})
        citation = trust_anchors.get("source_citation", "")
        proof = brief.get("script", {}).get("proof", "")
        weight = brief.get("script", {}).get("weight", "")

        combined = (citation + " " + proof + " " + weight).lower()

        # Named UK regulatory bodies / publications — high credibility
        uk_named_sources = [
            "fca", "hmrc", "fos", "fscs", "pra", "cma", "ons",
            "bank of england", "which?", "moneysavingexpert", "consumer duty",
            "mortgage market review", "value for money", "cp23", "ps22",
            "isa statistics", "chainalysis",
        ]
        named_count = sum(1 for src in uk_named_sources if src in combined)

        # Penalise vague phrases
        vague_phrases = ["studies show", "research shows", "experts say", "many people", "some research"]
        vague_count = sum(1 for ph in vague_phrases if ph in combined)

        # US-only regulatory references without UK context
        us_only = ["sec ", "finra", "cftc", "sec v.", "sec charged"]
        us_count = sum(1 for ref in us_only if ref in combined)

        score = min(5.0 + named_count * 1.5 - vague_count * 1.0 - us_count * 1.5, 10.0)
        return max(score, 0.0)

    def _score_uk_relevance(brief: dict) -> float:
        """Score how UK-specific the framing is for the target audience."""
        trust_anchors = brief.get("trust_anchors", {})
        uk_signal = trust_anchors.get("uk_relevance_signal", "")
        script = brief.get("script", {})
        combined = " ".join([
            uk_signal,
            script.get("mechanism", ""),
            script.get("proof", ""),
            script.get("weight", ""),
        ]).lower()

        uk_signals = [
            "fca", "fscs", "isa", "hmrc", "ns&i", "pension", "mortgage",
            "bank of england", "base rate", "consumer duty", "fos",
            "financial ombudsman", "pra", "savings account", "tax-free",
            "april 5", "£", "british", "uk ",
        ]
        us_signals = [
            "sec ", "401(k)", "roth ira", "dollar", "finra", "fed ", "federal reserve",
            "$1.4 million", "$241 million",
        ]

        uk_count = sum(1 for s in uk_signals if s in combined)
        us_count = sum(1 for s in us_signals if s in combined)

        score = min(3.0 + uk_count * 0.8 - us_count * 1.2, 10.0)
        return max(score, 0.0)

    def _score_affiliate_framing(brief: dict) -> float:
        """Score whether the affiliate is framed as empowerment vs promotion."""
        trust_anchors = brief.get("trust_anchors", {})
        integration_type = trust_anchors.get("affiliate_integration_type", "")
        move = brief.get("script", {}).get("move", "").lower()

        # Integration type scoring
        type_scores = {
            "educational_tool": 8.5,
            "comparison_resource": 8.0,
            "next_step": 7.5,
        }
        base = type_scores.get(integration_type, 5.0)

        # Penalise promotional language in move section
        promotional_phrases = [
            "logical tool", "best tool", "sign up", "click below", "use my link",
            "exclusive", "limited time", "deal", "discount",
        ]
        empowerment_phrases = [
            "what professionals use", "financially-aware people use",
            "gives you visibility", "navigate this", "track positions",
            "what i use", "used by people who understand",
        ]

        penalty = sum(1 for ph in promotional_phrases if ph in move) * 1.5
        bonus = sum(1 for ph in empowerment_phrases if ph in move) * 1.0

        score = min(base - penalty + bonus, 10.0)
        return max(score, 0.0)

    def _score_claim_specificity(brief: dict) -> float:
        """Score whether key claims are specific (figure + source) vs vague."""
        script = brief.get("script", {})
        proof = script.get("proof", "")
        weight = script.get("weight", "")
        combined = (proof + " " + weight).lower()

        # Specific elements: numbers, dates, named entities
        has_figure = bool(re.search(r'£[\d,]+|[\d,]+%|\d+\s*(million|billion|thousand)', combined))
        has_date = bool(re.search(r'20\d\d|january|february|march|april|may|june|july|august|september|october|november|december', combined))
        has_named_entity = bool(re.search(r'fca|hmrc|bank of england|which\?|moneysavingexpert|ons|pra|fscs', combined))

        # Penalise unverifiable generics
        generic_phrases = ["most people", "many banks", "all banks", "every bank", "countless", "invisible tax"]
        generic_count = sum(1 for ph in generic_phrases if ph in combined)

        score = 4.0
        if has_figure:
            score += 2.0
        if has_date:
            score += 1.5
        if has_named_entity:
            score += 2.0
        score -= generic_count * 0.8

        return max(min(score, 10.0), 0.0)

    def _trust_score(cred: float, uk: float, framing: float, spec: float) -> float:
        return round(cred * 0.30 + uk * 0.25 + framing * 0.25 + spec * 0.20, 2)

    # Load or initialise state
    state = {}
    if TRUST_STATE_FILE.exists():
        try:
            state = json.loads(TRUST_STATE_FILE.read_text())
        except Exception:
            state = {}

    scores_this_run = []
    flagged_retry = []
    flagged_review = []
    retired_this_run = []

    # Score all available creative briefs in manifest window
    brief_paths = sorted(
        LOGS_DIR.glob("creative_brief_*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:20]  # last 20 briefs

    # Build analytics lookup
    analytics_map = {}
    for a in analytics:
        pid = a.get("post_id") or a.get("slot")
        if pid:
            analytics_map[str(pid)] = a

    for bp in brief_paths:
        try:
            brief = json.loads(bp.read_text())
        except Exception:
            continue

        brief_id = bp.stem  # e.g. creative_brief_20260309_145702_slot3

        cred  = _score_source_credibility(brief)
        uk    = _score_uk_relevance(brief)
        frame = _score_affiliate_framing(brief)
        spec  = _score_claim_specificity(brief)
        ts    = _trust_score(cred, uk, frame, spec)

        entry = state.get(brief_id, {"posts": 0, "scores": []})
        entry["scores"].append(ts)
        entry["last_score"] = ts
        entry["last_run"] = DATE_STR
        entry["dimension_breakdown"] = {
            "source_credibility": round(cred, 2),
            "uk_relevance": round(uk, 2),
            "affiliate_framing": round(frame, 2),
            "claim_specificity": round(spec, 2),
        }
        state[brief_id] = entry

        scores_this_run.append(ts)

        if ts < 4.0:
            flagged_retry.append(brief_id)
            log(f"  TRUST AUTO-RETRY flag: {brief_id} score={ts}")
        elif ts < 6.0:
            flagged_review.append(brief_id)
            log(f"  TRUST REVIEW flag: {brief_id} score={ts}")

        # Retire if trust_score < 5.0 after 3+ posts
        posts_count = entry.get("posts", 0)
        avg_score = sum(entry["scores"][-3:]) / max(len(entry["scores"][-3:]), 1)
        if posts_count >= 3 and avg_score < 5.0:
            entry["status"] = "retired"
            retired_this_run.append(brief_id)
            log(f"  TRUST RETIRED: {brief_id} avg_score={avg_score:.2f} after {posts_count} posts")

    # Correlate trust scores with completion_rate and share_velocity if analytics available
    correlation_data = []
    for a in analytics:
        pid = str(a.get("post_id") or a.get("slot", ""))
        completion = a.get("completion_rate", 0) or 0
        shares = a.get("shares", 0) or 0
        # Match brief by slot in filename
        for brief_id, entry in state.items():
            if f"slot{a.get('slot', '')}" in brief_id:
                last_ts = entry.get("last_score")
                if last_ts is not None:
                    correlation_data.append({
                        "brief_id": brief_id,
                        "trust_score": last_ts,
                        "completion_rate": completion,
                        "shares": shares,
                    })

    avg_trust = round(sum(scores_this_run) / max(len(scores_this_run), 1), 2) if scores_this_run else None

    TRUST_STATE_FILE.write_text(json.dumps(state, indent=2))

    result = {
        "briefs_scored": len(scores_this_run),
        "avg_trust_score": avg_trust,
        "flagged_auto_retry": flagged_retry,
        "flagged_review": flagged_review,
        "retired_this_run": retired_this_run,
        "correlation_sample_size": len(correlation_data),
        "correlation_data": correlation_data[:10],  # cap for report size
    }

    log(f"  Loop 8 complete — scored={len(scores_this_run)}, avg={avg_trust}, "
        f"retry_flags={len(flagged_retry)}, review_flags={len(flagged_review)}, "
        f"retired={len(retired_this_run)}")
    return result


# Loop A — Post-render visual identity scorer
# ---------------------------------------------------------------------------
def loop_a_visual_scorer(slot: int, manifest: dict, directives: dict) -> dict:
    """
    Loop A: Post-render visual identity scoring via Claude Vision.

    Extracts frames from the rendered video, sends each to Claude Vision,
    and scores whether the visual identity the system derived from its own
    research was actually executed in the render.

    Failures update the Kling negative prompt in directives immediately.
    Always non-blocking — posts regardless of score.

    Requires ANTHROPIC_API_KEY. Gracefully skips if absent.
    """
    import subprocess, base64, tempfile

    result = {
        "status": "skipped",
        "reason": "ANTHROPIC_API_KEY not set",
        "shot_scores": [],
        "negative_prompt_additions": [],
        "avg_visual_score": None,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        log("  Loop A: ANTHROPIC_API_KEY absent — skipping visual scoring")
        return result

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
    except ImportError:
        result["reason"] = "anthropic SDK not installed"
        log("  Loop A: anthropic SDK not installed")
        return result

    # Find rendered video
    video_path = Path(manifest.get("video", ""))
    if not video_path.exists():
        ts  = manifest.get("timestamp", "").replace("-", "").replace(":", "").replace("T", "_")[:15]
        sl  = manifest.get("slot", slot)
        candidates = sorted(OUTPUT_DIR.glob(f"post_*_slot{sl}*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        video_path = candidates[0] if candidates else None

    if not video_path or not video_path.exists():
        result["reason"] = "rendered video not found"
        log("  Loop A: rendered video not found — skipping")
        return result

    # Get video duration
    probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    try:
        duration = float(subprocess.check_output(probe_cmd, stderr=subprocess.DEVNULL).decode().strip())
    except Exception:
        duration = 60.0

    # Extract 3 frames: 15%, 50%, 85% of duration (HOOK / MECHANISM / MOVE zones)
    frame_times = [duration * 0.15, duration * 0.50, duration * 0.85]
    frame_labels = ["HOOK", "MECHANISM", "MOVE"]

    # Visual identity: what the research said the visual should look and feel like
    vd = manifest.get("visual_direction", {}) or {}
    visual_identity = vd.get("visual_identity", "") or vd.get("kling_prompt", "")
    intended_mood = vd.get("mood", "") or vd.get("emotional_tone", "")
    intended_palette = vd.get("colour_palette", "") or vd.get("palette", "")
    topic = manifest.get("topic", "") or manifest.get("asymmetry", "")[:80]

    identity_description = (
        f"Topic: {topic}\n"
        f"Visual identity: {visual_identity[:300]}\n"
        f"Intended mood: {intended_mood}\n"
        f"Colour palette: {intended_palette}"
    ).strip()

    shot_scores = []
    negative_additions = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (t, label) in enumerate(zip(frame_times, frame_labels)):
            frame_path = Path(tmpdir) / f"frame_{i}.jpg"
            extract_cmd = [
                "ffmpeg", "-ss", str(t), "-i", str(video_path),
                "-vframes", "1", "-q:v", "3", str(frame_path), "-y",
            ]
            try:
                subprocess.run(extract_cmd, check=True, capture_output=True, timeout=15)
            except Exception as e:
                log(f"  Loop A: frame {label} extraction failed: {e}")
                continue

            if not frame_path.exists():
                continue

            img_b64 = base64.standard_b64encode(frame_path.read_bytes()).decode()

            prompt = (
                f"You are evaluating a single frame from a short-form finance video.\n\n"
                f"INTENDED VISUAL IDENTITY:\n{identity_description}\n\n"
                f"FRAME: {label} zone ({t:.1f}s into a {duration:.0f}s video)\n\n"
                f"Score this frame 1-10 on how well it executes the intended visual identity. "
                f"Consider: emotional tone, colour palette, framing, whether it creates the "
                f"precondition for the financial mechanism being described.\n\n"
                f"Respond as JSON only:\n"
                f'{{"score": 0, "executed_well": ["..."], "failed_elements": ["..."], '
                f'"negative_prompt_additions": ["specific elements to exclude in future Kling prompts"]}}'
            )

            try:
                resp = client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=400,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )
                import re as _re
                text = resp.content[0].text.strip()
                text = _re.sub(r'^```json\s*', '', text)
                text = _re.sub(r'\s*```$', '', text)
                score_data = json.loads(text)
                score_data["label"] = label
                score_data["time_s"] = round(t, 1)
                shot_scores.append(score_data)

                adds = score_data.get("negative_prompt_additions", [])
                negative_additions.extend(adds)

                log(f"  Loop A {label}: score={score_data.get('score')}/10 | "
                    f"failures={score_data.get('failed_elements', [])[:2]}")

            except Exception as e:
                log(f"  Loop A {label}: Claude call failed: {e}")

    # Update Kling negative prompt in directives immediately
    if negative_additions:
        existing = set(directives.get("kling_negative_prompt_additions", []))
        new_negs  = set(negative_additions) - existing
        if new_negs:
            directives["kling_negative_prompt_additions"] = list(existing | new_negs)
            log(f"  Loop A: added {len(new_negs)} Kling negative prompt elements: {list(new_negs)[:5]}")

    avg = round(sum(s.get("score", 0) for s in shot_scores) / len(shot_scores), 1) if shot_scores else None
    log(f"  Loop A complete — {len(shot_scores)} frames scored, avg={avg}")

    # Write per-slot score log
    score_log = LOGS_DIR / f"loop_a_scores_{TIMESTAMP}_slot{slot}.json"
    score_log.write_text(json.dumps({
        "timestamp": NOW.isoformat(),
        "slot": slot,
        "video": str(video_path),
        "visual_identity": identity_description,
        "shot_scores": shot_scores,
        "avg_visual_score": avg,
        "negative_prompt_additions": negative_additions,
    }, indent=2))

    return {
        "status": "complete",
        "shot_scores": shot_scores,
        "avg_visual_score": avg,
        "negative_prompt_additions": negative_additions,
    }


# Loop B nightly — pattern analysis across accumulated findings
# ---------------------------------------------------------------------------
def loop_b_pattern_analysis(directives: dict) -> dict:
    """
    Nightly Loop B: Analyses accumulated loop_b_findings.json to identify
    structural weaknesses in the brief architecture that repeat across slots.

    Updates creative_directives with permanent structural improvements
    for Agent 2 to absorb.
    """
    findings_path = LOGS_DIR / "loop_b_findings.json"
    if not findings_path.exists():
        log("  Loop B nightly: no findings yet")
        return {"status": "no_data"}

    try:
        findings = json.loads(findings_path.read_text())
    except Exception:
        return {"status": "error", "reason": "could not read loop_b_findings.json"}

    if len(findings) < 3:
        log(f"  Loop B nightly: {len(findings)} findings — waiting for more data")
        return {"status": "insufficient_data", "count": len(findings)}

    # Aggregate scores per chain link
    link_scores: dict[str, list[float]] = {}
    for f in findings[-50:]:  # last 50
        for link, score in f.get("chain_scores", {}).items():
            link_scores.setdefault(link, []).append(float(score))

    link_avgs = {k: round(sum(v) / len(v), 1) for k, v in link_scores.items() if v}
    worst_links = sorted(link_avgs.items(), key=lambda x: x[1])[:2]

    rewrite_rate = sum(1 for f in findings[-20:] if f.get("rewrite_required")) / min(len(findings), 20)
    avg_chain_score = round(sum(f.get("overall_chain_score", 0) for f in findings[-20:]) / min(len(findings), 20), 1)

    log(f"  Loop B nightly: avg chain score={avg_chain_score}, rewrite rate={rewrite_rate:.0%}")
    log(f"  Worst links: {worst_links}")

    # Inject structural directives into creative_directives for Agent 2
    improvements = []
    for link, avg in worst_links:
        if avg < 6.0:
            directive_map = {
                "visual_identity_precondition": (
                    "STRUCTURAL FIX (Loop B): Visual identity priming is chronically weak. "
                    "Every brief must open its visual direction with an extreme close-up of something "
                    "ordinary made unsettling. The first frame is the emotional permission slip for everything that follows."
                ),
                "mechanism_specificity": (
                    "STRUCTURAL FIX (Loop B): Mechanism specificity is chronically failing. "
                    "Every mechanism must include: a named UK regulatory body, a specific percentage or figure, "
                    "and a verifiable 2023-2025 source. No named source = no mechanism."
                ),
                "emotional_arc_to_cta": (
                    "STRUCTURAL FIX (Loop B): Emotional arc collapses before the CTA. "
                    "THE MOVE section must escalate, not explain. End with a command, not a description. "
                    "The tension must peak at the CTA, not before it."
                ),
                "affiliate_as_conclusion": (
                    "STRUCTURAL FIX (Loop B): Affiliate consistently lands as interruption. "
                    "Restructure THE MOVE so the affiliate is the answer to the question the mechanism just made urgent. "
                    "Name the problem, then name the tool. Never pitch."
                ),
                "comment_trigger_structural": (
                    "STRUCTURAL FIX (Loop B): Comment triggers are consistently absent or accidental. "
                    "Add a dedicated comment trigger as the penultimate sentence — before the CTA, after the mechanism. "
                    "Format: [provocative claim that invites disagreement or completion]"
                ),
            }
            if link in directive_map:
                improvements.append(directive_map[link])

    if improvements:
        existing_instructions = directives.get("instructions", "")
        # Remove old Loop B structural fixes before appending new ones
        existing_instructions = "\n".join(
            line for line in existing_instructions.splitlines()
            if "STRUCTURAL FIX (Loop B)" not in line
        )
        directives["instructions"] = (
            existing_instructions.strip() + "\n\n" + "\n".join(improvements)
        ).strip()
        log(f"  Loop B nightly: {len(improvements)} structural directives written to creative_directives")

    return {
        "status": "complete",
        "avg_chain_score": avg_chain_score,
        "rewrite_rate": round(rewrite_rate, 2),
        "worst_links": dict(worst_links),
        "structural_improvements_added": len(improvements),
    }


# Main
# ---------------------------------------------------------------------------
def run_micro(slot: int, dry_run: bool = False):
    """
    Per-render micro-loop — runs immediately after every production cycle.
    No external API calls. Only loops 6 (caption AB) and 7 (prompt genome).
    Fast (<60s), always fires regardless of analytics availability.
    """
    log("=" * 60)
    log(f"AGENT 4: QUALITY MIRROR (MICRO — slot {slot})")
    log("=" * 60)

    directives = load_directives()

    # Load only the single most-recent manifest for this slot
    recent = sorted(
        LOGS_DIR.glob(f"production_manifest_*_slot{slot}.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    manifests = []
    if recent:
        try:
            manifests = [json.loads(recent[0].read_text())]
            log(f"Micro: loaded manifest {recent[0].name}")
        except Exception:
            pass

    if not manifests:
        log("Micro: no manifest found — nothing to learn from yet")
        return

    loop_results = {}

    # Loop 6: Caption AB — advance AB state with new render data
    try:
        loop_results["caption_ab"] = loop_caption_ab(manifests, [])
    except Exception as e:
        log(f"Micro Loop 6 error: {e}")
        loop_results["caption_ab"] = {"error": str(e)}

    # Loop 7: Prompt Genome — score this video's prompt components (neutral score until analytics arrive)
    try:
        loop_results["prompt_genome"] = loop_prompt_genome(manifests, [])
    except Exception as e:
        log(f"Micro Loop 7 error: {e}")
        loop_results["prompt_genome"] = {"error": str(e)}

    # Loop A: Visual identity scorer — extract frames, score against research-derived identity
    # Always non-blocking. Requires ANTHROPIC_API_KEY. Updates Kling negative prompt immediately.
    directives = load_directives()
    try:
        loop_results["loop_a"] = loop_a_visual_scorer(slot, manifests[0], directives)
    except Exception as e:
        log(f"Micro Loop A error: {e}")
        loop_results["loop_a"] = {"status": "error", "reason": str(e)}

    if not dry_run and loop_results.get("loop_a", {}).get("negative_prompt_additions"):
        # Write directives with any new Kling negative prompts from Loop A
        save_directives(directives, ["kling_negative_prompt_additions"])

    # Timing Analysis: character-level ElevenLabs alignment → word timing → pacing report.
    # Measures actual hook pace, pre-hook silence, and structural pause positions.
    # Feeds Loop 2 (Silence Geometry) with measured values instead of estimates.
    try:
        sys.path.insert(0, str(BASE_DIR / "scripts"))
        from timing_analyser import analyse_slot as _ta_analyse, save_timing_report as _ta_save
        _output_dir = BASE_DIR / "output"
        _alignments = sorted(
            _output_dir.glob(f"audio_alignment_*_slot{slot}.json"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if _alignments:
            timing_report = _ta_analyse(_alignments[0])
            _ta_save(timing_report, TIMESTAMP, slot)
            loop_results["timing"] = timing_report
            hp = timing_report.get("hook_pace", {})
            sp = timing_report.get("structural_pauses", {})
            log(f"Timing: hook_wps={hp.get('hook_wps')} | "
                f"pre_hook={timing_report.get('pre_hook_silence_ms')}ms | "
                f"pauses={timing_report.get('pauses_detected')} | "
                f"after_hook={sp.get('after_hook_ms')}ms")
        else:
            log("Timing: no audio alignment file for this slot")
            loop_results["timing"] = {"status": "no_alignment_file"}
    except Exception as e:
        log(f"Micro timing analysis error: {e}")
        loop_results["timing"] = {"error": str(e)}

    genome   = loop_results.get("prompt_genome", {})
    caption  = loop_results.get("caption_ab", {})
    loop_a   = loop_results.get("loop_a", {})
    timing   = loop_results.get("timing", {})

    report = {
        "mode": "micro",
        "slot": slot,
        "timestamp": NOW.isoformat(),
        "prompt_genome": genome,
        "caption_ab": caption,
        "loop_a_visual": loop_a,
        "timing": timing,
    }
    report_path = LOGS_DIR / f"micro_delta_{TIMESTAMP}_slot{slot}.json"
    report_path.write_text(json.dumps(report, indent=2))
    log(f"Micro delta saved: {report_path.name}")

    loop_a_line = (
        f"Loop A: avg={loop_a.get('avg_visual_score', 'n/a')} | "
        f"negs={len(loop_a.get('negative_prompt_additions', []))}"
        if loop_a.get("status") == "complete"
        else f"Loop A: {loop_a.get('status', 'skipped')}"
    )
    hp = timing.get("hook_pace", {})
    timing_line = (
        f"Timing: hook={hp.get('hook_wps', '?')}wps | "
        f"pre={timing.get('pre_hook_silence_ms', '?')}ms silence"
        if hp else "Timing: n/a"
    )

    send_telegram(
        f"⚡ *Harbinger Slot {slot} — Micro Loop*\n"
        f"Genome: gen={genome.get('genome_generation', '?')} | "
        f"scored={genome.get('videos_scored', 0)} | "
        f"retired={genome.get('components_retired', 0)}\n"
        f"Caption AB: {caption.get('status', 'n/a')}\n"
        f"{loop_a_line}\n"
        f"{timing_line}"
    )
    log("=" * 60)
    log("Quality Mirror micro-loop complete")
    log("=" * 60)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run loops but don't write directives")
    parser.add_argument("--micro", action="store_true",
                        help="Per-render micro mode: only genome + caption AB, no API calls")
    parser.add_argument("--slot", type=int, default=0, choices=[0, 1, 2, 3, 4, 5],
                        help="Slot number for micro mode (0 = nightly full run)")
    args = parser.parse_args()

    load_env()

    if args.micro:
        run_micro(slot=args.slot or 1, dry_run=args.dry_run)
        return

    log("=" * 60)
    log("AGENT 4: QUALITY MIRROR")
    log("=" * 60)

    directives = load_directives()
    directives_original = json.dumps(directives, sort_keys=True)

    # Gather data
    manifests = load_production_manifests(days=2)
    viral_benchmarks = gather_viral_benchmarks()
    analytics = gather_harbinger_analytics(manifests)

    # Run all 5 loops
    loop_results = {}

    try:
        loop_results["first_frame"] = loop_first_frame(viral_benchmarks, manifests, directives)
    except Exception as e:
        log(f"Loop 1 error: {traceback.format_exc()[:300]}")
        loop_results["first_frame"] = {"error": str(e)}

    try:
        loop_results["silence_geometry"] = loop_silence_geometry(manifests, analytics, directives)
    except Exception as e:
        log(f"Loop 2 error: {traceback.format_exc()[:300]}")
        loop_results["silence_geometry"] = {"error": str(e)}

    try:
        loop_results["sound_ab"] = loop_sound_ab(manifests, analytics, directives)
    except Exception as e:
        log(f"Loop 3 error: {traceback.format_exc()[:300]}")
        loop_results["sound_ab"] = {"error": str(e)}

    try:
        loop_results["visual_language"] = loop_visual_language(viral_benchmarks, directives)
    except Exception as e:
        log(f"Loop 4 error: {traceback.format_exc()[:300]}")
        loop_results["visual_language"] = {"error": str(e)}

    try:
        loop_results["acoustic_space"] = loop_acoustic_space(manifests, analytics, directives)
    except Exception as e:
        log(f"Loop 5 error: {traceback.format_exc()[:300]}")
        loop_results["acoustic_space"] = {"error": str(e)}

    try:
        loop_results["caption_ab"] = loop_caption_ab(manifests, analytics)
    except Exception as e:
        log(f"Loop 6 error: {traceback.format_exc()[:300]}")
        loop_results["caption_ab"] = {"error": str(e)}

    try:
        loop_results["prompt_genome"] = loop_prompt_genome(manifests, analytics)
    except Exception as e:
        log(f"Loop 7 error: {traceback.format_exc()[:300]}")
        loop_results["prompt_genome"] = {"error": str(e)}

    try:
        loop_results["trust_signal"] = loop_trust_signal_scorer(manifests, analytics)
    except Exception as e:
        log(f"Loop 8 error: {traceback.format_exc()[:300]}")
        loop_results["trust_signal"] = {"error": str(e)}

    # Loop B nightly: pattern analysis of chain evaluator findings → structural directives for Agent 2
    try:
        loop_results["loop_b_patterns"] = loop_b_pattern_analysis(directives)
    except Exception as e:
        log(f"Loop B nightly error: {traceback.format_exc()[:300]}")
        loop_results["loop_b_patterns"] = {"error": str(e)}

    # Determine what changed
    directives_new = json.dumps(directives, sort_keys=True)
    changed_keys = []
    if directives_original != directives_new:
        orig = json.loads(directives_original)
        for k in directives:
            if directives.get(k) != orig.get(k):
                changed_keys.append(k)

    # Write directives
    if not args.dry_run:
        save_directives(directives, changed_keys)
    else:
        log("DRY RUN — directives not written")

    # Assemble delta report
    ff = loop_results.get("first_frame", {})
    sg = loop_results.get("silence_geometry", {})
    ab = loop_results.get("sound_ab", {})
    vl = loop_results.get("visual_language", {})
    ac = loop_results.get("acoustic_space", {})

    # Calculate learning velocity (gap closing rate)
    prev_visual_learnings = LOGS_DIR / "visual_learnings.json"
    prev_gap = 0
    if prev_visual_learnings.exists():
        try:
            prev = json.loads(prev_visual_learnings.read_text())
            prev_gap = prev.get("gap_score", 0)
        except Exception:
            pass
    current_gap = ff.get("gap_score", 0)
    velocity = round(prev_gap - current_gap, 1) if prev_gap else 0

    ts_result = loop_results.get("trust_signal", {})

    delta_report = {
        "date": DATE_STR,
        "visual_frame_gap": {
            "score": ff.get("gap_score", "n/a"),
            "top_3_patterns_to_adopt": ff.get("top_patterns", []),
            "viral_avg": ff.get("viral_avg_score", "n/a"),
            "harbinger_avg": ff.get("harbinger_avg_score", "n/a"),
        },
        "silence_winner": sg.get("winners", {}),
        "sound_ab_current_test": {
            "variable": ab.get("current_stage", "sub_bass_db"),
            "stage_winner_today": ab.get("stage_winner"),
            "locked_variables": ab.get("locked_variables", {}),
        },
        "captured_score_today": vl.get("mean_captured_score", "n/a"),
        "acoustic_winner": ac.get("winner_profile", "n/a"),
        "trust_signal_summary": {
            "avg_trust_score": ts_result.get("avg_trust_score", "n/a"),
            "briefs_scored": ts_result.get("briefs_scored", 0),
            "flagged_auto_retry": ts_result.get("flagged_auto_retry", []),
            "flagged_review": ts_result.get("flagged_review", []),
            "retired_this_run": ts_result.get("retired_this_run", []),
        },
        "loop_b_pattern_summary": {
            "avg_chain_score": loop_results.get("loop_b_patterns", {}).get("avg_chain_score", "n/a"),
            "rewrite_rate": loop_results.get("loop_b_patterns", {}).get("rewrite_rate", "n/a"),
            "worst_links": loop_results.get("loop_b_patterns", {}).get("worst_links", {}),
            "structural_improvements_added": loop_results.get("loop_b_patterns", {}).get("structural_improvements_added", 0),
        },
        "overall_learning_velocity": velocity,
        "directives_updated": changed_keys,
        "loop_results": loop_results,
    }

    report_path = LOGS_DIR / f"delta_report_{DATE_STR}.json"
    report_path.write_text(json.dumps(delta_report, indent=2))
    log(f"Delta report saved: {report_path.name}")

    # Telegram summary
    ff_gap = ff.get("gap_score", "n/a")
    cap_score = vl.get("mean_captured_score", "n/a")
    ts8 = loop_results.get("trust_signal", {})
    tg = (
        f"*Harbinger Quality Mirror — {DATE_STR}*\n\n"
        f"*Loop 1 — First Frame:* gap={ff_gap} (viral vs Harbinger)\n"
        f"*Loop 2 — Silence:* {len(sg.get('winners', {}))} sections optimised\n"
        f"*Loop 3 — Sound A/B:* testing `{ab.get('current_stage', '?')}`\n"
        f"*Loop 4 — Captured Score:* {cap_score}/100\n"
        f"*Loop 5 — Acoustic:* winner=`{ac.get('winner_profile', '?')}`\n"
        f"*Loop 6 — Caption AB:* {loop_results.get('caption_ab', {}).get('status', 'n/a')}\n"
        f"*Loop 7 — Prompt Genome:* gen={loop_results.get('prompt_genome', {}).get('genome_generation', '?')} | "
        f"retired={loop_results.get('prompt_genome', {}).get('components_retired', 0)} | "
        f"mutated={loop_results.get('prompt_genome', {}).get('mutations_added', 0)}\n"
        f"*Loop 8 — Trust Signal:* avg={ts8.get('avg_trust_score', 'n/a')} | "
        f"retry={len(ts8.get('flagged_auto_retry', []))} | "
        f"review={len(ts8.get('flagged_review', []))} | "
        f"retired={len(ts8.get('retired_this_run', []))}\n"
        f"*Loop B — Chain Patterns:* avg={loop_results.get('loop_b_patterns', {}).get('avg_chain_score', 'n/a')} | "
        f"rewrite_rate={loop_results.get('loop_b_patterns', {}).get('rewrite_rate', 'n/a')} | "
        f"fixes={loop_results.get('loop_b_patterns', {}).get('structural_improvements_added', 0)}\n\n"
        f"*Directives updated:* {', '.join(changed_keys) if changed_keys else 'none'}\n"
        f"*Learning velocity:* {velocity:+.1f} gap points"
    )
    send_telegram(tg)

    log("=" * 60)
    log(f"Quality Mirror complete. Loops run: {len(loop_results)}/9. Directives changed: {changed_keys}")
    log("=" * 60)

    print(json.dumps(delta_report, indent=2))


if __name__ == "__main__":
    main()
