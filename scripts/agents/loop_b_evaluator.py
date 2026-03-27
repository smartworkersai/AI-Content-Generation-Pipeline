#!/usr/bin/env python3
"""
loop_b_evaluator.py — Pre-render £100k chain evaluator.

Runs after Agent 2 (creative_synthesis) and before Agent 3 (production_agent).
Evaluates the full conversion chain against the brief and script:
  1. Visual identity → emotional precondition
  2. Mechanism specificity → share trigger
  3. Emotional arc → CTA hold
  4. Affiliate → logical conclusion (not interruption)
  5. Comment trigger → structural element (not afterthought)

Writes corrective directives to logs/loop_b_directives_slotN.json.
Agent 2 re-reads these before production commits.
Findings also accumulate in logs/loop_b_findings.json for Agent 4 pattern analysis.

Usage:
  python3 loop_b_evaluator.py --slot 3
"""
from __future__ import annotations
import os, sys, json, datetime, argparse, re
from pathlib import Path

BASE_DIR  = Path(__file__).parent.parent.parent
LOGS_DIR  = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

NOW       = datetime.datetime.utcnow()
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

CHAIN_LINKS = [
    "visual_identity_precondition",
    "mechanism_specificity",
    "emotional_arc_to_cta",
    "affiliate_as_conclusion",
    "comment_trigger_structural",
]

CHAIN_QUESTIONS = {
    "visual_identity_precondition": (
        "Did the research-derived visual identity create the right emotional precondition "
        "for the mechanism this script is attempting to fire? "
        "The visual must prime the viewer's distrust before the mechanism is named — "
        "not illustrate it generically."
    ),
    "mechanism_specificity": (
        "Was the mechanism executed with enough specificity to actually trigger a share? "
        "Shares happen when viewers feel they've learned something others haven't. "
        "A named mechanism with a verifiable example outperforms a described pattern by 4–7× on share rate. "
        "Is the mechanism named, specific, and verifiable by a UK viewer?"
    ),
    "emotional_arc_to_cta": (
        "Did the emotional state hold all the way to the CTA? "
        "The arc is: confusion → recognition → anger → clarity → action. "
        "Does the script sustain that arc, or does it collapse into explanation before the CTA? "
        "The CTA must feel like the natural release of the tension the script created."
    ),
    "affiliate_as_conclusion": (
        "Did the affiliate land as a logical conclusion, not an interruption? "
        "The affiliate must be the answer to the question the script has just made urgent. "
        "If the viewer has to shift mental gears to accept the affiliate, the chain is broken. "
        "Does this script make the affiliate feel inevitable?"
    ),
    "comment_trigger_structural": (
        "Was a comment trigger deliberately engineered as a structural element — not an afterthought? "
        "Comment triggers are: unfinished thoughts that demand completion, "
        "named villains viewers want to denounce, "
        "questions that expose asymmetric knowledge, "
        "or claims that feel personal enough to spark disagreement. "
        "Is there a specific, designed comment trigger in this script?"
    ),
}


def log(msg: str):
    print(f"[loop_b] {msg}")


def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()


