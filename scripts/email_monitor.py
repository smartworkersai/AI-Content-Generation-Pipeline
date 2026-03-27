#!/usr/bin/env python3
"""
email_monitor.py — Affiliate Approval Email Monitor

Polls smartworkers.ai@gmail.com via IMAP for affiliate approval emails.
Currently watches for: IG Markets, Speechify.

When approval detected:
  1. Extracts affiliate link from email
  2. Writes to logs/affiliate_approvals.json
  3. Updates .env with new affiliate keys
  4. Updates logs/daily_brief.txt affiliate section
  5. Fires Telegram alert (human action required: verify link, check terms)

Required env vars:
  GMAIL_ADDRESS      — smartworkers.ai@gmail.com
  GMAIL_APP_PASSWORD — Gmail App Password (not account password)
                       Generate at: myaccount.google.com → Security → App Passwords

Usage:
  python3 email_monitor.py              # check once
  python3 email_monitor.py --daemon     # loop every 15 min (use cron instead)
"""
from __future__ import annotations
import os, sys, json, imaplib, email, re, datetime
from email.header import decode_header
from pathlib import Path

BASE_DIR   = Path(__file__).parent.parent
LOGS_DIR   = BASE_DIR / "logs"
ENV_FILE   = BASE_DIR / ".env"
APPROVAL_LOG = LOGS_DIR / "affiliate_approvals.json"
MONITOR_LOG  = LOGS_DIR / "email_monitor.log"
NOW          = datetime.datetime.utcnow()

GMAIL_IMAP_HOST = "imap.gmail.com"
GMAIL_IMAP_PORT = 993

# Affiliate detection patterns
# Each entry: (name, sender_domains, subject_keywords, link_domain_pattern)
AFFILIATE_WATCHERS = [
    {
        "name":          "IG Markets",
        "sender_hints":  ["ig.com", "iggroup.com", "igmarkets"],
        "subject_hints": ["approved", "accepted", "welcome", "affiliate", "partner", "application"],
        "link_pattern":  r"https?://[^\s\"']*(?:ig\.com|iggroup\.com)[^\s\"']*(?:aff|affiliate|partner|ref)[^\s\"']*",
        "env_key":       "IG_MARKETS_AFFILIATE_URL",
        "cta_name":      "IG Markets",
    },
    {
        "name":          "Speechify",
        "sender_hints":  ["speechify.com", "speechify"],
        "subject_hints": ["approved", "accepted", "welcome", "affiliate", "partner", "application"],
        "link_pattern":  r"https?://[^\s\"']*speechify\.com[^\s\"']*(?:ref|aff|invite|affiliate)[^\s\"']*",
        "env_key":       "SPEECHIFY_AFFILIATE_URL",
        "cta_name":      "Speechify",
    },
    {
        "name":          "Fidelity ISA (Awin)",
        "sender_hints":  ["awin.com", "fidelity.co.uk", "awinmedia"],
        "subject_hints": ["approved", "accepted", "welcome", "publisher", "affiliate"],
        "link_pattern":  r"https?://[^\s\"']*(?:awin\.com|fidelity\.co\.uk)[^\s\"']*",
        "env_key":       "FIDELITY_AFFILIATE_URL",
        "cta_name":      "Fidelity ISA",
    },
    {
        "name":          "Hargreaves Lansdown SIPP (financeAds)",
        "sender_hints":  ["financeads.net", "hl.co.uk", "hargreaveslansdown"],
        "subject_hints": ["approved", "accepted", "welcome", "publisher", "affiliate"],
        "link_pattern":  r"https?://[^\s\"']*(?:financeads\.net|hl\.co\.uk)[^\s\"']*",
        "env_key":       "HL_AFFILIATE_URL",
        "cta_name":      "Hargreaves Lansdown",
    },
]


def log(msg: str):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    LOGS_DIR.mkdir(exist_ok=True)
    with open(MONITOR_LOG, "a") as f:
        f.write(line + "\n")


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() not in os.environ:
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
    except Exception:
        pass


def _decode_header_value(raw) -> str:
    parts = decode_header(raw or "")
    out = []
    for part, enc in parts:
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return " ".join(out)


def _get_email_body(msg: email.message.Message) -> str:
    """Extract plain text body from email."""
    body_parts = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body_parts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body_parts.append(payload.decode(charset, errors="replace"))
        except Exception:
            pass
    return "\n".join(body_parts)


def _matches_watcher(subject: str, sender: str, watcher: dict) -> bool:
    subject_l = subject.lower()
    sender_l  = sender.lower()
    has_sender  = any(h in sender_l  for h in watcher["sender_hints"])
    has_subject = any(h in subject_l for h in watcher["subject_hints"])
    return has_sender and has_subject


def _extract_affiliate_link(body: str, watcher: dict) -> str:
    matches = re.findall(watcher["link_pattern"], body, re.I)
    # Filter out unsubscribe/tracking noise
    cleaned = [m for m in matches if not any(n in m.lower() for n in
               ["unsubscribe", "optout", "manage-preferences", "click.email"])]
    return cleaned[0] if cleaned else ""


