#!/usr/bin/env python3
"""
footage_sourcer.py — Niche-aware footage and BGM sourcer for Harbinger Capital Engine.

Downloads fresh B-roll video AND background music for each niche on every run.
Falls back to local cache if yt-dlp fails.

Niche video/audio query sets:
  tech_ai        — kinetic/satisfying visuals + upbeat lofi/synthwave BGM
  dark_psychology — night city / rain aesthetic + phonk/sigma BGM
  micro_mystery  — deep ocean / space + 432hz ambient / creepy BGM

NOTE: "GTA 5 stunt jumps" was requested but excluded — Rockstar's Content Usage
Policy prohibits commercial use of GTA footage. Replaced with real-world
motorcycle POV footage which achieves the same high-energy, stunt visual effect.

Usage:
  python3 footage_sourcer.py --niche tech_ai --output output/
  python3 footage_sourcer.py --niche dark_psychology --output output/
  python3 footage_sourcer.py --brief logs/creative_brief_..._slot1.json --output output/
"""
from __future__ import annotations
import os, sys, json, datetime, subprocess, tempfile, shutil, re, time, random
from pathlib import Path

BASE_DIR        = Path(__file__).parent.parent.parent
OUTPUT_DIR      = BASE_DIR / "output"
LOGS_DIR        = BASE_DIR / "logs"
BROLL_CACHE_DIR = BASE_DIR / "assets" / "broll_cache"
BGM_CACHE_DIR   = BASE_DIR / "assets" / "bgm_cache"
USED_BROLL_LOG       = BASE_DIR / "assets" / "used_broll_log.json"
NICHE_OVERRIDES_FILE = BASE_DIR / "logs" / "niche_overrides.json"


def _load_video_query_overrides() -> dict[str, list[str]]:
    """Load trend_scraper override queries, merged with hardcoded defaults."""
    if NICHE_OVERRIDES_FILE.exists():
        try:
            overrides = json.loads(NICHE_OVERRIDES_FILE.read_text())
            return overrides.get("video_queries", {})
        except Exception:
            pass
    return {}
OUTPUT_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
BROLL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
for _d in ("tech", "psychology", "mystery"):
    (BGM_CACHE_DIR / _d).mkdir(parents=True, exist_ok=True)

NOW       = datetime.datetime.utcnow()
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

VALID_NICHES = ["tech_ai", "dark_psychology", "micro_mystery"]

# ---------------------------------------------------------------------------
# Per-niche video search queries
# GTA 5 excluded — Rockstar Content Usage Policy prohibits commercial use.
# Replaced with "POV motorcycle canyon stunt reel" (real-world, same energy).
# ---------------------------------------------------------------------------
NICHE_VIDEO_QUERIES = {
    "tech_ai": [
        "satisfying kinetic sand cutting ASMR 4k",
        "POV motorcycle canyon stunt reel no commentary",   # replaces GTA 5 (Rockstar IP)
    ],
    "dark_psychology": [
        "night city drive dashcam 4k",
        "rain on window dark aesthetic 4k",
    ],
    "micro_mystery": [
        "deep ocean footage 4k",
        "abstract space loop background 4k",
    ],
}

# ---------------------------------------------------------------------------
# Per-niche BGM (audio) search queries
# ---------------------------------------------------------------------------
NICHE_AUDIO_QUERIES = {
    "tech_ai": [
        "upbeat lofi background music no copyright",
        "fast synthwave instrumental royalty free",
    ],
    "dark_psychology": [
        "slowed and reverb phonk instrumental no copyright",
        "dark sigma male grindset background music",
    ],
    "micro_mystery": [
        "432hz dark ambient drone no copyright",
        "creepy unsolved mystery background music",
    ],
}

# Fallback B-roll queries when niche sourcing fails
BROLL_FALLBACK_QUERIES = [
    "satisfying marble run loop 4k",
    "city highway night drive dashcam 4k",
    "luxury supercar night drive POV 4k",
]

CLIP_MIN_OFFSET_S = 5
CLIP_MAX_OFFSET_S = 40
CLIP_DURATION_S   = 25
BGM_DURATION_S    = 30
BGM_TARGET_VOLUME = 0.10   # normalize BGM to 10% volume


def log(msg: str):
    print(f"[footage_sourcer] {msg}")


