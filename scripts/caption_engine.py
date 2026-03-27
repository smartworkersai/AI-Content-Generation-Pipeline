#!/usr/bin/env python3
"""
caption_engine.py — Harbinger Caption System v4 (Niche-Aware Karaoke)

Generates word-level karaoke ASS subtitles with niche-specific styling.
Karaoke \\k tags drive frame-perfect word-by-word highlight sync.

Niche styles:
  tech_ai        — Montserrat Black, White text, Neon Green highlight
  dark_psychology — Arial Bold, White text, Blood Red highlight
  micro_mystery  — Courier New, Yellow text, White highlight

Usage:
  from caption_engine import generate_ass
  ass_content, meta = generate_ass(script_text, alignment_data, niche="tech_ai")
"""
from __future__ import annotations
import os, sys, json, re, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

NOW = datetime.datetime.utcnow()


def log(msg: str):
    print(f"[caption_engine] {msg}")


# ── ASS colour format: &HAABBGGRR (alpha, blue, green, red) ──────────────────
WHITE       = "&H00FFFFFF"
BLACK       = "&H00000000"
SEMI_BG     = "&H99000000"   # ~60% opacity black background box
NEON_GREEN  = "&H0000FF00"   # R=0, G=255, B=0
BLOOD_RED   = "&H000000FF"   # R=255, G=0, B=0
YELLOW      = "&H0000FFFF"   # R=255, G=255, B=0

# ── Per-niche caption style definitions ──────────────────────────────────────
NICHE_CAPTION_STYLES = {
    "tech_ai": {
        "font":      "Montserrat",
        "fontsize":  90,
        "primary":   WHITE,
        "secondary": NEON_GREEN,   # karaoke highlight colour
        "outline":   BLACK,
        "bg":        SEMI_BG,
        "bold":      -1,
    },
    "dark_psychology": {
        "font":      "Arial",
        "fontsize":  88,
        "primary":   WHITE,
        "secondary": BLOOD_RED,
        "outline":   BLACK,
        "bg":        SEMI_BG,
        "bold":      -1,
    },
    "micro_mystery": {
        "font":      "Courier New",
        "fontsize":  82,
        "primary":   YELLOW,
        "secondary": WHITE,
        "outline":   BLACK,
        "bg":        SEMI_BG,
        "bold":      -1,
    },
}
DEFAULT_STYLE = NICHE_CAPTION_STYLES["tech_ai"]

# ASS header template — {fields} filled per-niche at generation time
# HookCaption: 40% larger, Alignment=8 (top-center), used for subtitle blocks with start < 3s (#5)
ASS_HEADER_TMPL = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{fontsize},{primary},{secondary},{outline},{bg},{bold},0,0,0,100,100,0,0,3,10,0,2,20,20,160,1
Style: HookCaption,{font},{hook_fontsize},{primary},{secondary},{outline},{bg},{bold},0,0,0,100,100,0,0,3,12,0,8,20,20,300,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

WORDS_PER_BLOCK = 3


def _ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp h:mm:ss.cc"""
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _strip_tags(text: str) -> str:
    """Remove voice direction tags like [pause], [authoritative], and SSML/XML tags."""
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'<[^>]+>', '', text)   # strip SSML tags like <break time="0.8s"/>
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def _get_word_timestamps(alignment: dict) -> list[tuple[str, float, float]]:
    """
    Parse ElevenLabs character-level alignment into word-level timestamps.
    Returns list of (word, start_sec, end_sec).
    SSML/XML fragments (e.g. <break time="0.8s"/>) that ElevenLabs may include
    verbatim in the character stream are stripped before any word is emitted.
    """
    chars  = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends   = alignment.get("character_end_times_seconds", [])

    if not chars or len(chars) != len(starts):
        return []

    words = []
    current_word = ""
    word_start   = 0.0
    word_end     = 0.0
    in_tag       = False   # True while accumulating characters inside an XML/SSML tag

    for i, ch in enumerate(chars):
        t_start = starts[i] if i < len(starts) else 0.0
        t_end   = ends[i]   if i < len(ends)   else t_start + 0.05

        # Detect opening of an XML/SSML tag — consume atomically until '>' then discard
        if ch == "<":
            # Flush any real word accumulated before this tag
            if current_word.strip():
                clean = _strip_tags(current_word).strip()
                if clean:
                    words.append((clean, word_start, word_end))
            current_word = "<"
            in_tag = True
            continue

        if in_tag:
            current_word += ch
            if ch == ">":
                in_tag = False
                current_word = ""   # discard entire tag — no word, no timestamp
            continue

        if ch in (" ", "\n", "\t"):
            if current_word.strip():
                clean = _strip_tags(current_word).strip()
                if clean:
                    words.append((clean, word_start, word_end))
            current_word = ""
        else:
            if not current_word:
                word_start = t_start
            current_word += ch
            word_end = t_end

    if current_word.strip() and not in_tag:
        clean = _strip_tags(current_word).strip()
        if clean:
            words.append((clean, word_start, word_end))

    return words


def _estimate_word_timestamps(words: list[str], total_duration: float = 20.0) -> list[tuple[str, float, float]]:
    """Assign proportional timestamps when ElevenLabs alignment is unavailable.
    Duration per word is weighted by character length so long words get more time
    and short words don't linger — prevents visible caption drift."""
    if not words:
        return []
    char_counts = [max(1, len(w)) for w in words]
    total_chars = sum(char_counts)
    result = []
    t = 0.0
    for w, chars in zip(words, char_counts):
        duration = total_duration * (chars / total_chars)
        result.append((w, t, t + duration))
        t += duration
    return result


