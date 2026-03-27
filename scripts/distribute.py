#!/usr/bin/env python3
"""
distribute.py — Capital Engine Distribution

Uploads the rendered video to Cloudinary (CDN), then schedules it across all
active Buffer channels (YouTube Shorts, Instagram Reels, TikTok).

Output: logs/manifest_YYYYMMDD_HHMMSS_slotN.json
  {
    "date", "slot", "scheduled_at", "niche", "hook",
    "video_file", "video_url",
    "captions": { "tiktok": "...", "youtube": "...", "instagram": "..." },
    "api_results": {
      "youtube":   { "status": "posted"|"failed", "post_id": "...", "scheduled": "..." },
      "instagram": { ... },
      "tiktok":    { ... }
    }
  }

Usage:
  python3 distribute.py --slot <1-10>
  python3 distribute.py --slot <1-10> --retry-failed
"""
from __future__ import annotations
import os, sys, json, datetime, subprocess
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent
LOGS_DIR    = BASE_DIR / "logs"
OUTPUT_DIR  = BASE_DIR / "output"
SCRIPTS_DIR = Path(__file__).parent

NOW       = datetime.datetime.utcnow()
DATE_STR  = NOW.strftime("%Y%m%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

DIST_LOG = LOGS_DIR / "distribution.log"

# FFmpeg binary — prefer ffmpeg-full (has libass)
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFMPEG_BIN = str(_FFMPEG_FULL) if _FFMPEG_FULL.exists() else "ffmpeg"

# Maximum video size (bytes) before re-encoding for Cloudinary upload.
# Cloudinary free tier: 10 MB image, 100 MB video; paid tier higher.
# Re-encode to CRF 30 to keep under 80 MB for reliable upload.
MAX_UPLOAD_BYTES = 80 * 1024 * 1024  # 80 MB

BUFFER_API_BASE = "https://api.bufferapp.com/1"

NICHE_LABELS = {
    "tech_ai":         "Tech / AI Hacks",
    "dark_psychology": "Dark Psychology",
    "micro_mystery":   "Micro-Mysteries",
}

HASHTAGS = {
    "tech_ai": (
        "#aitools #artificialintelligence #techhacks #productivity #chatgpt "
        "#futuretech #ailife #techsecrets #digitallife #fyp #foryoupage #viral #uk"
    ),
    "dark_psychology": (
        "#darkpsychology #psychology #mindcontrol #manipulation #socialengineering "
        "#bodyLanguage #mentalhealth #mindset #fyp #foryoupage #viral #uk"
    ),
    "micro_mystery": (
        "#mystery #unexplained #conspiracy #mindblown #didyouknow #facts "
        "#strangebutrue #unsolved #fyp #foryoupage #viral #uk"
    ),
}


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


def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    try:
        with open(DIST_LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_production_manifest(slot: int) -> dict:
    """Load the most recent production manifest for this slot."""
    manifests = sorted(
        LOGS_DIR.glob(f"production_manifest_*_slot{slot}.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not manifests:
        raise FileNotFoundError(f"No production manifest found for slot {slot}")
    data = json.loads(manifests[0].read_text())
    log(f"Loaded production manifest: {manifests[0].name}")
    return data


def compress_video(video_path: Path, slot: int) -> Path:
    """Re-encode video at CRF 30 to reduce file size for Cloudinary upload."""
    out_path = OUTPUT_DIR / f"upload_{TIMESTAMP}_slot{slot}.mp4"
    cmd = [
        FFMPEG_BIN, "-y", "-i", str(video_path),
        "-vcodec", "libx264", "-crf", "30", "-preset", "fast",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-acodec", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    log(f"Compressing video for upload: {video_path.name} → {out_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 100_000:
        log(f"Compressed: {out_path.stat().st_size / 1024 / 1024:.1f} MB")
        return out_path
    log(f"Compression failed ({result.stderr[-200:]}), using original")
    return video_path


def upload_to_cloudinary(video_path: Path, slot: int) -> str | None:
    """Upload video to Cloudinary. Returns secure_url or None on failure."""
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "")
    api_key    = os.environ.get("CLOUDINARY_API_KEY", "")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET", "")

    if not all([cloud_name, api_key, api_secret]):
        log("Cloudinary credentials not set — skipping CDN upload")
        return None

    file_size = video_path.stat().st_size
    log(f"Uploading: {video_path.name} ({file_size / 1024 / 1024:.1f} MB)")

    # Compress if over threshold
    if file_size > MAX_UPLOAD_BYTES:
        video_path = compress_video(video_path, slot)
        file_size  = video_path.stat().st_size
        log(f"After compression: {file_size / 1024 / 1024:.1f} MB")

    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(
            cloud_name=cloud_name,
            api_key=api_key,
            api_secret=api_secret,
        )
        folder    = f"capital-engine/{NOW.strftime('%Y%m%d')}"
        public_id = video_path.stem
        log(f"Using Cloudinary")
        log(f"Uploading to Cloudinary: {folder}/{public_id[:30]}")
        result = cloudinary.uploader.upload(
            str(video_path),
            resource_type="video",
            folder=folder,
            public_id=public_id,
            overwrite=True,
            chunk_size=6 * 1024 * 1024,  # 6 MB chunks for large files
        )
        url = result.get("secure_url", "")
        if url:
            log(f"Cloudinary upload OK: {url[:80]}")
            return url
        log("Cloudinary: no secure_url in response")
        return None
    except Exception as e:
        log(f"Video upload failed: {e}")
        return None


def _buffer_get(token: str, endpoint: str) -> dict:
    """GET request to Buffer v1 REST API."""
    import requests
    r = requests.get(
        f"{BUFFER_API_BASE}/{endpoint}",
        params={"access_token": token},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def _buffer_post(token: str, endpoint: str, data: dict) -> dict:
    """POST request to Buffer v1 REST API."""
    import requests
    payload = dict(data)
    payload["access_token"] = token
    r = requests.post(
        f"{BUFFER_API_BASE}/{endpoint}",
        data=payload,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_buffer_channels(token: str) -> list[dict]:
    """Return list of active Buffer profiles via v1 REST API."""
    profiles = _buffer_get(token, "profiles.json")
    if not isinstance(profiles, list):
        raise ValueError(f"Unexpected profiles response: {type(profiles)}")
    active = [p for p in profiles if not p.get("paused")]
    labels = [
        f"{p.get('service','?')}:{p.get('formatted_username', p.get('service_username','?'))}"
        for p in active
    ]
    log(f"Buffer channels: {len(active)} active of {len(profiles)} total")
    log(f"Active channels: {labels}")
    return active


def platform_label(channel: dict) -> str:
    """Map a Buffer profile to one of: youtube, instagram, tiktok."""
    svc  = (channel.get("service") or "").lower()
    name = (channel.get("formatted_username") or channel.get("service_username") or "").lower()
    # Buffer reports YouTube as 'google' on some account types
    if svc in ("youtube", "google") or "youtube" in name:
        return "youtube"
    for key in ("instagram", "tiktok"):
        if key in svc or key in name:
            return key
    return svc or "unknown"


def build_caption(hook: str, niche: str, platform: str) -> str:
    """Build platform-appropriate caption text."""
    tags = HASHTAGS.get(niche, HASHTAGS["tech_ai"])
    if platform == "youtube":
        return f"{hook}\n\nSubscribe for daily insights.\n\n{tags}"
    elif platform == "instagram":
        return f"{hook}\n\nSave this. Tag someone who needs it.\n\n{tags}"
    else:  # tiktok
        return f"{hook}\n\nSave this before it gets taken down.\n\n{tags}"


def create_buffer_post(
    token: str,
    profile_id: str,
    video_url: str,
    caption: str,
    scheduled_at: str,
) -> dict:
    """
    Create a scheduled Buffer post via v1 REST API.
    Uses profile_id (not channel_id) and ISO 8601 scheduled_at.
    Returns {"status": "posted"|"failed", "post_id": "...", "scheduled": "..."}.
    """
    # Convert ISO 8601 to Unix timestamp for v1 API
    try:
        dt = datetime.datetime.strptime(scheduled_at, "%Y-%m-%dT%H:%M:%SZ")
        scheduled_ts = int(dt.timestamp())
    except Exception:
        scheduled_ts = int(NOW.timestamp()) + 86400

    payload = {
        "profile_ids[]": profile_id,
        "text":          caption,
        "shorten":       "0",
        "scheduled_at":  str(scheduled_ts),
        "media[video]":  video_url,
        "media[link]":   video_url,  # fallback for platforms that prefer link
    }

    try:
        resp = _buffer_post(token, "updates/create.json", payload)
        # v1 response: {"success": true, "updates": [{"id": "...", ...}]}
        if resp.get("success") is False:
            return {"status": "failed", "error": resp.get("message", "Buffer rejected post")}
        updates = resp.get("updates", [])
        post_id = updates[0].get("id", "") if updates else ""
        if not post_id:
            return {"status": "failed", "error": f"No post ID in response: {resp}"}
        return {
            "status":    "posted",
            "post_id":   post_id,
            "scheduled": scheduled_at,
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


def schedule_time(slot: int) -> str:
    """
    Calculate scheduled publish time: push to next day at the slot's UTC hour.
    Slots map to: 1→06:00, 2→08:30, 3→11:00, 4→13:30, 5→16:00, 6→18:30, 7→21:00 UTC.
    """
    slot_hours = {1: 6, 2: 8, 3: 11, 4: 13, 5: 16, 6: 18, 7: 21}
    slot_mins  = {1: 0, 2: 30, 3: 0, 4: 30, 5: 0, 6: 30, 7: 0}
    h = slot_hours.get(slot, NOW.hour)
    m = slot_mins.get(slot, 0)
    publish = (NOW + datetime.timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
    return publish.strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",         type=int, required=True, choices=list(range(1, 11)))
    parser.add_argument("--retry-failed", action="store_true",
                        help="Re-attempt only platforms that failed in the existing manifest")
    args = parser.parse_args()
    slot = args.slot

    load_env()

    log("=" * 60)
    log(f"CAPITAL ENGINE — DISTRIBUTION [LIVE PUBLISH] (SLOT {slot})")
    log("=" * 60)

    # Load production manifest
    try:
        manifest = load_production_manifest(slot)
    except FileNotFoundError as e:
        log(f"ABORT: {e}")
        sys.exit(1)

    video_file = manifest.get("video") or manifest.get("video_file") or ""
    if not video_file or not Path(video_file).exists():
        # Try to find the video in output/
        candidates = sorted(
            OUTPUT_DIR.glob(f"post_*_slot{slot}.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            log("ABORT: No video file found")
            sys.exit(1)
        video_file = str(candidates[0])

    video_path = Path(video_file)
    log(f"Found video:     {video_path.name}")

    script     = manifest.get("script", {})
    hook_text  = (script.get("hook") or manifest.get("caption_text") or "")[:100]
    niche      = manifest.get("niche", "tech_ai")
    affiliate  = manifest.get("affiliate", "")
    if isinstance(affiliate, dict):
        aff_name = affiliate.get("name", "")
        aff_url  = affiliate.get("url", "")
    else:
        aff_name = str(affiliate) if affiliate and affiliate != "none_growth_mode" else ""
        aff_url  = ""

    log(f"Hook:            {hook_text[:80]}")
    log(f"Affiliate:        {aff_name} | {aff_url}")

    scheduled_at = schedule_time(slot)
    log(f"Scheduled:   {scheduled_at}")

    # ── Load existing dist manifest if --retry-failed ─────────────────────
    existing_results: dict = {}
    existing_manifest_path: Path | None = None
    if args.retry_failed:
        existing = sorted(
            LOGS_DIR.glob(f"manifest_{DATE_STR}*_slot{slot}.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if existing:
            try:
                existing_data       = json.loads(existing[0].read_text())
                existing_results    = existing_data.get("api_results", {})
                existing_manifest_path = existing[0]
                log(f"Retrying failed platforms from: {existing[0].name}")
            except Exception:
                pass

    # ── Upload video ──────────────────────────────────────────────────────
    video_url = upload_to_cloudinary(video_path, slot)
    if not video_url:
        log("ERROR: Video upload failed — cannot distribute without CDN URL")
        sys.exit(1)

    # ── Buffer distribution ───────────────────────────────────────────────
    buffer_token = os.environ.get("BUFFER_API_TOKEN", "")
    if not buffer_token:
        log("ERROR: BUFFER_API_TOKEN not set")
        sys.exit(1)

    try:
        channels = get_buffer_channels(buffer_token)
    except Exception as e:
        log(f"ERROR: Could not fetch Buffer channels: {e}")
        sys.exit(1)

    api_results: dict = dict(existing_results)

    captions: dict = {}
    for channel in channels:
        platform = platform_label(channel)
        if platform not in ("youtube", "instagram", "tiktok"):
            continue

        # Skip successfully posted platforms on --retry-failed
        if args.retry_failed and (existing_results.get(platform) or {}).get("status") == "posted":
            log(f"  {platform.capitalize()}: already posted — skipping")
            continue

        caption = build_caption(hook_text, niche, platform)
        captions[platform] = caption

        log(f"  Posting to {platform.capitalize()}...")
        result = create_buffer_post(
            buffer_token,
            channel["id"],
            video_url,
            caption,
            scheduled_at,
        )

        if result["status"] == "posted":
            post_id = result.get("post_id", "")
            log(f"  {platform.capitalize()}: SUCCESS — post ID {post_id}")
        else:
            err = result.get("error", "unknown error")
            log(f"  {platform.capitalize()}: FAILED — {err}")

        api_results[platform] = result

    # ── Write distribution manifest ───────────────────────────────────────
    dist_manifest = {
        "date":         NOW.strftime("%Y-%m-%d"),
        "slot":         slot,
        "scheduled_at": scheduled_at,
        "niche":        niche,
        "hook":         hook_text,
        "affiliate_name": aff_name,
        "affiliate_url":  aff_url,
        "video_file":   str(video_path),
        "video_url":    video_url,
        "captions":     captions,
        "api_results":  api_results,
    }

    if existing_manifest_path and args.retry_failed:
        out_path = existing_manifest_path
    else:
        out_path = LOGS_DIR / f"manifest_{TIMESTAMP}_slot{slot}.json"

    out_path.write_text(json.dumps(dist_manifest, indent=2))
    log(f"Distribution manifest: {out_path.name}")

    posted   = [p for p, r in api_results.items() if r.get("status") == "posted"]
    failed   = [p for p, r in api_results.items() if r.get("status") == "failed"]
    log(f"Result: {len(posted)} posted ({', '.join(posted)})"
        + (f", {len(failed)} failed ({', '.join(failed)})" if failed else ""))

    print(json.dumps(dist_manifest, indent=2))
    sys.exit(0 if not failed else 2)


if __name__ == "__main__":
    main()
