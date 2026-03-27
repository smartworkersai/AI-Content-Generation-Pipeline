#!/usr/bin/env python3
"""
algorithm_intelligence.py — Agent 5: Algorithm Intelligence

Maintains a ranked signal model of platform algorithm factors that gate
distribution and affiliate conversion. Seeded from practitioner research
(March 2026). Confidence scores update from actual performance data as it
accumulates.

Writes:
  logs/algorithm_signals.json  — the live signal model
  logs/algorithm_directives.json — instructions consumed by Agent 2 + 3

Sends Telegram: top 3 signals + confidence deltas on each run.

Cadence: per-signal (not fixed weekly). Run daily at 05:45 via cron.
Some signals update every 5 posts. Some are stable for months.
Warm-up signals (posts 1-20) auto-expire.

Usage:
  python3 algorithm_intelligence.py          # standard run
  python3 algorithm_intelligence.py --init   # rebuild model from seed data
  python3 algorithm_intelligence.py --report # Telegram report only, no update
"""
from __future__ import annotations
import os, sys, json, datetime, re
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent.parent
LOGS_DIR   = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR.mkdir(exist_ok=True)

ALGO_LOG       = LOGS_DIR / "algorithm_intelligence.log"
SIGNALS_FILE   = LOGS_DIR / "algorithm_signals.json"
DIRECTIVES_FILE = LOGS_DIR / "algorithm_directives.json"

NOW       = datetime.datetime.utcnow()
DATE_STR  = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

ISA_DEADLINE = datetime.datetime(2026, 4, 5)

# ---------------------------------------------------------------------------
# Logging + env
# ---------------------------------------------------------------------------
def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(ALGO_LOG, "a") as f:
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
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
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
        log(f"Telegram send failed: {e}")

