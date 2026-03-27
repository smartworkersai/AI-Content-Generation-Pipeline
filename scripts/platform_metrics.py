#!/usr/bin/env python3
"""
platform_metrics.py — Capital Engine platform analytics scraper

Scrapes real engagement data from YouTube, Instagram, and TikTok and writes
to logs/platform_metrics.json for consumption by Quality Mirror loops.

Platforms:
  YouTube   — YouTube Data API v3  (YOUTUBE_API_KEY in .env)
  Instagram — Playwright + API intercept on public @harbingerhq profile
  TikTok    — Playwright; falls back gracefully if blocked

Matching: videos are matched to production manifests by hook text + post date.

Run standalone:  python3 scripts/platform_metrics.py
Called by:       harbinger_core.py after each distribution cycle
                 crontab at 22:15 UTC before nightly quality mirror

Output: logs/platform_metrics.json
"""

from __future__ import annotations
import os, sys, json, re, time, datetime, traceback
from pathlib import Path
from typing import Optional

BASE_DIR   = Path(__file__).parent.parent
LOGS_DIR   = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
METRICS_FILE = LOGS_DIR / "platform_metrics.json"

NOW      = datetime.datetime.utcnow()
DATE_STR = NOW.strftime("%Y-%m-%d")

# Hardcoded channel constants (discovered 2026-03-17)
YT_CHANNEL_ID       = "UCNMkvhsXKGPnHMa4AJtJXyw"
YT_UPLOADS_PLAYLIST = "UUNMkvhsXKGPnHMa4AJtJXyw"
IG_HANDLE           = "harbingerhq"
TT_HANDLE           = "harbingerhq"
TT_SEC_UID          = "MS4wLjABAAAAlw8Os0DvPIzWC3K_vFn-6axM28MM8LjD8Yim6IfXoOVvIV_bFx-GqRpE0k_d4mDE"


def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()


def log(msg: str):
    print(f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}")


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------

