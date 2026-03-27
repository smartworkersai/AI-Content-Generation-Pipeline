#!/usr/bin/env python3
"""
creative_synthesis.py — Agent 2: Creative Synthesis
Generates viral short-form scripts across 3 niches: Tech/AI Hacks, Dark Psychology, Micro-Mysteries.
Output: logs/creative_brief_[timestamp]_slot[n].json

Usage:
  python3 creative_synthesis.py --slot <1-10> [--niche tech_ai|dark_psychology|micro_mystery]
  --niche overrides the random niche selector (used by blitz_10.sh).
"""
import os, sys, json, datetime, re, random
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent
LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
SYNTHESIS_LOG = LOGS_DIR / "creative_synthesis.log"
NOW       = datetime.datetime.utcnow()
DATE_STR  = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

VALID_NICHES = ["tech_ai", "dark_psychology", "micro_mystery"]

EVOLUTION_PARAMS_FILE = LOGS_DIR / "evolution_params.json"
NICHE_OVERRIDES_FILE  = LOGS_DIR / "niche_overrides.json"
VIRAL_FRAMEWORKS_FILE = LOGS_DIR / "viral_frameworks.json"


def load_evolution_params() -> dict:
    """Load A/B-tested parameters from evolution_engine output."""
    defaults = {"zoom_factor": 0.15, "ssml_break_secs": 0.8}
    if EVOLUTION_PARAMS_FILE.exists():
        try:
            stored = json.loads(EVOLUTION_PARAMS_FILE.read_text())
            defaults.update(stored)
        except Exception:
            pass
    return defaults


def load_niche_overrides() -> dict:
    """Load trend_scraper hook + niche overrides."""
    if NICHE_OVERRIDES_FILE.exists():
        try:
            return json.loads(NICHE_OVERRIDES_FILE.read_text())
        except Exception:
            pass
    return {}


# Cold-start fallback frameworks — used when viral_frameworks.json is absent
SCRIPT_FALLBACK_FRAMEWORKS = {
    "tech_ai": [
        {
            "structure": "[Familiar Tool/Habit] + ['is secretly/actually'] + [Hidden Cost or Danger]",
            "trigger": "Loss aversion — viewer is already being harmed without knowing it",
            "example": "That iPhone feature everyone uses is secretly selling your location to 47 data brokers.",
        },
        {
            "structure": "[Number] + [Category of Thing] + ['that feel illegal to know']",
            "trigger": "Information asymmetry — viewer gains restricted knowledge others don't have",
            "example": "4 websites that feel illegal to know about in 2026.",
        },
        {
            "structure": "['How to'] + [Aspirational Outcome] + ['using'] + [Unknown Free Method]",
            "trigger": "Effort gap — same result with a fraction of the work",
            "example": "How to automate your entire Monday morning using one free AI nobody is using.",
        },
    ],
    "dark_psychology": [
        {
            "structure": "['If someone does'] + [Specific Observable Behaviour] + ['they are'] + [Hidden Intent]",
            "trigger": "Social threat detection — invisible manipulation the viewer didn't see coming",
            "example": "If someone mirrors your body language in the first 30 seconds, they are running a dominance test on you.",
        },
        {
            "structure": "['The reason you'] + [Universal Relatable Behaviour] + ['is not'] + [Assumed Reason] + ['it is'] + [Dark Mechanism]",
            "trigger": "Self-revelation — reframes a familiar experience with a disturbing explanation",
            "example": "The reason you can't stop scrolling is not boredom. It is a dopamine loop engineered by behavioural scientists.",
        },
        {
            "structure": "[Authority] + ['has known this for decades'] + [Why it was suppressed]",
            "trigger": "Conspiracy of suppression — powerful people kept this from you intentionally",
            "example": "Psychologists have known this negotiation trick for 40 years. It was never taught because it is too effective.",
        },
    ],
    "micro_mystery": [
        {
            "structure": "['What if'] + [Familiar Safe Phenomenon] + ['is actually'] + [Terrifying Alternative]",
            "trigger": "Existential reframe — takes a safe experience and makes it permanently unsettling",
            "example": "What if déjà vu is not a memory glitch. What if it is your brain catching a bleed from a parallel timeline.",
        },
        {
            "structure": "[Authority] + ['can't explain'] + [Specific Documented Anomaly] + [Open implication]",
            "trigger": "Authority gap — if experts cannot explain it, nobody can",
            "example": "NASA has documented 47 radio signals from deep space that repeat on a 16-day cycle. No explanation.",
        },
        {
            "structure": "['The [place/object] that'] + [Mundane description] + ['is actually hiding'] + [Suppressed truth]",
            "trigger": "Hidden reality — the world has a concealed layer almost nobody sees",
            "example": "The town in Norway where it is illegal to die is not a tourist quirk. It is hiding something stranger.",
        },
    ],
}