# ---------------------------------------------------------------------------
# Signal seed data
# Ranked by direct impact on affiliate conversion:
#   zero-follower UK finance account, posts 1-20, 18-day revenue sprint.
# Each signal: evidence, confidence, action, agent(s), feedback_loop, cadence.
# ---------------------------------------------------------------------------
SEED_SIGNALS: list[dict] = [
    {
        "id": "completion_rate_70",
        "name": "Completion Rate ≥70% (Distribution Gate — All Platforms)",
        "rank": 1,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "distribution_gate",
        "evidence": (
            "TikTok test pool: 60% completion unlocks 1K-10K distribution, 70%+ unlocks "
            "10K-100K, 75%+ triggers viral push. YouTube Shorts: 75% threshold added 2025 "
            "(up from 50%). Instagram: completion is #2 Explore signal after DM shares. "
            "Source: Syncstudio 2026, Shortimize, Hootsuite. Multiple practitioner accounts "
            "confirm these brackets are consistent across niches."
        ),
        "confidence": 0.92,
        "confidence_history": [],
        "action": (
            "MAX VIDEO DURATION 60s for posts 1-20. Script word count target: 150-170 words "
            "(≈60s at 0.88 speed). First 20 seconds must deliver standalone value — no slow "
            "reveal past 20s. The INTRUSION + WEIGHT sections must together be complete enough "
            "that a viewer who stops at 20s still got something real. MECHANISM is the reward "
            "for continuing. Never pad to fill time."
        ),
        "agent": ["creative_synthesis", "production_agent"],
        "feedback_loop": (
            "Post-accumulation (5+ posts): check video durations in production manifests as "
            "a proxy. When TikTok/YouTube analytics available: map completion rate brackets "
            "to distribution outcome per video. Update confidence thresholds if observed data "
            "contradicts research (e.g. finance niche completes better at longer duration)."
        ),
        "update_cadence_days": 7,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "tiktok_20s_sustained",
        "name": "TikTok 15-20s Sustained Engagement Threshold (Dec 2024 Shift)",
        "rank": 2,
        "platforms": ["TikTok"],
        "category": "hook_mechanics",
        "evidence": (
            "TikTok shifted its primary engagement measurement window from 3-5s hold to "
            "15-20s sustained engagement in December 2024. The algorithm now evaluates whether "
            "content holds past 20s, not just whether it survives the first hook. Source: "
            "Syncstudio 2026, PostEverywhere. Confidence note: this is reported by 2-3 "
            "practitioners; no official confirmation."
        ),
        "confidence": 0.88,
        "confidence_history": [],
        "action": (
            "Script structure: THE WEIGHT section (3-15s) and the opening of THE MECHANISM "
            "(15-25s) must collectively drive continued watching past the 20s mark. "
            "Do not resolve the core tension before 20s. The MECHANISM reveal IS the 20s+ "
            "reward. In the LLM prompt, add explicit instruction: 'The viewer must not yet "
            "understand HOW the mechanism works by the 20-second mark — they must feel the "
            "WEIGHT of not knowing and want the answer.'"
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "When TikTok analytics available: compare 20s watch-through rate vs. full "
            "completion rate across posts. If the 20s drop-off is consistently large, "
            "update the WEIGHT section pacing directives. Update confidence if platform "
            "behaviour contradicts the threshold (e.g. 10s content outperforms 20s content)."
        ),
        "update_cadence_days": 30,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "tiktok_business_account",
        "name": "TikTok Business Account: Bio Link from Post 1 (No 1K Follower Gate)",
        "rank": 3,
        "platforms": ["TikTok"],
        "category": "conversion_infrastructure",
        "evidence": (
            "TikTok Creator accounts: bio link requires 1,000 followers. Business accounts: "
            "bio link available from day 1. Capital Engine uses original ElevenLabs audio — "
            "commercial music library loss (Business account trade-off) is irrelevant. "
            "Source: Buffer bio link guide, TikTok Business Centre. Confidence: Very High — "
            "this is a confirmed platform mechanic, not a practitioner inference."
        ),
        "confidence": 0.97,
        "confidence_history": [],
        "action": (
            "ACTION REQUIRED BEFORE POST 1: Switch TikTok account to Business account in "
            "Settings → Manage Account → Switch to Business Account. Category: Finance. "
            "Verify bio link is active. Set bridge page URL in bio before any post goes live. "
            "This is a one-time action — mark complete and set cadence to 999."
        ),
        "agent": ["harbinger_core"],
        "feedback_loop": (
            "Binary: bio link active or not. Verify once. Mark resolved in signal model "
            "by setting expires_after_post to 1 when confirmed active."
        ),
        "update_cadence_days": 999,
        "warm_up_only": True,
        "expires_after_post": 1,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "fca_on_screen_risk_warning",
        "name": "FCA On-Screen Risk Warning Throughout Video (Criminal Liability if Missing)",
        "rank": 4,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "compliance",
        "evidence": (
            "FCA FG24/1 (March 2024): risk warnings in captions alone are NOT sufficient. "
            "On-screen text risk warnings required THROUGHOUT any video promoting a regulated "
            "product. Violation = criminal offence under FSMA s.21 (2 years imprisonment + "
            "unlimited fine). FCA issued 650+ takedown requests June 2025; 3 criminal "
            "proceedings. Source: FCA FG24/1, AO Shearman, Hogan Lovells. Confidence: "
            "Very High — primary regulatory source."
        ),
        "confidence": 0.99,
        "confidence_history": [],
        "action": (
            "Production Agent must burn a persistent risk warning into ALL videos where a "
            "specific regulated product (HL, Fidelity, Trading212, eToro) is named or "
            "its affiliate link is used. Required text (minimum): 'Capital at risk. Tax "
            "treatment depends on individual circumstances.' Position: bottom of frame, "
            "white text on semi-transparent dark bar, persistent throughout video. "
            "This is implemented in the FFmpeg render pipeline via drawtext filter. "
            "Threshold: brief.affiliate.name is set and non-empty."
        ),
        "agent": ["production_agent"],
        "feedback_loop": (
            "Verified at render time — check brief for affiliate name before each render. "
            "Binary pass/fail. Update cadence: 90 days (regulatory framework stable). "
            "Monitor FCA enforcement actions for any updated guidance on required wording."
        ),
        "update_cadence_days": 90,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "cta_platform_differentiation",
        "name": "Platform CTA: Sends/DM (Instagram) · Saves (TikTok) · Channel Desc (YouTube)",
        "rank": 5,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "engagement_optimization",
        "evidence": (
            "Instagram #1 Explore signal (Mosseri confirmed, 2025): sends per reach (DM shares). "
            "TikTok highest-weight post-completion signal: saves (2025 algorithm). YouTube Shorts: "
            "no clickable bio links; channel description is the only linked path. "
            "Engagement hierarchy TikTok: shares (3x weight) > saves > comments > rewatches > likes. "
            "Source: Hootsuite citing Mosseri, Buffer, Affiverse, Socialinsider 2025."
        ),
        "confidence": 0.87,
        "confidence_history": [],
        "action": (
            "Creative Synthesis must generate platform-differentiated MOVE sections. "
            "TikTok MOVE: 'Save this before the ISA deadline.' "
            "Instagram MOVE (verbal): 'If this changes what you do with your ISA this year, "
            "send it to someone who needs to see it.' "
            "YouTube Shorts MOVE: 'The link is in my channel description.' "
            "NEVER use 'click the link in bio' — use 'check my profile' to reduce NLP moderation "
            "flags. NEVER make 'comment below' the primary CTA — it is the lowest-weight signal."
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "After 10+ posts: compare save rate for TikTok posts with explicit save CTA vs. "
            "generic CTA. When Instagram analytics available: sends-per-reach for DM CTA posts "
            "vs. generic. Target: Instagram sends/reach >3% for Explore eligibility. "
            "Update confidence if one CTA type consistently outperforms."
        ),
        "update_cadence_days": 14,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "niche_consistency_warmup",
        "name": "100% Finance Niche Consistency (Posts 1-20 — Algorithm Classification)",
        "rank": 6,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "warm_up",
        "evidence": (
            "TikTok: requires 5-7 consecutive on-niche posts to classify a new account. "
            "Off-topic posts during this window reset confidence and can misclassify the account. "
            "YouTube: 9-12 consistent Shorts before reliable niche resolution. "
            "Instagram: micro-niche classification converges at posts 9-12. "
            "Source: MakeViral warm-up guide, Syncstudio, Napolify, onestream."
        ),
        "confidence": 0.90,
        "confidence_history": [],
        "action": (
            "Creative Synthesis: content category must be one of: savings/ISA, tax efficiency, "
            "investing basics, mortgage/base rate impact, salary benchmarking. "
            "ZERO off-topic content until post 20. Format variation within niche is encouraged "
            "(hook types, video lengths, visual approaches). Zero topic variation. "
            "If asymmetry_brief.json surfaces a non-finance topic, discard and use previous brief."
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "Check each creative brief output for niche category. Track niche distribution "
            "across all briefs. Alert via Telegram if any non-finance topic detected. "
            "Signal expires at post 20 — after that, limited cross-topic testing permitted."
        ),
        "update_cadence_days": 5,
        "warm_up_only": True,
        "expires_after_post": 20,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "hook_number_loss_frame",
        "name": "Hook: Specific Number + Loss/Violation Frame (No Question Hooks)",
        "rank": 7,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "hook_mechanics",
        "evidence": (
            "71% of watch continuation determined within 3 seconds. 3-second retention >65% "
            "delivers 4-7x more impressions. Documented high-performing finance hook patterns: "
            "(1) Balance reveal: 'I'm 28 with £47 in my account and £31K in my ISA' — 1.3M-7.1M views, "
            "(2) Pattern interrupt stat: 'Most people lose £3K/year to this', "
            "(3) Counter-intuitive claim: 'Saving money is the worst thing you can do.' "
            "Question hooks ('Did you know...?') now underperform — overused. "
            "Source: OpusClip hook analysis, Social Growth Engineers 44M-view case study, HeyOrca."
        ),
        "confidence": 0.85,
        "confidence_history": [],
        "action": (
            "Creative Synthesis: THE INTRUSION must contain either: "
            "(1) A specific £/number figure, "
            "(2) A named violation of assumed reality ('The savings account your bank is hiding'), "
            "(3) A counter-intuitive claim with no question mark. "
            "NEVER open with a question. NEVER use 'Did you know'. "
            "The first word should be: 'You', a number, or a named entity. "
            "Inject into LLM prompt: 'The INTRUSION must assume the viewer already suspects "
            "something is wrong — not ask them if they do.'"
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "Categorise each intrusion by hook type: number / loss-frame / counter-intuitive / "
            "question (flag as violation). Track completion rate per hook type when available. "
            "After 10 posts with analytics: update confidence and rerank hook types by "
            "observed completion rate. Replace lowest-performing hook type in directives."
        ),
        "update_cadence_days": 7,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "isa_savings_topic_priority",
        "name": "ISA/Savings Topic = Highest Affiliate Value + Lowest FCA Risk",
        "rank": 8,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "content_strategy",
        "evidence": (
            "Fidelity ISA: £100/conversion (Awin) — highest per-conversion rate in accessible UK "
            "finance affiliate programmes. ISA content: low FCA risk (educational, no direct product "
            "recommendation required), high save behaviour, April 5 deadline creates natural urgency. "
            "Finance topic virality ranking: savings rate comparisons #1, tax efficiency #2. "
            "HL Pension Calculator: £80/conversion — underutilised, strong content angle. "
            "Source: Awin Fidelity listing, LinkClicky HL rates, Social Growth Engineers."
        ),
        "confidence": 0.82,
        "confidence_history": [],
        "action": (
            "Posts 1-10: minimum 60% ISA/savings-adjacent content. "
            "Posts 11-20: maintain 40%+ ISA/savings, introduce HL Pension Calculator "
            "('Are you saving enough for retirement?') and investing basics (Trading212 CPA). "
            "Cultural Radar: from March 15, flag ISA deadline as maximum-urgency signal — "
            "all 3 daily slots should have ISA-adjacent angle. "
            "Affiliate priority order: (1) Fidelity ISA £100, (2) HL Pension Calculator £80, "
            "(3) Trading212 up to $1000."
        ),
        "agent": ["creative_synthesis", "cultural_radar"],
        "feedback_loop": (
            "Track affiliate category per post in production manifests. "
            "When conversion data available: compare revenue per affiliate programme vs. post count. "
            "If HL Pension Calculator outperforms Fidelity ISA per post: rerank. "
            "ISA deadline urgency expires April 5."
        ),
        "update_cadence_days": 7,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "isa_deadline_countdown",
        "name": "ISA Deadline Countdown: April 5 — Maximum Leverage Window (27 Days)",
        "rank": 9,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "content_strategy",
        "evidence": (
            "UK ISA allowance: £20,000. Unused by April 5 = permanently lost (not carried forward). "
            "This is a hard, verifiable deadline creating genuine urgency — not manufactured scarcity. "
            "Fidelity ISA affiliate: £100/conversion. Window: March 9 - April 5 = 27 days, "
            "overlapping almost exactly with the 18-day sprint. Last 10 days of March are the "
            "peak ISA search volume period. Time-bound finance hooks have highest documented "
            "completion + save rates. Source: HMRC ISA statistics, Google Trends UK ISA patterns, "
            "practitioner pattern analysis."
        ),
        "confidence": 0.85,
        "confidence_history": [],
        "action": (
            "From March 15 onwards: ISA deadline countdown MUST appear in every ISA-adjacent hook. "
            "Format: 'You have [X] days left to use this year's ISA allowance.' "
            "Days remaining injected dynamically from algorithm_directives.json (isa_deadline_days field). "
            "Creative Synthesis: if topic is ISA/savings, the countdown is MANDATORY in the INTRUSION "
            "or first line of WEIGHT. Do not soft-pedal it — the deadline is real and the urgency "
            "is the conversion hook."
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "Track click-throughs on Fidelity ISA affiliate link per week. "
            "Expected pattern: conversion rate rises as April 5 approaches, peaks March 28 - April 4. "
            "If conversion rate does not rise in final 10 days: investigate whether bridge page is "
            "performing and whether the CTA is reaching the right audience."
        ),
        "update_cadence_days": 1,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "original_audio_preference",
        "name": "Original Audio: 3x Algorithmic Weight vs Trending Sounds (TikTok)",
        "rank": 10,
        "platforms": ["TikTok"],
        "category": "production_quality",
        "evidence": (
            "2025 practitioner reports: original audio receives approximately 3x more algorithmic "
            "weight than trending sounds. Using trending audio without adding unique value now "
            "results in ~60% reach reduction vs. original audio. 38% of trending sounds have AI "
            "involvement; original AI audio not penalised. "
            "Source: Dash Social, Napolify, UseVisuals. "
            "Confidence note: the 3x figure is from a single source — treat as directional. "
            "Confidence held at 0.75 until second-source confirmation."
        ),
        "confidence": 0.75,
        "confidence_history": [],
        "action": (
            "Maintain ElevenLabs original voice + SFX + sub-bass ambient stack. "
            "Do NOT add trending sounds as background under the voiceover — "
            "this is now counterproductive. The three-layer audio mix (voice + ambience + sub-bass) "
            "is the correct approach and is already implemented."
        ),
        "agent": ["production_agent"],
        "feedback_loop": (
            "If an A/B test is run (trending sound vs. original audio on same content): "
            "compare completion rates and distribution breadth. Update confidence from 0.75 "
            "to 0.90+ if original outperforms, or to 0.40 if trending consistently outperforms. "
            "Currently no test data."
        ),
        "update_cadence_days": 30,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "bridge_page_mandatory",
        "name": "Bridge Page Pre-Sell: Reduces Bio-Link Drop-Off by 30-50%",
        "rank": 11,
        "platforms": ["TikTok", "YouTube", "Instagram"],
        "category": "conversion_infrastructure",
        "evidence": (
            "Requiring app exit via bio link reduces conversions 30-50% vs. in-app checkout. "
            "No in-app checkout exists for UK finance products (HL, Fidelity not on TikTok Shop). "
            "Bridge page with one CTA + pre-sell copy (restates hook, shows product benefit) "
            "outperforms sending traffic directly to the affiliate landing page. "
            "Source: Affiverse CTA guide, Way2Earning, EcommerceFastlane."
        ),
        "confidence": 0.78,
        "confidence_history": [],
        "action": (
            "Bridge page must be live before post 1. Requirements: "
            "(1) Restate the hook from the video (continuity — visitor arrived because of the hook), "
            "(2) One product benefit statement (not the affiliate landing page copy), "
            "(3) Single button CTA linking to the affiliate URL. "
            "Do NOT send traffic directly to HL/Fidelity affiliate URL from bio — "
            "bridge page is a required conversion layer."
        ),
        "agent": ["harbinger_core"],
        "feedback_loop": (
            "When UTM data available: track click-through rate from bridge page to affiliate URL. "
            "Compare with and without pre-sell copy variants. "
            "Target bridge page CTR: >35%. If below 25%: revise bridge page copy."
        ),
        "update_cadence_days": 14,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "no_deletion_set_private",
        "name": "No Post Deletion: Set Underperformers to Private (Not Delete)",
        "rank": 12,
        "platforms": ["TikTok", "Instagram"],
        "category": "warm_up",
        "evidence": (
            "TikTok penalises deletion patterns as a low-quality account signal. "
            "Repeated deletion in the first 20 posts can trigger shadow restriction. "
            "Setting to 'Only Me' (TikTok) or restricting visibility preserves account health. "
            "Source: Shopify shadow ban guide, GoLogin shadow ban analysis, MakeViral."
        ),
        "confidence": 0.80,
        "confidence_history": [],
        "action": (
            "Do not delete any posts. If a post receives under 500 views after 72 hours on TikTok: "
            "change visibility to 'Only Me' — do not delete. "
            "Log which posts are set to private for tracking. "
            "Telegram alert if any deletion event is detected."
        ),
        "agent": ["harbinger_core"],
        "feedback_loop": (
            "Track any deletion events. If shadow ban occurs after deletion: "
            "update confidence to 0.95 and escalate via Telegram. "
            "Signal expires at post 20."
        ),
        "update_cadence_days": 999,
        "warm_up_only": True,
        "expires_after_post": 20,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "instagram_dm_share_cta",
        "name": "Instagram: DM Share CTA = #1 Explore Signal (Mosseri Confirmed)",
        "rank": 13,
        "platforms": ["Instagram"],
        "category": "engagement_optimization",
        "evidence": (
            "Adam Mosseri (Instagram CEO), 2025: 'sends per reach' is the single most powerful "
            "Instagram Explore signal. DM sharing = 'strong enough to recommend to someone you care "
            "about.' Saves are #2. Likes are #4. Comments are lowest weight. "
            "Viewers decide in 1.7 seconds whether to continue watching on Instagram. "
            "Source: Hootsuite citing Mosseri statements, Dataslayer, funnl.ai."
        ),
        "confidence": 0.90,
        "confidence_history": [],
        "action": (
            "Instagram Reels MOVE section must include verbal DM share CTA: "
            "'If this changes what you do with your ISA this year, send it to someone who needs it.' "
            "This CTA must be spoken by the narrator — not just in the caption. "
            "Caption CTA: 'Tag someone who's leaving money on the table.' "
            "Secondary: 'Save this for when you open your ISA.' "
            "NEVER make 'comment below' the primary Instagram CTA."
        ),
        "agent": ["creative_synthesis"],
        "feedback_loop": (
            "When Instagram analytics available: track sends-per-reach metric. "
            "Target: >3% sends per reach for Explore page eligibility. "
            "Compare posts with explicit DM CTA vs. save CTA — whichever drives more Explore "
            "reach becomes the primary. Update confidence based on observed sends data."
        ),
        "update_cadence_days": 14,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "native_upload_warmup",
        "name": "Native Upload Only (No Buffer) for Posts 1-20 — Shadow Ban Avoidance",
        "rank": 14,
        "platforms": ["TikTok", "Instagram"],
        "category": "warm_up",
        "evidence": (
            "TikTok gives algorithmic preference to native creation (in-app camera or TikTok's own "
            "scheduling API). Third-party scheduling tools may trigger new account shadow restriction "
            "flags in the first 20 posts. Confidence is medium — no confirmed platform statement; "
            "this is practitioner consensus. "
            "Source: MakeViral warm-up guide, GoLogin shadow ban analysis."
        ),
        "confidence": 0.72,
        "confidence_history": [],
        "action": (
            "Distribute.sh: for posts 1-20, use TikTok's own scheduling API rather than Buffer "
            "for TikTok uploads. Instagram: use Instagram's Creator Studio scheduling (not Buffer) "
            "for posts 1-20. Reassess after post 20 — Buffer is acceptable once account is "
            "established. Telegram alert when post count reaches 18 to plan Buffer reactivation."
        ),
        "agent": ["harbinger_core"],
        "feedback_loop": (
            "If shadow ban is detected (content not appearing in hashtag search for non-followers): "
            "immediately check if Buffer was used and update confidence to 0.90+. "
            "If no shadow ban occurs at post 20 with native upload: signal expires, confidence confirmed."
        ),
        "update_cadence_days": 5,
        "warm_up_only": True,
        "expires_after_post": 20,
        "last_validated": DATE_STR,
        "active": True,
    },
    {
        "id": "youtube_shorts_channel_desc_link",
        "name": "YouTube Shorts: No Clickable Bio Links — Channel Description Is the Only Path",
        "rank": 15,
        "platforms": ["YouTube"],
        "category": "conversion_infrastructure",
        "evidence": (
            "YouTube removed clickable links from Shorts descriptions August 31 2023 — not restored. "
            "Clickable links in pinned comments also blocked for Shorts. "
            "Channel description About section: links are clickable from day 1 (no follower threshold). "
            "YouTube does not suppress content for mentioning affiliate products. "
            "Alternative workaround: QR codes in video (QR scanning up 2x among mobile users 2025). "
            "Source: Affiverse, Logie.ai, Cuelinks — confirmed by multiple sources. Very High confidence."
        ),
        "confidence": 0.97,
        "confidence_history": [],
        "action": (
            "YouTube Shorts CTA: verbal instruction to 'find the link in my channel description.' "
            "Channel About section must have affiliate link live before post 1. "
            "Alternative: burn QR code into bottom-right of video frame (above risk warning) "
            "pointing to bridge page — implement in production_agent as optional overlay. "
            "Do not use 'link in description' as the verbal CTA — Shorts have no description link."
        ),
        "agent": ["production_agent", "creative_synthesis"],
        "feedback_loop": (
            "Track channel description link clicks when YouTube analytics available. "
            "Compare with QR code scan rate if QR overlay is implemented. "
            "Signal is stable — update cadence 90 days unless YouTube restores Shorts bio links."
        ),
        "update_cadence_days": 90,
        "warm_up_only": False,
        "expires_after_post": None,
        "last_validated": DATE_STR,
        "active": True,
    },
]

