#!/usr/bin/env python3
"""
visual_router.py — Visual Format Router

For each creative brief, decides the optimal visual format:
  flux_ken_burns  — Flux image (PiAPI ~$0.004/image) + FFmpeg Ken Burns motion
  kling_video     — PiAPI Kling AI video (~$0.13/clip × 3 = ~$0.39/video)
  dark_background — Emergency fallback only, never a first choice

Decision is content-driven, not rule-based. Factors:
  1. Topic and visual direction signals
  2. PiAPI credit headroom
  3. Brief urgency and emotional weight
  4. Recent format performance from quality_mirror

The contract: never black screen, never slop, never waste credit on a format
that doesn't serve the content. A perfect Flux+KB document frame is worth more
than a mediocre AI motion clip for pension/savings/mortgage content.
"""
from __future__ import annotations
import os, json, re, datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
LOGS_DIR = BASE_DIR / "logs"

# Format keys
FLUX_KB = "flux_ken_burns"
KLING   = "kling_video"
DARK    = "dark_background"

# PiAPI point costs (empirically confirmed 2026-03-09)
# ~35,000,000 points = $1 USD
POINTS_PER_USD   = 35_000_000
POINTS_FLUX      = 150_000    # per image → ~$0.004
POINTS_KLING_5S  = 4_600_000  # per 5s clip → ~$0.13

COST_FLUX_KB     = round(POINTS_FLUX * 3 / POINTS_PER_USD, 4)   # 3 images
COST_KLING       = round(POINTS_KLING_5S * 3 / POINTS_PER_USD, 3)  # 3 clips

# Topics where documentary Flux+KB beats AI motion
DOCUMENTARY_TOPICS = {"pension", "savings", "mortgage", "isa", "investing", "tax_efficiency", "cost_of_living", "debt", "general"}
# Topics where AI motion / abstract aesthetics are on-brand
MOTION_TOPICS = {"influencer", "crypto"}

# Visual direction text keywords → format signal
DOCUMENTARY_KEYWORDS = {
    "close-up", "document", "statement", "screen", "phone", "paper", "desk",
    "lamp", "fluorescent", "hand", "keyboard", "real", "physical", "texture",
    "ken burns", "slow zoom", "lateral drift", "pull back", "push in",
    "bank statement", "pension statement", "balance", "monitor",
}
MOTION_KEYWORDS = {
    "particles", "flowing", "data streams", "geometric", "liquid", "abstract",
    "virtual cinematography", "digital render", "void space", "self-luminous",
    "flowing data", "particle system", "data flow", "information space",
    "3d render", "cgi", "abstract motion",
}


# ---------------------------------------------------------------------------
# Credit tracking
# ---------------------------------------------------------------------------
CREDIT_FILE = LOGS_DIR / "piapi_credit.json"

def get_piapi_credit() -> float:
    """Return last known PiAPI credit in USD. Defaults to confirmed $5.04 on first run."""
    if not CREDIT_FILE.exists():
        return 5.04  # user confirmed top-up 2026-03-09
    try:
        return float(json.loads(CREDIT_FILE.read_text()).get("balance_usd", 5.04))
    except Exception:
        return 5.04

def deduct_piapi_credit(cost_usd: float):
    """Record credit deduction after a successful render."""
    balance = get_piapi_credit()
    new_balance = max(0.0, round(balance - cost_usd, 4))
    CREDIT_FILE.write_text(json.dumps({
        "balance_usd": new_balance,
        "last_deduction": cost_usd,
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }, indent=2))
    return new_balance

def set_piapi_credit(balance_usd: float):
    """Manually set credit balance (after top-up or API confirmation)."""
    CREDIT_FILE.write_text(json.dumps({
        "balance_usd": round(balance_usd, 4),
        "updated_at": datetime.datetime.utcnow().isoformat(),
    }, indent=2))


