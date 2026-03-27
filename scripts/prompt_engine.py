#!/usr/bin/env python3
"""
prompt_engine.py — Harbinger Visual Prompt Engine

Turns a creative brief into a 7-shot Kling 3.0 shot list with individual
clip prompts built from the 8-layer cinematographic scaffold. Every decision
recorded in the production manifest. quality_mirror evolves the genome nightly.

Architecture:
  PromptGenome     — the evolving library of prompt components, fitness-scored
  ShotList         — maps the brief's emotional arc to shot types
  ClipPromptBuilder — assembles 8-layer prompts from genome + brief
  VisualDNA        — the cross-clip continuity anchor locked per video
  self_improve()   — evolution loop called by quality_mirror every 6h

The 8 prompt layers (from 2026 professional cinematography research):
  1. Subject       — who/what, exact physical descriptors
  2. Action        — what happens, emotional energy
  3. Optics        — focal length, aperture, lens type
  4. Motion        — camera movement, speed, character
  5. Lighting      — source, direction, colour temperature, softness
  6. Film Stock    — grain, colour science, codec tokens
  7. Audio         — implied Foley, room tone (Kling reads this for mood)
  8. Continuity    — Visual DNA anchor, seed lock, temporal consistency flag

Usage:
  python3 prompt_engine.py --brief <creative_brief.json>
                           --output <shot_list.json>
                           [--evolve] [--dry-run]
"""
from __future__ import annotations
import os, sys, json, math, random, datetime, hashlib, re, argparse
from pathlib import Path
from copy import deepcopy

BASE_DIR  = Path(__file__).parent.parent
LOGS_DIR  = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)
GENOME_FILE = LOGS_DIR / "prompt_genome.json"
NOW = datetime.datetime.utcnow()


# ═══════════════════════════════════════════════════════════════════════════
# AESTHETIC PROFILES
# Ten complete visual worlds. Each is a total ontology — not just colour
# grading but the relationship between camera and subject, what continuity
# means, what physics are allowed, what rendering paradigm is in play.
#
# brief_fit:  signal_name → importance weight for brief-signal matching
# shot_fit:   shot_type → prior score (this aesthetic's natural fit per shot)
# overrides_camera: True = aesthetic fully specifies camera/motion/film layers,
#                   genome is not consulted for those (abstract/degraded/surreal)
# ═══════════════════════════════════════════════════════════════════════════

