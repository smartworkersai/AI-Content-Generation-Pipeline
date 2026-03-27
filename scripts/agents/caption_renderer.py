#!/usr/bin/env python3
"""
caption_renderer.py — Clean caption burn for Harbinger videos.

ONE text element. Bold white. Black outline. Bottom third.
Nothing else. No watermarks baked into background. No floating elements.
No design decisions. Just readable text on a phone screen.

Usage:
    python3 caption_renderer.py --video output/footage.mp4 --script "Your caption text here" --output output/final.mp4
    python3 caption_renderer.py --video output/footage.mp4 --ass captions/slot1.ass --output output/final.mp4
    python3 caption_renderer.py --video output/footage.mp4 --brief logs/creative_brief_slot1.json --output output/final.mp4
"""
from __future__ import annotations
import os, sys, json, re, subprocess, tempfile, shutil
from pathlib import Path
import datetime

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR   = BASE_DIR / "logs"
OUTPUT_DIR.mkdir(exist_ok=True)

NOW       = datetime.datetime.utcnow()
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

# ---------------------------------------------------------------------------
# Caption style — one spec, no A/B, no parameters
# This is what's readable on a phone. Nothing else.
# ---------------------------------------------------------------------------
CAPTION_STYLE = {
    "font_name":       "Arial",       # universally available
    "font_size":       88,            # readable on phone screen
    "primary_colour":  "&H00FFFFFF",  # white
    "outline_colour":  "&H00000000",  # black
    "back_colour":     "&H80000000",  # semi-transparent black bg
    "bold":            1,
    "outline":         3,             # outline thickness px
    "shadow":          0,
    "margin_l":        80,
    "margin_r":        80,
    "margin_v":        160,           # push up from bottom edge
    "alignment":       2,             # bottom-centre
    "words_per_block": 3,             # 3 words per caption block
}


def log(msg: str):
    print(f"[caption_renderer] {msg}")


def make_ass_header() -> str:
    s = CAPTION_STYLE
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{s['font_name']},{s['font_size']},{s['primary_colour']}&H000000FF,{s['outline_colour']},{s['back_colour']},{s['bold']},0,0,0,100,100,0,0,1,{s['outline']},{s['shadow']},{s['alignment']},{s['margin_l']},{s['margin_r']},{s['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def seconds_to_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format: H:MM:SS.cc"""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int((seconds % 1) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def text_to_ass(text: str, audio_duration: float,
                alignment_data: list = None) -> str:
    """
    Convert script text to ASS subtitle file.
    
    If alignment_data (ElevenLabs word timestamps) provided, uses real timing.
    Otherwise estimates timing based on word count and speaking rate.
    
    alignment_data format: list of {word, start, end} dicts (seconds)
    """
    header = make_ass_header()
    events = []

    words_per_block = CAPTION_STYLE["words_per_block"]

    if alignment_data and len(alignment_data) > 0:
        # Real word-level timestamps from ElevenLabs
        # Filter to only dict items — non-dict elements (e.g. strings) are silently dropped
        words = [w for w in alignment_data if isinstance(w, dict)]

        # Group into blocks of N words
        i = 0
        while i < len(words):
            block = words[i:i + words_per_block]
            if not block:
                break

            start  = block[0].get("start", 0)
            end    = block[-1].get("end", start + 0.8)

            # Add small overlap so captions don't flash out
            end = min(end + 0.15, audio_duration)

            block_text = " ".join(w.get("word", "") for w in block).strip()
            if block_text:
                events.append(
                    f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},"
                    f"Default,,0,0,0,,{block_text}"
                )
            i += words_per_block

    else:
        # Estimated timing — split text into blocks, distribute across duration
        # Use 80% of audio duration for captions (leave tail space)
        caption_duration = audio_duration * 0.88

        # Clean text — strip stage directions, audio tags, SSML/XML tags
        clean = re.sub(r'<[^>]+>', '', text)           # strip SSML/XML (e.g. <break time="0.8s"/>)
        clean = re.sub(r'\[.*?\]', '', clean)          # remove [tense] etc
        clean = re.sub(r'\*.*?\*', '', clean)          # remove *emphasis*
        clean = re.sub(r'\s+', ' ', clean).strip()

        all_words = clean.split()
        if not all_words:
            return header

        # Estimate: avg speaking rate ~2.8 words/sec
        # Distribute caption blocks evenly
        total_blocks    = (len(all_words) + words_per_block - 1) // words_per_block
        time_per_block  = caption_duration / max(total_blocks, 1)

        i = 0
        block_idx = 0
        while i < len(all_words):
            block = all_words[i:i + words_per_block]
            start = 0.3 + block_idx * time_per_block  # 0.3s lead-in
            end   = start + time_per_block - 0.05      # slight gap between blocks

            block_text = " ".join(block).strip()
            if block_text:
                events.append(
                    f"Dialogue: 0,{seconds_to_ass_time(start)},{seconds_to_ass_time(end)},"
                    f"Default,,0,0,0,,{block_text}"
                )

            i         += words_per_block
            block_idx += 1

    return header + "\n".join(events) + "\n"


