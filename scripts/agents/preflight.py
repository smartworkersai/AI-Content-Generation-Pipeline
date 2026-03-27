#!/usr/bin/env python3
"""
preflight.py — Pre-flight API health check for Harbinger blitz runs (#12).

Checks:
  1. ElevenLabs character quota (requires ≥5,000 chars remaining)
  2. Buffer API token validity

Exit 0 = all checks pass — safe to run blitz
Exit 1 = one or more checks failed — blitz should abort

Usage:
  python3 preflight.py
"""
import os, sys, json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent


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


def check_elevenlabs() -> bool:
    """Check ElevenLabs character quota. Returns True if ≥5,000 chars remain."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        print("[preflight] WARN: ELEVENLABS_API_KEY not set — skipping quota check")
        return True
    try:
        import requests
        r = requests.get(
            "https://api.elevenlabs.io/v1/user/subscription",
            headers={"xi-api-key": api_key},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[preflight] FAIL: ElevenLabs API returned HTTP {r.status_code}")
            return False
        data   = r.json()
        used   = data.get("character_count", 0) or 0
        limit  = data.get("character_limit", 1) or 1
        remain = limit - used
        print(f"[preflight] ElevenLabs: {remain:,} chars remaining ({used:,}/{limit:,})")
        if remain < 5000:
            print(f"[preflight] FAIL: ElevenLabs quota critically low ({remain:,} chars < 5,000 threshold)")
            return False
        return True
    except Exception as e:
        print(f"[preflight] WARN: ElevenLabs check error: {e} — proceeding")
        return True   # don't abort on transient network error


def check_buffer() -> bool:
    """Check Buffer API token validity. Returns True if token authenticates."""
    buffer_token = os.environ.get("BUFFER_ACCESS_TOKEN", "")
    if not buffer_token:
        print("[preflight] WARN: BUFFER_ACCESS_TOKEN not set — skipping Buffer check")
        return True
    try:
        import requests
        r = requests.post(
            "https://api.bufferapp.com/graphql",
            headers={
                "Authorization": f"Bearer {buffer_token}",
                "Content-Type": "application/json",
            },
            json={"query": "{ currentUser { id name } }"},
            timeout=10,
        )
        if r.status_code == 401:
            print("[preflight] FAIL: Buffer API token invalid (401 Unauthorized)")
            return False
        if r.status_code != 200:
            print(f"[preflight] WARN: Buffer API returned HTTP {r.status_code} — proceeding")
            return True
        data = r.json()
        user = (data.get("data") or {}).get("currentUser", {})
        if user:
            print(f"[preflight] Buffer: authenticated as '{user.get('name', '?')}'")
            return True
        errors = data.get("errors", [])
        print(f"[preflight] FAIL: Buffer auth errors: {errors[:2]}")
        return False
    except Exception as e:
        print(f"[preflight] WARN: Buffer check error: {e} — proceeding")
        return True


def main():
    load_env()
    print("[preflight] Running pre-flight API checks...")

    results = {
        "elevenlabs": check_elevenlabs(),
        "buffer":     check_buffer(),
    }

    all_pass = all(results.values())
    print(f"[preflight] Results: {results}")
    if all_pass:
        print("[preflight] PASS — all systems go")
        sys.exit(0)
    else:
        failed = [k for k, v in results.items() if not v]
        print(f"[preflight] FAIL — aborting blitz. Failed checks: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