AESTHETIC_PROFILES = {

    "cinematic_dark": {
        "label": "Cinematic Institutional",
        "overrides_camera": False,
        "brief_fit": {
            "urgency_mid": 0.6, "topic_finance": 0.9, "tone_revelation": 0.8,
            "tone_weight": 0.7, "urgency_high": 0.4, "tone_authority": 0.7,
        },
        "shot_fit": {
            "HOOK": 0.7, "WEIGHT": 0.9, "INTRUSION": 0.9,
            "MECHANISM": 0.8, "PROOF": 0.8, "STAT_REVEAL": 0.7, "MOVE": 0.6,
        },
        "world": "glass-and-steel financial building interior, late evening, city grid visible through floor-to-ceiling windows",
        "camera_system": "ARRI Alexa 35, anamorphic Panavision Primo lenses, LOG-C3 science",
        "lighting_primary": "warm overhead practical 2800K, chiaroscuro ratio 5:1, deep shadows preserved",
        "lighting_secondary": "cool monitor glow blue fill from below-right",
        "film": "Kodak Vision3 500T pushed one stop — fine grain, lifted blacks, cinematic toe curve",
        "motion_character": "every movement intentional, deliberate, controlled — slow to very slow",
        "continuity_logic": "warm practical 2800K overhead + evening city light + ARRI colour science",
        "negative_additions": "outdoor sunlight, cheerful colour, handheld jitter, warm daylight colour balance",
    },

    "photorealistic_documentary": {
        "label": "Documentary Observation",
        "overrides_camera": False,
        "brief_fit": {
            "urgency_low": 0.7, "tone_investigative": 0.9, "topic_savings": 0.7,
            "topic_mortgage": 0.6, "tone_relatable": 0.8, "tone_evidence": 0.7,
        },
        "shot_fit": {
            "HOOK": 0.5, "WEIGHT": 0.9, "INTRUSION": 0.7,
            "MECHANISM": 0.8, "PROOF": 0.9, "STAT_REVEAL": 0.5, "MOVE": 0.9,
        },
        "world": "real uncontrolled environments — kitchens, high streets, offices, banks — observed without staging",
        "camera_system": "Sony Venice 2, Sigma Art 35mm f/1.4, S-Cinetone natural colour science",
        "lighting_primary": "available light only — window light, existing overhead fluorescent, practical lamps",
        "lighting_secondary": "none added — real-world secondary sources only",
        "film": "Kodak Portra 400 emulation — natural tones, minimal processing, honest colour, no pushed grain",
        "motion_character": "handheld observation — camera is witness, not director, human breath visible in movement",
        "continuity_logic": "colour temperature + ambient light level + handheld energy consistency",
        "negative_additions": "artificial staging, dramatic lighting rigs, studio environments, posed subjects, cinematic grade",
    },

    "hyperreal_macro": {
        "label": "Hyperreal Macro",
        "overrides_camera": False,
        "brief_fit": {
            "topic_finance": 0.7, "tone_evidence": 0.9, "urgency_high": 0.5,
            "tone_revelation": 0.8, "tone_authority": 0.6,
        },
        "shot_fit": {
            "HOOK": 0.8, "WEIGHT": 0.3, "INTRUSION": 0.6,
            "MECHANISM": 0.5, "PROOF": 1.0, "STAT_REVEAL": 0.9, "MOVE": 0.2,
        },
        "world": "extreme close-up of physical objects — paper fibres, metal grain, ink, fabric weave, glass surface — texture becomes landscape",
        "camera_system": "Canon EF 100mm L macro, bellows extension, f/4-f/8, extreme magnification ratio",
        "lighting_primary": "single raking side light at 10° angle — surface texture revealed as topography",
        "lighting_secondary": "ring flash fill at quarter power — depth preserved",
        "film": "Fujichrome Velvia 100 — ultra-saturation, microscopic grain, hyper-sharp, colour density",
        "motion_character": "micro-dolly or macro rail — movement measured in millimetres over seconds",
        "continuity_logic": "subject material + raking light angle + magnification scale consistency",
        "negative_additions": "wide establishing shots, human faces, architecture, depth of field, environmental context",
    },

    "abstract_motion": {
        "label": "Abstract Data Motion",
        "overrides_camera": True,
        "brief_fit": {
            "topic_crypto": 0.9, "topic_finance": 0.5, "tone_mechanism": 0.9,
            "urgency_high": 0.7, "tone_revelation": 0.7, "tone_data": 0.9,
        },
        "shot_fit": {
            "HOOK": 0.9, "WEIGHT": 0.2, "INTRUSION": 0.8,
            "MECHANISM": 1.0, "PROOF": 0.4, "STAT_REVEAL": 0.9, "MOVE": 0.7,
        },
        "world": "pure motion and light — flowing data particles, geometric transformations, liquid dynamics, no physical environment",
        "camera_system": "virtual cinematography — camera moves through information space, physically-based light simulation",
        "lighting_primary": "self-illuminating elements — light emanates from data streams themselves",
        "lighting_secondary": "void space with singular distant light sources at opposite poles",
        "film": "8K clean digital render — no grain, perfect deep blacks, HDR colour volume, self-luminous elements",
        "motion_character": "particles stream toward viewer, data flows directionally with controlled urgency, system alive",
        "continuity_logic": "colour palette of data streams + particle system coherence + directional flow consistency",
        "negative_additions": "people, realistic physical environments, film grain, natural materials, architectural space",
    },

    "surreal_temporal": {
        "label": "Surreal Temporal",
        "overrides_camera": True,
        "brief_fit": {
            "tone_surprise": 0.9, "urgency_extreme": 0.9, "urgency_high": 0.6,
            "tone_revelation": 0.8, "topic_crypto": 0.5, "tone_injustice": 0.7,
        },
        "shot_fit": {
            "HOOK": 1.0, "WEIGHT": 0.4, "INTRUSION": 0.9,
            "MECHANISM": 0.7, "PROOF": 0.2, "STAT_REVEAL": 0.8, "MOVE": 0.5,
        },
        "world": "environments that defy physics — reversed causality, impossible scale, dreamscape logic, events out of sequence",
        "camera_system": "Phantom Flex 4K at extreme frame rates, perspective geometry deliberately broken",
        "lighting_primary": "golden hour frozen in time — light that doesn't change as camera moves",
        "lighting_secondary": "shadow without source, light without origin — physically incorrect but emotionally right",
        "film": "Fuji Superia 800 pushed — colour shifts during motion, temporal colour temperature drift, blur as intent",
        "motion_character": "time dilation — events appear slow-motion, gravity optional, camera defies physical space",
        "continuity_logic": "emotional register + colour temperature + temporal distortion level consistency",
        "negative_additions": "normal time perception, consistent physics, conventional camera logic, photorealistic accuracy, static objects",
    },

    "cold_clinical": {
        "label": "Cold Clinical",
        "overrides_camera": False,
        "brief_fit": {
            "tone_evidence": 0.9, "tone_authority": 0.8, "topic_finance": 0.6,
            "urgency_mid": 0.5, "tone_weight": 0.5, "tone_investigative": 0.7,
        },
        "shot_fit": {
            "HOOK": 0.5, "WEIGHT": 0.6, "INTRUSION": 0.7,
            "MECHANISM": 0.7, "PROOF": 1.0, "STAT_REVEAL": 0.9, "MOVE": 0.4,
        },
        "world": "sterile white or pale grey environments — examination rooms, clean surfaces, clinical precision over warmth",
        "camera_system": "Leica SL2, APO-Summicron 50mm f/2 ASPH, ultra-clinical resolution, no aberration",
        "lighting_primary": "overhead fluorescent 6500K — perfectly diffused, shadowless, flat institutional",
        "lighting_secondary": "cold white fill panels — no warmth permitted anywhere in frame",
        "film": "clean digital — zero grain, daylight balanced, clinical saturation 0.75 — truth over emotion",
        "motion_character": "surgical precision — no unnecessary movement, slow clinical dolly, human element removed",
        "continuity_logic": "colour temperature 6500K + shadow absence + surface sterility consistency",
        "negative_additions": "warm tones, soft light, bokeh, natural environments, human warmth, texture, atmosphere",
    },

    "urban_noir": {
        "label": "Urban Noir",
        "overrides_camera": False,
        "brief_fit": {
            "urgency_high": 0.7, "tone_anger": 0.9, "tone_injustice": 0.9,
            "urgency_extreme": 0.8, "topic_wealth_gap": 0.8, "tone_revelation": 0.6,
        },
        "shot_fit": {
            "HOOK": 0.9, "WEIGHT": 0.8, "INTRUSION": 1.0,
            "MECHANISM": 0.5, "PROOF": 0.4, "STAT_REVEAL": 0.6, "MOVE": 0.8,
        },
        "world": "wet streets, alley reflections, neon signs in rain, anonymous urban space at 02:00 — danger aestheticised",
        "camera_system": "Cooke S4/i anamorphic primes, ARRI Alexa Mini LF — horizontal lens flare signature",
        "lighting_primary": "neon practical: red and blue mixed from signage above, colour isolation in darkness",
        "lighting_secondary": "sodium vapour 2100K backlight — wet surface reflections as secondary fill",
        "film": "Kodak 5219 500T — heavy grain in shadows, deep blacks, colour isolation, halation on practicals",
        "motion_character": "slow Steadicam push through wet space — reveal from shadow into pool of neon light",
        "continuity_logic": "neon colour palette + rain + deep shadow ratio + grain consistency",
        "negative_additions": "daylight, clean environments, cheerful colour palettes, studio lighting, suburban domesticity",
    },

    "archive_degraded": {
        "label": "Archive Documentary",
        "overrides_camera": True,
        "brief_fit": {
            "tone_historical": 0.9, "topic_finance": 0.4, "tone_weight": 0.7,
            "tone_evidence": 0.7, "urgency_low": 0.5,
        },
        "shot_fit": {
            "HOOK": 0.7, "WEIGHT": 1.0, "INTRUSION": 0.6,
            "MECHANISM": 0.5, "PROOF": 0.8, "STAT_REVEAL": 0.4, "MOVE": 0.3,
        },
        "world": "archival footage aesthetic — scan lines, gate weave, colour fade, timestamp burns — the look of historical truth",
        "camera_system": "Super 8 or 16mm emulation, Bolex Rex-5 look, optical sound artifacts present in frame",
        "lighting_primary": "available light, over-exposed highlights, colour shift to cyan-orange crossprocess",
        "lighting_secondary": "flicker from aging lamp — unstable light source as period detail",
        "film": "Kodachrome 40 expired — heavy magenta shift, dense grain, dust and scratch overlay, faded highlights, vignette",
        "motion_character": "gate weave 2-pixel random drift per frame, speed fluctuation ±5%, jump cuts between observations",
        "continuity_logic": "grain density + colour grading + vignette + scan line texture consistency",
        "negative_additions": "modern crisp digital video, clean aesthetic, contemporary equipment, smooth motion, current technology",
    },

    "luxury_aspirational": {
        "label": "Luxury Aspirational",
        "overrides_camera": False,
        "brief_fit": {
            "topic_investment": 0.7, "tone_aspiration": 0.9, "urgency_mid": 0.4,
            "topic_isa": 0.3, "topic_pension": 0.4, "tone_move": 0.8,
        },
        "shot_fit": {
            "HOOK": 0.4, "WEIGHT": 0.4, "INTRUSION": 0.3,
            "MECHANISM": 0.3, "PROOF": 0.2, "STAT_REVEAL": 0.5, "MOVE": 1.0,
        },
        "world": "high-end environments: marble surfaces, brushed steel, penthouse glass, private spaces — wealth made visible and tactile",
        "camera_system": "RED Monstro 8K, Zeiss Supreme Prime 50mm T1.5, clean digital with controlled latitude",
        "lighting_primary": "warm directional 3200K tungsten from 45°, gold accent practicals reinforcing warmth",
        "lighting_secondary": "reflective surfaces as secondary sources — luxury amplifies and redirects light",
        "film": "clean digital with subtle warm LUT — rich colour volume, controlled saturation 0.95, no grain",
        "motion_character": "smooth crane reveals, environment opening up — wealth is patient, movement assured",
        "continuity_logic": "warm colour temperature 3200K + surface quality (marble/steel/glass) + controlled lighting",
        "negative_additions": "poverty markers, degradation, rough textures, fluorescent lighting, disorder, film grain",
    },

    "lo_fi_authentic": {
        "label": "Lo-fi Authentic",
        "overrides_camera": True,
        "brief_fit": {
            "tone_relatable": 0.9, "topic_savings": 0.8, "urgency_low": 0.6,
            "tone_weight": 0.7, "topic_mortgage": 0.5, "tone_investigative": 0.4,
        },
        "shot_fit": {
            "HOOK": 0.6, "WEIGHT": 1.0, "INTRUSION": 0.5,
            "MECHANISM": 0.7, "PROOF": 0.6, "STAT_REVEAL": 0.5, "MOVE": 0.8,
        },
        "world": "real domestic environments: kitchens, bedrooms, living rooms, commutes — the ordinary world as protagonist",
        "camera_system": "iPhone 15 Pro Max or similar consumer device, natural lens, no stabilisation, real compression",
        "lighting_primary": "window light at noon or lamp from wrong angle — imperfect, real, what was there",
        "lighting_secondary": "screen glow as fill — the device used to access information",
        "film": "compressed video codec — slight purple fringing, real exposure decisions, no grade applied",
        "motion_character": "handheld human breath visible, zoom instead of dolly, accidental reframes kept",
        "continuity_logic": "ambient light character + room environment + human-held movement energy",
        "negative_additions": "studio, professional lighting, crisp production value, cinematic grading, cinematic lenses",
    },
}

SHOT_TYPES = list(AESTHETIC_PROFILES["cinematic_dark"]["shot_fit"].keys())


# ═══════════════════════════════════════════════════════════════════════════
# SHOT TYPE DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

SHOT_TYPES = [
    "HOOK",          # 0 — fast cut opening, stops the scroll
    "WEIGHT",        # 1 — establishes the world-before, authority
    "INTRUSION",     # 2 — the revelation, charged medium shot
    "MECHANISM",     # 3 — documentary, information being discovered
    "PROOF",         # 4 — evidence, data, institutional cold light
    "STAT_REVEAL",   # 5 — macro close, the number, money moment
    "MOVE",          # 6 — forward motion, resolution, the action
]

# Maps creative brief sections → shot types
SECTION_SHOT_MAP = {
    "INTRUSION": ["HOOK", "INTRUSION"],
    "WEIGHT":    ["WEIGHT"],
    "MECHANISM": ["MECHANISM"],
    "PROOF":     ["PROOF", "STAT_REVEAL"],
    "MOVE":      ["MOVE"],
}