# ---------------------------------------------------------------------------
# Topic detection
# ---------------------------------------------------------------------------
def detect_topic(brief: dict) -> str:
    text = " ".join([
        brief.get("asymmetry", ""),
        brief.get("visual_direction", {}).get("frame_description", ""),
        brief.get("visual_direction", {}).get("kling_prompt", ""),
        str(brief.get("script", {})),
    ]).lower()

    if any(w in text for w in ["influenc", "follower", "pump", "dump", "social media", "creator"]):
        return "influencer"
    if any(w in text for w in ["pension", "retirement", "defined benefit", "annuity"]):
        return "pension"
    if any(w in text for w in ["mortgage", "rate fix", "svr", "lender", "homeowner"]):
        return "mortgage"
    if any(w in text for w in ["isa", "allowance", "wrapper", "tax-free"]):
        return "isa"
    if any(w in text for w in ["crypto", "bitcoin", "token", "coin", "blockchain"]):
        return "crypto"
    if any(w in text for w in ["hmrc", "self-assessment", "tax relief", "tax return", "tax code", "paye", "tax efficiency"]):
        return "tax_efficiency"
    if any(w in text for w in ["debt", "credit card", "final demand", "overdraft", "minimum payment", "loan"]):
        return "debt"
    if any(w in text for w in ["cost of living", "utility bill", "energy bill", "grocery", "supermarket", "inflation", "household"]):
        return "cost_of_living"
    if any(w in text for w in ["invest", "portfolio", "fund", "etf", "stock", "shares", "dividend", "broker"]):
        return "investing"
    if any(w in text for w in ["savings", "interest", "base rate", "deposit", "account"]):
        return "savings"
    return "general"


# ---------------------------------------------------------------------------
# Visual direction signal analysis
# ---------------------------------------------------------------------------
def _score_visual_signals(brief: dict) -> tuple[int, int]:
    """Returns (flux_signal 0-40, kling_signal 0-40) from visual direction text."""
    vd = brief.get("visual_direction", {})
    text = " ".join([
        vd.get("frame_description", ""),
        vd.get("motion", ""),
        vd.get("kling_prompt", ""),
        vd.get("light_source", ""),
    ]).lower()

    flux_signal = sum(4 for kw in DOCUMENTARY_KEYWORDS if kw in text)
    kling_signal = sum(4 for kw in MOTION_KEYWORDS if kw in text)
    return min(flux_signal, 40), min(kling_signal, 40)


# ---------------------------------------------------------------------------
# Performance data
# ---------------------------------------------------------------------------
def _get_format_performance() -> dict:
    """
    Read format performance from quality_mirror logs.
    Returns {format: relative_score} — higher = better.
    Defaults to parity until data exists.
    """
    perf = {FLUX_KB: 50.0, KLING: 50.0}

    # If Harbinger's AI content scores low on "looks real" test, favour Flux+KB
    vl_file = LOGS_DIR / "visual_language_learnings.json"
    if vl_file.exists():
        try:
            vl = json.loads(vl_file.read_text())
            harbinger_score = vl.get("mean_harbinger_captured_score", 50)
            if harbinger_score < 65:
                bonus = (65 - harbinger_score) * 0.5
                perf[FLUX_KB] += bonus
        except Exception:
            pass

    return perf