# ---------------------------------------------------------------------------
# Observable behavior study — derive rules from what actually happens
# ---------------------------------------------------------------------------

def _fetch_youtube_view_counts(yt_key: str, video_ids: list[str]) -> dict[str, int]:
    """Fetch public view counts for a list of YouTube video IDs.
    Returns {video_id: view_count}. Batches in groups of 50."""
    if not yt_key or not video_ids:
        return {}
    try:
        from googleapiclient.discovery import build
        youtube = build("youtube", "v3", developerKey=yt_key)
        views   = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]
            resp  = youtube.videos().list(
                part="statistics", id=",".join(batch)
            ).execute()
            for item in resp.get("items", []):
                views[item["id"]] = int(
                    item.get("statistics", {}).get("viewCount", 0)
                )
        return views
    except Exception as e:
        log(f"observe: YouTube view fetch failed: {e}")
        return {}


def _load_distribution_manifests() -> list[dict]:
    """Load all distribution manifests (logs/manifest_*.json)."""
    files = sorted(
        LOGS_DIR.glob("manifest_*_slot*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:30]
    results = []
    for f in files:
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            pass
    return results


def _load_timing_reports() -> list[dict]:
    """Load all timing reports (character-level → word timing analysis)."""
    files = sorted(
        LOGS_DIR.glob("timing_report_*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:20]
    results = []
    for f in files:
        try:
            tr = json.loads(f.read_text())
            # Attach slot from filename
            m = __import__("re").search(r"_slot(\d)", f.name)
            if m:
                tr["_slot"] = int(m.group(1))
            results.append(tr)
        except Exception:
            pass
    return results


def _load_loop_a_scores() -> list[dict]:
    """Load all Loop A visual scores."""
    files = sorted(
        LOGS_DIR.glob("loop_a_scores_*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )[:20]
    results = []
    for f in files:
        try:
            results.append(json.loads(f.read_text()))
        except Exception:
            pass
    return results


def study_observable_behavior(model: dict) -> dict:
    """Derive algorithm signal confidence updates from actual observed behavior.

    The algorithm is not a black box. It has rules that surface in data:
    - which posting slots get more distribution (view counts by slot time)
    - which hook delivery pace correlates with higher retention proxies
    - which visual identity scores (Loop A) correlate with distribution
    - which chain scores (Loop B) correlate with what gets posted vs skipped

    Updates signal confidence IN PLACE in model['signals'].
    Returns findings dict for logging and Telegram.
    """
    yt_key   = os.environ.get("YOUTUBE_API_KEY", "")
    findings = {
        "timestamp": NOW.isoformat(),
        "slot_distribution": {},
        "timing_patterns":   {},
        "visual_patterns":   {},
        "confidence_updates": [],
    }

    # ── 1. Slot time → YouTube view counts ──────────────────────────────────
    # Each distribution manifest contains api_results.youtube.post_id
    dist_manifests = _load_distribution_manifests()
    slot_view_data: dict[int, list[int]] = {}

    if dist_manifests and yt_key:
        # Collect YouTube post_ids by slot
        yt_ids_by_slot: dict[int, list[str]] = {}
        for dm in dist_manifests:
            slot = dm.get("slot")
            yt_result = dm.get("api_results", {}).get("youtube", {})
            post_id   = yt_result.get("post_id")
            if slot and post_id and yt_result.get("status") == "posted":
                yt_ids_by_slot.setdefault(slot, []).append(post_id)

        # Fetch views for all IDs at once
        all_ids   = [vid for vids in yt_ids_by_slot.values() for vid in vids]
        view_map  = _fetch_youtube_view_counts(yt_key, all_ids)

        for slot, vids in yt_ids_by_slot.items():
            counts = [view_map.get(v, 0) for v in vids if view_map.get(v, 0) > 0]
            if counts:
                slot_view_data[slot] = counts
                avg = sum(counts) // len(counts)
                findings["slot_distribution"][f"slot_{slot}"] = {
                    "videos": len(counts),
                    "avg_views": avg,
                    "max_views": max(counts),
                }
                log(f"observe: slot {slot} → {len(counts)} videos, avg {avg} views, max {max(counts)}")

        # Infer best/worst slot from views if ≥2 slots have data
        if len(slot_view_data) >= 2:
            best_slot = max(slot_view_data, key=lambda s: sum(slot_view_data[s]) / len(slot_view_data[s]))
            findings["slot_distribution"]["best_slot"] = best_slot
            log(f"observe: best distribution slot = {best_slot}")

    # ── 2. Hook delivery pace from timing reports ─────────────────────────
    timing_reports = _load_timing_reports()
    if timing_reports:
        wps_values = [
            tr.get("hook_pace", {}).get("hook_wps", 0)
            for tr in timing_reports
            if tr.get("hook_pace", {}).get("hook_wps", 0) > 0
        ]
        silence_values = [
            tr.get("pre_hook_silence_ms", 0)
            for tr in timing_reports
            if tr.get("pre_hook_silence_ms", 0) >= 0
        ]
        if wps_values:
            avg_wps = round(sum(wps_values) / len(wps_values), 2)
            findings["timing_patterns"]["avg_hook_wps"]     = avg_wps
            findings["timing_patterns"]["samples"]          = len(wps_values)
            findings["timing_patterns"]["avg_pre_hook_silence_ms"] = (
                round(sum(silence_values) / len(silence_values)) if silence_values else 0
            )
            log(f"observe: avg hook pace = {avg_wps} wps over {len(wps_values)} renders")

            # If hook pace is below 2.5 wps, flag the hook_3s_pattern signal
            if avg_wps < 2.5:
                findings["timing_patterns"]["flag"] = "hook_pace_slow — consider tighter hook delivery"
            elif avg_wps > 4.5:
                findings["timing_patterns"]["flag"] = "hook_pace_fast — may be losing comprehension"

    # ── 3. Visual identity patterns from Loop A scores ───────────────────
    loop_a_scores = _load_loop_a_scores()
    if loop_a_scores:
        visual_scores = [
            s.get("avg_visual_score", 0)
            for s in loop_a_scores
            if s.get("avg_visual_score") is not None
        ]
        if visual_scores:
            avg_visual = round(sum(visual_scores) / len(visual_scores), 2)
            findings["visual_patterns"]["avg_loop_a_score"]  = avg_visual
            findings["visual_patterns"]["samples"]           = len(visual_scores)
            findings["visual_patterns"]["below_threshold"]   = sum(1 for s in visual_scores if s < 6)
            log(f"observe: avg visual identity score = {avg_visual} over {len(visual_scores)} renders")

            # If average visual score < 6.0, `visual_identity_precondition` needs attention
            if avg_visual < 6.0:
                findings["visual_patterns"]["flag"] = (
                    "visual_identity_weak — Loop A scores below 6.0 average; "
                    "visual identity is not executing research-derived brief"
                )

    # ── 4. Signal confidence updates from observed data ──────────────────
    for signal in model["signals"]:
        sid = signal["id"]
        old_conf = signal["confidence"]
        new_conf = old_conf  # default: no change

        # completion_rate_gate: if slot_view_data exists, high view counts
        # on short videos is indirect evidence the completion gate was passed
        if sid == "completion_rate_70" and slot_view_data:
            # Any video with >1000 views has almost certainly passed the test pool
            total_vids   = sum(len(v) for v in slot_view_data.values())
            high_dist    = sum(1 for vids in slot_view_data.values() for v in vids if v > 1000)
            if total_vids >= 3:
                pass_rate = high_dist / total_vids
                # Nudge confidence toward observed pass rate (dampened)
                new_conf = round(old_conf + 0.05 * (pass_rate - old_conf), 3)

        # hook_3s_pattern: if timing shows avg_hook_wps < 2.0, lower confidence
        # (our hooks are too slow; the signal's prescription isn't being executed)
        if sid == "hook_3s_pattern" and findings["timing_patterns"].get("avg_hook_wps"):
            wps = findings["timing_patterns"]["avg_hook_wps"]
            if wps < 2.0:
                new_conf = round(min(old_conf, 0.65), 3)  # cap — execution gap
            elif wps >= 2.5:
                new_conf = round(min(0.95, old_conf + 0.01), 3)  # evidence of compliance

        if new_conf != old_conf and abs(new_conf - old_conf) > 0.005:
            signal["confidence_history"].append({
                "date":   DATE_STR,
                "old":    round(old_conf, 3),
                "new":    new_conf,
                "source": "observe_behavior",
            })
            signal["confidence"] = new_conf
            signal["last_validated"] = DATE_STR
            findings["confidence_updates"].append({
                "signal": sid,
                "old":    old_conf,
                "new":    new_conf,
            })
            log(f"observe: {sid} confidence {old_conf:.3f} → {new_conf:.3f}")

    findings["total_signals_updated"] = len(findings["confidence_updates"])

    # Write findings to disk
    obs_path = LOGS_DIR / f"observed_behavior_{DATE_STR}.json"
    obs_path.write_text(json.dumps(findings, indent=2))
    log(f"observe: findings saved to {obs_path.name}")

    return findings


# ---------------------------------------------------------------------------
# Model load / save
# ---------------------------------------------------------------------------
def load_signals() -> dict:
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text())
        except Exception:
            pass
    return {
        "model_version": "1.0",
        "created": NOW.isoformat(),
        "last_updated": NOW.isoformat(),
        "post_count": 0,
        "warm_up_active": True,
        "signals": SEED_SIGNALS,
        "telegram_last_top3": [],
    }

def save_signals(model: dict):
    model["last_updated"] = NOW.isoformat()
    SIGNALS_FILE.write_text(json.dumps(model, indent=2))

# ---------------------------------------------------------------------------
# Performance data ingestion
# ---------------------------------------------------------------------------
def get_post_count() -> int:
    """Estimate post count from production manifests."""
    return len(list(LOGS_DIR.glob("production_manifest_*_slot*.json")))

def get_recent_performance() -> list[dict]:
    """Load 10 most recent production manifests."""
    manifests = sorted(
        LOGS_DIR.glob("production_manifest_*_slot*.json"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )[:10]
    results = []
    for m in manifests:
        try:
            results.append(json.loads(m.read_text()))
        except Exception:
            pass
    return results

# ---------------------------------------------------------------------------
# Signal lifecycle
# ---------------------------------------------------------------------------
def is_signal_due(signal: dict) -> bool:
    last = signal.get("last_validated")
    if not last:
        return True
    try:
        last_dt = datetime.datetime.strptime(last, "%Y-%m-%d")
        return (NOW - last_dt).days >= signal.get("update_cadence_days", 7)
    except Exception:
        return True

def is_signal_expired(signal: dict, post_count: int) -> bool:
    expires = signal.get("expires_after_post")
    if expires is None:
        return False
    return post_count >= expires

def update_confidence(signal: dict, performance: list[dict]) -> float:
    """Update confidence score from observable production data.
    Returns new confidence. Platform analytics not yet available —
    uses production manifest proxies where possible."""
    current = signal["confidence"]
    if not performance:
        return current

    sid = signal["id"]

    if sid == "completion_rate_70":
        # Proxy: videos longer than 65s are unlikely to hit 70% completion
        durations = [
            p.get("quality_check", {}).get("duration_s", 0)
            for p in performance
            if p.get("quality_check", {}).get("duration_s")
        ]
        if durations:
            over_65 = sum(1 for d in durations if d > 65) / len(durations)
            if over_65 > 0.5:
                log(f"  [WARN] {sid}: {int(over_65*100)}% of recent videos exceed 65s")
        # Confidence in the signal itself remains stable until analytics confirm
        return current

    if sid == "video_duration_60s_max":
        durations = [
            p.get("quality_check", {}).get("duration_s", 0)
            for p in performance
            if p.get("quality_check", {}).get("duration_s")
        ]
        if durations:
            compliant = sum(1 for d in durations if d <= 65) / len(durations)
            # If we're consistently hitting the target, slight confidence boost
            if compliant > 0.8:
                return min(0.95, current + 0.02)
            elif compliant < 0.4:
                log(f"  [WARN] {sid}: only {int(compliant*100)}% of videos within 65s target")
        return current

    # All other signals: hold confidence stable until platform analytics arrive
    return current

# ---------------------------------------------------------------------------
# Directives generation
# ---------------------------------------------------------------------------
def generate_directives(model: dict) -> dict:
    """Generate structured directives for Agent 2 (creative_synthesis) and
    Agent 3 (production_agent) from the current ranked signal model."""
    post_count     = model.get("post_count", 0)
    days_to_isa    = max(0, (ISA_DEADLINE - NOW).days)
    warm_up_active = post_count < 20

    active_signals = [
        s for s in model["signals"]
        if s.get("active", True) and not is_signal_expired(s, post_count)
    ]
    active_signals.sort(key=lambda s: s["rank"])

    synthesis_parts   = []
    production_parts  = []
    infra_parts       = []

    for s in active_signals:
        agents = s.get("agent", [])
        if isinstance(agents, str):
            agents = [agents]
        action = s.get("action", "")

        if "creative_synthesis" in agents:
            synthesis_parts.append(
                f"[ALGO SIGNAL #{s['rank']} — confidence {int(s['confidence']*100)}%]\n"
                f"{s['name']}\n{action}"
            )
        if "production_agent" in agents:
            production_parts.append(
                f"[ALGO SIGNAL #{s['rank']} — confidence {int(s['confidence']*100)}%]\n"
                f"{s['name']}\n{action}"
            )
        if "harbinger_core" in agents:
            infra_parts.append(
                f"[INFRA SIGNAL #{s['rank']}] {s['name']}: {action}"
            )

    # ISA countdown injection
    if days_to_isa <= 27:
        urgency = (
            f"\n[CRITICAL TIME SIGNAL — {days_to_isa} DAYS TO ISA DEADLINE]\n"
            f"If content is ISA/savings-adjacent, the hook MUST include: "
            f"'You have {days_to_isa} days left to use this year's ISA allowance.' "
            f"This is a non-negotiable directive until April 5. Priority: maximum."
        )
        synthesis_parts.insert(0, urgency)

    # Warm-up status
    warmup_note = (
        f"\n[WARM-UP STATUS: {post_count}/20 posts published — "
        f"{'ACTIVE: algorithm is classifying this account' if warm_up_active else 'COMPLETE: niche established'}]\n"
        f"{'Zero topic variation permitted until post 20.' if warm_up_active else ''}"
    )
    synthesis_parts.insert(0, warmup_note)

    return {
        "generated":              NOW.isoformat(),
        "post_count":             post_count,
        "warm_up_active":         warm_up_active,
        "isa_deadline_days":      days_to_isa,
        "instructions":           "\n\n---\n\n".join(synthesis_parts),
        "production_instructions": "\n\n---\n\n".join(production_parts),
        "infra_instructions":     infra_parts,
        "top_signals": [
            {
                "rank":       s["rank"],
                "name":       s["name"],
                "confidence": s["confidence"],
                "action_summary": s["action"][:120],
            }
            for s in active_signals[:5]
        ],
    }

# ---------------------------------------------------------------------------
# Telegram report
# ---------------------------------------------------------------------------
def build_telegram_report(model: dict, prev_top3: list) -> str:
    post_count  = model.get("post_count", 0)
    days_to_isa = max(0, (ISA_DEADLINE - NOW).days)

    active = [
        s for s in model["signals"]
        if s.get("active", True) and not is_signal_expired(s, post_count)
    ]
    active.sort(key=lambda s: s["rank"])
    top3 = active[:3]

    prev_map = {p.get("id"): p.get("confidence", 0) for p in prev_top3}

    lines = [
        "🧠 *Algorithm Intelligence*",
        f"Sprint day {(NOW - datetime.datetime(2026, 3, 9)).days + 1}/18 | "
        f"Posts: {post_count}/20 warm-up | ISA deadline: {days_to_isa}d",
        "",
        "*Top 3 Signals:*",
    ]

    for s in top3:
        prev_conf = prev_map.get(s["id"])
        if prev_conf is not None:
            delta = s["confidence"] - prev_conf
            delta_str = f" ▲{delta:.2f}" if delta > 0.005 else (
                f" ▼{abs(delta):.2f}" if delta < -0.005 else " →"
            )
        else:
            delta_str = " (new)"

        conf_bar = "█" * int(s["confidence"] * 10) + "░" * (10 - int(s["confidence"] * 10))
        lines.append(
            f"*{s['rank']}.* {s['name'][:55]}\n"
            f"   {conf_bar} {int(s['confidence']*100)}%{delta_str}"
        )

    # Flag any warm-up signals expiring soon
    expiring = [
        s for s in active
        if s.get("warm_up_only") and s.get("expires_after_post") and
        post_count >= (s["expires_after_post"] - 3)
    ]
    if expiring:
        lines.append("")
        lines.append("⏳ *Expiring warm-up signals:*")
        for s in expiring:
            lines.append(f"  • {s['name'][:60]} (expires post {s['expires_after_post']})")

    if days_to_isa <= 10:
        lines.append("")
        lines.append(f"🚨 *ISA DEADLINE: {days_to_isa} DAYS — maximum urgency active*")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--init",   action="store_true", help="Rebuild model from seed data")
    parser.add_argument("--report", action="store_true", help="Telegram report only, no signal updates")
    args = parser.parse_args()

    load_env()
    log("=" * 60)
    log("AGENT 5: ALGORITHM INTELLIGENCE")
    log("=" * 60)

    if args.init:
        log("Initialising model from seed data...")
        model = {
            "model_version": "1.0",
            "created": NOW.isoformat(),
            "last_updated": NOW.isoformat(),
            "post_count": 0,
            "warm_up_active": True,
            "signals": SEED_SIGNALS,
            "telegram_last_top3": [],
        }
    else:
        model = load_signals()

    # Update post count and warm-up status
    post_count = get_post_count()
    model["post_count"]     = post_count
    model["warm_up_active"] = post_count < 20
    log(f"Post count: {post_count} | Warm-up: {'active' if model['warm_up_active'] else 'complete'}")

    if not args.report:
        # Observable behavior study — derive signal confidence from actual data,
        # not documentation. What gets distributed, what gets suppressed, what pacing
        # the system is actually producing. Runs before the documentation-based update loop
        # so behavioral evidence takes priority over theoretical confidence estimates.
        obs = study_observable_behavior(model)
        log(f"Observed behavior: {obs.get('total_signals_updated', 0)} signals updated from data")

        performance = get_recent_performance()
        log(f"Loaded {len(performance)} recent production manifests for confidence updates")

        updates = 0
        for signal in model["signals"]:
            # Auto-expire warm-up signals
            if is_signal_expired(signal, post_count) and signal.get("active", True):
                signal["active"] = False
                log(f"  Expired: {signal['name']} (post {post_count} ≥ {signal['expires_after_post']})")
                updates += 1
                continue

            if is_signal_due(signal) or args.init:
                old_conf = signal["confidence"]
                new_conf = update_confidence(signal, performance)
                if abs(new_conf - old_conf) > 0.01:
                    signal["confidence_history"].append({
                        "date": DATE_STR,
                        "old":  round(old_conf, 3),
                        "new":  round(new_conf, 3),
                    })
                    signal["confidence"] = round(new_conf, 3)
                    log(f"  Updated: {signal['name']} {old_conf:.2f} → {new_conf:.2f}")
                    updates += 1
                signal["last_validated"] = DATE_STR

        log(f"Signal updates: {updates}")

        # Write directives for Agent 2 + 3
        directives = generate_directives(model)
        DIRECTIVES_FILE.write_text(json.dumps(directives, indent=2))
        log(f"Directives written: {DIRECTIVES_FILE.name} ({len(directives['instructions'])} chars synthesis, "
            f"{len(directives['production_instructions'])} chars production)")
    else:
        directives = generate_directives(model)

    # Telegram report
    prev_top3  = model.get("telegram_last_top3", [])
    report     = build_telegram_report(model, prev_top3)
    send_telegram(report)
    log("Telegram report sent")

    # Update last top3 record
    active = sorted(
        [s for s in model["signals"] if s.get("active", True)],
        key=lambda s: s["rank"]
    )
    model["telegram_last_top3"] = [
        {"id": s["id"], "confidence": s["confidence"]}
        for s in active[:3]
    ]

    save_signals(model)
    log(f"Model saved: {SIGNALS_FILE.name}")
    log("=" * 60)

    print(json.dumps(directives, indent=2))

if __name__ == "__main__":
    main()