# ═══════════════════════════════════════════════════════════════════════════
# DEFAULT PROMPT GENOME
# Each component: {text, fitness, uses, wins, generation, tags}
# fitness: 0-100 (start 60). Retire <25. Replicate+mutate >80.
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_GENOME = {

    # ── OPTICS — Lens + aperture per shot type ──────────────────────────────
    "optics": {
        "HOOK": [
            {"text": "24mm lens, f/2.0, wide field, slight environmental distortion", "fitness": 60},
            {"text": "35mm lens, f/1.8, medium wide, natural perspective, shallow DoF", "fitness": 60},
            {"text": "anamorphic 40mm, f/2.8, oval bokeh, horizontal lens flare potential", "fitness": 60},
        ],
        "WEIGHT": [
            {"text": "35mm lens, f/2.8, documentary field, subject mid-frame", "fitness": 60},
            {"text": "50mm lens, f/2.0, natural compression, slight background separation", "fitness": 60},
            {"text": "28mm lens, f/4.0, deep focus, environment visible and present", "fitness": 60},
        ],
        "INTRUSION": [
            {"text": "50mm lens, f/1.8, subject isolation, background falls away", "fitness": 60},
            {"text": "85mm lens, f/1.4, portrait compression, maximum background separation", "fitness": 60},
            {"text": "anamorphic 75mm, f/2.0, oval bokeh, compressed perspective, horizontal flares", "fitness": 60},
        ],
        "MECHANISM": [
            {"text": "35mm lens, f/2.8, observational framing, handheld logic", "fitness": 60},
            {"text": "50mm lens, f/4.0, enough DoF to show environment and subject together", "fitness": 60},
        ],
        "PROOF": [
            {"text": "100mm macro lens, f/4.0, extreme close-up of surface detail", "fitness": 60},
            {"text": "85mm lens, f/2.8, medium close, clinical sharpness across frame", "fitness": 60},
        ],
        "STAT_REVEAL": [
            {"text": "100mm macro, f/2.8, maximum detail isolation, background dissolves", "fitness": 60},
            {"text": "50mm lens, f/1.4, subject in sharp focus, world behind out of focus", "fitness": 60},
        ],
        "MOVE": [
            {"text": "35mm lens, f/2.0, forward motion framing, camera advances with subject", "fitness": 60},
            {"text": "24mm lens, f/2.8, wide to emphasise forward movement and environment opening up", "fitness": 60},
        ],
    },

    # ── MOTION — Camera movement per shot type ───────────────────────────────
    "motion": {
        "HOOK": [
            {"text": "fast whip pan entering frame, then hard lock — arrests the scroll", "fitness": 60},
            {"text": "sudden smash cut push-in from wide to tight in 1.5 seconds", "fitness": 60},
            {"text": "static for 0.5 seconds, then aggressive slow dolly forward", "fitness": 60},
        ],
        "WEIGHT": [
            {"text": "slow lateral dolly right, camera moving at 0.3m/s, ground level", "fitness": 60},
            {"text": "locked off tripod, subject enters from left and walks toward camera, slightly overhead angle", "fitness": 60},
            {"text": "slow crane descent from overhead to eye level over 4 seconds", "fitness": 60},
        ],
        "INTRUSION": [
            {"text": "slow deliberate push-in, camera closes distance 40cm over 5 seconds, subject fills frame by end", "fitness": 60},
            {"text": "handheld subtle micro-jitter, camera at rest but organically alive, not mechanical", "fitness": 60},
            {"text": "rack focus from soft foreground element to sharp subject at 2-second mark", "fitness": 60},
        ],
        "MECHANISM": [
            {"text": "handheld controlled micro-jitter, observational documentary tracking, camera follows discovery", "fitness": 60},
            {"text": "slow orbital around object or environment element, 180 degrees over 5 seconds", "fitness": 60},
            {"text": "slow lateral pan revealing context from left to right, camera moves at rhythm of information", "fitness": 60},
        ],
        "PROOF": [
            {"text": "locked off, subject or object static, camera perfectly still, evidence speaks alone", "fitness": 60},
            {"text": "very slow dolly-in 20cm total, barely perceptible, clinical observation", "fitness": 60},
        ],
        "STAT_REVEAL": [
            {"text": "static macro, subject perfectly still, light catches surface texture, no camera movement", "fitness": 60},
            {"text": "extremely slow dolly-in 10cm over 4 seconds, macro subject expanding to fill frame", "fitness": 60},
        ],
        "MOVE": [
            {"text": "camera accelerates forward with subject, parallax on background architecture", "fitness": 60},
            {"text": "Steadicam forward tracking following subject, smooth resolute movement, environment opens ahead", "fitness": 60},
            {"text": "slow crane upward revealing scale and forward direction simultaneously", "fitness": 60},
        ],
    },

    # ── LIGHTING — Per shot type and brief topic ─────────────────────────────
    "lighting": {
        "institutional": [
            {"text": "harsh overhead fluorescent 5600K, cold blue-white, flat institutional light, deep shadows beneath subject", "fitness": 60},
            {"text": "practical desk lamp warm 2800K from below left, harsh shadow on right side, chiaroscuro ratio 5:1", "fitness": 60},
            {"text": "single overhead practical tungsten 3200K, pool of light, surrounding darkness, isolation", "fitness": 60},
        ],
        "wealth": [
            {"text": "warm practical ambient 2700K, city lights contributing blue fill from window, balanced 3:1 ratio", "fitness": 60},
            {"text": "soft north-facing window light 6500K diffused through sheer, even and revealing, no harsh shadows", "fitness": 60},
            {"text": "golden hour practical sunlight from left, warm 3200K, long shadows, depth across frame", "fitness": 60},
        ],
        "revelation": [
            {"text": "single motivated source from above-right 3200K, rest of frame intentionally underexposed", "fitness": 60},
            {"text": "backlight rim from behind subject 5600K, face in 60% shadow, information withheld and revealed", "fitness": 60},
            {"text": "mixed light: warm practical left 2800K, cold blue ambient right 6500K, tension in the colour split", "fitness": 60},
        ],
        "documentary": [
            {"text": "available light only, overcast diffused daylight 7000K, no fill, naturalistic", "fitness": 60},
            {"text": "practical sources only: overhead practical fluorescent, monitor glow contributing blue fill", "fitness": 60},
        ],
    },

    # ── FILM STOCK — Colour science + grain tokens ───────────────────────────
    "film_stock": {
        "finance_dark": [
            {"text": "Kodak Vision3 500T tungsten stock, slight blue cast in daylight, fine grain, lifted blacks, cinematic toe curve", "fitness": 60},
            {"text": "shot on 35mm, Kodak Vision3 200T, natural skin tones, fine grain structure, professional cinema colour science", "fitness": 60},
        ],
        "finance_warm": [
            {"text": "Kodak Portra 400 colour science, warm lifted shadows, natural saturation, organic grain, photographic not rendered", "fitness": 60},
            {"text": "Fuji Superia 400 colour tone, slight green-teal midtone cast, fine grain, photojournalistic authenticity", "fitness": 60},
        ],
        "high_contrast": [
            {"text": "Kodak Tri-X 400 desaturated grade, high contrast, coarse grain, deep blacks, documentary authority", "fitness": 60},
            {"text": "bleach bypass process simulation, desaturated, increased contrast, silver retention, photochemical grit", "fitness": 60},
        ],
        "cinema_neutral": [
            {"text": "ARRI Alexa 35 colour science, LOG-C3 basegrade, natural tones, subtle grain, professional broadcast standard", "fitness": 60},
            {"text": "Sony Venice colour matrix, cinematic neutrality, excellent shadow detail, film halation on practical lights", "fitness": 60},
        ],
    },

    # ── ENVIRONMENT — Scene-setting descriptions by topic ─────────────────────
    "environment": {
        "finance_institutional": [
            {"text": "glass-and-steel office building interior, evening, city grid visible through floor-to-ceiling windows below", "fitness": 60},
            {"text": "dark wood-panelled banking hall, high ceiling, marble floor, single occupied desk illuminated, all else in shadow", "fitness": 60},
            {"text": "modern open-plan office after hours, overhead lighting half-off, screens glowing, city visible through windows", "fitness": 60},
        ],
        "finance_aspiration": [
            {"text": "penthouse apartment, sparse and expensive, city skyline below, late evening, minimal furniture, maximum space", "fitness": 60},
            {"text": "rooftop terrace with city below, golden hour, distant buildings sharp against pale sky, isolation and scale", "fitness": 60},
        ],
        "finance_street": [
            {"text": "wet London street at night, sodium vapour streetlight reflections in puddles, red brick architecture, deserted", "fitness": 60},
            {"text": "commuter station, off-peak hours, long empty platform, overhead industrial lighting, echo implied", "fitness": 60},
        ],
        "finance_data": [
            {"text": "clean dark surface, single object or document in exact centre, controlled studio negative space", "fitness": 60},
            {"text": "close surface of printed bank statement or official document, edge softly out of focus, cold overhead light", "fitness": 60},
        ],
    },

    # ── CONTINUITY ANCHORS — Cross-clip vocabulary locks ─────────────────────
    "continuity_anchor": [
        {"text": "glass-walled office interior, evening blue-hour, city lights visible below, warm practical overhead", "fitness": 60},
        {"text": "dark professional environment, single warm practical source, evening, muted ambient city light", "fitness": 60},
        {"text": "institutional London interior, overcast ambient, fluorescent overhead, no direct sunlight", "fitness": 60},
        {"text": "contemporary financial environment, evening, controlled cool ambient with warm practical accent", "fitness": 60},
        {"text": "minimal dark environment, one motivated light source, city visible distantly, no clutter", "fitness": 60},
    ],

    # ── AVOID CLAUSES — Embedded directly in prompt (hixx.ai format) ─────────
    "avoid": {
        "human": "Avoid: facial distortion, extra fingers, hand deformities, morphing skin, eye asymmetry, blink artifacts, warped proportions.",
        "environment": "Avoid: watermarks, text overlays, subtitle artifacts, camera jitter, morphing surfaces, flickering light, inconsistent shadows.",
        "data": "Avoid: readable text on screens, identifiable account numbers, facial distortion, extra fingers, screen flicker, moiré patterns.",
        "general": "Avoid: blur, distortion, oversaturation, smooth waxy skin, plastic textures, over-sharpening, artificial bokeh halos, morphing edges.",
    },

    # ── SUBJECT DESCRIPTORS — Finance content human subjects ─────────────────
    "subject": {
        "authority": [
            {"text": "person in their 40s, experienced, no wasted movement, wearing dark jacket, direct gaze", "fitness": 60},
            {"text": "professional mid-career, unhurried, carrying weight of knowledge, neutral expression that could shift", "fitness": 60},
        ],
        "everyday": [
            {"text": "ordinary person, unremarkable clothes, tired in the way the system makes people tired", "fitness": 60},
            {"text": "person in their 30s, smart but not wealthy, the kind of person who reads the fine print too late", "fitness": 60},
        ],
        "abstract": [
            {"text": "no human subject, object or environment carries the scene", "fitness": 60},
        ],
    },

    # ── PERFORMANCE DIRECTION — Emotion delivered to subject ─────────────────
    "performance": {
        "revelation": [
            {"text": "expression neutral to start, subtle brow shift at 2-second mark as comprehension registers, no overacting", "fitness": 60},
            {"text": "controlled stillness, jaw slightly set, the weight of knowing something others do not", "fitness": 60},
        ],
        "authority": [
            {"text": "deliberate pacing, unhurried gaze, each movement communicates confidence not urgency", "fitness": 60},
            {"text": "steady and precise, the expression of someone laying out facts they have long understood", "fitness": 60},
        ],
        "concern": [
            {"text": "slightly furrowed brow, eyes that have calculated something unwelcome, contained not panicked", "fitness": 60},
        ],
    },

    # ── TEMPORAL PROGRESSION — For atmosphere within clip ────────────────────
    "temporal": [
        {"text": "light quality static throughout, no atmospheric progression", "fitness": 60},
        {"text": "at 3-second mark, distant city light shifts slightly as cloud passes, barely perceptible ambient change", "fitness": 60},
        {"text": "warm practical light flickers once at 2 seconds — power-grid suggestion — then stabilises", "fitness": 60},
        {"text": "steam or breath barely visible in cold air, secondary motion from ventilation barely stirs any fabric", "fitness": 60},
    ],

    # Aesthetic fitness: {aesthetic_key: {shot_type: fitness_0_to_100}}
    # Starts empty — populated from AESTHETIC_PROFILES on first load.
    "aesthetic_fitness": {},

    # Which composition mode performs better overall
    "aesthetic_mode_fitness": {
        "single": {"fitness": 60.0, "uses": 0, "wins": 0},
        "multi":  {"fitness": 60.0, "uses": 0, "wins": 0},
    },

    "_meta": {
        "generation": 1,
        "created": NOW.isoformat(),
        "last_evolved": NOW.isoformat(),
        "total_videos_scored": 0,
        "evolution_history": [],
    }
}