# ---------------------------------------------------------------------------
# Asset deduplication tracker (#9)
# Prevents reuse of the same YouTube video_id across blitz runs.
# ---------------------------------------------------------------------------
def _load_used_log() -> dict:
    if USED_BROLL_LOG.exists():
        try:
            data = json.loads(USED_BROLL_LOG.read_text())
            if isinstance(data, dict):
                return data
            # Corrupted — not a dict (e.g. a list); back it up and reset
            backup = USED_BROLL_LOG.with_suffix(f".corrupt_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json")
            shutil.copy2(str(USED_BROLL_LOG), str(backup))
            log(f"WARNING: used_broll_log.json is {type(data).__name__}, not dict — backed up to {backup.name} and reset")
        except Exception as e:
            log(f"WARNING: could not parse used_broll_log.json ({e}) — treating as empty")
    return {}


def _save_used_log(log_data: dict):
    USED_BROLL_LOG.parent.mkdir(parents=True, exist_ok=True)
    USED_BROLL_LOG.write_text(json.dumps(log_data, indent=2))


def _is_used(video_id: str) -> bool:
    return bool(video_id) and video_id in _load_used_log()


def _mark_used(video_id: str, start_offset: float, duration: float):
    log_data = _load_used_log()
    log_data[video_id] = {
        "start_offset": round(start_offset, 2),
        "duration":     round(duration, 2),
        "used_at":      datetime.datetime.utcnow().isoformat(),
    }
    _save_used_log(log_data)
    log(f"Dedup: logged video_id={video_id[:8]} (total logged: {len(log_data)})")


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


def ytdlp_available() -> bool:
    return shutil.which("yt-dlp") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Local cache fallbacks
# ---------------------------------------------------------------------------
def get_cache_fallback(output_dir: Path = OUTPUT_DIR) -> Path | None:
    """Return a random .mp4 from broll_cache/, or None if empty."""
    cached = list(BROLL_CACHE_DIR.glob("*.mp4"))
    if not cached:
        log(f"broll_cache is empty — add fallback clips to {BROLL_CACHE_DIR}")
        return None
    chosen = random.choice(cached)
    dest   = output_dir / f"cache_fallback_{TIMESTAMP}_{chosen.name}"
    shutil.copy(str(chosen), str(dest))
    log(f"Using broll_cache fallback: {chosen.name}")
    return dest


def get_bgm_cache_fallback(niche: str, output_dir: Path = OUTPUT_DIR) -> Path | None:
    """Return a random .mp3 from bgm_cache/{subdir}/, or any bgm_cache .mp3, or None."""
    subdir_map = {"tech_ai": "tech", "dark_psychology": "psychology", "micro_mystery": "mystery"}
    subdir = subdir_map.get(niche, "tech")
    niche_dir = BGM_CACHE_DIR / subdir
    tracks = list(niche_dir.glob("*.mp3"))
    if not tracks:
        # Try any BGM cache dir as last resort
        tracks = list(BGM_CACHE_DIR.rglob("*.mp3"))
    if not tracks:
        log("bgm_cache is empty — add .mp3 files to assets/bgm_cache/{tech,psychology,mystery}/")
        return None
    chosen = random.choice(tracks)
    dest   = output_dir / f"bgm_fallback_{TIMESTAMP}_{chosen.name}"
    shutil.copy(str(chosen), str(dest))
    log(f"Using BGM cache fallback: {chosen.name}")
    return dest


# ---------------------------------------------------------------------------
# yt-dlp helpers
# ---------------------------------------------------------------------------
def search_videos(query: str, max_results: int = 8) -> list[dict]:
    """Search YouTube for videos matching query. Returns metadata list."""
    if not ytdlp_available():
        log("ERROR: yt-dlp not installed. Run: pip install yt-dlp")
        return []

    search_query = f"ytsearch{max_results}:{query}"
    cmd = [
        "yt-dlp", "--flat-playlist", "--dump-json",
        "--no-warnings", "--quiet",
        "--extractor-args", "youtube:player_skip=webpage,configs",
        search_query,
    ]
    log(f"Searching: '{query}' (max {max_results})")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                dur  = data.get("duration", 0) or 0
                if 15 <= dur < 900:   # minimum 15s clip, maximum 15 min (900s)
                    videos.append({
                        "url":        f"https://www.youtube.com/watch?v={data.get('id', '')}",
                        "id":         data.get("id", ""),
                        "title":      data.get("title", ""),
                        "duration":   dur,
                        "view_count": data.get("view_count", 0) or 0,
                    })
            except (json.JSONDecodeError, KeyError):
                continue
        log(f"Found {len(videos)} results")
        return videos
    except subprocess.TimeoutExpired:
        log("Search timed out")
        return []
    except Exception as e:
        log(f"Search error: {e}")
        return []