# ---------------------------------------------------------------------------
# Ken Burns motion selector — emotion-derived, not applied
# ---------------------------------------------------------------------------
def select_ken_burns(vd: dict, brief: dict = None) -> dict:
    """
    Derive Ken Burns motion from the emotional content of the brief.
    Motion is chosen from content, not applied as a default.

    Emotional motion taxonomy — each style has a reason:
      push_in_threat   — frame narrows as deadline/loss approaches. Anxiety escalates.
      pull_back_reveal — world expands as the mechanism is exposed. Understanding arrives.
      drift_scan       — camera reads the scene left-to-right. Investigation in progress.
      micro_drift      — near-static with imperceptible drift. Authority. Certainty.
      push_in_resolve  — decisive push after resolution found. Action energy. Forward.
    """
    if brief:
        topic    = detect_topic(brief)
        urgency  = brief.get("urgency_score", 50)
        asym     = brief.get("asymmetry", "").lower()
        script   = brief.get("script", {})
        intrusion = (script.get("intrusion") or "").lower()

        # Threat: deadline / loss / expiry / money at risk → frame narrows
        threat_words = {"expire", "expires", "deadline", "lost", "gone", "permanently",
                        "never recover", "risk", "banned", "forfeited", "miss", "closes"}
        is_threat = any(w in asym or w in intrusion for w in threat_words) or urgency > 65

        # Revelation: hidden mechanism being exposed → world expands
        reveal_words = {"mechanism", "hidden", "how", "behind", "exposes", "reveals",
                        "most people", "never told", "deliberately", "structural", "invisible"}
        is_reveal = any(w in asym for w in reveal_words) and not is_threat

        # Authority: official data, HMRC, statistics → solid, near-static
        authority_words = {"hmrc", "fca", "bank of england", "ons", "statistics", "official",
                           "data shows", "percent", "billion", "audit", "hmrc confirmed"}
        is_authority = any(w in asym for w in authority_words) and not is_threat and not is_reveal

        if is_threat:
            style = "push_in_threat"
        elif is_reveal:
            style = "pull_back_reveal"
        elif is_authority:
            style = "micro_drift"
        elif topic in {"crypto", "influencer"}:
            style = "push_in_resolve"
        else:
            style = "drift_scan"
    else:
        # No brief: read visual direction text as weak signal
        motion_text = vd.get("motion", "").lower()
        if any(w in motion_text for w in ["push in", "zoom in", "tighten"]):
            style = "push_in_threat"
        elif any(w in motion_text for w in ["pull back", "zoom out", "reveal"]):
            style = "pull_back_reveal"
        elif any(w in motion_text for w in ["lateral", "drift"]):
            style = "drift_scan"
        elif any(w in motion_text for w in ["static", "authority", "data"]):
            style = "micro_drift"
        else:
            style = "push_in_threat"

    presets = {
        "push_in_threat": {
            "z_expr": "zoom+0.0020",           # slightly faster than default — urgency
            "x_expr": "iw/2-(iw/zoom/2)",
            "y_expr": "ih/2-(ih/zoom/2)",
            "desc":   "slow push in — frame narrows, mirrors deadline pressure. Chosen for: urgency/threat.",
        },
        "pull_back_reveal": {
            "z_expr": "if(eq(on,1),1.24,zoom-0.0016)",  # start tight, pull back to reveal
            "x_expr": "iw/2-(iw/zoom/2)",
            "y_expr": "ih/2-(ih/zoom/2)",
            "desc":   "pull back from tight — reveals context, mechanism made visible. Chosen for: revelation.",
        },
        "drift_scan": {
            "z_expr": "1.10",
            "x_expr": "iw*0.07*(on/125)",     # lateral drift reads the scene
            "y_expr": "ih/2-(ih/zoom/2)",
            "desc":   "lateral drift — camera reads the scene, investigative. Chosen for: mechanism/investigation.",
        },
        "micro_drift": {
            "z_expr": "1.03",
            "x_expr": "iw*0.015*(on/125)+iw/2-(iw/zoom/2)",  # barely perceptible
            "y_expr": "ih/2-(ih/zoom/2)",
            "desc":   "near-static micro drift — authority, data certainty, institutional weight. Chosen for: authority.",
        },
        "push_in_resolve": {
            "z_expr": "zoom+0.0024",           # decisive, committed
            "x_expr": "iw/2-(iw/zoom/2)",
            "y_expr": "ih/2-(ih/zoom/2)",
            "desc":   "decisive push — momentum, resolution, action energy. Chosen for: CTA/crypto/energy content.",
        },
    }
    return {"style": style, **presets[style]}


# ---------------------------------------------------------------------------
# Flux prompt builder — stop-scroll documentary photography
# ---------------------------------------------------------------------------

# Appended to every scene. Locks Flux 1.1 Pro Ultra into photographic distribution.
# Embedded positively because Pro Ultra does not accept negative prompts via Replicate.
REALISM_LOCK = (
    "shot on film, visible grain in the shadows, natural colour cast, "
    "no AI glow, no studio lighting, no smooth plastic surfaces, "
    "no symmetrical composition, no white backgrounds, no text overlays, "
    "no watermarks, no smiling faces, no corporate attire, "
    "photojournalism, reportage documentary"
)