# ═══════════════════════════════════════════════════════════════════════════
# GENOME
# ═══════════════════════════════════════════════════════════════════════════

class PromptGenome:
    """Manages the evolving prompt component library."""

    def __init__(self):
        self.data = self._load()

    def _load(self) -> dict:
        if GENOME_FILE.exists():
            try:
                g = json.loads(GENOME_FILE.read_text())
                for cat, val in DEFAULT_GENOME.items():
                    if cat not in g:
                        g[cat] = deepcopy(val)
                self._init_aesthetic_fitness(g)
                return g
            except Exception:
                pass
        g = deepcopy(DEFAULT_GENOME)
        self._init_aesthetic_fitness(g)
        GENOME_FILE.write_text(json.dumps(g, indent=2))
        return g

    @staticmethod
    def _init_aesthetic_fitness(g: dict):
        """Populate aesthetic_fitness from AESTHETIC_PROFILES if entries are missing."""
        af = g.setdefault("aesthetic_fitness", {})
        for akey, profile in AESTHETIC_PROFILES.items():
            if akey not in af:
                af[akey] = {
                    shot_type: round(prior * 60 + 30, 1)   # seed from prior: range ~42-90
                    for shot_type, prior in profile["shot_fit"].items()
                }

    def get_aesthetic_fitness(self, aesthetic_key: str, shot_type: str) -> float:
        return self.data.get("aesthetic_fitness", {}).get(aesthetic_key, {}).get(shot_type, 60.0)

    def update_aesthetic_fitness(self, aesthetic_key: str, shot_type: str, visual_score: float):
        """Update aesthetic fitness for a specific shot type based on video score."""
        delta = (visual_score - 50) * 0.3   # max ±15 per evaluation
        af = self.data.setdefault("aesthetic_fitness", {})
        bucket = af.setdefault(aesthetic_key, {})
        old = bucket.get(shot_type, 60.0)
        bucket[shot_type] = round(max(0.0, min(100.0, old + delta)), 1)

    def update_mode_fitness(self, mode: str, visual_score: float):
        """Update single vs multi aesthetic mode fitness."""
        mf = self.data.setdefault("aesthetic_mode_fitness", {
            "single": {"fitness": 60.0, "uses": 0, "wins": 0},
            "multi":  {"fitness": 60.0, "uses": 0, "wins": 0},
        })
        entry = mf.setdefault(mode, {"fitness": 60.0, "uses": 0, "wins": 0})
        entry["uses"] = entry.get("uses", 0) + 1
        delta = (visual_score - 50) * 0.3
        entry["fitness"] = round(max(0.0, min(100.0, entry.get("fitness", 60.0) + delta)), 1)
        if visual_score > 65:
            entry["wins"] = entry.get("wins", 0) + 1

    def save(self):
        self.data["_meta"]["last_evolved"] = NOW.isoformat()
        GENOME_FILE.write_text(json.dumps(self.data, indent=2))

    def get(self, category: str, subkey: str | None = None, top_n: int = 3) -> list[dict]:
        """Return weighted-random top_n components from category[subkey]."""
        bucket = self.data.get(category, {})
        if subkey:
            bucket = bucket.get(subkey, bucket) if isinstance(bucket, dict) else bucket

        if isinstance(bucket, list):
            items = bucket
        elif isinstance(bucket, dict):
            # Flatten subkeys
            items = [item for sublist in bucket.values()
                     for item in (sublist if isinstance(sublist, list) else [])]
        else:
            return []

        if not items:
            return []

        # Weighted selection by fitness
        total_fitness = sum(max(c.get("fitness", 60), 1) for c in items)
        selected = []
        pool = list(items)
        for _ in range(min(top_n, len(pool))):
            r = random.uniform(0, sum(max(c.get("fitness", 60), 1) for c in pool))
            cumulative = 0
            for i, c in enumerate(pool):
                cumulative += max(c.get("fitness", 60), 1)
                if cumulative >= r:
                    selected.append(c)
                    pool.pop(i)
                    break
        return selected

    def best(self, category: str, subkey: str | None = None) -> str:
        """Return the single highest-fitness component text."""
        results = self.get(category, subkey, top_n=1)
        return results[0]["text"] if results else ""

    def pick(self, category: str, subkey: str | None = None) -> str:
        """Weighted-random pick, return text only."""
        results = self.get(category, subkey, top_n=1)
        return results[0]["text"] if results else ""

    def record_usage(self, component_texts: list[str]):
        """Increment use count for recorded components."""
        for cat_val in self.data.values():
            if isinstance(cat_val, dict) and cat_val:
                for subval in cat_val.values():
                    if isinstance(subval, list):
                        for item in subval:
                            if item.get("text") in component_texts:
                                item["uses"] = item.get("uses", 0) + 1
            elif isinstance(cat_val, list):
                for item in cat_val:
                    if item.get("text") in component_texts:
                        item["uses"] = item.get("uses", 0) + 1

    def apply_score(self, component_texts: list[str], visual_score: float):
        """Update fitness of components used in a video based on quality score."""
        # Score is 0-100. Neutral=50. Above 50 → fitness up. Below 50 → fitness down.
        delta = (visual_score - 50) * 0.4   # max ±20 fitness per evaluation

        def update_items(items):
            for item in items:
                if item.get("text") in component_texts:
                    item["wins"] = item.get("wins", 0) + (1 if visual_score > 60 else 0)
                    old = item.get("fitness", 60)
                    item["fitness"] = round(max(0, min(100, old + delta)), 1)

        for cat_val in self.data.values():
            if isinstance(cat_val, dict):
                for subval in cat_val.values():
                    if isinstance(subval, list):
                        update_items(subval)
            elif isinstance(cat_val, list):
                update_items(cat_val)

    def evolve(self):
        """
        Evolution step:
        1. Retire components with fitness < 25 (unless it's the last in category)
        2. Mutate top performers (fitness > 80) to generate experimental variants
        3. Advance generation counter
        """
        generation = self.data["_meta"].get("generation", 1)
        retired = 0
        mutated = 0

        def evolve_list(items: list, category_name: str) -> list:
            nonlocal retired, mutated
            survivors = []
            to_mutate = []

            for item in items:
                f = item.get("fitness", 60)
                if f < 25 and len(items) > 2:
                    retired += 1
                    continue   # retire
                survivors.append(item)
                if f > 80:
                    to_mutate.append(item)

            # Mutate top performers — add experimental variant
            for item in to_mutate:
                variant = _mutate_component(item, generation)
                if variant and not any(s["text"] == variant["text"] for s in survivors):
                    survivors.append(variant)
                    mutated += 1

            return survivors

        # Walk genome
        for cat_key, cat_val in self.data.items():
            if cat_key == "_meta":
                continue
            if isinstance(cat_val, list):
                self.data[cat_key] = evolve_list(cat_val, cat_key)
            elif isinstance(cat_val, dict):
                for sub_key, sub_val in cat_val.items():
                    if isinstance(sub_val, list):
                        cat_val[sub_key] = evolve_list(sub_val, f"{cat_key}.{sub_key}")

        self.data["_meta"]["generation"] = generation + 1
        self.data["_meta"]["evolution_history"].append({
            "generation": generation + 1,
            "timestamp":  NOW.isoformat(),
            "retired":    retired,
            "mutated":    mutated,
        })
        self.data["_meta"]["evolution_history"] = \
            self.data["_meta"]["evolution_history"][-50:]

        self.save()
        return {"retired": retired, "mutated": mutated, "generation": generation + 1}


