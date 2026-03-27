#!/usr/bin/env python3
"""
ceo_audit.py — CEO Intelligence Loop

After every distribution, reviews the slot's brief, render data, and caption
timing. Sends one additional Telegram message: brutal, specific, no padding.

Standard: someone scrolling on a phone at 2am should stop on this video.
The audit identifies the single biggest thing preventing that, and states
exactly what changes for the next slot. If it has nothing specific to say,
it is not looking hard enough.

Usage: python3 ceo_audit.py --slot <1-7>
"""
from __future__ import annotations
import os, sys, json, datetime, argparse
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent.parent
LOGS_DIR   = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
SCRIPTS_DIR = Path(__file__).parent.parent

NOW = datetime.datetime.utcnow()


def log(msg: str):
    print(f"[ceo_audit] {msg}")


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


def send_telegram(msg: str):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log("Telegram not configured — audit message not sent")
        return
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )
        if r.status_code == 200:
            log("CEO audit sent to Telegram")
        else:
            log(f"Telegram send failed: {r.status_code}")
    except Exception as e:
        log(f"Telegram error: {e}")


def load_latest(pattern: str) -> dict:
    files = sorted(LOGS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text())
    except Exception:
        return {}


def run_ceo_audit(slot: int):
    """
    Load slot data, call Claude, send brutal specific Telegram message.
    Non-blocking — if Claude call fails, sends a fallback heuristic audit.
    """
    log(f"CEO audit: slot {slot}")

    # Load slot's production manifest
    manifest = load_latest(f"production_manifest_*_slot{slot}.json")
    if not manifest:
        log(f"No manifest found for slot {slot} — skipping audit")
        return

    # Load timing report if available
    timing = load_latest(f"timing_report_*_slot{slot}.json")

    # Load Loop A visual scores if available
    loop_a = load_latest(f"loop_a_scores_*_slot{slot}.json")

    # Load Loop B findings if available
    loop_b_findings = {}
    lb_file = LOGS_DIR / "loop_b_findings.json"
    if lb_file.exists():
        try:
            findings = json.loads(lb_file.read_text())
            # Get most recent finding for this slot
            slot_findings = [f for f in findings if f.get("slot") == slot]
            if slot_findings:
                loop_b_findings = slot_findings[-1]
        except Exception:
            pass

    # Assemble audit data
    script   = manifest.get("script") or {}
    qc       = manifest.get("quality_check") or {}
    vd       = manifest.get("visual_direction") or {}
    asym     = manifest.get("asymmetry") or "No asymmetry data provided"
    affiliate_raw = manifest.get("affiliate")
    affiliate = affiliate_raw if isinstance(affiliate_raw, dict) else {}
    visual_source = manifest.get("visual_source") or "unknown"
    caption_meta  = {}  # populated from quality_mirror micro manifest if available
    micro_manifest = load_latest(f"manifest_*_slot{slot}.json")
    if micro_manifest:
        caption_meta = micro_manifest.get("caption_metadata", {})

    # Timing metrics
    hook_wps           = timing.get("hook_wps", timing.get("zone_wps", {}).get("hook", "N/A"))
    pre_hook_silence   = timing.get("pre_hook_silence_ms", "N/A")
    mechanism_wps      = timing.get("zone_wps", {}).get("mechanism", "N/A")
    structural_pauses  = timing.get("structural_pauses", "N/A")

    # Loop A score
    loop_a_score = loop_a.get("overall_score") if loop_a else None
    loop_a_weakest = loop_a.get("weakest_element") if loop_a else None

    # Loop B chain score
    loop_b_score  = loop_b_findings.get("chain_score")
    loop_b_weakest = loop_b_findings.get("weakest_link")

    affiliate_label = (
        affiliate_raw if isinstance(affiliate_raw, str)
        else affiliate.get("name", "none")
    )

    audit_context = f"""SLOT {slot} POST-DISTRIBUTION AUDIT
Date: {NOW.strftime('%Y-%m-%d %H:%M')} UTC

BRIEF:
- Asymmetry: {asym[:300]}
- Hook: {(script.get('hook') or script.get('intrusion') or 'N/A')[:150]}
- Body/Mechanism: {(script.get('body') or script.get('mechanism') or '')[:200]}
- CTA: {(script.get('cta') or script.get('move') or 'N/A')[:100]}
- Affiliate: {affiliate_label}

VISUAL:
- Source: {visual_source}
- Visual direction: {vd.get('frame_description', 'N/A')[:150]}
- Motion: {vd.get('motion', 'N/A')}

CAPTIONS:
- Words per block (REVEAL): {caption_meta.get('reveal_words_per_block', 'N/A')}
- Reveal fontsize: {caption_meta.get('reveal_fontsize', 'N/A')}
- Pop duration: {caption_meta.get('pop_duration_ms', 'N/A')}ms
- Total caption events: {caption_meta.get('total_events', 'N/A')}
- AB variant: {caption_meta.get('ab_variant', 'N/A')}

TIMING:
- Pre-hook silence: {pre_hook_silence}ms
- Hook delivery speed: {hook_wps} words/sec
- Mechanism delivery speed: {mechanism_wps} words/sec
- Structural pauses: {structural_pauses}

QUALITY CHECK:
- File size: {qc.get('file_size_mb', 'N/A')} MB
- Duration: {qc.get('duration_s', 'N/A')}s
- Resolution: {qc.get('resolution', 'N/A')}
- All checks: {'PASS' if all(v is True for k, v in qc.items() if k.endswith('_ok')) else 'PARTIAL'}

LOOP A (visual identity execution):
- Score: {loop_a_score if loop_a_score is not None else 'no data'}
- Weakest: {loop_a_weakest or 'no data'}

LOOP B (£100k chain evaluation):
- Chain score: {loop_b_score if loop_b_score is not None else 'no data'}
- Weakest link: {loop_b_weakest or 'no data'}"""

    claude_prompt = f"""{audit_context}

You are the CEO of a faceless AI content business. Your only metric: does this video stop a scroll on a phone at 2am?

Review the slot data above. Identify the SINGLE most impactful weakness. Name it specifically — not "the visuals could be better" but "the hook is 4.67 words/sec which is too fast for a mortgage mechanism explanation — it reads as noise before the viewer's attention locks."

State exactly what you are changing for the next slot. Be specific about the variable and the direction.

Rules:
- One paragraph. Maximum 80 words.
- Start with the weakness. End with the specific change.
- Never say "good job" or "well done."
- If you genuinely cannot find a weakness, you are not looking hard enough.
- No hedging. No "it seems" or "possibly." State it.

Output only the paragraph. Nothing else."""

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    audit_text = None

    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                messages=[{"role": "user", "content": claude_prompt}],
            )
            audit_text = response.content[0].text.strip()
            log(f"Claude audit: {len(audit_text)} chars")
        except Exception as e:
            log(f"Claude call failed: {e} — using heuristic fallback")

    if not audit_text:
        # Heuristic fallback: identify the most obvious weakness from data
        issues = []
        if isinstance(hook_wps, (int, float)) and hook_wps > 4.0:
            issues.append(f"hook delivery {hook_wps:.1f} wps is above the 4.0 wps threshold for mobile retention — narration is outrunning comprehension at the scroll-stop moment")
        if isinstance(pre_hook_silence, (int, float)) and pre_hook_silence < 400:
            issues.append(f"pre-hook silence is {pre_hook_silence}ms — below the 400ms minimum needed for the first frame to register before audio begins")
        if qc.get("duration_s", 60) > 75:
            issues.append(f"video is {qc.get('duration_s')}s — past the 60-75s mobile attention window; the last 15s are viewed by a fraction of the hook audience")
        if loop_a_score is not None and loop_a_score < 6.0:
            issues.append(f"Loop A visual identity score {loop_a_score}/10 — the render did not execute the brief's intended visual. Kling prompt specificity insufficient for Flux+KB photo generation")
        if not issues:
            issues.append("no performance data available yet for this slot — ensure YouTube API key is active to enable metric-based audit on next cycle")
        audit_text = f"CEO AUDIT Slot {slot}: {issues[0].capitalize()}. Next: increase pre_hook_silence_ms to 600ms and reduce MECHANISM word budget by 10 words to allow breath before the mechanism explanation."

    telegram_msg = f"🔍 *CEO AUDIT — Slot {slot}*\n\n{audit_text}"
    send_telegram(telegram_msg)

    # Save audit to log for quality_mirror pattern analysis
    audit_log = LOGS_DIR / "ceo_audit_log.json"
    try:
        history = json.loads(audit_log.read_text()) if audit_log.exists() else []
    except Exception:
        history = []
    history.append({
        "timestamp": NOW.isoformat(),
        "slot": slot,
        "audit": audit_text,
        "hook_wps": hook_wps,
        "pre_hook_silence_ms": pre_hook_silence,
        "duration_s": qc.get("duration_s"),
        "loop_a_score": loop_a_score,
        "loop_b_score": loop_b_score,
    })
    history = history[-200:]
    audit_log.write_text(json.dumps(history, indent=2))
    log("CEO audit saved to ceo_audit_log.json")

    # ── Write-back: apply extractable directives to creative_directives.json ─
    _apply_audit_directives(audit_text, pre_hook_silence, hook_wps)