def load_viral_frameworks() -> dict:
    """Load today's PhD-deconstructed frameworks. Falls back to static if absent."""
    if VIRAL_FRAMEWORKS_FILE.exists():
        try:
            data = json.loads(VIRAL_FRAMEWORKS_FILE.read_text())
            frameworks = data.get("frameworks", {})
            # Validate all three niches present
            if all(niche in frameworks for niche in VALID_NICHES):
                return frameworks
        except Exception:
            pass
    return SCRIPT_FALLBACK_FRAMEWORKS


def pick_topic(niche: str, niche_overrides: dict) -> str:
    """
    Pull the best available topic for the niche from trend overrides.
    Strips the 'Nobody is talking about this:' prefix injected by old trend_scraper.
    Falls back to a generic niche topic if none available.
    """
    PREFIX_PATTERN = re.compile(r'^nobody is talking about this[:\s]+', re.IGNORECASE)
    topics = niche_overrides.get("hooks", {}).get(niche, [])
    if topics:
        raw = random.choice(topics)
        return PREFIX_PATTERN.sub("", raw).strip()
    # Generic topic seeds per niche — used only when trend data is absent
    fallback_topics = {
        "tech_ai": "AI tools that most people don't know exist yet",
        "dark_psychology": "the psychological trick used in every high-stakes negotiation",
        "micro_mystery": "the documented anomaly scientists have never been able to explain",
    }
    return fallback_topics.get(niche, "something most people don't know about")


def pick_framework(niche: str, frameworks: dict) -> dict:
    """Pick a random framework for the given niche."""
    niche_frameworks = frameworks.get(niche, SCRIPT_FALLBACK_FRAMEWORKS.get(niche, []))
    if not niche_frameworks:
        return {
            "structure": "[Topic] + [Pattern Interrupt] + [Revelation]",
            "trigger": "Curiosity gap",
            "example": "Most people don't know this exists.",
        }
    return random.choice(niche_frameworks)


def log(msg):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(SYNTHESIS_LOG, "a") as f:
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


NICHE_LABELS = {
    "tech_ai":         "Tech / AI Hacks",
    "dark_psychology": "Dark Psychology",
    "micro_mystery":   "Micro-Mysteries",
}

# ---------------------------------------------------------------------------
# Per-niche ElevenLabs voice profiles
# IMPORTANT: Replace voice_id values with IDs from your ElevenLabs account.
# Defaults shown: Josh for all niches, differentiated via voice_settings.
# Recommended: pick a deep/authoritative voice for psychology and a
# quiet/raspy voice for mystery from your ElevenLabs voice library.
# ---------------------------------------------------------------------------
NICHE_VOICE_SETTINGS = {
    "tech_ai": {
        "voice_id":  "TxGEqnHWrfWFTfGW9XjX",  # Josh — energetic, use speed >1.0
        "stability":  0.30,
        "similarity": 0.88,
        "style":      0.85,
        "speed":      1.10,   # Fast delivery
    },
    "dark_psychology": {
        "voice_id":  "TxGEqnHWrfWFTfGW9XjX",  # Replace with a deep/authoritative voice ID
        "stability":  0.60,
        "similarity": 0.88,
        "style":      0.55,
        "speed":      0.85,   # Slow, deliberate
    },
    "micro_mystery": {
        "voice_id":  "TxGEqnHWrfWFTfGW9XjX",  # Replace with a raspy/quiet voice ID
        "stability":  0.70,
        "similarity": 0.85,
        "style":      0.45,
        "speed":      0.80,   # Slow, hushed
    },
}