def burn_captions(
    video_path: Path,
    ass_content: str,
    output_path: Path,
) -> Path | None:
    """
    Burn ASS captions into video using FFmpeg.
    Single subtitle track. Nothing else added.
    """
    # Write ASS to temp file
    ass_file = output_path.parent / f"captions_{TIMESTAMP}.ass"
    ass_file.write_text(ass_content, encoding="utf-8")

    log(f"Burning captions: {video_path.name} → {output_path.name}")
    log(f"  ASS events: {ass_content.count('Dialogue:')}")

    # Copy ASS to /tmp with a safe, colon-free filename so FFmpeg's subtitles= filter
    # never has to deal with drive letters, spaces, or macOS path colons.
    import shutil as _shutil, tempfile as _tempfile
    safe_ass = Path(_tempfile.gettempdir()) / f"harbinger_captions_{TIMESTAMP}.ass"
    _shutil.copy2(str(ass_file), str(safe_ass))
    ass_path_str = str(safe_ass)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"subtitles={ass_path_str}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        ass_file.unlink(missing_ok=True)

        if result.returncode != 0:
            log(f"FFmpeg caption burn FAILED (exit {result.returncode}): {result.stderr[-400:]}")
            log("WARNING: captions could NOT be burned — final video will have NO subtitles. Check FFmpeg/libass installation.")
            shutil.copy(str(video_path), str(output_path))
            return output_path

        if not output_path.exists():
            log("Output file not created")
            return None

        size_mb = output_path.stat().st_size / 1024 / 1024
        log(f"Caption burn complete: {output_path.name} ({size_mb:.1f}MB)")
        return output_path

    except subprocess.TimeoutExpired:
        ass_file.unlink(missing_ok=True)
        log("FFmpeg timeout")
        return None
    except Exception as e:
        ass_file.unlink(missing_ok=True)
        log(f"Burn error: {e}")
        return None


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        data   = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return 45.0  # fallback


def process_brief(brief_path: Path, video_path: Path, output_path: Path) -> Path | None:
    """
    Pull script text and alignment data from a creative brief,
    burn clean captions onto a video.
    """
    try:
        brief = json.loads(brief_path.read_text())
    except Exception as e:
        log(f"Failed to load brief: {e}")
        return None

    # Get script text — concatenate all script sections
    script = brief.get("script", {})
    if isinstance(script, dict):
        text = " ".join(v for v in script.values() if isinstance(v, str))
    elif isinstance(script, str):
        text = script
    else:
        text = brief.get("caption_text", brief.get("asymmetry", ""))

    if not text:
        log("No script text found in brief")
        return None

    # Get alignment data if available
    alignment = brief.get("audio_alignment_data", None)
    duration  = get_video_duration(video_path)

    ass_content = text_to_ass(text, duration, alignment)
    return burn_captions(video_path, ass_content, output_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Burn clean captions onto a video")
    parser.add_argument("--video",  required=True, help="Input video path")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--script", help="Raw script text to caption")
    parser.add_argument("--ass",    help="Pre-built .ass subtitle file path")
    parser.add_argument("--brief",  help="Creative brief JSON path")
    args = parser.parse_args()

    video_path  = Path(args.video)
    output_path = Path(args.output)

    if not video_path.exists():
        print(f"ERROR: Video not found: {video_path}")
        sys.exit(1)

    if args.brief:
        result = process_brief(Path(args.brief), video_path, output_path)

    elif args.ass:
        ass_content = Path(args.ass).read_text(encoding="utf-8")
        result = burn_captions(video_path, ass_content, output_path)

    elif args.script:
        duration    = get_video_duration(video_path)
        ass_content = text_to_ass(args.script, duration)
        result = burn_captions(video_path, ass_content, output_path)

    else:
        parser.error("Provide --script, --ass, or --brief")
        return

    if result:
        log(f"\nSUCCESS: {result}")
        print(str(result))
        sys.exit(0)
    else:
        log("\nFAILED: Caption burn failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