def _mutate_component(item: dict, generation: int) -> dict | None:
    """Generate a subtle variation of a high-fitness component."""
    text = item.get("text", "")
    if not text:
        return None

    # Mutation strategies — pick one randomly
    mutations = [
        # Swap a lighting temperature value
        lambda t: re.sub(r'(\d{4})K', lambda m: f"{int(m.group(1)) + random.choice([-200, 200, -300, 300])}K", t, count=1),
        # Add subtle qualifier
        lambda t: t.replace(", ", ", barely perceptible ", 1) if "barely" not in t else t,
        # Swap focal length
        lambda t: re.sub(r'(\d+)mm', lambda m: f"{int(m.group(1)) + random.choice([-5, 5, -10, 10])}mm", t, count=1),
        # Swap speed descriptor
        lambda t: t.replace("slow", "very slow", 1) if "slow" in t and "very slow" not in t else t,
        # Add film halation
        lambda t: t + ", film halation on practical lights" if "halation" not in t else t,
    ]

    strategy = random.choice(mutations)
    try:
        new_text = strategy(text)
        if new_text == text:
            return None  # mutation produced no change
        return {
            "text":       new_text,
            "fitness":    55,   # experimental — slightly below neutral
            "uses":       0,
            "wins":       0,
            "generation": generation,
            "parent":     text[:60],
        }
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════
# AESTHETIC SELECTION — Brief → signals → aesthetic plan
# ═══════════════════════════════════════════════════════════════════════════

def _extract_brief_signals(brief: dict) -> dict:
    """
    Parse a creative brief into emotional and content signals (0.0–1.0).
    These signals drive aesthetic selection — not topic alone.
    """
    text = " ".join([
        brief.get("asymmetry", ""),
        brief.get("full_script_text", ""),
        str(brief.get("script", {})),
        brief.get("intrusion", ""),
        brief.get("weight", ""),
    ]).lower()

    urgency = float(brief.get("urgency_score") or 50)

    # Urgency bands
    s = {
        "urgency_low":     max(0.0, (50 - urgency) / 50),
        "urgency_mid":     max(0.0, 1.0 - abs(urgency - 55) / 55),
        "urgency_high":    max(0.0, (urgency - 60) / 40),
        "urgency_extreme": max(0.0, (urgency - 80) / 20),
    }

    # Topic
    topic = _detect_topic(brief)
    s["topic_finance"]    = 1.0 if topic in ("isa", "pension", "mortgage", "savings") else 0.3
    s["topic_isa"]        = 1.0 if topic == "isa" else 0.0
    s["topic_pension"]    = 1.0 if topic == "pension" else 0.0
    s["topic_savings"]    = 1.0 if topic == "savings" else 0.0
    s["topic_mortgage"]   = 1.0 if topic == "mortgage" else 0.0
    s["topic_crypto"]     = 1.0 if topic == "crypto" else 0.0
    s["topic_investment"] = 1.0 if any(w in text for w in ["invest", "return", "portfolio", "compound"]) else 0.1
    s["topic_wealth_gap"] = 1.0 if any(w in text for w in ["inequality", "rich", "wealth gap", "poor"]) else 0.1

    # Tone — derived from script content
    s["tone_revelation"]   = 1.0 if any(w in text for w in ["didn't know", "nobody told", "discover", "found out", "realise"]) else 0.3
    s["tone_evidence"]     = 1.0 if any(w in text for w in ["data", "report", "fca", "hmrc", "statistic", "%", "percent", "research"]) else 0.2
    s["tone_anger"]        = 1.0 if any(w in text for w in ["exploit", "scam", "wrong", "cheat", "hidden fee", "rigged"]) else 0.1
    s["tone_injustice"]    = 1.0 if any(w in text for w in ["unfair", "shouldn't", "system", "power", "rigged", "you're owed"]) else 0.1
    s["tone_weight"]       = 1.0 if any(w in text for w in ["most people", "average", "majority", "millions", "everyone"]) else 0.3
    s["tone_aspiration"]   = 1.0 if any(w in text for w in ["could", "should", "possible", "achieve", "goal", "build", "future"]) else 0.2
    s["tone_authority"]    = 1.0 if any(w in text for w in ["rule", "regulation", "law", "policy", "official", "fca", "hmrc"]) else 0.2
    s["tone_relatable"]    = 1.0 if any(w in text for w in [" you ", "your ", " we ", " us ", "ordinary", "everyday", "most of us"]) else 0.3
    s["tone_investigative"]= 1.0 if any(w in text for w in ["investigate", "examine", "look at", "look closer", "research"]) else 0.2
    s["tone_surprise"]     = 1.0 if any(w in text for w in ["shocking", "surprising", "bet you", "most people don't", "hardly anyone"]) else 0.2
    s["tone_mechanism"]    = 1.0 if any(w in text for w in ["how", "why", "mechanism", "works", "because", "structure"]) else 0.3
    s["tone_historical"]   = 1.0 if any(w in text for w in ["always", "decades", "years", "since", "ever since", "historically"]) else 0.1
    s["tone_data"]         = 1.0 if any(w in text for w in ["algorithm", "system", "flow", "process", "compounding", "rate"]) else 0.2
    s["tone_move"]         = 1.0 if any(w in text for w in ["start", "open", "switch", "move", "take action", "link in bio"]) else 0.2

    return s


def _score_aesthetic(akey: str, signals: dict, shot_type: str, genome: PromptGenome) -> float:
    """Score a single aesthetic for a given shot type + brief signals. Returns 0.0–1.0."""
    profile = AESTHETIC_PROFILES[akey]

    # Brief signal alignment: weighted sum of matching signals
    brief_score = sum(
        signals.get(sig, 0.0) * weight
        for sig, weight in profile["brief_fit"].items()
    )
    n = len(profile["brief_fit"])
    brief_score = min(1.0, brief_score / n) if n else 0.5

    # Shot type prior (how naturally this aesthetic serves this shot)
    shot_prior = profile["shot_fit"].get(shot_type, 0.5)

    # Genome-learned fitness for this aesthetic + shot type
    genome_fit = genome.get_aesthetic_fitness(akey, shot_type) / 100.0

    # Combined: brief alignment (40%) + shot prior (30%) + genome fitness (30%)
    return (brief_score * 0.40) + (shot_prior * 0.30) + (genome_fit * 0.30)