def _build_karaoke_block(chunk: list[tuple[str, float, float]]) -> str:
    """
    Build ASS karaoke text for a block of words.
    Each word gets a \\k{N} tag where N = duration in centiseconds.
    The active word will be rendered in SecondaryColour (highlight).
    Returns empty string for empty/whitespace-only chunks — caller must skip these.
    """
    if not chunk:
        return ""
    parts = []
    for word, w_start, w_end in chunk:
        if not word.strip():
            continue
        dur_cs = max(1, int(round((w_end - w_start) * 100)))
        parts.append(f"{{\\k{dur_cs}}}{word}")
    return " ".join(parts)


def generate_ass(
    script_text: str,
    alignment: dict | None = None,
    niche: str = "tech_ai",
    directives: dict | None = None,
    urgency_score: int = 50,
) -> tuple[str, dict]:
    """
    Generate a niche-styled ASS subtitle file with word-level karaoke sync.

    Karaoke \\k tags: each word highlights in SecondaryColour as it's spoken,
    driven by real ElevenLabs alignment timestamps or estimated fallback.

    Returns (ass_content: str, metadata: dict).
    """
    style = NICHE_CAPTION_STYLES.get(niche, DEFAULT_STYLE)
    hook_fontsize = int(style["fontsize"] * 1.4)  # HookCaption: 40% larger (#5)
    header = ASS_HEADER_TMPL.format(
        font=style["font"],
        fontsize=style["fontsize"],
        hook_fontsize=hook_fontsize,
        primary=style["primary"],
        secondary=style["secondary"],
        outline=style["outline"],
        bg=style["bg"],
        bold=style["bold"],
    )

    text = _strip_tags(script_text or "")
    raw_words = [w for w in text.split() if w]

    # Build word timestamps
    if alignment and alignment.get("characters"):
        word_times = _get_word_timestamps(alignment)
        timing_mode = "real_timestamps"
        log(f"Using ElevenLabs word-level timestamps ({len(word_times)} words, niche={niche})")
    else:
        total_dur = max(15.0, len(raw_words) * 0.35)
        word_times = _estimate_word_timestamps(raw_words, total_dur)
        timing_mode = "estimated"
        log(f"Estimating timestamps ({len(word_times)} words, {total_dur:.1f}s, niche={niche})")

    if not word_times:
        log("No words to render — returning empty ASS")
        return header, {"total_events": 0, "timing_mode": timing_mode, "niche": niche}

    # Group into WORDS_PER_BLOCK karaoke blocks
    events = []
    for i in range(0, len(word_times), WORDS_PER_BLOCK):
        chunk       = word_times[i : i + WORDS_PER_BLOCK]
        block_start = chunk[0][1]
        block_end   = max(chunk[-1][2], chunk[0][1] + 0.4)
        karaoke_txt = _build_karaoke_block(chunk)
        if not karaoke_txt:          # skip empty/whitespace-only blocks — never write blank Dialogue lines
            continue
        # Use HookCaption style (40% larger, top-center) for the opening 3 seconds (#5)
        style_name  = "HookCaption" if block_start < 3.0 else "Caption"
        line = (
            f"Dialogue: 0,{_ass_time(block_start)},{_ass_time(block_end)},"
            f"{style_name},,0,0,0,,{karaoke_txt}"
        )
        events.append(line)

    ass_content = header + "\n".join(events) + "\n"
    meta = {
        "total_events": len(events),
        "timing_mode":  timing_mode,
        "words":        len(word_times),
        "blocks":       len(events),
        "niche":        niche,
        "style_font":   style["font"],
    }
    log(f"Generated {len(events)} karaoke blocks ({timing_mode}, font={style['font']}, niche={niche})")
    return ass_content, meta


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--script",    required=True)
    parser.add_argument("--alignment", default=None)
    parser.add_argument("--niche",     default="tech_ai",
                        choices=["tech_ai", "dark_psychology", "micro_mystery"])
    parser.add_argument("--output",    default="captions.ass")
    args = parser.parse_args()

    script_text = Path(args.script).read_text()
    alignment   = {}
    if args.alignment:
        alignment = json.loads(Path(args.alignment).read_text())

    content, meta = generate_ass(script_text, alignment, niche=args.niche)
    Path(args.output).write_text(content, encoding="utf-8")
    print(f"Written: {args.output} ({meta})")
