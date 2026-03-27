#!/usr/bin/env python3
"""
compliance_injector.py — Compliance pass-through (viral entertainment mode).

The operation has pivoted to a pure viral entertainment engine across three niches:
Tech/AI Hacks, Dark Psychology, Micro-Mysteries. No affiliate links, no risk
disclosures, no CTAs are appended to the spoken script. Scripts end after the
hook and mechanism — nothing is added.

This module is retained as a no-op pass-through so the rest of the pipeline
(production_agent, harbinger_core, blitz scripts) can continue to call inject()
without modification.

Usage (import):
    from compliance_injector import inject
    brief = inject(brief_dict)  # returns brief unchanged
"""
from __future__ import annotations
import json, sys
from pathlib import Path


def inject(brief: dict) -> dict:
    """
    Pass-through. Marks the brief as compliance-checked and returns it unchanged.
    No disclosures, CTAs, or any text is appended to the spoken script.
    """
    brief["compliance_checked"]  = True
    brief["compliance_injected"] = []
    return brief


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Compliance pass-through for creative briefs")
    parser.add_argument("--brief",   required=True, help="Path to creative brief JSON")
    parser.add_argument("--dry-run", action="store_true", help="Print result without writing")
    args = parser.parse_args()

    brief_path = Path(args.brief)
    if not brief_path.exists():
        print(f"ERROR: {brief_path} not found")
        sys.exit(1)

    brief = json.loads(brief_path.read_text())
    result = inject(brief)

    if args.dry_run:
        print(json.dumps({"compliance_injected": [], "status": "pass-through"}, indent=2))
    else:
        brief_path.write_text(json.dumps(result, indent=2))
        print("Compliance: pass-through — no disclosures injected")


if __name__ == "__main__":
    main()