def _pick_aesthetic_for_shot(signals: dict, shot_type: str, genome: PromptGenome) -> str:
    """Weighted-random aesthetic selection for a single shot — not purely greedy."""
    scores = {
        akey: _score_aesthetic(akey, signals, shot_type, genome)
        for akey in AESTHETIC_PROFILES
    }
    total = sum(scores.values())
    if total == 0:
        return "cinematic_dark"
    r = random.uniform(0, total)
    cumulative = 0.0
    for akey, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        cumulative += score
        if cumulative >= r:
            return akey
    return "cinematic_dark"


def select_aesthetic_plan(brief: dict, genome: PromptGenome) -> dict:
    """
    Decide whether this video uses one aesthetic throughout (single) or
    a different aesthetic per shot type (multi). Then pick the aesthetic(s).

    Multi mode is unlocked after 20+ scored videos. The genome learns which
    mode performs better overall and biases future selection accordingly.

    Returns:
      {
        "mode": "single" | "multi",
        "primary": "cinematic_dark",
        "shot_aesthetics": {"HOOK": "abstract_motion", "WEIGHT": "cinematic_dark", ...},
        "signals": {...},
      }
    """
    signals = _extract_brief_signals(brief)
    total_scored = genome.data["_meta"].get("total_videos_scored", 0)

    # Multi mode unlocks after sufficient data
    multi_unlocked = total_scored >= 20

    mode = "single"
    if multi_unlocked:
        mf = genome.data.get("aesthetic_mode_fitness", {})
        single_fit = mf.get("single", {}).get("fitness", 60.0)
        multi_fit  = mf.get("multi",  {}).get("fitness", 60.0)
        # Weighted probability — multi capped at 35% until it clearly wins
        multi_prob = min(0.35, (multi_fit / (single_fit + multi_fit)) * 0.6)
        if random.random() < multi_prob:
            mode = "multi"

    if mode == "multi":
        shot_aesthetics = {
            st: _pick_aesthetic_for_shot(signals, st, genome)
            for st in SHOT_TYPES
        }
        primary = shot_aesthetics.get("HOOK", "cinematic_dark")
    else:
        # Single: pick best aesthetic for WEIGHT shot (most representative of full video)
        primary = _pick_aesthetic_for_shot(signals, "WEIGHT", genome)
        shot_aesthetics = {st: primary for st in SHOT_TYPES}

    return {
        "mode":            mode,
        "primary":         primary,
        "shot_aesthetics": shot_aesthetics,
        "signals":         signals,
    }


# ═══════════════════════════════════════════════════════════════════════════
# VISUAL DNA — Cross-clip consistency anchor
# ═══════════════════════════════════════════════════════════════════════════

def generate_visual_dna(brief: dict, genome: PromptGenome, aesthetic_plan: dict) -> dict:
    """
    Generate the Visual DNA — the locked vocabulary embedded verbatim in
    every clip prompt to enforce cross-clip continuity.

    Now driven entirely by the selected aesthetic profile, not hardcoded maps.
    For multi-aesthetic plans, the DNA records per-shot aesthetics and uses the
    primary aesthetic's continuity_logic as the cross-shot anchor.
    """
    topic   = _detect_topic(brief)
    primary = aesthetic_plan["primary"]
    profile = AESTHETIC_PROFILES[primary]

    # The continuity anchor is the aesthetic's own logic — stated as a constant
    anchor_text = profile["continuity_logic"]

    # For single-aesthetic videos: full anchor embeds the profile's world + film
    # For multi-aesthetic videos: anchor is minimal — only cross-shot constants survive
    mode = aesthetic_plan["mode"]
    if mode == "single":
        full_anchor = (
            f"[Visual DNA — embed unchanged in every clip] "
            f"{anchor_text}. {profile['film']}. "
            f"Temporal consistency across all clips."
        )
    else:
        # Multi-aesthetic: only lock colour temperature + energy — not world or film
        full_anchor = (
            f"[Visual DNA — embed unchanged in every clip] "
            f"Consistent emotional register and colour temperature throughout. "
            f"Each shot's aesthetic serves its moment. Temporal energy consistency."
        )

    # Seed phrase — fingerprint of brief content for vocabulary locking
    seed_phrase = hashlib.md5(
        (brief.get("asymmetry", "") + primary).encode()
    ).hexdigest()[:8]

    return {
        "primary_aesthetic":  primary,
        "aesthetic_label":    profile["label"],
        "aesthetic_plan":     aesthetic_plan,
        "world":              profile["world"],
        "camera_system":      profile["camera_system"],
        "film":               profile["film"],
        "lighting_primary":   profile["lighting_primary"],
        "lighting_secondary": profile["lighting_secondary"],
        "motion_character":   profile["motion_character"],
        "anchor_text":        anchor_text,
        "full_anchor":        full_anchor,
        "seed_phrase":        seed_phrase,
        "topic":              topic,
    }


# ═══════════════════════════════════════════════════════════════════════════
# SHOT LIST GENERATOR
# ═══════════════════════════════════════════════════════════════════════════

def generate_shot_list(brief: dict, dna: dict) -> list[dict]:
    """
    Map the brief's emotional arc to 3 shots: HOOK → MECHANISM → MOVE.

    3-shot structure (down from 7) — reduces Kling render cost ~57% and
    cuts generation time from ~18 min to ~8 min per slot.
    Visual loops over narration via FFmpeg stream_loop; 3×5s = 15s loops fine.

    HOOK(5s) + MECHANISM(5s) + MOVE(5s) = 15s raw visual, loops to audio length.
    """
    intrusion = brief.get("intrusion", "")
    mechanism = brief.get("mechanism", "")
    move      = brief.get("move",      "")

    shots = [
        {
            "index":         0,
            "type":          "HOOK",
            "duration_s":    5,
            "brief_section": "INTRUSION",
            "intent":        "Stop the scroll. Establish tension before explanation.",
            "source_text":   intrusion[:120],
            "has_human":     False,
            "money_value":   None,
        },
        {
            "index":         1,
            "type":          "MECHANISM",
            "duration_s":    5,
            "brief_section": "MECHANISM",
            "intent":        "Show how it works. Documentary observation, information discovered.",
            "source_text":   mechanism[:120],
            "has_human":     False,
            "money_value":   None,
        },
        {
            "index":         2,
            "type":          "MOVE",
            "duration_s":    5,
            "brief_section": "MOVE",
            "intent":        "Resolution energy. Forward motion. The action the viewer must take.",
            "source_text":   move[:120],
            "has_human":     False,
            "money_value":   None,
        },
    ]

    return shots


# ═══════════════════════════════════════════════════════════════════════════
# CLIP PROMPT BUILDER — 8-layer scaffold
# ═══════════════════════════════════════════════════════════════════════════

