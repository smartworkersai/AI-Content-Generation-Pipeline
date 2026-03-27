#!/usr/bin/env python3
"""
final_qa_gate.py — Pre-upload integrity firewall.

Mathematically verifies a rendered MP4 before it is allowed to reach the
Buffer API.  Four hard gates — all must pass.

  Check 1  Audio stream present   (ffprobe)
  Check 2  Not silent             max_volume > -40 dB  (ffmpeg volumedetect)
  Check 3  Duration               10s – 90s
  Check 4  File size              > 1 MB

Exit codes:
  0  PASS — file is cleared for upload
  1  FAIL — file is quarantined; upload must be aborted

Usage:
  python3 final_qa_gate.py <path_to_mp4>
  python3 final_qa_gate.py <path_to_mp4> --json   # machine-readable output
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys
from pathlib import Path

FFMPEG_BIN  = "ffmpeg"
FFPROBE_BIN = "ffprobe"

# Prefer ffmpeg-full (keg-only, has libass) when available
_FFMPEG_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
if _FFMPEG_FULL.exists():
    FFMPEG_BIN  = str(_FFMPEG_FULL)
    FFPROBE_BIN = str(_FFMPEG_FULL.parent / "ffprobe")

SILENT_THRESHOLD_DB  = -40.0   # max_volume below this → silent
MIN_DURATION_S       = 10.0
MAX_DURATION_S       = 90.0
MIN_FILE_SIZE_BYTES  = 1 * 1024 * 1024   # 1 MB


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_audio_stream(mp4: Path) -> tuple[bool, str]:
    """Check 1: at least one active audio stream must be present."""
    try:
        r = subprocess.run(
            [
                FFPROBE_BIN, "-v", "quiet",
                "-print_format", "json",
                "-show_streams", str(mp4),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"ffprobe failed (exit {r.returncode}): {r.stderr[:200]}"
        data = json.loads(r.stdout)
        audio_streams = [
            s for s in data.get("streams", [])
            if s.get("codec_type") == "audio"
        ]
        if not audio_streams:
            return False, "No audio stream found in file"
        codec = audio_streams[0].get("codec_name", "unknown")
        ch    = audio_streams[0].get("channels", "?")
        return True, f"Audio stream present: codec={codec} channels={ch}"
    except Exception as e:
        return False, f"ffprobe error: {e}"


def check_volume(mp4: Path) -> tuple[bool, str]:
    """Check 2: max_volume must be above -40 dB (not silent)."""
    try:
        r = subprocess.run(
            [
                FFMPEG_BIN, "-i", str(mp4),
                "-af", "volumedetect",
                "-vn", "-sn", "-dn",
                "-f", "null", "/dev/null",
            ],
            capture_output=True, text=True, timeout=60,
        )
        # volumedetect writes to stderr
        output = r.stderr

        # Anchor to the volumedetect filter's own output lines to avoid matching
        # stray "max_volume:" strings that may appear in FFmpeg error messages.
        max_vol_match  = re.search(r"\[Parsed_volumedetect[^\]]*\].*?max_volume:\s*([-0-9.]+)\s*dB", output)
        mean_vol_match = re.search(r"\[Parsed_volumedetect[^\]]*\].*?mean_volume:\s*([-0-9.]+)\s*dB", output)

        if not max_vol_match:
            return False, "volumedetect produced no output — audio stream unreadable"

        max_vol  = float(max_vol_match.group(1))
        mean_vol = float(mean_vol_match.group(1)) if mean_vol_match else None

        mean_str = f", mean={mean_vol:.1f}dB" if mean_vol is not None else ""
        detail   = f"max_volume={max_vol:.1f}dB{mean_str}"

        if max_vol < SILENT_THRESHOLD_DB:
            return False, f"Silent audio detected: {detail} (threshold {SILENT_THRESHOLD_DB}dB)"

        return True, f"Volume OK: {detail}"
    except Exception as e:
        return False, f"volumedetect error: {e}"


def check_duration(mp4: Path) -> tuple[bool, str]:
    """Check 3: duration must be between 10s and 90s."""
    try:
        r = subprocess.run(
            [
                FFPROBE_BIN, "-v", "quiet",
                "-print_format", "json",
                "-show_format", str(mp4),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return False, f"ffprobe failed (exit {r.returncode}): {r.stderr[:200]}"
        data     = json.loads(r.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        if duration < MIN_DURATION_S:
            return False, f"Duration too short: {duration:.1f}s (min {MIN_DURATION_S}s)"
        if duration > MAX_DURATION_S:
            return False, f"Duration too long: {duration:.1f}s (max {MAX_DURATION_S}s)"
        return True, f"Duration OK: {duration:.1f}s"
    except Exception as e:
        return False, f"Duration probe error: {e}"


def check_file_size(mp4: Path) -> tuple[bool, str]:
    """Check 4: file must be larger than 1 MB."""
    try:
        size_bytes = mp4.stat().st_size
        size_mb    = size_bytes / (1024 * 1024)
        if size_bytes < MIN_FILE_SIZE_BYTES:
            return False, f"File too small: {size_mb:.2f}MB (min 1MB) — likely corrupted or video-only"
        return True, f"File size OK: {size_mb:.2f}MB"
    except Exception as e:
        return False, f"File size check error: {e}"


# ---------------------------------------------------------------------------
# Gate runner
# ---------------------------------------------------------------------------

def run_qa_gate(mp4_path: str | Path) -> dict:
    """
    Run all four checks against the given MP4.
    Returns a result dict with keys: passed (bool), checks (list), mp4 (str).
    """
    mp4 = Path(mp4_path)
    if not mp4.exists():
        return {
            "passed": False,
            "mp4": str(mp4),
            "checks": [{"name": "file_exists", "passed": False, "detail": f"File not found: {mp4}"}],
        }

    checks_run = [
        ("audio_stream",  check_audio_stream(mp4)),
        ("volume",        check_volume(mp4)),
        ("duration",      check_duration(mp4)),
        ("file_size",     check_file_size(mp4)),
    ]

    results = []
    all_passed = True
    for name, (ok, detail) in checks_run:
        results.append({"name": name, "passed": ok, "detail": detail})
        if not ok:
            all_passed = False

    return {
        "passed": all_passed,
        "mp4": str(mp4),
        "checks": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pre-upload QA gate for rendered MP4s")
    parser.add_argument("mp4", help="Path to the rendered MP4 file")
    parser.add_argument("--json", action="store_true", dest="json_out",
                        help="Output machine-readable JSON instead of human text")
    args = parser.parse_args()

    result = run_qa_gate(args.mp4)

    if args.json_out:
        print(json.dumps(result, indent=2))
    else:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"\n{'='*55}")
        print(f"  FINAL QA GATE — {status}")
        print(f"  File: {Path(result['mp4']).name}")
        print(f"{'='*55}")
        for c in result["checks"]:
            icon = "✓" if c["passed"] else "✗"
            print(f"  [{icon}] {c['name']:<16}  {c['detail']}")
        print(f"{'='*55}\n")

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