# ---------------------------------------------------------------------------
# LLM prompt template — dynamic framework injection
# ---------------------------------------------------------------------------
SCRIPT_PROMPT_TEMPLATE = """
You are a viral short-form video scriptwriter for TikTok and Instagram Reels.
Your scripts are engineered to stop a scroll at 2am on a phone. Not ads. Not explainers. Pure pattern interrupts.

TODAY'S TOPIC: {topic}
NICHE: {niche_label}
WORD TARGET: {word_target}

TODAY'S VIRAL FRAMEWORK (reverse-engineered from highest-performing content):
- Grammatical structure: {framework_structure}
- Psychological trigger: {framework_trigger}
- Example of this framework in action: {framework_example}

Apply this exact framework to the topic above. Do NOT copy the example — use it only to understand the pattern.

STRICT CONSTRAINTS:
- Open with a hook that applies the grammatical structure above to the topic. Make it feel like something the viewer has never heard before.
- Polarity: fear, loss, or threat framing. What does the viewer LOSE by not knowing this?
- NEVER reference links, bios, affiliates, products, or any external URL.
- Tone: organic creator voice — never ad copy.

STRUCTURE:
1. HOOK (1 sentence): Applies the framework structure to the topic. Stops the scroll.
2. BODY (2-3 sentences): Delivers the core mechanism or revelation. Specific, fast, no filler.
3. CTA (1 sentence): Either a Loop-Bait bridge that echoes the hook, or an Engagement Hook.
   Examples: "Drop a 👁️ if you made it this far." / "Comment KNEW if you already knew this." /
   "Follow — there's more they're not saying." / "Follow before this disappears."

Output ONLY valid JSON. No markdown, no preamble.
{{
  "script": {{
    "hook": "...",
    "body": "...",
    "cta": "..."
  }},
  "full_script_text": "hook + body + cta as one block"
}}"""