def build_clip_prompt(
    shot: dict,
    brief: dict,
    dna: dict,
    genome: PromptGenome,
    aesthetic_key: str | None = None,
) -> tuple[str, dict]:
    """
    Build a complete Kling 3.0 clip prompt for a single shot.

    aesthetic_key selects which AESTHETIC_PROFILES entry governs this clip.
    If the aesthetic overrides_camera=True, genome optics/motion/film are
    bypassed — the aesthetic fully defines those layers (abstract, surreal, etc.)

    Returns (prompt_string, component_registry).
    """
    shot_type   = shot["type"]
    topic       = dna["topic"]
    source_text = shot.get("source_text", "")
    components_used = []

    akey    = aesthetic_key or dna.get("primary_aesthetic", "cinematic_dark")
    profile = AESTHETIC_PROFILES.get(akey, AESTHETIC_PROFILES["cinematic_dark"])
    overrides = profile.get("overrides_camera", False)

    # ── Layer 1: Subject ─────────────────────────────────────────────────────
    # Avoid human subjects in abstract/surreal/archive aesthetics entirely.
    # In other aesthetics: INTRUSION/MOVE shots have 40% human subject chance.
    subject_line = ""
    perf_line    = ""
    human_allowed = not overrides and akey not in ("abstract_motion", "surreal_temporal", "archive_degraded")
    if human_allowed and shot_type in ("INTRUSION", "MOVE") and random.random() < 0.4:
        subj = genome.pick("subject", "authority")
        perf = genome.pick("performance", "revelation" if shot_type == "INTRUSION" else "authority")
        subject_line = f"Subject: {subj}. "
        perf_line    = f"Performance: {perf}. "
        components_used += [subj, perf]

    # ── Layer 2: Scene action (always content-driven, never aesthetic-driven) ─
    action_line = _derive_action(shot_type, topic, source_text, brief)

    # ── Layers 3-6: Camera-system layers ─────────────────────────────────────
    if overrides:
        # Aesthetic fully specifies these layers — genome not consulted
        optics     = profile["camera_system"]
        motion     = profile["motion_character"]
        lighting   = f"{profile['lighting_primary']}. {profile['lighting_secondary']}"
        film_stock = profile["film"]
        components_used += [optics, motion, lighting, film_stock]
    else:
        # Genome provides specific within-aesthetic decisions
        optics   = genome.pick("optics", shot_type)
        motion   = genome.pick("motion", shot_type)
        # Lighting: use aesthetic's primary, enriched with genome's specific ratio
        light_key = _topic_to_light_key(topic, shot_type)
        genome_light = genome.pick("lighting", light_key)
        lighting  = f"{profile['lighting_primary']}. {genome_light}"
        film_stock = profile["film"]
        components_used += [optics, motion, genome_light, film_stock]

    # ── Layer 7: Temporal atmosphere ─────────────────────────────────────────
    temporal = genome.pick("temporal")
    components_used.append(temporal)

    # ── Layer 8: Continuity anchor (Visual DNA) ──────────────────────────────
    continuity = dna["full_anchor"]

    # ── Avoid clause — base + aesthetic-specific additions ───────────────────
    avoid_key  = "human" if subject_line else ("data" if shot_type == "STAT_REVEAL" else "environment")
    avoid_base = genome.data["avoid"].get(avoid_key, genome.data["avoid"]["general"])
    neg_adds   = profile.get("negative_additions", "")
    avoid_line = avoid_base + (f" Also avoid: {neg_adds}." if neg_adds else "")

    # ── Aesthetic world preamble (only for non-override aesthetics) ───────────
    world_preamble = ""
    if not overrides:
        world_preamble = f"World: {profile['world']}. Camera system: {profile['camera_system']}. "

    # ── Assemble ─────────────────────────────────────────────────────────────
    if overrides:
        # Abstract/surreal/archive: aesthetic layers first, then action
        parts = [p for p in [
            f"Aesthetic: {profile['label']}. {profile['world']}.",
            action_line,
            f"Camera: {optics}.",
            f"Motion: {motion}.",
            f"Lighting: {lighting}.",
            f"Film stock: {film_stock}.",
            f"Atmosphere: {temporal}.",
            continuity,
            avoid_line,
        ] if p.strip()]
    else:
        parts = [p for p in [
            world_preamble,
            subject_line + perf_line if subject_line else "",
            action_line,
            f"Optics: {optics}.",
            f"Camera: {motion}.",
            f"Lighting: {lighting}.",
            f"Film stock: {film_stock}.",
            f"Atmosphere: {temporal}.",
            continuity,
            avoid_line,
        ] if p.strip()]

    shot_label = (
        f"Shot {shot['index'] + 1} (0–{shot['duration_s']}s): "
        if shot["index"] == 0 else
        f"Shot {shot['index'] + 1}: "
    )
    final_prompt = shot_label + " ".join(parts)

    component_registry = {
        "shot_index":      shot["index"],
        "shot_type":       shot_type,
        "aesthetic_key":   akey,
        "aesthetic_label": profile["label"],
        "overrides_camera": overrides,
        "components":      [c for c in components_used if c],
        "action_line":     action_line,
        "optics":          optics,
        "motion":          motion,
        "lighting":        lighting,
        "film_stock":      film_stock,
        "dna_anchor":      dna["anchor_text"],
    }

    return final_prompt, component_registry


def build_negative_prompt(topic: str, has_human: bool, aesthetic_key: str = "cinematic_dark") -> str:
    """Build the Kling negative_prompt — base + human-specific + aesthetic-specific avoidances."""
    base = (
        "blur, distortion, watermark, text overlay, low quality, "
        "compression artifacts, flickering, inconsistent lighting, "
        "morphing faces, morphing textures, oversaturation, "
        "plastic skin, smooth waxy texture, artificial bokeh halos"
    )
    if has_human:
        base += (
            ", extra limbs, extra fingers, hand deformities, facial distortion, "
            "eye asymmetry, blink artifacts, warped proportions, unnatural head rotation"
        )
    if topic in ("isa", "pension", "savings"):
        base += ", readable account numbers, readable financial data, screen moiré"

    aesthetic_neg = AESTHETIC_PROFILES.get(aesthetic_key, {}).get("negative_additions", "")
    if aesthetic_neg:
        base += f", {aesthetic_neg}"

    return base


# ═══════════════════════════════════════════════════════════════════════════
# FULL PIPELINE — Brief → Shot List → All Clip Prompts
# ═══════════════════════════════════════════════════════════════════════════