def load_latest_brief(slot: int) -> dict:
    candidates = sorted(
        LOGS_DIR.glob(f"creative_brief_*_slot{slot}.json"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No creative brief found for slot {slot}")
    return json.loads(candidates[0].read_text()), candidates[0]


def evaluate_with_claude(brief: dict, script: dict) -> dict:
    """Use Claude to evaluate the £100k chain. Returns scores and directives."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    except ImportError:
        return None

    brief_summary = json.dumps({
        "asymmetry": brief.get("asymmetry", "")[:200],
        "visual_direction": brief.get("visual_direction", {}),
        "script": script,
        "trust_anchors": brief.get("trust_anchors", {}),
        "affiliate": brief.get("affiliate", {}),
    }, indent=2)

    prompt = f"""You are evaluating a short-form finance video brief for a system targeting £100,000 in affiliate revenue in 18 days.

BRIEF:
{brief_summary}

Evaluate each link in the £100k conversion chain. For each link:
- Score 1-10 (10 = perfect execution)
- Identify the specific failure if score < 7
- Write a corrective directive for Agent 2 to fix (concrete instruction, not description of problem)

CHAIN LINKS TO EVALUATE:
{json.dumps(CHAIN_QUESTIONS, indent=2)}

Respond as JSON only:
{{
  "chain_scores": {{
    "visual_identity_precondition": {{"score": 0, "failure": "...", "directive": "..."}},
    "mechanism_specificity": {{"score": 0, "failure": "...", "directive": "..."}},
    "emotional_arc_to_cta": {{"score": 0, "failure": "...", "directive": "..."}},
    "affiliate_as_conclusion": {{"score": 0, "failure": "...", "directive": "..."}},
    "comment_trigger_structural": {{"score": 0, "failure": "...", "directive": "..."}}
  }},
  "weakest_link": "<link_name>",
  "overall_chain_score": 0,
  "corrective_directives": ["<top 3 specific directives for Agent 2, most impactful first>"],
  "rewrite_required": true
}}"""

    try:
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        # Strip markdown if wrapped
        text = re.sub(r'^```json\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        return json.loads(text)
    except Exception as e:
        log(f"Claude evaluation failed: {e}")
        return None


def evaluate_heuristic(brief: dict, script: dict) -> dict:
    """
    Heuristic fallback chain evaluator — no API calls.
    Checks structural presence of each chain element.
    """
    scores = {}

    # 1. Visual identity precondition
    vd = brief.get("visual_direction", {})
    kling_prompt = vd.get("kling_prompt", "")
    has_specific_visual = len(kling_prompt) > 80 and any(
        w in kling_prompt.lower() for w in ["shadow", "cold", "contrast", "close-up", "extreme", "rack focus"]
    )
    scores["visual_identity_precondition"] = {
        "score": 7 if has_specific_visual else 4,
        "failure": "" if has_specific_visual else
            "Visual prompt is generic — no emotional priming. Add cold, high-contrast, close-up framing that creates unease before the mechanism is named.",
        "directive": "" if has_specific_visual else
            "Rewrite visual_direction.kling_prompt to open with an extreme close-up of something ordinary made unsettling — a number on a screen, a hand, a phone notification.",
    }

    # 2. Mechanism specificity
    mechanism = script.get("mechanism", "")
    has_named_mechanism = bool(re.search(r"[A-Z][a-z]+ (scheme|rule|law|rate|fee|clause|mechanism)", mechanism))
    has_uk_source = any(s in mechanism.lower() for s in ["fca", "hmrc", "bank of england", "ons", "which?", "mse", "fos"])
    spec_score = 4 + (2 if has_named_mechanism else 0) + (2 if has_uk_source else 0)
    scores["mechanism_specificity"] = {
        "score": spec_score,
        "failure": "" if spec_score >= 7 else
            "Mechanism lacks a named UK source or specific verifiable claim. Viewers share specifics, not descriptions.",
        "directive": "" if spec_score >= 7 else
            "Add a named UK regulatory body or specific percentage/figure with source in THE MECHANISM section. E.g. 'FCA data shows X% of Y in 2024'.",
    }

    # 3. Emotional arc to CTA
    move = script.get("move", "") or script.get("edge", "")
    intrusion = script.get("intrusion", "")
    # Arc: intrusion creates urgency, move resolves it into action
    arc_broken = not move or not intrusion or len(move) < 30
    scores["emotional_arc_to_cta"] = {
        "score": 5 if arc_broken else 8,
        "failure": "CTA is absent or too short to resolve the tension the intrusion created." if arc_broken else "",
        "directive": "The MOVE section must name the action, make it feel urgent (time-bounded or quantity-bounded), and connect explicitly to the mechanism just named. Don't explain — instruct." if arc_broken else "",
    }

    # 4. Affiliate as conclusion
    affiliate_name = (brief.get("affiliate") or {})
    if isinstance(affiliate_name, dict):
        affiliate_name = affiliate_name.get("name", "")
    affiliate_in_move = affiliate_name and affiliate_name.lower() in move.lower()
    scores["affiliate_as_conclusion"] = {
        "score": 8 if affiliate_in_move else 4,
        "failure": "" if affiliate_in_move else
            f"Affiliate '{affiliate_name}' not mentioned in THE MOVE — affiliate will feel like an interruption, not a conclusion.",
        "directive": "" if affiliate_in_move else
            f"Rewrite THE MOVE so '{affiliate_name}' is introduced as the direct answer to the vulnerability just exposed. The viewer should feel the affiliate is obvious, not pitched.",
    }

    # 5. Comment trigger
    all_text = " ".join(v for v in script.values() if isinstance(v, str))
    comment_signals = [
        any(re.search(r"\?", s.strip()) for s in all_text.split(".")),  # sentence ends with question
        "comment" in all_text.lower(),
        any(w in all_text.lower() for w in ["tell me", "let me know", "reply", "share this"]),
        bool(re.search(r"\b(still|always|every|all|never|nobody)\b", all_text.lower())),  # absolute claims
    ]
    comment_score = 4 + sum(2 for s in comment_signals if s)
    comment_score = min(comment_score, 10)
    scores["comment_trigger_structural"] = {
        "score": comment_score,
        "failure": "" if comment_score >= 6 else
            "No deliberate comment trigger found. Without one, algorithmic distribution stalls within 2 hours of posting.",
        "directive": "" if comment_score >= 6 else
            "Add a comment hook as the last line before the CTA: either a question that only informed viewers can answer, or a claim provocative enough to invite disagreement (e.g. 'Most people still don't know this exists.').",
    }

    overall = round(sum(v["score"] for v in scores.values()) / len(scores), 1)
    weakest = min(scores, key=lambda k: scores[k]["score"])

    directives = [
        v["directive"]
        for v in sorted(scores.values(), key=lambda x: x["score"])
        if v.get("directive")
    ][:3]

    return {
        "chain_scores": scores,
        "weakest_link": weakest,
        "overall_chain_score": overall,
        "corrective_directives": directives,
        "rewrite_required": overall < 6.5,
        "evaluation_method": "heuristic",
    }


def load_findings_history() -> list:
    f = LOGS_DIR / "loop_b_findings.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return []


def save_findings(slot: int, brief_name: str, evaluation: dict):
    history = load_findings_history()
    history.append({
        "timestamp": NOW.isoformat(),
        "slot": slot,
        "brief": brief_name,
        "overall_chain_score": evaluation.get("overall_chain_score"),
        "weakest_link": evaluation.get("weakest_link"),
        "rewrite_required": evaluation.get("rewrite_required"),
        "evaluation_method": evaluation.get("evaluation_method", "claude"),
        "chain_scores": {
            k: v["score"] for k, v in evaluation.get("chain_scores", {}).items()
        },
    })
    # Keep last 200
    history = history[-200:]
    (LOGS_DIR / "loop_b_findings.json").write_text(json.dumps(history, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", type=int, required=True, choices=[1, 2, 3, 4, 5, 6, 7])
    args = parser.parse_args()
    slot = args.slot

    load_env()

    log("=" * 60)
    log(f"LOOP B — £100k CHAIN EVALUATOR (SLOT {slot})")
    log("=" * 60)

    try:
        brief, brief_path = load_latest_brief(slot)
    except FileNotFoundError as e:
        log(f"ERROR: {e}")
        sys.exit(1)

    script = brief.get("script", {})
    log(f"Evaluating: {brief_path.name}")

    # Try Claude first; fall back to heuristic
    evaluation = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        log("Evaluating with Claude Vision...")
        evaluation = evaluate_with_claude(brief, script)
        if evaluation:
            evaluation["evaluation_method"] = "claude"

    if not evaluation:
        log("Heuristic evaluation (ANTHROPIC_API_KEY absent or Claude failed)...")
        evaluation = evaluate_heuristic(brief, script)

    overall = evaluation.get("overall_chain_score", 0)
    weakest = evaluation.get("weakest_link", "unknown")
    rewrite = evaluation.get("rewrite_required", False)

    log(f"Chain score: {overall}/10 | Weakest link: {weakest} | Rewrite: {rewrite}")
    for link, data in evaluation.get("chain_scores", {}).items():
        log(f"  {link}: {data['score']}/10" + (f" — {data['failure'][:80]}" if data.get('failure') else ""))

    # Write directives for Agent 2 to read on re-run
    directives_path = LOGS_DIR / f"loop_b_directives_slot{slot}.json"
    directives_out = {
        "timestamp": NOW.isoformat(),
        "slot": slot,
        "brief_evaluated": brief_path.name,
        "overall_chain_score": overall,
        "weakest_link": weakest,
        "rewrite_required": rewrite,
        "corrective_directives": evaluation.get("corrective_directives", []),
        "chain_scores": evaluation.get("chain_scores", {}),
        "evaluation_method": evaluation.get("evaluation_method", "heuristic"),
    }
    directives_path.write_text(json.dumps(directives_out, indent=2))
    log(f"Directives written: {directives_path.name}")

    # Accumulate findings for Agent 4 pattern analysis
    save_findings(slot, brief_path.name, evaluation)
    log(f"Findings logged to loop_b_findings.json ({len(load_findings_history())} total)")

    # Exit code signals harbinger_core whether to re-run Agent 2
    # 0 = acceptable, 1 = rewrite required
    if rewrite:
        log("CHAIN SCORE BELOW THRESHOLD — signalling rewrite to harbinger_core")
        sys.exit(1)
    else:
        log("Chain score acceptable — production can proceed")
        sys.exit(0)


if __name__ == "__main__":
    main()