def build_flux_prompt(brief: dict, topic: str, vd: dict) -> dict:
    """
    Build a Flux 1.1 Pro Ultra prompt that stops the scroll.

    Research-derived principles (T5-XXL encoder, raw=True):
    1. Complete sentences — T5-XXL reads language, not keyword bags.
    2. Specific physical object + specific domestic location.
    3. Directional light SOURCE, not quality ("from a sash window to the left").
    4. Camera + lens + film stock triplet (activates photographic training distribution).
    5. ONE deliberate imperfection per scene (creased corner, coffee ring, chewed biro).
    6. Partial human presence — forearms, shadows — never faces (avoids uncanny valley).
    7. Photojournalism label at end unlocks editorial training distribution.
    8. REALISM_LOCK appended to every scene — avoidances embedded positively.
    """
    asym      = brief.get("asymmetry", "")
    script    = brief.get("script", {})
    intrusion = script.get("intrusion", "")
    urgency   = brief.get("urgency_score", 50)

    # Extract money values — ground the image in a specific, legible reality
    money_vals = re.findall(
        r'£[\d,]+(?:\.\d+)?(?:bn|m|k)?|€[\d,]+|\$[\d,]+|[\d,.]+\s*%|[\d,.]+\s*(?:billion|million|trillion)',
        (asym + " " + intrusion), re.I
    )
    money_str = money_vals[0].strip() if money_vals else ""

    # Visual direction from brief — used to refine scene if specific
    vd_frame  = vd.get("frame_description", "").lower()
    vd_light  = vd.get("light_source", "")
    is_extreme_closeup = "extreme close" in vd_frame or "close-up" in vd_frame

    # ── Topic-specific scenes ────────────────────────────────────────────────
    # SCROLL-STOP PRINCIPLE: the money value IS the frame.
    # Not annotated in a column. Not circled at the edge. The dominant readable
    # element — legible at arm's length on a phone within 0.5 seconds.
    # For documents: number is printed large or written large as the centrepiece.
    # For phone screens: the balance/payment fills the display in large numerals.
    # Two variants per topic — rotated by brief hash for visual variety.
    SCENES: dict[str, list[str]] = {
        "isa": [
            # Scene A: unused allowance as the dominant visual element
            f"Extreme close-up of a handwritten note on lined paper — "
            f"{'the figure ' + money_str + ' written in large bold ballpoint strokes, underlined twice, filling the centre of the frame' if money_str else 'the words UNUSED ALLOWANCE written in large bold ballpoint strokes, filling the centre of the frame'}, "
            f"below it: APRIL 5 — circled in red, "
            f"the paper lies flat on a pale pine kitchen table, "
            f"soft grey morning light from a sash window to the left casting a diagonal shadow across the page, "
            f"one corner of the paper slightly folded, a cold mug of tea out of focus at the frame edge, "
            f"Canon EOS 5D Mark III, 85mm f/1.8, Kodak Portra 400 colour rendering, "
            f"documentary photography, shallow depth of field, film grain in shadows, no faces",

            # Scene B: banking app — allowance figure dominant on screen
            f"A banking app open on a phone screen, "
            f"{'the figure ' + money_str + ' displayed in large white numerals at the top of the screen — the ISA allowance remaining this tax year — filling a third of the visible display' if money_str else 'the ISA allowance remaining displayed in large white numerals filling a third of the visible display'}, "
            f"below the figure the label TAX-FREE ALLOWANCE REMAINING in small grey text, "
            f"phone held loosely in one hand in a dim room, LED screen glow the only light source, "
            f"screen slightly smudged, a hairline crack at the lower corner, dark background, "
            f"Sony A7 III, 35mm f/2.0, Fujifilm Superia 400 grain rendering, "
            f"reportage photography, no faces, muted blue-grey tones",
        ],
        "mortgage": [
            # Scene A: monthly payment as the dominant element
            f"Extreme close-up of a mortgage renewal letter — "
            f"{'the monthly payment figure ' + money_str + ' written in large bold ballpoint across the centre of the page, dwarfing the surrounding printed small print' if money_str else 'the monthly payment figure written in large bold ballpoint across the centre of the page, dwarfing the surrounding printed small print'}, "
            f"below it a handwritten calculation: OLD vs NEW, "
            f"cool fluorescent overhead light at 4200K casting slight greenish shadows on the paper, "
            f"the opened envelope lying beside it with a visible postmark, a ballpoint pen resting across the page, "
            f"Canon EOS R5, 85mm f/2.0, Kodak Vision3 500T colour rendering, "
            f"reportage photography, no faces, shallow depth of field",

            # Scene B: mortgage app — monthly payment dominant on screen
            f"A mortgage provider app open on a phone screen, "
            f"{'the next monthly payment ' + money_str + ' displayed in large bold numerals dominating the top half of the screen' if money_str else 'the next monthly payment displayed in large bold numerals dominating the top half of the screen'}, "
            f"below it the label NEXT PAYMENT DUE and a date in smaller text, "
            f"phone resting on a dark kitchen worktop, a single overhead LED lamp, cool 4000K light, "
            f"screen slightly smudged, notification bar showing unread messages, "
            f"Sony A7 IV, 35mm f/2.0, Kodak Vision3 250D colour rendering, "
            f"documentary photography, no faces, muted cool tones",
        ],
        "pension": [
            # Scene A: projected pot value as the centrepiece
            f"Extreme close-up of a pension annual statement — "
            f"{'the projected retirement income ' + money_str + ' in large bold print filling a third of the visible page, highlighted in yellow marker' if money_str else 'the projected retirement income in large bold print filling a third of the visible page, highlighted in yellow marker'}, "
            f"below it a small note in blue ballpoint: NOT ENOUGH, "
            f"overcast north light from a window, flat and slightly grey, "
            f"a forearm in its sixties partially in frame at the left edge, reading glasses resting beside the document, "
            f"one page corner bent back, the opened envelope nearby, "
            f"Canon EOS 5D Mark IV, 85mm f/1.8, Kodak Portra 400, "
            f"long-form editorial photography, no faces, film grain",

            # Scene B: pension app — pot value dominant on screen
            f"A pension provider app open on a phone screen, "
            f"{'the current pot value ' + money_str + ' in large white numerals filling the top half of the display' if money_str else 'the current pot value in large white numerals filling the top half of the display'}, "
            f"below it two smaller lines: PROJECTED AT 67 and a lower figure, "
            f"phone held in one hand, dim room, screen glow the only light source, "
            f"slight reflection of a window in the screen surface, dark background, "
            f"Leica M10-R, 50mm f/2.0, natural grain, "
            f"reportage documentary photography, no faces, cool blue tones",
        ],
        "savings": [
            # Scene A: interest rate vs actual rate as the dominant element
            f"Extreme close-up of a printed savings account statement — "
            f"{'the interest rate figure ' + money_str + ' in the small print at the top of the page, circled in thick red ballpoint so it dominates the frame' if money_str else 'the interest rate in the small print at the top, circled in thick red ballpoint so it dominates the frame'}, "
            f"beside the circle a handwritten note in capital letters: BETTER RATE EXISTS, "
            f"warm desk lamp at 2800K pooling amber light on the document, edges in shadow, "
            f"a biro with a chewed lid resting across the page, paper creased from the envelope, "
            f"Sony A7 IV, 85mm f/1.8, Kodak Portra 400, "
            f"documentary photography, no faces, warm amber tones, shallow depth of field",

            # Scene B: banking app — savings balance and rate both readable
            f"A banking app open on a phone screen, "
            f"{'the savings balance ' + money_str + ' in large white numerals at the top of the screen' if money_str else 'the savings balance in large white numerals at the top of the screen'}, "
            f"below it the annual interest rate in smaller text, the figure conspicuously low, "
            f"phone lying on a pale kitchen counter, ambient daylight from a window to the left, "
            f"a charger cable partially in frame, screen slightly smudged with fingerprints, "
            f"Canon EOS R6, 35mm f/2.0, Fujifilm Classic Chrome rendering, "
            f"reportage photography, no faces, muted daylight tones",
        ],
        "crypto": [
            # Scene A: P&L figure dominant on a trading terminal
            f"Extreme close-up of a trading terminal — "
            f"{'the P&L figure ' + money_str + ' in large numerals dominating the centre of the screen, red colouring indicating a loss' if money_str else 'a large red P&L figure dominating the centre of the screen'}, "
            f"candlestick chart visible below it, small and secondary, "
            f"two monitors glowing in a dark room, monitor glow the only light source, "
            f"forearms resting on a mechanical keyboard, an empty energy drink can at the desk edge, "
            f"Sony Venice, 35mm, Kodak Vision3 200T, cinematic grain, "
            f"documentary photography, no faces, muted blue-green tones",
        ],
        "influencer": [
            # Scene A: revenue figure dominant on an analytics dashboard
            f"Extreme close-up of a laptop screen showing a social media analytics dashboard — "
            f"{'the revenue figure ' + money_str + ' in large bold numerals at the top of the dashboard, the primary readable element in frame' if money_str else 'a revenue figure in large bold numerals at the top of the dashboard, the primary readable element in frame'}, "
            f"below it an engagement graph in steep decline, small and secondary, "
            f"blue-white screen glow the only light source in a dark room, "
            f"a hand partially visible at the frame edge, forearm only, curtains drawn, "
            f"Canon EOS R5, 85mm f/1.8, natural colour, available light photography, "
            f"documentary editorial photography, no faces",
        ],
        "investing": [
            # Scene A: total return figure as the centrepiece
            f"Extreme close-up of a printed investment portfolio statement — "
            f"{'the total return figure ' + money_str + ' highlighted in yellow marker, printed in large bold type filling a third of the visible page' if money_str else 'the total return figure highlighted in yellow marker, printed in large bold type filling a third of the visible page'}, "
            f"beside it in blue ballpoint: MINUS FEES, "
            f"warm desk lamp at 2700K pooling amber light on the document, deep shadow at the edges, "
            f"a biro with a chewed lid resting across the page, one corner dog-eared, "
            f"Leica M10-R, 85mm f/2.0, Kodak Portra 800, "
            f"documentary editorial photography, no faces, shallow depth of field",

            # Scene B: brokerage app — portfolio value dominant on screen
            f"A brokerage app open on a phone screen, "
            f"{'the total portfolio value ' + money_str + ' in large white numerals dominating the top half of the display' if money_str else 'the total portfolio value in large white numerals dominating the top half of the display'}, "
            f"below it TOTAL FEES PAID THIS YEAR and a figure in smaller red text, "
            f"phone resting on a pale kitchen counter, morning daylight from a window to the left, "
            f"screen slightly smudged, notification bar visible at top, cold cup of coffee at the frame edge, "
            f"Canon EOS R6, 35mm f/2.0, Fujifilm Classic Chrome rendering, "
            f"reportage photography, no faces, muted daylight tones",
        ],
        "tax_efficiency": [
            # Scene A: overpaid tax figure as the dominant element
            f"Extreme close-up of an HMRC tax calculation notice — "
            f"{'the figure ' + money_str + ' printed in large bold type in the centre of the page — the amount overpaid — underlined twice in blue ballpoint' if money_str else 'the overpaid tax amount printed in large bold type in the centre of the page, underlined twice in blue ballpoint'}, "
            f"beside it a handwritten note: SHOULD HAVE CLAIMED, "
            f"cool grey daylight from a north-facing sash window, flat and overcast, "
            f"a calculator beside the letter showing a recent calculation, "
            f"the HMRC envelope open nearby, fold lines still visible in the paper, "
            f"Sony A7 III, 85mm f/1.8, Kodak Portra 400, "
            f"documentary photography, no faces, muted cool tones, shallow depth of field",

            # Scene B: HMRC app — tax owed or refund dominant on screen
            f"The HMRC app open on a phone screen, "
            f"{'the figure ' + money_str + ' displayed in large numerals at the top of the screen — the tax owed or refund due — the dominant readable element in frame' if money_str else 'the tax owed displayed in large numerals at the top of the screen — the dominant readable element in frame'}, "
            f"below it TAX YEAR 2025-26 and a smaller status line, "
            f"phone resting on a pale desk, diffuse daylight from a window behind, "
            f"a pen and a paper form partially in frame beside the phone, "
            f"Leica SL2, 50mm f/1.4, natural grain, overcast ambient, "
            f"reportage documentary photography, no faces",
        ],
        "cost_of_living": [
            # Scene A: annual cost figure as the centrepiece
            f"Extreme close-up of a household utility bill — "
            f"{'the annual cost ' + money_str + ' in the largest print on the page, circled in thick red ballpoint so it fills a third of the frame' if money_str else 'the annual cost figure in the largest print on the page, circled in thick red ballpoint so it fills a third of the frame'}, "
            f"below it DIRECT DEBIT AMOUNT in smaller text, "
            f"cool fluorescent kitchen light overhead at 4200K, slight greenish cast, "
            f"the bill creased where it was folded into thirds, a food item at the frame edge, "
            f"Canon EOS 5D Mark III, 85mm f/1.8, Kodak Vision3 250D colour rendering, "
            f"documentary editorial photography, no faces, muted tones",

            # Scene B: supermarket receipt — total as the centrepiece
            f"Extreme close-up of the bottom of a supermarket receipt — "
            f"{'the total ' + money_str + ' in the largest text on the slip, filling the top half of the frame' if money_str else 'the TOTAL figure in the largest text on the slip, filling the top half of the frame'}, "
            f"above it ITEMS: and a count, small and secondary, "
            f"receipt lying on a pale laminate kitchen counter, soft diffuse daylight from a window behind, "
            f"a set of keys partially overlapping the top edge, "
            f"Fujifilm GFX50S, 63mm f/2.8, Fujifilm Superia 400 grain rendering, "
            f"reportage photography, no faces, flat muted daylight tones",
        ],
        "debt": [
            # Scene A: amount outstanding as the dominant element
            f"Extreme close-up of a final demand letter — "
            f"{'the figure ' + money_str + ' — AMOUNT OUTSTANDING — in the largest print on the page, filling a third of the frame, printed in bold' if money_str else 'the AMOUNT OUTSTANDING figure in the largest print on the page, filling a third of the frame, printed in bold'}, "
            f"below it FINAL NOTICE in red text, small and secondary, "
            f"pale cold light from a frosted glass front door, slightly blue, "
            f"two other unopened envelopes visible beneath it on a worn hallway doormat, "
            f"Sony A7 IV, 85mm f/2.0, Kodak Portra 400 colour rendering, "
            f"documentary photography, no faces, muted cold tones, shallow depth of field",

            # Scene B: banking app — debt balance dominant on screen
            f"A banking app open on a phone screen, "
            f"{'the credit card balance ' + money_str + ' in large red numerals dominating the top half of the display — the outstanding debt — the primary readable element in frame' if money_str else 'the outstanding credit card balance in large red numerals dominating the top half of the display — the primary readable element in frame'}, "
            f"below it MINIMUM PAYMENT DUE and a much smaller figure in grey text, "
            f"phone resting on a dark wooden desk, warm tungsten lamp from the right, amber light, "
            f"screen slightly smudged, a calculator beside the phone with a figure still displayed, "
            f"Canon EOS R5, 50mm f/1.8, Kodak Vision3 500T, "
            f"long-form editorial photography, no faces, muted warm tones",
        ],
        "general": [
            # Versatile documentary finance scene — number as centrepiece
            f"Extreme close-up of a financial document on a worn wooden desk — "
            f"{'the figure ' + money_str + ' circled in thick red ballpoint, the largest readable element in the frame' if money_str else 'a figure circled in thick red ballpoint, the largest readable element in the frame'}, "
            f"below it a handwritten note: CHECK THIS, "
            f"warm desk lamp at 2800K pooling light on the document, edges of frame in deep shadow, "
            f"a ballpoint pen resting across the page, one corner slightly folded, "
            f"a secondary document partially visible at the frame edge, "
            f"Canon EOS 5D Mark III, 85mm f/1.8, Kodak Portra 400, "
            f"documentary editorial photography, no faces, muted tones, shallow depth of field",
        ],
    }

    scenes = SCENES.get(topic, SCENES["general"])
    brief_hash = abs(hash(asym)) % len(scenes)
    chosen = scenes[brief_hash]

    # Add extreme close-up modifier if brief calls for it
    if is_extreme_closeup:
        chosen = "Extreme close-up framing, document fills the frame. " + chosen

    # Add urgency signal to motion/atmosphere
    if urgency >= 70:
        chosen += (
            ", the scene has an edge of urgency — something unresolved is visible in the composition"
        )

    # Append REALISM_LOCK — embeds all avoidances positively (Pro Ultra has no negative prompt)
    chosen = chosen.rstrip(", ") + ", " + REALISM_LOCK

    return {
        "positive": chosen[:1500],
        "negative": "",   # avoidances embedded in positive; Pro Ultra ignores this field
        "raw": True,      # disable BFL post-processing for maximum photorealism
    }