def _apply_audit_directives(audit_text: str, pre_hook_silence, hook_wps):
    """
    Extract concrete numeric changes from audit text and write to creative_directives.json.
    Uses Claude Haiku for extraction; silently skips on any failure.
    """
    directives_file = LOGS_DIR / "creative_directives.json"
    try:
        current = json.loads(directives_file.read_text()) if directives_file.exists() else {}
    except Exception:
        current = {}

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Heuristic write-back — apply known patterns from the audit data directly,
    # without needing another LLM call.
    updates: dict = {}

    # Pre-hook silence: audit consistently flags <400ms — enforce the 600ms target.
    if isinstance(pre_hook_silence, (int, float)) and pre_hook_silence < 400:
        updates["pre_hook_silence_ms"] = 600

    # Hook delivery speed: if clearly too fast, enforce cap.
    if isinstance(hook_wps, (int, float)) and hook_wps > 4.0:
        updates["hook_max_wps"] = 3.5

    # LLM extraction for anything not covered by heuristics.
    if anthropic_key:
        try:
            import anthropic as _anthropic
            client = _anthropic.Anthropic(api_key=anthropic_key)
            extract_prompt = (
                "Extract ONLY concrete numeric parameter changes from this CEO audit.\n"
                "Return ONLY valid JSON like {\"pre_hook_silence_ms\": 600}.\n"
                "Valid keys: pre_hook_silence_ms, hook_max_wps, mechanism_max_words, "
                "silence_geometry_after_intrusion, silence_geometry_after_mechanism, "
                "silence_geometry_before_move.\n"
                "If nothing is extractable return {}.\n\n"
                f"Audit:\n{audit_text}"
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": extract_prompt}],
            )
            raw = resp.content[0].text.strip()
            start = raw.find('{')
            if start != -1:
                depth = 0
                for idx, ch in enumerate(raw[start:], start):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            llm_updates = json.loads(raw[start:idx + 1])
                            # Only accept known safe keys with numeric values.
                            safe_keys = {
                                "pre_hook_silence_ms", "hook_max_wps",
                                "mechanism_max_words", "silence_geometry_after_intrusion",
                                "silence_geometry_after_mechanism", "silence_geometry_before_move",
                            }
                            for k, v in llm_updates.items():
                                if k in safe_keys and isinstance(v, (int, float)):
                                    updates[k] = v
                            break
        except Exception as _e:
            log(f"Directive LLM extraction failed (non-fatal): {_e}")

    if not updates:
        log("No directive updates extracted from audit")
        return

    # Merge silence_geometry sub-keys into nested dict.
    silence_map = {
        "silence_geometry_after_intrusion": "after_intrusion",
        "silence_geometry_after_mechanism": "after_mechanism",
        "silence_geometry_before_move":     "before_move",
    }
    silence_updates = {v: updates.pop(k) for k, v in silence_map.items() if k in updates}
    if silence_updates:
        current.setdefault("silence_geometry", {}).update(silence_updates)

    current.update(updates)
    try:
        directives_file.write_text(json.dumps(current, indent=2))
        log(f"creative_directives.json updated: {list(updates.keys()) + list(silence_updates.keys())}")
    except Exception as _e:
        log(f"Failed to write creative_directives.json: {_e}")


def main():
    load_env()
    parser = argparse.ArgumentParser(description="CEO audit — brutal slot review after distribution")
    parser.add_argument("--slot", type=int, required=True, choices=list(range(1, 8)),
                        help="Slot number (1-7)")
    args = parser.parse_args()
    run_ceo_audit(args.slot)


if __name__ == "__main__":
    main()