def download_video_clip(video: dict, target_duration: float,
                        output_dir: Path, start_offset: float | None = None) -> Path | None:
    """Download and trim a video clip via yt-dlp → ffmpeg."""
    if not ytdlp_available():
        return None

    if start_offset is None:
        vid_dur = int(video.get("duration", 60))
        if vid_dur <= int(target_duration) + 5:
            # Video is too short to safely offset — start from the beginning
            start_offset = 0.0
            log(f"  Short source ({vid_dur}s ≤ target {int(target_duration)}s+5) — start_offset forced to 0")
        else:
            max_off = min(CLIP_MAX_OFFSET_S, vid_dur - int(target_duration) - 5)
            start_offset = random.uniform(CLIP_MIN_OFFSET_S, max(CLIP_MIN_OFFSET_S, max_off))

    url      = video["url"]
    vid_id   = video.get("id", TIMESTAMP)
    out_path = output_dir / f"footage_{TIMESTAMP}_{vid_id[:8]}.mp4"

    log(f"Downloading video clip: {video['title'][:60]}")
    log(f"  offset={start_offset:.1f}s, duration={target_duration}s")

    MAX_FILESIZE_BYTES = 100 * 1024 * 1024  # 100 MB

    cmd = [
        "yt-dlp", "--no-warnings", "--quiet",
        "-f", "bestvideo[acodec=none][height<=1080][ext=mp4]/bestvideo[acodec=none][height<=1080]/bestvideo[acodec=none]",
        "--max-filesize", "100M",           # hard 100 MB cap per download
        "--match-filter", "duration < 900", # skip videos longer than 15 min
        "-o", str(out_path),
        "--postprocessor-args", f"ffmpeg:-an -ss {start_offset} -t {target_duration}",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode != 0:
            log(f"  Video download failed: {result.stderr[:200]}")
            return None
        if not out_path.exists() or out_path.stat().st_size < 100_000:
            raw_candidates = [c for c in output_dir.glob(f"footage_{TIMESTAMP}_{vid_id[:8]}*")
                              if c.suffix == ".mp4" and c.stat().st_size > 100_000]
            # Validate each candidate with ffprobe — reject truncated/partial files
            valid_candidates = []
            for c in raw_candidates:
                probe = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(c)],
                    capture_output=True, text=True, timeout=15,
                )
                if probe.returncode == 0 and probe.stdout.strip():
                    valid_candidates.append(c)
            if valid_candidates:
                out_path = sorted(valid_candidates, key=lambda f: f.stat().st_size, reverse=True)[0]
            else:
                log("  Downloaded file missing, too small, or ffprobe-invalid")
                return None

        # Enforce 100 MB cap — delete and signal fallback if exceeded
        if out_path.stat().st_size > MAX_FILESIZE_BYTES:
            log(f"  Clip exceeds 100 MB ({out_path.stat().st_size // (1024*1024)}MB) — deleting, will use broll cache")
            out_path.unlink(missing_ok=True)
            return None

        log(f"  Video saved: {out_path.name} ({out_path.stat().st_size // 1024}KB)")
        return out_path
    except subprocess.TimeoutExpired:
        log("  Video download timed out")
        return None
    except Exception as e:
        log(f"  Video download error: {e}")
        return None