def generate_all_prompts(brief: dict) -> dict:
    """
    Top-level call from production_agent.py.

    Returns:
    {
      "visual_dna": {...},
      "shot_list": [
        {
          ...shot metadata...,
          "prompt": "Shot 1: ...",
          "negative_prompt": "...",
          "component_registry": {...},
          "fal_params": { "duration": 5, "aspect_ratio": "9:16", ... }
        },
        ...
      ],
      "genome_generation": 4,
      "seed_phrase": "a1b2c3d4",
    }
    """
    genome         = PromptGenome()
    aesthetic_plan = select_aesthetic_plan(brief, genome)
    dna            = generate_visual_dna(brief, genome, aesthetic_plan)
    shots          = generate_shot_list(brief, dna)

    shot_results   = []
    all_components = []

    for shot in shots:
        akey               = aesthetic_plan["shot_aesthetics"].get(shot["type"], aesthetic_plan["primary"])
        prompt, registry   = build_clip_prompt(shot, brief, dna, genome, akey)
        neg_prompt         = build_negative_prompt(dna["topic"], shot["has_human"], akey)

        shot_out = {
            **shot,
            "prompt":             prompt,
            "negative_prompt":    neg_prompt,
            "component_registry": registry,
            "aesthetic_key":      akey,
            "aesthetic_label":    AESTHETIC_PROFILES[akey]["label"],
            "fal_params": {
                "duration":     5 if shot["duration_s"] <= 5 else 10,
                "aspect_ratio": "9:16",
                "mode":         "pro",
            },
        }
        shot_results.append(shot_out)
        all_components.extend(registry["components"])

    genome.record_usage(all_components)
    genome.save()

    return {
        "visual_dna":         dna,
        "aesthetic_plan":     aesthetic_plan,
        "shot_list":          shot_results,
        "genome_generation":  genome.data["_meta"].get("generation", 1),
        "seed_phrase":        dna["seed_phrase"],
        "total_shots":        len(shot_results),
        "total_duration_s":   sum(s["duration_s"] for s in shots),
        "generated_at":       NOW.isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# SELF-IMPROVEMENT — Called by quality_mirror every 6 hours
# ═══════════════════════════════════════════════════════════════════════════

def self_improve(manifests: list[dict]) -> dict:
    """
    Process production manifests with visual quality scores.
    Update genome fitness. Evolve if enough data has accumulated.

    Expected manifest structure:
      manifest["prompt_metadata"]["shot_list"][i]["component_registry"]
      manifest["prompt_metadata"]["shot_list"][i]["visual_score"]  ← from quality_mirror

    Returns summary of what changed.
    """
    genome = PromptGenome()
    scored_videos = 0
    scored_clips  = 0

    for manifest in manifests:
        pm = manifest.get("prompt_metadata", {})
        shot_list = pm.get("shot_list", [])
        if not shot_list:
            continue

        video_score = manifest.get("visual_score", 50)
        plan_mode   = pm.get("aesthetic_plan", {}).get("mode", "single")

        for shot in shot_list:
            score = shot.get("visual_score", video_score)
            if score is None:
                continue
            score = float(score)

            # Update component fitness
            components = shot.get("component_registry", {}).get("components", [])
            if components:
                genome.apply_score(components, score)
                scored_clips += 1

            # Update aesthetic fitness for this shot type
            akey      = shot.get("aesthetic_key") or shot.get("component_registry", {}).get("aesthetic_key")
            shot_type = shot.get("type", "HOOK")
            if akey and akey in AESTHETIC_PROFILES:
                genome.update_aesthetic_fitness(akey, shot_type, score)

        # Update mode fitness (single vs multi)
        genome.update_mode_fitness(plan_mode, float(video_score))
        scored_videos += 1

    genome.data["_meta"]["total_videos_scored"] = (
        genome.data["_meta"].get("total_videos_scored", 0) + scored_videos
    )

    # Evolve if we have meaningful data
    evolution_result = {}
    total_scored = genome.data["_meta"]["total_videos_scored"]
    last_gen     = genome.data["_meta"].get("generation", 1)

    # Evolve every 20 scored videos, or if generation is 1 (first run always evolves)
    if total_scored > 0 and (total_scored % 20 == 0 or last_gen == 1):
        evolution_result = genome.evolve()

    genome.save()

    return {
        "scored_videos":  scored_videos,
        "scored_clips":   scored_clips,
        "total_scored":   total_scored,
        "genome_generation": genome.data["_meta"].get("generation", 1),
        "evolution":      evolution_result,
    }


# ═══════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════

def _detect_topic(brief: dict) -> str:
    text = " ".join([
        brief.get("asymmetry", ""),
        brief.get("intrusion", ""),
        brief.get("topic", ""),
    ]).lower()
    keywords = {
        "isa":        ["isa", "allowance", "april 5", "stocks and shares"],
        "pension":    ["pension", "sipp", "retirement", "annuity", "drawdown"],
        "mortgage":   ["mortgage", "remortgage", "equity", "lender", "conveyancing"],
        "influencer": ["influencer", "exit liquidity", "sponsor", "paid promotion", "pump"],
        "crypto":     ["crypto", "bitcoin", "token", "defi", "blockchain", "nft"],
        "savings":    ["savings", "savings account", "interest rate", "easy access"],
    }
    for topic, kws in keywords.items():
        if any(kw in text for kw in kws):
            return topic
    return "general"


def _extract_money(text: str) -> str:
    if not text:
        return ""
    m = re.search(r'(£[\d,]+|€[\d,]+|\$[\d,]+|[\d,]+%)', text)
    return m.group(1) if m else ""


def _topic_to_light_key(topic: str, shot_type: str) -> str:
    if shot_type == "STAT_REVEAL":
        return "documentary"
    if shot_type == "PROOF":
        return "institutional"
    if topic in ("isa", "pension", "mortgage", "savings"):
        return "institutional"
    if topic in ("influencer", "crypto"):
        return "revelation"
    return "institutional"


def _derive_action(shot_type: str, topic: str, source_text: str, brief: dict) -> str:
    """
    Derive a scene action description from the shot type and brief content.
    Does NOT use the source_text verbatim (Kling doesn't render text).
    Translates abstract content into visual scene instructions.
    """
    actions = {
        "HOOK": {
            "isa":        "Empty office desk. Untouched ISA paperwork sits under a paperweight. A phone screen lights up — notification unseen.",
            "pension":    "Pension statement in an envelope, unopened, on a kitchen counter. Day light changes through window — weeks passing.",
            "mortgage":   "Dark living room. Bank letter on table. Someone picks it up slowly. Does not open it. Sets it down.",
            "influencer": "Phone screen scrolling fast through financial content. A comment reads: 'Should I invest?' No reply ever comes.",
            "savings":    "A savings account balance on a screen: 1.5%. Market return beside it: 10.7%. Static. Both numbers visible.",
            "crypto":     "Chart on screen. Goes up. Someone buys. Chart goes down fast. That person's face reflected in the dark screen.",
            "general":    "A document. An envelope. A number. The camera finds it slowly, as if it has always been there.",
        },
        "WEIGHT": {
            "isa":        "Wide of a bank branch interior. Glass, marble, queues. People waiting, unaware of the question they should be asking.",
            "pension":    "Office environment, working people, none of them thinking about what 2045 looks like from here.",
            "mortgage":   "Street of houses. Camera slowly tracks. Each one with a figure attached to it that the owners have mostly forgotten.",
            "influencer": "Social media scrolling on a screen. Same confident face, different product, different week. Pattern visible if you look.",
            "savings":    "A bank branch, ordinary, and inside it the gap between what the bank earns on your money and what they pay you.",
            "general":    "The ordinary world. Things as most people understand them. The camera sees it without judgement.",
        },
        "INTRUSION": {
            "isa":        "The £20,000 ISA allowance — a number — floating alone in the frame, the tax saving invisible beside it.",
            "pension":    "A pension projection document, the actual number visible, the gap to what was expected sitting beneath it.",
            "mortgage":   "A mortgage rate document, two columns: what was offered, what was available. They are different numbers.",
            "influencer": "A sell order. A timestamp. The same timestamp as a buy recommendation published six hours earlier.",
            "savings":    "Bank's own annual report. What they earned on deposits. What they paid depositors. The gap lives on one page.",
            "general":    "The document that says the thing nobody said out loud. Camera finds it without drama.",
        },
        "MECHANISM": {
            "isa":        "ISA wrapper mechanism shown through simple visual metaphor — money inside protected versus money outside taxed.",
            "pension":    "Compound interest curve over 30 years. Two lines. Starting amount identical. One started at 25. One at 35.",
            "mortgage":   "Total interest paid over mortgage term. A number that most people are never shown on day one.",
            "influencer": "The sell order and the buy recommendation in the same frame. Timestamps aligned. The mechanism is visible.",
            "savings":    "Bank of England base rate, chart over 12 months. The savings rate offered by high street banks below it. The gap, constant.",
            "general":    "The mechanism itself — whatever structure creates the asymmetry — rendered visible for the first time.",
        },
        "PROOF": {
            "isa":        "HMRC data. ISA uptake figures. Percentage of eligible UK adults who use their full allowance. The number is small.",
            "pension":    "Pension Bee data. Average pension pot at retirement versus what is needed for median lifestyle. Cold comparison.",
            "mortgage":   "FCA data on mortgage switch rates. Percentage of borrowers who stay on revert rate unnecessarily. The number is high.",
            "influencer": "FCA register. Creator's status on it. The promotion. The timing. Three facts that form a complete picture.",
            "savings":    "Comparison document: best easy-access savings rate available today versus average high-street savings rate paid today.",
            "general":    "The evidence, properly cited, present in frame. Not explained. Present.",
        },
        "STAT_REVEAL": {
            "isa":        f"The number alone. {brief.get('weight', '')[:40]}. Extreme close-up. Nothing else in frame.",
            "pension":    f"The pension gap. A number. Nothing else. Macro isolation.",
            "mortgage":   f"Total interest overpaid. One number. Full frame.",
            "general":    f"The number that makes it concrete. Close. Unavoidable.",
        },
        "MOVE": {
            "isa":        "Person at a laptop. ISA application page open. Cursor moves. The action is being taken. Camera moves with the resolution.",
            "pension":    "Phone in hand. Pension provider's number dialled. The first step being taken — forward motion, camera follows.",
            "mortgage":   "Mortgage comparison site open. The switch being researched. Camera tracking forward as the decision approaches.",
            "influencer": "FCA register website. A search being made. The check anyone can do in 90 seconds.",
            "savings":    "A transfer being initiated. Moving money from a 1.5% account to a 5.2% account. Camera sees the confirmation screen.",
            "general":    "The first action. Whatever the move is. Camera moves forward as it is taken.",
        },
    }

    topic_actions = actions.get(shot_type, {})
    return topic_actions.get(topic, topic_actions.get("general", f"Scene relevant to: {source_text[:80]}"))


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Harbinger Visual Prompt Engine")
    parser.add_argument("--brief",   help="Path to creative_brief JSON file")
    parser.add_argument("--output",  help="Path to write shot_list JSON output")
    parser.add_argument("--evolve",  action="store_true",
                        help="Run genome evolution pass against scored manifests")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print output without writing")
    parser.add_argument("--show-genome", action="store_true",
                        help="Print genome fitness summary")
    args = parser.parse_args()

    if args.show_genome:
        g = PromptGenome()
        print(json.dumps({
            "generation": g.data["_meta"].get("generation"),
            "total_scored": g.data["_meta"].get("total_videos_scored"),
            "evolution_history": g.data["_meta"].get("evolution_history", [])[-5:],
        }, indent=2))
        return

    if args.evolve:
        # Load all scored manifests
        manifests = []
        for p in sorted(LOGS_DIR.glob("production_manifest_*.json"),
                        key=lambda x: x.stat().st_mtime, reverse=True)[:100]:
            try:
                manifests.append(json.loads(p.read_text()))
            except Exception:
                pass
        result = self_improve(manifests)
        print(json.dumps(result, indent=2))
        return

    if not args.brief:
        parser.print_help()
        return

    brief_data = json.loads(Path(args.brief).read_text())
    result     = generate_all_prompts(brief_data)

    if args.dry_run or not args.output:
        print(json.dumps(result, indent=2))
    else:
        Path(args.output).write_text(json.dumps(result, indent=2))
        print(f"Shot list written to {args.output}")
        print(f"Shots: {result['total_shots']} | Duration: {result['total_duration_s']}s | "
              f"Genome gen: {result['genome_generation']}")


if __name__ == "__main__":
    main()