def load_distribution_manifests(days: int = 30) -> list[dict]:
    """Load recent distribution manifests (slot-level, from distribute.py output)."""
    cutoff = NOW - datetime.timedelta(days=days)
    manifests = []
    for path in sorted(LOGS_DIR.glob("manifest_*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(path.read_text())
            sched = d.get("scheduled_at", "")
            if sched:
                dt = datetime.datetime.strptime(sched[:19], "%Y-%m-%dT%H:%M:%S")
                if dt < cutoff:
                    continue
            d["_manifest_id"] = path.stem
            manifests.append(d)
        except Exception:
            pass
    return manifests


# ---------------------------------------------------------------------------
# YouTube Data API v3
# ---------------------------------------------------------------------------

def scrape_youtube(manifests: list[dict]) -> dict[str, dict]:
    """
    Fetch stats for all @harbingerhq YouTube videos published in the last 30 days.
    Returns dict keyed by manifest_id -> youtube metrics dict.
    """
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not yt_key:
        log("YouTube: SKIP — YOUTUBE_API_KEY not set")
        return {}

    log("YouTube: fetching channel uploads...")
    try:
        from googleapiclient.discovery import build
    except ImportError:
        log("YouTube: SKIP — google-api-python-client not installed")
        return {}

    try:
        yt = build("youtube", "v3", developerKey=yt_key)

        # Fetch up to 50 recent uploads
        playlist_resp = yt.playlistItems().list(
            part="snippet,contentDetails",
            playlistId=YT_UPLOADS_PLAYLIST,
            maxResults=50,
        ).execute()

        videos = []
        for item in playlist_resp.get("items", []):
            vid_id    = item["contentDetails"]["videoId"]
            title     = item["snippet"]["title"]
            published = item["snippet"]["publishedAt"]  # ISO8601
            videos.append({"id": vid_id, "title": title, "published": published})

        if not videos:
            log("YouTube: no videos found in uploads playlist")
            return {}

        # Batch-fetch stats (50 at a time)
        stats_map = {}
        for i in range(0, len(videos), 50):
            batch_ids = ",".join(v["id"] for v in videos[i:i+50])
            stats_resp = yt.videos().list(
                part="statistics,contentDetails",
                id=batch_ids,
            ).execute()
            for v_item in stats_resp.get("items", []):
                s = v_item.get("statistics", {})
                d = v_item.get("contentDetails", {})
                stats_map[v_item["id"]] = {
                    "views":    int(s.get("viewCount",    0)),
                    "likes":    int(s.get("likeCount",    0)),
                    "comments": int(s.get("commentCount", 0)),
                    "duration": d.get("duration", ""),
                }
            time.sleep(0.1)

        log(f"YouTube: fetched stats for {len(stats_map)} videos")

        # Match videos to manifests
        results: dict[str, dict] = {}
        for manifest in manifests:
            hook      = manifest.get("hook", "")
            sched_str = manifest.get("scheduled_at", "")
            mid       = manifest.get("_manifest_id", "")

            if not hook or not sched_str:
                continue

            hook_prefix = hook[:40].lower()
            sched_dt    = datetime.datetime.strptime(sched_str[:19], "%Y-%m-%dT%H:%M:%S")

            best_match = None
            for v in videos:
                # Title must contain first 40 chars of the hook (case-insensitive)
                if hook_prefix not in v["title"].lower():
                    continue
                # Published within ±2 days of scheduled_at
                pub_dt = datetime.datetime.strptime(v["published"][:19], "%Y-%m-%dT%H:%M:%S")
                if abs((pub_dt - sched_dt).total_seconds()) > 172800:  # 48h
                    continue
                # Prefer most recently published if multiple match
                if best_match is None or pub_dt > datetime.datetime.strptime(
                        best_match["published"][:19], "%Y-%m-%dT%H:%M:%S"):
                    best_match = v

            if best_match and best_match["id"] in stats_map:
                s = stats_map[best_match["id"]]
                results[mid] = {
                    "video_id": best_match["id"],
                    "views":    s["views"],
                    "likes":    s["likes"],
                    "comments": s["comments"],
                    "duration": s["duration"],
                    "url":      f"https://youtube.com/watch?v={best_match['id']}",
                }

        log(f"YouTube: matched {len(results)}/{len(manifests)} manifests to videos")
        return results

    except Exception as e:
        log(f"YouTube scrape failed: {traceback.format_exc()[:300]}")
        return {}


# ---------------------------------------------------------------------------
# Instagram (Playwright + API intercept)
# ---------------------------------------------------------------------------

def scrape_instagram(manifests: list[dict]) -> dict[str, dict]:
    """
    Scrape public @harbingerhq Instagram profile via Playwright.
    Intercepts the web_profile_info API call which returns post-level metrics.
    Returns dict keyed by manifest_id -> instagram metrics dict.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("Instagram: SKIP — playwright not installed")
        return {}

    log("Instagram: scraping public profile...")
    ig_posts = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()

            def handle_response(response):
                if "web_profile_info" in response.url:
                    try:
                        data = response.json()
                        user = data.get("data", {}).get("user", {})
                        edges = (
                            user.get("edge_owner_to_timeline_media", {}).get("edges", [])
                            or user.get("edge_felix_video_timeline", {}).get("edges", [])
                        )
                        for e in edges:
                            node = e.get("node", {})
                            if not node.get("is_video"):
                                continue
                            ig_posts.append({
                                "shortcode":   node.get("shortcode", ""),
                                "plays":       node.get("video_view_count") or 0,
                                "likes":       node.get("edge_liked_by", {}).get("count", 0),
                                "comments":    node.get("edge_media_to_comment", {}).get("count", 0),
                                "taken_at":    node.get("taken_at_timestamp", 0),
                                "caption":     (
                                    node.get("edge_media_to_caption", {})
                                        .get("edges", [{}])[0]
                                        .get("node", {})
                                        .get("text", "")
                                ),
                            })
                    except Exception:
                        pass

            page.on("response", handle_response)
            page.goto(f"https://www.instagram.com/{IG_HANDLE}/", wait_until="networkidle", timeout=25000)
            page.wait_for_timeout(3000)
            browser.close()

    except Exception as e:
        log(f"Instagram scrape failed: {traceback.format_exc()[:300]}")
        return {}

    log(f"Instagram: found {len(ig_posts)} video posts")

    # Match to manifests by timestamp proximity to scheduled_at
    results: dict[str, dict] = {}
    for manifest in manifests:
        sched_str = manifest.get("scheduled_at", "")
        hook      = manifest.get("hook", "")
        mid       = manifest.get("_manifest_id", "")

        if not sched_str:
            continue

        sched_dt = datetime.datetime.strptime(sched_str[:19], "%Y-%m-%dT%H:%M:%S")

        best       = None
        best_delta = float("inf")

        for post in ig_posts:
            if not post["taken_at"]:
                continue
            post_dt = datetime.datetime.utcfromtimestamp(post["taken_at"])
            delta   = abs((post_dt - sched_dt).total_seconds())
            if delta < 21600 and delta < best_delta:  # within 6 hours
                best       = post
                best_delta = delta

        if best:
            results[mid] = {
                "shortcode": best["shortcode"],
                "plays":     best["plays"],
                "likes":     best["likes"],
                "comments":  best["comments"],
                "url":       f"https://www.instagram.com/p/{best['shortcode']}/",
            }

    log(f"Instagram: matched {len(results)}/{len(manifests)} manifests")
    return results


# ---------------------------------------------------------------------------
# TikTok (Playwright; blocked by anti-bot on new accounts — best effort)
# ---------------------------------------------------------------------------

def scrape_tiktok(manifests: list[dict]) -> dict[str, dict]:
    """
    Attempt to scrape public @harbingerhq TikTok metrics via Playwright.
    TikTok aggressively blocks headless scrapers; this returns empty gracefully
    until posting is live and we can match via known video IDs from manifests.
    """
    # If we have TikTok post IDs stored in manifests (from successful posts),
    # we can try to fetch those specific video pages.
    tt_manifests = [
        m for m in manifests
        if m.get("api_results", {}).get("tiktok", {}).get("status") == "posted"
    ]

    if not tt_manifests:
        log("TikTok: no successful TikTok posts in manifests yet — skipping")
        return {}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("TikTok: SKIP — playwright not installed")
        return {}

    log(f"TikTok: scraping {len(tt_manifests)} known post pages...")
    results: dict[str, dict] = {}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            for manifest in tt_manifests:
                mid     = manifest.get("_manifest_id", "")
                tt_info = manifest["api_results"]["tiktok"]
                # TikTok post IDs come from the TikTok Content API direct posts
                post_id = tt_info.get("post_id") or tt_info.get("video_id")
                if not post_id:
                    continue

                try:
                    page_data = {}
                    page = ctx.new_page()

                    def handle(response):
                        url = response.url
                        if f"/@{TT_HANDLE}/video/{post_id}" in url or "item/detail" in url:
                            try:
                                page_data.update(response.json())
                            except Exception:
                                pass

                    page.on("response", handle)
                    page.goto(f"https://www.tiktok.com/@{TT_HANDLE}/video/{post_id}",
                              timeout=20000)
                    page.wait_for_timeout(5000)

                    content = page.content()
                    # Parse SIGI_STATE or UNIVERSAL_DATA
                    sigi = re.search(r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
                                     content, re.DOTALL)
                    if sigi:
                        try:
                            d = json.loads(sigi.group(1))
                            item = d.get("ItemModule", {}).get(str(post_id), {})
                            if item:
                                s = item.get("stats", {})
                                results[mid] = {
                                    "video_id": post_id,
                                    "views":    s.get("playCount",   0),
                                    "likes":    s.get("diggCount",   0),
                                    "comments": s.get("commentCount", 0),
                                    "shares":   s.get("shareCount",  0),
                                    "url":      f"https://www.tiktok.com/@{TT_HANDLE}/video/{post_id}",
                                }
                        except Exception:
                            pass

                    page.close()
                    time.sleep(1)
                except Exception:
                    pass

            browser.close()

    except Exception as e:
        log(f"TikTok scrape failed: {traceback.format_exc()[:200]}")

    log(f"TikTok: matched {len(results)}/{len(tt_manifests)} posts")
    return results


# ---------------------------------------------------------------------------
# Merge and write
# ---------------------------------------------------------------------------

def build_metrics(
    manifests: list[dict],
    yt_data:   dict[str, dict],
    ig_data:   dict[str, dict],
    tt_data:   dict[str, dict],
) -> dict:
    """Merge per-platform data into unified platform_metrics.json structure."""

    # Load existing to preserve historical records
    existing_by_mid: dict[str, dict] = {}
    if METRICS_FILE.exists():
        try:
            old = json.loads(METRICS_FILE.read_text())
            for p in old.get("posts", []):
                existing_by_mid[p["manifest_id"]] = p
        except Exception:
            pass

    posts = []
    for manifest in manifests:
        mid      = manifest.get("_manifest_id", "")
        yt       = yt_data.get(mid, {})
        ig       = ig_data.get(mid, {})
        tt       = tt_data.get(mid, {})

        # Pull prior data for this manifest so we don't lose it on re-runs
        prior = existing_by_mid.get(mid, {})
        yt    = yt or prior.get("youtube", {})
        ig    = ig or prior.get("instagram", {})
        tt    = tt or prior.get("tiktok", {})

        yt_views = yt.get("views",  0) if yt else 0
        ig_views = ig.get("plays",  0) if ig else 0
        tt_views = tt.get("views",  0) if tt else 0
        total    = yt_views + ig_views + tt_views

        # post_id used by quality_mirror — prefer YouTube video ID
        post_id  = yt.get("video_id") if yt else None

        posts.append({
            "manifest_id":  mid,
            "date":         manifest.get("scheduled_at", "")[:10],
            "slot":         manifest.get("slot"),
            "hook":         manifest.get("hook", ""),
            "niche":        manifest.get("niche", ""),
            "scheduled_at": manifest.get("scheduled_at", ""),
            "youtube":      yt or None,
            "instagram":    ig or None,
            "tiktok":       tt or None,
            # Quality Mirror compatibility fields
            "post_id":         post_id,
            "views":           total,
            "yt_views":        yt_views,
            "ig_views":        ig_views,
            "tt_views":        tt_views,
            "likes":           (yt.get("likes", 0) if yt else 0) + (ig.get("likes", 0) if ig else 0) + (tt.get("likes", 0) if tt else 0),
            "comments":        (yt.get("comments", 0) if yt else 0) + (ig.get("comments", 0) if ig else 0) + (tt.get("comments", 0) if tt else 0),
            "shares":          tt.get("shares", 0) if tt else 0,
            "completion_rate": None,   # not available from public APIs
            "watch_time_pct":  None,   # not available from public APIs
        })

    # Sort by scheduled_at descending
    posts.sort(key=lambda p: p.get("scheduled_at", ""), reverse=True)

    return {
        "last_updated":     NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "yt_channel_id":    YT_CHANNEL_ID,
        "ig_handle":        IG_HANDLE,
        "tt_handle":        TT_HANDLE,
        "posts":            posts,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run():
    load_env()
    log("=" * 60)
    log("PLATFORM METRICS SCRAPER")
    log("=" * 60)

    manifests = load_distribution_manifests(days=30)
    log(f"Manifests loaded: {len(manifests)}")

    if not manifests:
        log("No manifests found — nothing to scrape")
        return

    yt_data = scrape_youtube(manifests)
    ig_data = scrape_instagram(manifests)
    tt_data = scrape_tiktok(manifests)

    metrics = build_metrics(manifests, yt_data, ig_data, tt_data)
    METRICS_FILE.write_text(json.dumps(metrics, indent=2))
    log(f"platform_metrics.json written — {len(metrics['posts'])} posts")

    # Summary
    yt_matched = sum(1 for p in metrics["posts"] if p.get("youtube"))
    ig_matched = sum(1 for p in metrics["posts"] if p.get("instagram"))
    tt_matched = sum(1 for p in metrics["posts"] if p.get("tiktok"))
    total_views = sum(p.get("views", 0) for p in metrics["posts"])
    log(f"YouTube matched: {yt_matched}  |  Instagram: {ig_matched}  |  TikTok: {tt_matched}")
    log(f"Total views across all posts: {total_views:,}")
    log("=" * 60)


if __name__ == "__main__":
    run()