def download_audio_clip(video: dict, target_duration: float, output_dir: Path) -> Path | None:
    """Download audio-only clip via yt-dlp, convert to mp3."""
    if not ytdlp_available():
        return None

    url      = video["url"]
    vid_id   = video.get("id", TIMESTAMP)
    out_path = output_dir / f"bgm_{TIMESTAMP}_{vid_id[:8]}.mp3"

    log(f"Downloading BGM: {video['title'][:60]}")

    cmd = [
        "yt-dlp", "--no-warnings", "--quiet",
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "-x", "--audio-format", "mp3", "--audio-quality", "192K",
        "--max-filesize", "30M",            # BGM hard cap: 30 MB
        "--match-filter", "duration < 900", # skip tracks longer than 15 min
        "-o", str(output_dir / f"bgm_{TIMESTAMP}_{vid_id[:8]}.%(ext)s"),
        "--postprocessor-args", f"ffmpeg:-t {target_duration}",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"  BGM download failed: {result.stderr[:200]}")
            return None
        if not out_path.exists() or out_path.stat().st_size < 10_000:
            candidates = [c for c in output_dir.glob(f"bgm_{TIMESTAMP}_{vid_id[:8]}*")
                          if c.suffix in (".mp3", ".m4a") and c.stat().st_size > 10_000]
            if candidates:
                out_path = candidates[0]
            else:
                log("  BGM file missing or too small")
                return None
        log(f"  BGM saved: {out_path.name} ({out_path.stat().st_size // 1024}KB)")
        return out_path
    except subprocess.TimeoutExpired:
        log("  BGM download timed out")
        return None
    except Exception as e:
        log(f"  BGM download error: {e}")
        return None


def normalize_audio_volume(audio_path: Path, target_volume: float,
                            output_dir: Path) -> Path | None:
    """
    Normalize audio to target_volume fraction (0.0-1.0) using ffmpeg.
    target_volume=0.10 → 10% volume (quiet BGM bed under voiceover).
    """
    if not ffmpeg_available():
        log("ffmpeg not found — cannot normalize audio volume")
        return audio_path  # return original, better than nothing

    out_path = output_dir / f"bgm_norm_{TIMESTAMP}_{audio_path.stem}.mp3"
    # Convert to dB reduction: 0.10 volume ≈ -20dB
    db_gain = 20.0 * (target_volume if target_volume > 0 else 0.001)
    # Use volume filter with linear value
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-filter:a", f"volume={target_volume:.3f}",
        "-b:a", "192k",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and out_path.exists():
            log(f"  BGM normalized to {int(target_volume * 100)}% volume: {out_path.name}")
            return out_path
        log(f"  Volume normalization failed: {result.stderr[-150:]}")
        return audio_path
    except Exception as e:
        log(f"  Volume normalization error: {e}")
        return audio_path