# ---------------------------------------------------------------------------
# Core routing function
# ---------------------------------------------------------------------------
def route(brief: dict, credit_override: float | None = None) -> dict:
    """
    Route a creative brief to optimal visual format.

    Returns:
      format: "flux_ken_burns" | "kling_video" | "dark_background"
      topic: detected topic
      flux_score: int
      kling_score: int
      rationale: str
      estimated_cost_usd: float
      [if flux_ken_burns]  flux_prompt: dict, ken_burns: dict
      [if kling_video]     kling_model: str
    """
    topic     = detect_topic(brief)
    urgency   = brief.get("urgency_score", 50)
    vd        = brief.get("visual_direction", {})
    credit    = credit_override if credit_override is not None else get_piapi_credit()
    flux_vis, kling_vis = _score_visual_signals(brief)
    perf      = _get_format_performance()

    # ── Score Flux+KB ────────────────────────────────────────────────────────
    flux_score  = 0
    flux_reasons = []

    if topic in DOCUMENTARY_TOPICS:
        flux_score += 35
        flux_reasons.append(f"{topic}: documentary close-up is more credible than AI motion for this content")

    if flux_vis > kling_vis:
        flux_score += flux_vis
        flux_reasons.append(f"visual direction describes physical scene ({flux_vis}pt signal)")

    if credit < 2.00:
        flux_score += 25
        flux_reasons.append(f"PiAPI ${credit:.2f} — conserving credit for high-urgency briefs")

    if urgency < 58:
        flux_score += 15
        flux_reasons.append(f"urgency {urgency} — methodical documentary pace appropriate")

    if perf[FLUX_KB] > perf[KLING]:
        d = round(perf[FLUX_KB] - perf[KLING], 1)
        flux_score += int(d)
        flux_reasons.append(f"Flux KB outperforming Kling by {d}pts on captured_score")

    # ── Score Kling ──────────────────────────────────────────────────────────
    kling_score  = 0
    kling_reasons = []

    if topic in MOTION_TOPICS:
        kling_score += 35
        kling_reasons.append(f"{topic}: abstract AI motion is the brand aesthetic for this content")

    if kling_vis >= flux_vis:
        kling_score += kling_vis
        kling_reasons.append(f"visual direction is abstract/motion ({kling_vis}pt signal)")

    if credit >= 1.50:
        kling_score += 20
        kling_reasons.append(f"PiAPI ${credit:.2f} — sufficient credit for video render")

    if urgency >= 62:
        kling_score += 20
        kling_reasons.append(f"urgency {urgency} — high energy matches AI motion")

    if perf[KLING] > perf[FLUX_KB]:
        d = round(perf[KLING] - perf[FLUX_KB], 1)
        kling_score += int(d)
        kling_reasons.append(f"Kling outperforming Flux KB by {d}pts")

    # ── Hard override: insufficient credit ──────────────────────────────────
    if credit < 0.40:
        return {
            "format": FLUX_KB,
            "topic": topic,
            "flux_score": 999,
            "kling_score": 0,
            "rationale": f"CREDIT OVERRIDE: PiAPI ${credit:.2f} — forced Flux+KB",
            "estimated_cost_usd": COST_FLUX_KB,
            "flux_prompt": build_flux_prompt(brief, topic, vd),
            "ken_burns": select_ken_burns(vd, brief),
        }

    # ── Decision ─────────────────────────────────────────────────────────────
    # Kling is disabled: credits are low and Flux+KB is the only active provider.
    # Kling was $0.39/render vs $0.012 for Flux — too expensive when credits are scarce.
    _rationale = "; ".join(flux_reasons[:3]) if flux_reasons else f"Flux+KB forced (kling disabled); scored flux={flux_score} kling={kling_score}"
    return {
        "format": FLUX_KB,
        "topic": topic,
        "flux_score": flux_score,
        "kling_score": kling_score,
        "rationale": _rationale,
        "estimated_cost_usd": COST_FLUX_KB,
        "flux_prompt": build_flux_prompt(brief, topic, vd),
        "ken_burns": select_ken_burns(vd, brief),
    }


if __name__ == "__main__":
    # Quick test against today's slot 2 brief
    import sys
    briefs = sorted(LOGS_DIR.glob("creative_brief_*_slot2.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if briefs:
        brief = json.loads(briefs[0].read_text())
        result = route(brief)
        print(json.dumps(result, indent=2))
    else:
        print("No slot 2 brief found")