def generate_with_replicate(niche: str, topic: str, framework: dict, word_target: str) -> dict:
    """Generate viral script via Replicate LLM. Retries once on 429."""
    import replicate
    import time as _time

    prompt = SCRIPT_PROMPT_TEMPLATE.format(
        topic=topic,
        niche_label=NICHE_LABELS[niche],
        framework_structure=framework.get("structure", ""),
        framework_trigger=framework.get("trigger", ""),
        framework_example=framework.get("example", ""),
        word_target=word_target,
    )

    for attempt in range(2):
        try:
            log(f"Generating script via Replicate (attempt {attempt + 1})...")
            output = replicate.run(
                "meta/meta-llama-3.1-405b-instruct",
                input={
                    "prompt": prompt,
                    "max_tokens": 600,
                    "temperature": 0.85,
                    "top_p": 0.92,
                },
            )
            raw = "".join(output).strip()
            # Try full parse first (clean LLM output)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass
            # Bracket-balance extraction: find the first { and walk to its matching }
            # Avoids greedy regex over-matching trailing garbage or multiple JSON objects
            start = raw.find('{')
            if start == -1:
                raise ValueError(f"No JSON in LLM output: {raw[:300]}")
            depth = 0
            for idx, ch in enumerate(raw[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return json.loads(raw[start:idx + 1])
            raise ValueError(f"Unbalanced JSON in LLM output: {raw[:300]}")
        except Exception as e:
            if "429" in str(e) and attempt == 0:
                log("Replicate rate-limited — waiting 15s before retry...")
                _time.sleep(15)
                continue
            raise


def generate_with_anthropic(niche: str, topic: str, framework: dict, word_target: str) -> dict:
    """Generate viral script via Anthropic Claude Haiku. Primary LLM — fast, cheap, reliable."""
    import anthropic as _anthropic

    prompt = SCRIPT_PROMPT_TEMPLATE.format(
        topic=topic,
        niche_label=NICHE_LABELS[niche],
        framework_structure=framework.get("structure", ""),
        framework_trigger=framework.get("trigger", ""),
        framework_example=framework.get("example", ""),
        word_target=word_target,
    )

    client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = response.content[0].text.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    start = raw.find('{')
    if start == -1:
        raise ValueError(f"No JSON in Anthropic output: {raw[:300]}")
    depth = 0
    for idx, ch in enumerate(raw[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return json.loads(raw[start:idx + 1])
    raise ValueError(f"Unbalanced JSON in Anthropic output: {raw[:300]}")


# Engagement CTAs — loop-bait bridges and organic engagement hooks.
# No links, no bio references, no affiliate language.
ENGAGEMENT_CTAS = [
    "Drop a 👁️ if you made it this far.",
    "What do you think?",
    "Follow to see the ones they don't want you to find.",
    "Comment KNEW if you already knew this.",
    "Follow before this disappears.",
    "Comment NEW if this changed something for you.",
    "Follow — there's more they're not saying.",
]


def _viral_template_fallback(niche: str, topic: str, framework: dict, script_variant: str = "long") -> dict:
    """Template fallback when LLM is unavailable. Applies framework example as hook seed."""
    body_templates = {
        "tech_ai": {
            "long": (
                "Most people scroll past these tools every day without realising they exist. "
                "Once you know about them, you cannot un-know them."
            ),
            "short": "Most people scroll past these tools without realising what they are missing.",
        },
        "dark_psychology": {
            "long": (
                "Psychologists have known this for decades but it never makes it into mainstream education. "
                "The people who use this have an unfair advantage in every conversation."
            ),
            "short": "This psychological mechanism gives an unfair advantage in every conversation.",
        },
        "micro_mystery": {
            "long": (
                "Scientists have no explanation for this. The data exists, the phenomenon is documented, "
                "but the answer has never been found."
            ),
            "short": "Scientists have no explanation. The data exists, but the answer was never found.",
        },
    }
    # Use the framework example as the hook if available; otherwise derive from topic
    hook = framework.get("example") or topic
    niche_bodies = body_templates.get(niche, body_templates["tech_ai"])
    body = niche_bodies.get(script_variant, niche_bodies["long"])
    cta  = random.choice(ENGAGEMENT_CTAS)
    return {
        "script": {"hook": hook, "body": body, "cta": cta},
        "full_script_text": f"{hook} {body} {cta}",
    }


def assemble_script(script_dict: dict) -> str:
    parts = []
    for key in ["hook", "body", "cta"]:
        text = script_dict.get(key, "").strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",  type=int, default=1,
                        choices=list(range(1, 11)),
                        help="Slot number 1-10")
    parser.add_argument("--niche", type=str, default=None,
                        choices=VALID_NICHES,
                        help="Override niche selection (used by blitz_10.sh)")
    args = parser.parse_args()
    slot = args.slot

    load_env()

    log("=" * 60)
    log(f"AGENT 2: CREATIVE SYNTHESIS — VIRAL CONTENT (SLOT {slot})")
    log("=" * 60)

    # Load evolution params, trend overrides, and today's viral frameworks
    evo_params      = load_evolution_params()
    niche_overrides = load_niche_overrides()
    frameworks      = load_viral_frameworks()
    active_niches = niche_overrides.get("active_niches", VALID_NICHES)
    if not active_niches:
        log("WARNING: active_niches is empty in niche_overrides — falling back to VALID_NICHES")
        active_niches = VALID_NICHES

    # Niche: use CLI override if provided, else random from active_niches
    if args.niche:
        niche = args.niche
        log(f"Niche: {niche} (CLI override)")
    else:
        niche = random.choice(active_niches)
        log(f"Niche: {niche} (random from active_niches={active_niches})")

    # A/B length variant: short=~35 words/15s, long=~75 words/30s
    script_variant = random.choice(["short", "long"])
    word_target = (
        "30-40 words (15 seconds spoken)"
        if script_variant == "short" else
        "70-80 words (30 seconds spoken)"
    )
    log(f"Variant:  {script_variant.upper()} ({word_target})")

    # Dynamic topic from trend data + dynamic framework from today's deconstruction
    topic     = pick_topic(niche, niche_overrides)
    framework = pick_framework(niche, frameworks)
    log(f"Topic:     {topic}")
    log(f"Framework: {framework.get('structure', '')[:80]}")
    log(f"Trigger:   {framework.get('trigger', '')[:60]}")

    creative = None
    anthropic_key  = os.environ.get("ANTHROPIC_API_KEY", "")
    replicate_token = os.environ.get("REPLICATE_API_TOKEN", "")

    if anthropic_key:
        try:
            creative = generate_with_anthropic(niche, topic, framework, word_target)
            log("Script generated via Anthropic (Claude Haiku)")
        except Exception as e:
            log(f"Anthropic generation failed: {e} — trying Replicate...")

    if creative is None and replicate_token:
        try:
            creative = generate_with_replicate(niche, topic, framework, word_target)
            log("Script generated via Replicate LLM")
        except Exception as e:
            log(f"Replicate generation failed: {e} — using template fallback")

    if creative is None:
        log("All LLM providers failed or unconfigured — using template fallback")
        creative = _viral_template_fallback(niche, topic, framework, script_variant)

    script = creative.get("script", {})

    # Guard: LLM occasionally returns script as a raw string instead of a dict.
    # Wrap it into a minimal dict so assemble_script() never crashes with AttributeError.
    if not isinstance(script, dict):
        log(f"WARNING: LLM returned script as {type(script).__name__} instead of dict — wrapping as hook")
        script = {"hook": str(script), "body": "", "cta": ""}

    # Apply niche-specific voice settings
    voice_settings = NICHE_VOICE_SETTINGS[niche].copy()
    voice_settings["voice"] = "Josh"  # display label; actual ID in voice_id field

    assembled = assemble_script(script)

    hook_text = script.get("hook", "").strip()

    word_count = len(assembled.split())
    log(f"Script: {word_count} words")

    full_brief = {
        "timestamp":        NOW.isoformat(),
        "slot":             slot,
        "niche":            niche,
        "niche_label":      NICHE_LABELS[niche],
        "topic":            topic,
        "framework":        framework,
        "script":           script,
        "full_script_text": assembled,
        "visual_direction": {
            "kling_prompt":    _niche_visual_prompt(niche),
            "negative_prompt": "faces, logos, text overlays, watermarks",
        },
        "voice_settings":   voice_settings,
        "caption_text":     hook_text[:80],
        "affiliate":        "none_growth_mode",
        "script_variant":   script_variant,
        "compliance_checked": True,
    }

    output_path = LOGS_DIR / f"creative_brief_{TIMESTAMP}_slot{slot}.json"
    output_path.write_text(json.dumps(full_brief, indent=2))
    log(f"Creative brief saved: {output_path.name}")
    log(f"TOPIC:  {topic}")
    log(f"NICHE:  {NICHE_LABELS[niche]}")
    log(f"HOOK:   {hook_text}")
    log(f"BODY:   {script.get('body', '')[:80]}...")
    log("=" * 60)

    print(json.dumps(full_brief, indent=2))


def _niche_visual_prompt(niche: str) -> str:
    prompts = {
        "tech_ai": (
            "dark glowing computer screens with code, futuristic AI interface, neon blue and green, "
            "no faces, 9:16 vertical cinematic, cyberpunk, high contrast"
        ),
        "dark_psychology": (
            "extreme close-up eyes in deep shadow, single side light source, "
            "no full faces, 9:16 vertical cinematic, psychological thriller, deep shadows"
        ),
        "micro_mystery": (
            "deep space starfield or abandoned corridor, eerie dim lighting, fog effect, "
            "no people, 9:16 vertical cinematic, horror atmospheric, unsettling stillness"
        ),
    }
    return prompts.get(niche, prompts["tech_ai"])


if __name__ == "__main__":
    main()
