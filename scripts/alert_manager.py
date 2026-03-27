#!/usr/bin/env python3
"""
alert_manager.py — Gated Telegram Alert System

Fires Telegram alerts ONLY for these conditions:
  1. platform_banned      — any platform banned or restricted
  2. buffer_auth_expired  — Buffer API returns 401/403
  3. elevenlabs_low       — ElevenLabs quota below 20%
  4. piapi_low            — PiAPI credit below $1.00
  5. viral_post           — any post exceeds 50k views in 24 hours
  6. first_conversion     — first affiliate conversion detected
  7. tiktok_trial_expiring — TikTok Business trial expires within 7 days

All other conditions are logged silently. No operational noise.
Cooldown per condition: 6h (except first_conversion = fire once ever).
"""
from __future__ import annotations
import os, json, datetime
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent
LOGS_DIR  = BASE_DIR / "logs"
STATE_FILE = LOGS_DIR / "alert_state.json"

# Cooldown in hours per condition. 0 = fire once ever.
COOLDOWNS = {
    "platform_banned":       2,
    "buffer_auth_expired":   6,
    "elevenlabs_low":        12,
    "piapi_low":             6,
    "viral_post":            6,
    "first_conversion":      0,   # once ever
    "tiktok_trial_expiring": 24,
}


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"last_sent": {}, "fired_once": []}


def _save_state(state: dict):
    LOGS_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _send_telegram(msg: str):
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    try:
        import requests
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


def fire(condition: str, message: str, force: bool = False) -> bool:
    """
    Attempt to fire a Telegram alert for a condition.
    Returns True if alert was sent, False if suppressed by cooldown.

    condition: one of the 7 keys above
    message:   the full Telegram message to send
    force:     bypass cooldown (use for escalation only)
    """
    if condition not in COOLDOWNS:
        # Unknown condition — suppress silently
        return False

    state = _load_state()
    now   = datetime.datetime.utcnow()
    cd    = COOLDOWNS[condition]

    # Once-ever conditions
    if cd == 0:
        if condition in state.get("fired_once", []):
            return False  # already fired, never again

    # Cooldown check
    if not force and cd > 0:
        last = state.get("last_sent", {}).get(condition)
        if last:
            last_dt  = datetime.datetime.fromisoformat(last)
            elapsed  = (now - last_dt).total_seconds() / 3600
            if elapsed < cd:
                return False

    # Send
    sent = _send_telegram(message)
    if sent:
        state.setdefault("last_sent", {})[condition] = now.isoformat()
        if cd == 0:
            state.setdefault("fired_once", []).append(condition)
        _save_state(state)
    return sent


# ---------------------------------------------------------------------------
# Condition checkers — called by harbinger_core health check
# ---------------------------------------------------------------------------
def check_piapi_credit():
    """Alert if PiAPI credit drops below $1.00."""
    credit_file = LOGS_DIR / "piapi_credit.json"
    if not credit_file.exists():
        return
    try:
        balance = float(json.loads(credit_file.read_text()).get("balance_usd", 99))
        if balance < 1.00:
            fire("piapi_low",
                 f"🔴 *Harbinger Alert: PiAPI Credit Low*\n"
                 f"Balance: ${balance:.2f}\n"
                 f"Kling renders will fail below $0.40. Top up at piapi.ai")
    except Exception:
        pass


def check_elevenlabs_quota():
    """Alert if ElevenLabs character quota drops below 20%."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        return
    try:
        import requests
        r = requests.get(
            "https://api.elevenlabs.io/v1/user",
            headers={"xi-api-key": api_key},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            sub  = data.get("subscription", {})
            used = sub.get("character_count", 0)
            limit = sub.get("character_limit", 1)
            if limit > 0:
                pct_remaining = round((1 - used / limit) * 100, 1)
                if pct_remaining < 20:
                    fire("elevenlabs_low",
                         f"⚠️ *Harbinger Alert: ElevenLabs Quota Low*\n"
                         f"Remaining: {pct_remaining:.1f}% ({limit - used:,} chars)\n"
                         f"Audio generation will fail at 0%. Upgrade or wait for reset.")
    except Exception:
        pass


def check_viral_post(views: int, platform: str, post_id: str = ""):
    """Alert if any post exceeds 50k views. Called by feedback cycle."""
    if views >= 50_000:
        fire("viral_post",
             f"🚀 *Harbinger: Viral Post Detected*\n"
             f"Platform: {platform}\n"
             f"Views: {views:,}\n"
             f"Post: {post_id}\n"
             f"Action: double production frequency on this asymmetry immediately.")


def check_first_conversion(affiliate: str, revenue_gbp: float = 0):
    """Fire once-ever alert when first affiliate conversion is detected."""
    fire("first_conversion",
         f"💰 *Harbinger: FIRST AFFILIATE CONVERSION*\n"
         f"Affiliate: {affiliate}\n"
         f"Revenue: £{revenue_gbp:.2f}\n"
         f"The revenue loop is open. Scale what converted.")


def check_platform_status(platform: str, reason: str):
    """Alert on any platform ban or restriction."""
    fire("platform_banned",
         f"🚨 *Harbinger Alert: Platform Restricted*\n"
         f"Platform: {platform}\n"
         f"Reason: {reason}\n"
         f"Action required: check {platform} account immediately.")


def check_buffer_auth(status_code: int):
    """Alert if Buffer API returns auth failure."""
    if status_code in (401, 403):
        fire("buffer_auth_expired",
             f"🔴 *Harbinger Alert: Buffer Auth Expired*\n"
             f"HTTP {status_code} from Buffer API.\n"
             f"Action: re-authenticate at buffer.com → Settings → Connected Accounts")


def check_tiktok_trial(days_remaining: int):
    """Alert if TikTok Business trial expires within 7 days."""
    if days_remaining <= 7:
        fire("tiktok_trial_expiring",
             f"⏰ *Harbinger Alert: TikTok Business Trial Expiring*\n"
             f"Days remaining: {days_remaining}\n"
             f"Action: convert to paid plan or apply for Business API access "
             f"before autonomous posting breaks.")


def run_all_checks():
    """Run all passive checks. Call from health_check in harbinger_core."""
    check_piapi_credit()
    check_elevenlabs_quota()
    # Platform, buffer, viral, conversion checks are event-driven (called inline)


if __name__ == "__main__":
    import sys
    run_all_checks()
    print("Alert checks complete. State:", json.dumps(_load_state(), indent=2))