def resize_to_vertical(clip_path: Path, output_dir: Path) -> Path | None:
    """Resize/crop footage to 1080x1920 (9:16 vertical)."""
    out_path = output_dir / f"vertical_{clip_path.stem}.mp4"

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(clip_path)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        data  = json.loads(probe.stdout)
        vs    = next((s for s in data["streams"] if s.get("codec_type") == "video"), {})
        src_w = int(vs.get("width",  1920))
        src_h = int(vs.get("height", 1080))
    except Exception:
        src_w, src_h = 1920, 1080

    target_w, target_h = 1080, 1920
    src_aspect    = src_w / src_h
    target_aspect = target_w / target_h  # 0.5625

    if src_aspect > target_aspect:
        vf = f"scale=-2:{target_h},crop={target_w}:{target_h}"
    elif src_aspect < target_aspect:
        vf = (
            f"[0:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black[fg];"
            f"[0:v]scale={target_w}:{target_h},boxblur=20:20[bg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
        )
    else:
        vf = f"scale={target_w}:{target_h}"

    cmd = [
        "ffmpeg", "-y", "-i", str(clip_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"Resize failed: {result.stderr[-200:]}")
            return None
        log(f"Resized to 1080x1920: {out_path.name}")
        return out_path
    except Exception as e:
        log(f"Resize error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main sourcing functions
# ---------------------------------------------------------------------------
# One canonical seed query per niche — used by fetch_niche_bgm() to auto-populate an empty cache
NICHE_BGM_SEED_QUERIES = {
    "tech_ai":         'ytsearch1:"upbeat synthwave instrumental royalty free"',
    "dark_psychology": 'ytsearch1:"slowed phonk instrumental no copyright"',
    "micro_mystery":   'ytsearch1:"dark ambient drone no copyright"',
}


def fetch_niche_bgm(niche: str) -> Path | None:
    """
    Auto-seed bgm_cache/{subdir}/ with one track if the folder is empty.
    Uses the canonical seed query for the niche.
    Saves permanently to the cache dir so subsequent runs reuse the file.
    Returns the cached path, or None on failure.
    """
    subdir_map = {"tech_ai": "tech", "dark_psychology": "psychology", "micro_mystery": "mystery"}
    subdir     = subdir_map.get(niche, "tech")
    cache_dir  = BGM_CACHE_DIR / subdir
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Already seeded — skip download
    existing = list(cache_dir.glob("*.mp3"))
    if existing:
        log(f"BGM cache already seeded for '{niche}': {len(existing)} file(s)")
        return random.choice(existing)

    if not ytdlp_available():
        log("yt-dlp not found — cannot auto-seed BGM cache")
        return None

    seed_query = NICHE_BGM_SEED_QUERIES.get(niche, NICHE_BGM_SEED_QUERIES["tech_ai"])
    out_template = str(cache_dir / f"bgm_seed_{niche}_%(id)s.%(ext)s")

    log(f"Auto-seeding BGM cache for '{niche}': {seed_query}")
    cmd = [
        "yt-dlp", "--no-warnings", "--quiet",
        "-f", "bestaudio[ext=m4a]/bestaudio/best",
        "-x", "--audio-format", "mp3", "--audio-quality", "192K",
        "-o", out_template,
        "--no-playlist",
        seed_query,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        seeded = list(cache_dir.glob("*.mp3"))
        if seeded:
            log(f"BGM cache seeded: {seeded[0].name} ({seeded[0].stat().st_size // 1024}KB)")
            return seeded[0]
        log(f"BGM seed download produced no file: {result.stderr[:150]}")
    except subprocess.TimeoutExpired:
        log("BGM seed download timed out")
    except Exception as e:
        log(f"BGM seed download error: {e}")
    return None


def source_bgm(niche: str, output_dir: Path = OUTPUT_DIR) -> Path | None:
    """
    Download BGM for the given niche.
    1. Auto-seeds bgm_cache/{subdir}/ if empty via fetch_niche_bgm().
    2. Tries each audio query via yt-dlp, normalizes to BGM_TARGET_VOLUME (10%).
    3. Falls back to cached file if live download fails.
    """
    # Ensure cache is seeded before attempting a live search
    fetch_niche_bgm(niche)

    queries = NICHE_AUDIO_QUERIES.get(niche, NICHE_AUDIO_QUERIES["tech_ai"]).copy()
    random.shuffle(queries)

    last_failed_query = None
    for query in queries:
        log(f"BGM search: '{query}'")
        videos = search_videos(query, max_results=5)
        if not videos:
            last_failed_query = f"'{query}' (no search results)"
            continue

        # Pick the video with most views (proxy for quality/popularity)
        best = max(videos, key=lambda v: v.get("view_count", 0))
        raw_audio = download_audio_clip(best, BGM_DURATION_S, output_dir)
        if raw_audio:
            normalized = normalize_audio_volume(raw_audio, BGM_TARGET_VOLUME, output_dir)
            # Clean up raw (unnormalized) file if normalization produced a new file
            if normalized and normalized != raw_audio and raw_audio.exists():
                raw_audio.unlink(missing_ok=True)
            return normalized or raw_audio

        last_failed_query = f"'{query}' (download failed for: {best.get('title', '?')[:60]})"
        time.sleep(2)

    log(f"All BGM download attempts failed — last failure: {last_failed_query} — falling back to bgm_cache")
    return get_bgm_cache_fallback(niche, output_dir)


def source_footage(niche: str = "tech_ai", target_duration: float = CLIP_DURATION_S,
                   output_dir: Path = OUTPUT_DIR) -> Path | None:
    """
    Download niche-specific video B-roll.
    Falls back to broll_cache on failure.
    Returns a 1080x1920 mp4 path, or None.
    """
    # Merge hardcoded queries with any trend_scraper overrides for this niche
    override_queries = _load_video_query_overrides().get(niche, [])
    base_queries     = NICHE_VIDEO_QUERIES.get(niche, BROLL_FALLBACK_QUERIES)
    queries          = list(base_queries) + override_queries
    random.shuffle(queries)

    for query in queries:
        log(f"Video search: '{query}'")
        videos = search_videos(query, max_results=8)
        if not videos:
            continue

        scored = sorted(videos, key=lambda v: v.get("view_count", 0), reverse=True)
        log(f"  Top result: {scored[0]['title'][:60]} ({scored[0]['view_count']:,} views)")

        for candidate in scored[:3]:
            vid_id = candidate.get("id", "")
            if _is_used(vid_id):
                log(f"  Dedup: skipping {vid_id[:8]} (already in used_broll_log)")
                continue
            # Pre-compute start_offset — guard against source shorter than target
            dur = int(candidate.get("duration", 60))
            if dur <= int(target_duration) + 5:
                start_off = 0.0  # source too short for a safe offset — start from beginning
            else:
                max_off   = min(CLIP_MAX_OFFSET_S, dur - int(target_duration) - 5)
                start_off = random.uniform(CLIP_MIN_OFFSET_S, max(CLIP_MIN_OFFSET_S, max_off))
            clip = download_video_clip(candidate, target_duration, output_dir, start_offset=start_off)
            if clip:
                vertical = resize_to_vertical(clip, output_dir)
                if vertical:
                    clip.unlink(missing_ok=True)
                    if vid_id:
                        _mark_used(vid_id, start_off, target_duration)  # only mark after successful convert
                    log(f"  B-roll ready: {vertical.name}")
                    return vertical
                return clip
            time.sleep(2)

    # All niche queries failed — try generic fallback queries
    log(f"Niche queries exhausted for '{niche}' — trying generic fallback queries")
    for query in BROLL_FALLBACK_QUERIES:
        videos = search_videos(query, max_results=5)
        if not videos:
            continue
        best = max(videos, key=lambda v: v.get("view_count", 0))
        clip = download_video_clip(best, target_duration, output_dir)
        if clip:
            vertical = resize_to_vertical(clip, output_dir)
            if vertical:
                clip.unlink(missing_ok=True)
                return vertical
            return clip

    log("All yt-dlp attempts failed — falling back to broll_cache")
    cached = get_cache_fallback(output_dir)
    if cached:
        vertical = resize_to_vertical(cached, output_dir)
        return vertical or cached

    log("B-roll sourcing completely failed (cache empty)")
    return None


def source_from_brief(brief_path: Path, output_dir: Path = OUTPUT_DIR) -> Path | None:
    """Source B-roll for a creative brief. Uses niche from brief if present."""
    try:
        brief = json.loads(brief_path.read_text())
        niche = brief.get("niche", "tech_ai")
        log(f"Sourcing footage for brief: {brief_path.name} (niche={niche})")
    except Exception as e:
        log(f"Failed to load brief: {e}")
        niche = "tech_ai"
    return source_footage(niche=niche, output_dir=output_dir)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Source niche-specific footage and BGM for Harbinger")
    parser.add_argument("--niche",    type=str, default="tech_ai", choices=VALID_NICHES)
    parser.add_argument("--brief",    type=str, help="Path to creative brief JSON (niche auto-detected)")
    parser.add_argument("--duration", type=float, default=CLIP_DURATION_S)
    parser.add_argument("--output",   type=str, default=str(OUTPUT_DIR))
    parser.add_argument("--bgm-only", action="store_true", help="Download BGM only, skip video")
    parser.add_argument("--video-only", action="store_true", help="Download video only, skip BGM")
    args = parser.parse_args()

    load_env()
    out_dir = Path(args.output)
    out_dir.mkdir(exist_ok=True)

    niche = args.niche
    if args.brief:
        try:
            brief = json.loads(Path(args.brief).read_text())
            niche = brief.get("niche", niche)
        except Exception:
            pass

    if not args.bgm_only:
        video_result = source_footage(niche, args.duration, out_dir)
        if video_result:
            log(f"\nVIDEO SUCCESS: {video_result}")
            print(f"VIDEO:{video_result}")
        else:
            log("\nVIDEO FAILED: No footage sourced")

    if not args.video_only:
        bgm_result = source_bgm(niche, out_dir)
        if bgm_result:
            log(f"\nBGM SUCCESS: {bgm_result}")
            print(f"BGM:{bgm_result}")
        else:
            log("\nBGM FAILED: No BGM sourced")

    if (args.bgm_only and not bgm_result) or (args.video_only and not video_result):
        sys.exit(1)


if __name__ == "__main__":
    main()