def _update_env(key: str, value: str):
    """Add or update a key in .env file."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(f"{key}={value}\n")
        return

    lines = ENV_FILE.read_text().splitlines()
    found = False
    new_lines = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            new_lines.append(f"{key}={value}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    log(f"  .env updated: {key}=...")


def _update_daily_brief(affiliate_name: str, affiliate_url: str):
    """
    Update the affiliate entry in daily_brief.txt for the approved affiliate.
    Replaces [AFFILIATE] placeholders matching the name.
    """
    brief_file = LOGS_DIR / "daily_brief.txt"
    if not brief_file.exists():
        return

    content = brief_file.read_text()
    # Update URL line for this affiliate
    updated = re.sub(
        rf"((?:1|2|3)\.\s+{re.escape(affiliate_name)}.*?\n\s*URL:\s*)\[URL\]",
        rf"\g<1>{affiliate_url}",
        content, flags=re.S
    )
    if updated != content:
        brief_file.write_text(updated)
        log(f"  daily_brief.txt updated for {affiliate_name}")


def _load_processed_ids() -> set:
    if APPROVAL_LOG.exists():
        try:
            data = json.loads(APPROVAL_LOG.read_text())
            return {e.get("email_id", "") for e in data if e.get("email_id")}
        except Exception:
            pass
    return set()


def _record_approval(entry: dict):
    existing = []
    if APPROVAL_LOG.exists():
        try:
            existing = json.loads(APPROVAL_LOG.read_text())
        except Exception:
            pass
    existing.append(entry)
    APPROVAL_LOG.write_text(json.dumps(existing, indent=2))


def check_inbox() -> list[dict]:
    """
    Connect to Gmail IMAP, search for affiliate approval emails.
    Returns list of newly detected approvals.
    """
    gmail_addr = os.environ.get("GMAIL_ADDRESS", "smartworkers.ai@gmail.com")
    gmail_pass = os.environ.get("GMAIL_APP_PASSWORD", "")

    if not gmail_pass:
        log("GMAIL_APP_PASSWORD not set — email monitor inactive")
        log("To enable: generate at myaccount.google.com → Security → App Passwords")
        log("Then add GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx to .env")
        return []

    processed = _load_processed_ids()
    new_approvals = []

    try:
        log(f"Connecting to Gmail IMAP ({gmail_addr})...")
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_HOST, GMAIL_IMAP_PORT)
        mail.login(gmail_addr, gmail_pass)
        mail.select("INBOX")

        # Search last 30 days for unprocessed messages
        since = (NOW - datetime.timedelta(days=30)).strftime("%d-%b-%Y")
        _, msg_ids = mail.search(None, f'SINCE {since}')
        ids = msg_ids[0].split() if msg_ids[0] else []
        log(f"Found {len(ids)} emails since {since}")

        for email_id in ids:
            email_id_str = email_id.decode()
            if email_id_str in processed:
                continue

            _, msg_data = mail.fetch(email_id, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            subject = _decode_header_value(msg.get("Subject", ""))
            sender  = _decode_header_value(msg.get("From", ""))
            date    = msg.get("Date", "")

            for watcher in AFFILIATE_WATCHERS:
                if not _matches_watcher(subject, sender, watcher):
                    continue

                log(f"  MATCH: {watcher['name']} — '{subject[:60]}' from {sender[:50]}")
                body = _get_email_body(msg)
                link = _extract_affiliate_link(body, watcher)

                entry = {
                    "email_id":    email_id_str,
                    "affiliate":   watcher["name"],
                    "detected_at": NOW.isoformat(),
                    "email_date":  date,
                    "subject":     subject,
                    "sender":      sender,
                    "link":        link,
                    "env_key":     watcher["env_key"],
                    "link_found":  bool(link),
                }
                _record_approval(entry)
                new_approvals.append(entry)

                # Update .env if link found
                if link:
                    _update_env(watcher["env_key"], link)
                    _update_daily_brief(watcher["name"], link)

                # Alert — human must verify before going live
                tg_msg = (
                    f"✅ *Harbinger: Affiliate Approved — {watcher['name']}*\n\n"
                    f"From: `{sender[:60]}`\n"
                    f"Subject: `{subject[:80]}`\n\n"
                    f"{'Link extracted: `' + link[:80] + '`' if link else '⚠️ No affiliate link found in email body — open manually'}\n\n"
                    f"Action required:\n"
                    f"1. Verify link is correct at {watcher['name']} dashboard\n"
                    f"2. Confirm `{watcher['env_key']}` in .env\n"
                    f"3. System will use new affiliate in next creative synthesis cycle"
                )
                send_telegram(tg_msg)
                log(f"  Telegram alert sent for {watcher['name']}")
                break  # one watcher match per email

        mail.logout()

    except imaplib.IMAP4.error as e:
        log(f"IMAP auth error: {e}")
        log("Check GMAIL_APP_PASSWORD — must be an App Password, not your account password")
    except Exception as e:
        log(f"Email check error: {e}")

    log(f"Check complete. New approvals: {len(new_approvals)}")
    return new_approvals


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--daemon", action="store_true",
                        help="Run in loop every 15 min (use cron instead)")
    args = parser.parse_args()

    load_env()

    if args.daemon:
        import time
        log("Daemon mode: checking every 15 minutes")
        while True:
            check_inbox()
            time.sleep(900)
    else:
        results = check_inbox()
        if results:
            print(json.dumps(results, indent=2))
        else:
            print("No new affiliate approvals detected.")


if __name__ == "__main__":
    main()
