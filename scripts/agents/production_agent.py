#!/usr/bin/env python3
"""
production_agent.py — Agent 3: Production
Executes the creative brief with zero creative decisions.
Input: logs/creative_brief_[timestamp]_slot[n].json
Output: output/post_[timestamp]_slot[n].mp4

Usage: python3 production_agent.py --slot <1|2|3>
"""
from __future__ import annotations
import os, sys, json, datetime, subprocess, time, re, shutil
from pathlib import Path

BASE_DIR    = Path(__file__).parent.parent.parent
SCRIPTS_DIR = Path(__file__).parent.parent  # scripts/
AGENTS_DIR  = Path(__file__).parent          # scripts/agents/
LOGS_DIR = BASE_DIR / "logs"
OUTPUT_DIR = BASE_DIR / "output"
ADS_READY_DIR = BASE_DIR / "ads_ready_for_review"
LOGS_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
ADS_READY_DIR.mkdir(exist_ok=True)
PRODUCTION_LOG = LOGS_DIR / "production_quality.log"
NOW = datetime.datetime.utcnow()
DATE_STR = NOW.strftime("%Y-%m-%d")
TIMESTAMP = NOW.strftime("%Y%m%d_%H%M%S")

ELEVENLABS_VOICE_ID  = "TxGEqnHWrfWFTfGW9XjX"
EVOLUTION_PARAMS_FILE = LOGS_DIR / "evolution_params.json"


def load_evolution_params() -> dict:
    """Load A/B-tested FFmpeg parameters from evolution_engine output."""
    defaults = {"zoom_factor": 0.15, "ssml_break_secs": 0.8}
    if EVOLUTION_PARAMS_FILE.exists():
        try:
            stored = json.loads(EVOLUTION_PARAMS_FILE.read_text())
            defaults.update(stored)
        except Exception:
            pass
    return defaults


def log(msg):
    line = f"[{NOW.strftime('%Y-%m-%d %H:%M:%S')} UTC] {msg}"
    print(line)
    with open(PRODUCTION_LOG, "a") as f:
        f.write(line + "\n")


def _retry_call(fn, *args, max_attempts: int = 3, wait_secs: float = 30.0,
                label: str = "", result_ok=None, **kwargs):
    """
    Call fn(*args, **kwargs) up to max_attempts times.
    Retries on exception OR when result_ok(result) returns False.
    Waits wait_secs between attempts.
    Returns the result on success, or None after all attempts are exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn(*args, **kwargs)
            if result_ok is None or result_ok(result):
                if attempt > 1:
                    log(f"  [{label}] succeeded on attempt {attempt}/{max_attempts}")
                return result
            raise ValueError(f"result_ok check failed (attempt {attempt})")
        except Exception as e:
            if attempt < max_attempts:
                log(f"  [{label}] attempt {attempt}/{max_attempts} failed: {e}")
                log(f"  [{label}] waiting {wait_secs:.0f}s before retry...")
                time.sleep(wait_secs)
            else:
                log(f"  [{label}] all {max_attempts} attempts failed: {e}")
    return None

def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                if k.strip() and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()

def load_creative_brief(slot: int) -> dict:
    """Load most recent creative brief for this slot."""
    briefs = sorted(
        LOGS_DIR.glob(f"creative_brief_*_slot{slot}.json"),
        key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not briefs:
        log(f"ERROR: No creative brief found for slot {slot}")
        sys.exit(1)
    brief = json.loads(briefs[0].read_text())
    log(f"Loaded creative brief: {briefs[0].name}")
    return brief

def load_directives() -> dict:
    """Load creative_directives.json for sound profile and acoustic settings."""
    f = BASE_DIR / "logs" / "creative_directives.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}

# ---------------------------------------------------------------------------
# Step 1: Visual generation — visual_router decides format per brief
#
# Format selection (visual_router.py):
#   flux_ken_burns — Flux1-dev image (PiAPI ~$0.004/img) + FFmpeg Ken Burns
#   kling_video    — PiAPI Kling (~$0.13/clip × 3 shots = ~$0.39)
#
# Fallback chain within each format:
#   kling_video:  PiAPI Kling → fal.ai Kling → Runway → Pika → flux_ken_burns fallback
#   flux_ken_burns: PiAPI Flux → dark background (never used as first choice)
# ---------------------------------------------------------------------------
def concatenate_clips(clip_paths: list, slot: int) -> Path | None:
    """Concatenate multiple mp4 clips into a single visual track via FFmpeg concat."""
    if not clip_paths:
        return None
    if len(clip_paths) == 1:
        return clip_paths[0]

    concat_list = OUTPUT_DIR / f"concat_{TIMESTAMP}_slot{slot}.txt"
    concat_list.write_text("\n".join(f"file '{p.resolve()}'" for p in clip_paths))

    out = OUTPUT_DIR / f"visual_{TIMESTAMP}_slot{slot}.mp4"
    cmd = [
        FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out),
    ]
    log(f"Concatenating {len(clip_paths)} clips...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    concat_list.unlink(missing_ok=True)
    if result.returncode != 0:
        log(f"Concat failed: {result.stderr[-400:]}")
        return None
    log(f"Visual track assembled: {out.name}")
    return out


def _piapi_submit_poll(api_key: str, payload: dict, provider_tag: str,
                        poll_interval: int = 5, max_polls: int = 120,
                        wall_clock_timeout: int = 600) -> dict | None:
    """Submit a task to PiAPI and poll until complete. Returns task data dict or None.
    Hard-stops after wall_clock_timeout seconds regardless of max_polls iterations."""
    import requests
    headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
    try:
        r = requests.post("https://api.piapi.ai/api/v1/task",
                          headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            log(f"    [{provider_tag}] Submit failed: HTTP {r.status_code}: {r.text[:200]}")
            return None
        body = r.json()
        if body.get("code") not in (200, 0):
            log(f"    [{provider_tag}] API error: {body.get('message', 'unknown')}")
            return None
        task_id = (body.get("data") or {}).get("task_id")
        if not task_id:
            log(f"    [{provider_tag}] No task_id in response")
            return None
    except Exception as e:
        log(f"    [{provider_tag}] Submit exception: {e}")
        return None

    deadline = time.time() + wall_clock_timeout
    for _ in range(max_polls):
        if time.time() >= deadline:
            log(f"    [{provider_tag}] ABORT: wall-clock timeout ({wall_clock_timeout}s) exceeded — slot will not hang")
            return None
        time.sleep(poll_interval)
        try:
            r2 = requests.get(f"https://api.piapi.ai/api/v1/task/{task_id}",
                              headers=headers, timeout=30)
            if r2.status_code != 200:
                continue
            result = r2.json()
            status = (result.get("data") or {}).get("status", "")
            if status == "completed":
                return result.get("data") or {}
            if status in ("failed", "error"):
                err = (result.get("data") or {}).get("error", {})
                log(f"    [{provider_tag}] Task failed: {err}")
                return None
        except Exception:
            continue
    log(f"    [{provider_tag}] Timed out after {max_polls * poll_interval}s")
    return None


def _generate_shots_piapi_kling(shot_list: list, slot: int) -> list[Path]:
    """Generate clips via PiAPI Kling reseller (~$0.13/clip, 9:16 pro).
    Uses resource_without_watermark URL for clean output."""
    import requests
    api_key = os.environ.get("PIAPI_KEY", "")
    if not api_key:
        return []
    log("  Provider: PiAPI Kling")
    clips = []
    for shot in shot_list:
        idx = shot["index"] + 1
        log(f"  Shot {idx}/{len(shot_list)}: {shot['type']} ({shot['duration_s']}s) [PiAPI Kling]...")
        payload = {
            "model": "kling",
            "task_type": "video_generation",
            "input": {
                "prompt": shot["prompt"],
                "negative_prompt": shot.get("negative_prompt", ""),
                "duration": int(shot["fal_params"]["duration"]),
                "aspect_ratio": "9:16",
                "mode": "pro",
            },
        }
        result = _piapi_submit_poll(api_key, payload, "PiAPI-Kling")
        if not result:
            continue
        # Extract video URL — prefer resource_without_watermark
        output = result.get("output") or {}
        works  = output.get("works") or []
        video_url = output.get("video_url", "")
        if not video_url and works:
            vid = (works[0].get("video") or {})
            video_url = (
                vid.get("resource_without_watermark") or
                vid.get("resource") or
                works[0].get("resource_without_watermark") or
                works[0].get("resource") or
                ""
            )
        if video_url:
            try:
                r = requests.get(video_url, timeout=180)
                if r.status_code == 200:
                    p = OUTPUT_DIR / f"clip_{TIMESTAMP}_slot{slot}_shot{shot['index']}.mp4"
                    p.write_bytes(r.content)
                    clips.append(p)
                    log(f"    Saved: {p.name} ({len(r.content)//1024}KB)")
                else:
                    log(f"    Download failed: HTTP {r.status_code}")
            except Exception as e:
                log(f"    Download error: {e}")
        else:
            log(f"    No video URL in PiAPI response: {list(output.keys())}")
        time.sleep(2)
    return clips


def _derive_shot_motion(shot_type: str, brief: dict, routing: dict) -> dict:
    """
    Derive FFmpeg zoompan parameters for a specific shot from the brief's emotional content.
    Each shot's motion is chosen from its role in the narrative arc, not applied as a default.

    Shot roles:
      HOOK      — interrupt the scroll. Must be confrontational. Push toward the viewer.
      MECHANISM — the thing being explained. Camera investigates. Drift reads the scene.
      MOVE      — resolution energy. Pull back reveals consequence, or push forward = action.
    """
    urgency  = brief.get("urgency_score", 50)
    asym     = brief.get("asymmetry", "").lower()
    script   = brief.get("script", {})

    threat_words = {"expire", "deadline", "lost", "gone", "permanently", "never recover",
                    "risk", "miss", "closes", "forfeited"}
    is_threat = any(w in asym for w in threat_words) or urgency > 65

    reveal_words = {"mechanism", "hidden", "behind", "exposes", "reveals",
                    "most people", "never told", "structural", "invisible"}
    is_reveal = any(w in asym for w in reveal_words)

    if shot_type == "HOOK":
        if is_threat:
            # Hard threat: fast push creates unease. Viewer feels cornered.
            return {"z_expr": "zoom+0.0022", "x_expr": "iw/2-(iw/zoom/2)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "hook_threat_push"}
        else:
            # Revelation hook: start tight, pull back slightly to reveal context
            return {"z_expr": "if(eq(on,1),1.18,zoom-0.0010)", "x_expr": "iw/2-(iw/zoom/2)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "hook_reveal_pullback"}

    elif shot_type == "MECHANISM":
        if is_reveal:
            # Investigative drift — camera reads the mechanism left to right
            return {"z_expr": "1.10", "x_expr": "iw*0.07*(on/125)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "mechanism_drift_investigate"}
        else:
            # Authority data — near-static, institutional certainty
            return {"z_expr": "1.04", "x_expr": "iw*0.012*(on/125)+iw/2-(iw/zoom/2)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "mechanism_authority_static"}

    elif shot_type == "MOVE":
        if is_threat:
            # Urgency resolution: decisive push — time is short, act now
            return {"z_expr": "zoom+0.0020", "x_expr": "iw/2-(iw/zoom/2)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "move_urgency_push"}
        else:
            # Revelation resolution: pull back — the full picture is now visible
            return {"z_expr": "if(eq(on,1),1.22,zoom-0.0016)", "x_expr": "iw/2-(iw/zoom/2)", "y_expr": "ih/2-(ih/zoom/2)",
                    "motion_label": "move_reveal_pullback"}

    # Fallback: use routing's global Ken Burns
    kb = routing.get("ken_burns", {})
    return {"z_expr": kb.get("z_expr", "zoom+0.0018"),
            "x_expr": kb.get("x_expr", "iw/2-(iw/zoom/2)"),
            "y_expr": kb.get("y_expr", "ih/2-(ih/zoom/2)"),
            "motion_label": "fallback_routing"}


def _generate_shots_flux_ken_burns(shot_list: list, routing: dict, slot: int, brief: dict = None) -> list[Path]:
    """
    Generate video clips via Flux1-dev image (PiAPI ~$0.004/img) + FFmpeg Ken Burns motion.
    Each shot's motion is derived from the brief's emotional content — not applied as a default.
    Format: 576x1024 Flux image → scale 1440x2560 → zoompan 1080x1920 → libx264
    """
    import requests
    api_key = os.environ.get("PIAPI_KEY", "")
    if not api_key:
        log("  Flux+KB: PIAPI_KEY not set")
        return []

    log("  Provider: Flux1-dev + Ken Burns (PiAPI) — emotion-derived motion per shot")
    flux_prompt = routing.get("flux_prompt", {})
    clips       = []

    for shot in shot_list:
        idx      = shot["index"] + 1
        shot_type = shot["type"]
        duration  = int(shot.get("duration_s", 5))
        frames    = duration * 25
        log(f"  Shot {idx}/{len(shot_list)}: {shot_type} ({duration}s) [Flux+KB]...")

        # Build Flux prompt — use brief's positive with quality tokens
        positive = (flux_prompt.get("positive") or shot["prompt"])[:500]
        negative = (flux_prompt.get("negative") or shot.get("negative_prompt", ""))[:300]

        payload = {
            "model": "Qubico/flux1-dev",
            "task_type": "txt2img",
            "input": {
                "prompt": positive,
                "negative_prompt": negative,
                "width": 576,
                "height": 1024,
            },
        }
        result = _piapi_submit_poll(api_key, payload, "Flux", poll_interval=3, max_polls=60)
        if not result:
            log(f"    Flux shot {idx} failed — skipping")
            continue

        output    = result.get("output") or {}
        image_url = output.get("image_url", "")
        if not image_url:
            log(f"    Flux: no image_url in response")
            continue

        # Download image
        try:
            img_r = requests.get(image_url, timeout=60)
            if img_r.status_code != 200:
                log(f"    Flux image download failed: HTTP {img_r.status_code}")
                continue
            img_path = OUTPUT_DIR / f"flux_{TIMESTAMP}_slot{slot}_shot{shot['index']}.jpg"
            img_path.write_bytes(img_r.content)
            log(f"    Flux image: {img_path.name} ({len(img_r.content)//1024}KB)")
        except Exception as e:
            log(f"    Flux image download error: {e}")
            continue

        # Apply Ken Burns via FFmpeg zoompan — motion derived from brief emotional content
        kb = _derive_shot_motion(shot_type, brief or {}, routing)
        z_expr = kb.get("z_expr", "zoom+0.0018")
        x_expr = kb.get("x_expr", "iw/2-(iw/zoom/2)")
        y_expr = kb.get("y_expr", "ih/2-(ih/zoom/2)")
        log(f"    Motion: {kb.get('motion_label', 'default')} ({shot_type})")

        clip_path = OUTPUT_DIR / f"clip_{TIMESTAMP}_slot{slot}_flux_shot{shot['index']}.mp4"
        kb_filter = (
            f"scale=1440:2560:flags=lanczos,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={frames}:s=1080x1920:fps=25,setsar=1"
        )
        cmd = [
            FFMPEG_BIN, "-y",
            "-loop", "1", "-i", str(img_path),
            "-vf", kb_filter,
            "-t", str(duration),
            "-r", "25",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(clip_path),
        ]
        try:
            kb_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if kb_result.returncode != 0:
                log(f"    Ken Burns render failed: {kb_result.stderr[-300:]}")
                continue
            clips.append(clip_path)
            log(f"    KB clip: {clip_path.name} ({clip_path.stat().st_size//1024}KB)")
        except Exception as e:
            log(f"    Ken Burns error: {e}")
        time.sleep(1)

    return clips


def _generate_shots_runway(shot_list: list, slot: int) -> list[Path]:
    """Fallback: generate clips via RunwayML Gen-3 Alpha Turbo (RUNWAYML_API_SECRET).
    Text-to-video, 5 or 10s, 720:1280 (9:16)."""
    import requests
    api_key = os.environ.get("RUNWAYML_API_SECRET", "")
    if not api_key:
        return []
    try:
        import runwayml
    except ImportError:
        log("  runwayml SDK not installed — run: pip install runwayml")
        return []

    log("  Provider: RunwayML Gen-3 Alpha Turbo (fallback)")
    client = runwayml.RunwayML(api_key=api_key)
    clips = []
    for shot in shot_list:
        idx = shot["index"] + 1
        duration = min(shot["duration_s"], 10)
        duration = 10 if duration > 5 else 5  # Runway only supports 5 or 10
        log(f"  Shot {idx}/{len(shot_list)}: {shot['type']} ({duration}s) [Runway]...")
        try:
            task = client.text_to_video.create(
                model="gen3a_turbo",
                prompt_text=shot["prompt"][:500],
                duration=duration,
                ratio="720:1280",
            )
            for _ in range(60):
                time.sleep(5)
                t = client.tasks.retrieve(task.id)
                if t.status == "SUCCEEDED":
                    video_url = t.output[0] if t.output else None
                    if video_url:
                        r = requests.get(video_url, timeout=180)
                        if r.status_code == 200:
                            p = OUTPUT_DIR / f"clip_{TIMESTAMP}_slot{slot}_runway_shot{shot['index']}.mp4"
                            p.write_bytes(r.content)
                            clips.append(p)
                            log(f"    Saved: {p.name} ({len(r.content)//1024}KB)")
                    break
                if t.status in ("FAILED", "CANCELLED"):
                    log(f"    Runway shot {idx} {t.status}: {getattr(t, 'failure', '')}")
                    break
        except Exception as e:
            log(f"    Runway shot {idx} error: {e}")
        time.sleep(2)
    return clips


def _generate_shots_pika(shot_list: list, slot: int) -> list[Path]:
    """Final fallback: generate clips via Pika 2.2 through PiAPI (PIAPI_KEY required)."""
    import requests
    api_key = os.environ.get("PIAPI_KEY", "")
    if not api_key:
        return []

    log("  Provider: Pika 2.2 via PiAPI (final fallback)")
    clips = []
    for shot in shot_list:
        idx = shot["index"] + 1
        log(f"  Shot {idx}/{len(shot_list)}: {shot['type']} ({shot['duration_s']}s) [Pika]...")
        payload = {
            "model": "pika",
            "task_type": "text-to-video",
            "input": {
                "prompt": shot["prompt"][:300],
                "negative_prompt": shot.get("negative_prompt", ""),
                "duration": min(shot["duration_s"], 5),
                "aspect_ratio": "9:16",
            },
        }
        result = _piapi_submit_poll(api_key, payload, "PiAPI-Pika")
        if not result:
            continue
        output = result.get("output") or {}
        video_url = output.get("video_url") or ((output.get("works") or [{}])[0]).get("resource") or ""
        if video_url:
            try:
                r = requests.get(video_url, timeout=180)
                if r.status_code == 200:
                    p = OUTPUT_DIR / f"clip_{TIMESTAMP}_slot{slot}_pika_shot{shot['index']}.mp4"
                    p.write_bytes(r.content)
                    clips.append(p)
                    log(f"    Saved: {p.name} ({len(r.content)//1024}KB)")
                else:
                    log(f"    Download failed: HTTP {r.status_code}")
            except Exception as e:
                log(f"    Download error: {e}")
        else:
            log(f"    No video URL in Pika response")
        time.sleep(2)
    return clips


def _replicate_flux_call(client, prompt: str, shot_index: int, slot: int) -> bytes | None:
    """
    Single Flux 1.1 Pro Ultra call on Replicate.

    Model: flux-1.1-pro-ultra — 4MP native resolution, photographed realism.
    Parameters (confirmed against Replicate schema 2026-03-17):
      raw=True              — disables post-processing polish; raw photographic output.
                             Critical for avoiding the "plastic AI" look.
      prompt_upsampling=True — T5-XXL reads full sentence structure; upsampling
                               preserves specificity rather than averaging tokens.
      safety_tolerance=4    — permissive enough for finance content (currency, debt,
                               financial stress imagery) without triggering false flags.
      aspect_ratio="9:16"   — native vertical; no cropping or upscale required.
      output_format="jpg"   — smaller file size; indistinguishable from PNG at 95 quality.
      output_quality=95     — higher than default 80; preserves micro-texture in paper,
                               fabric, and metal that the quality mirror scores on.

    Cost: ~$0.06/image (20× flux-dev). At 3 shots × 7 slots = ~$1.26/day.
    """
    import requests as _req
    output = client.run(
        "black-forest-labs/flux-1.1-pro-ultra",
        input={
            "prompt":             prompt,
            "aspect_ratio":       "9:16",
            "raw":                True,
            "prompt_upsampling":  True,
            "safety_tolerance":   4,
            "output_format":      "jpg",
            "output_quality":     95,
        },
    )
    # Replicate returns FileOutput list, single FileOutput, or URL string
    if isinstance(output, list) and output:
        return output[0].read()
    if hasattr(output, "read"):
        return output.read()
    if isinstance(output, str) and output.startswith("http"):
        r = _req.get(output, timeout=60)
        return r.content if r.status_code == 200 else None
    return None


def _generate_shots_replicate_flux_kb(shot_list: list, routing: dict, slot: int, brief: dict = None) -> list[Path]:
    """
    Generate video clips via Flux 1.1 Pro Ultra on Replicate + FFmpeg Ken Burns motion.

    PiAPI is suspended indefinitely. Flux 1.1 Pro Ultra via Replicate is the sole
    visual provider — this is not a fallback, it is the format.

    Prompt architecture (research-derived, 2026-03-17):
      Full sentences > keyword bags (T5-XXL encoder reads language, not lists)
      Specific object + specific location + directional light source
      Camera + lens + film stock triplet (activates photographic training data)
      One deliberate imperfection per scene (separates real from AI-rendered)
      Partial human presence — hands/forearms only, never faces
      Avoidances embedded as positive requirements, not negative prompts
      raw=True disables BFL post-processing — critical for documentary realism
    """
    import requests
    api_token = os.environ.get("REPLICATE_API_TOKEN", "")
    if not api_token:
        log("  REPLICATE_API_TOKEN not set")
        return []

    try:
        import replicate as _replicate
    except ImportError:
        log("  replicate package not installed — run: pip install replicate")
        return []

    log("  Provider: Flux 1.1 Pro Ultra + Ken Burns (Replicate) — raw photographic mode")
    flux_prompt = routing.get("flux_prompt", {})
    clips = []

    for shot in shot_list:
        idx       = shot["index"] + 1
        shot_type = shot["type"]
        duration  = int(shot.get("duration_s", 5))
        frames    = duration * 25
        log(f"  Shot {idx}/{len(shot_list)}: {shot_type} ({duration}s) [Flux 1.1 Pro Ultra]...")

        # Full T5 prompt budget — positive only, avoidances embedded as requirements
        positive = (flux_prompt.get("positive") or shot["prompt"])[:1500]

        try:
            client    = _replicate.Client(api_token=api_token)
            img_bytes = _replicate_flux_call(client, positive, shot["index"], slot)

            if img_bytes is None:
                log(f"    Flux: no output returned for shot {idx}")
                continue

            img_path = OUTPUT_DIR / f"flux_{TIMESTAMP}_slot{slot}_shot{shot['index']}.jpg"
            img_path.write_bytes(img_bytes)
            log(f"    Flux image: {img_path.name} ({len(img_bytes)//1024}KB)")

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "throttled" in err_str or "rate limit" in err_str:
                log(f"    Rate-limited — waiting 15s then retrying shot {idx}...")
                time.sleep(15)
                try:
                    img_bytes = _replicate_flux_call(client, positive, shot["index"], slot)
                    if img_bytes:
                        img_path = OUTPUT_DIR / f"flux_{TIMESTAMP}_slot{slot}_shot{shot['index']}.jpg"
                        img_path.write_bytes(img_bytes)
                        log(f"    Flux image (retry): {img_path.name} ({len(img_bytes)//1024}KB)")
                    else:
                        log(f"    Retry returned no output — skipping shot {idx}")
                        continue
                except Exception as e2:
                    log(f"    Retry failed: {e2} — skipping shot {idx}")
                    continue
            else:
                log(f"    Flux shot {idx} failed: {e}")
                continue

        # Apply Ken Burns — emotion-derived motion, not applied as a default
        kb = _derive_shot_motion(shot_type, brief or {}, routing)
        z_expr = kb.get("z_expr", "zoom+0.0018")
        x_expr = kb.get("x_expr", "iw/2-(iw/zoom/2)")
        y_expr = kb.get("y_expr", "ih/2-(ih/zoom/2)")
        log(f"    Motion: {kb.get('motion_label', 'default')} ({shot_type})")

        clip_path = OUTPUT_DIR / f"clip_{TIMESTAMP}_slot{slot}_flux_shot{shot['index']}.mp4"
        kb_filter = (
            f"scale=1440:2560:flags=lanczos,"
            f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}'"
            f":d={frames}:s=1080x1920:fps=25,setsar=1"
        )
        cmd = [
            FFMPEG_BIN, "-y",
            "-loop", "1", "-i", str(img_path),
            "-vf", kb_filter,
            "-t", str(duration),
            "-r", "25",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(clip_path),
        ]
        try:
            kb_result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if kb_result.returncode != 0:
                log(f"    Ken Burns render failed: {kb_result.stderr[-300:]}")
                continue
            clips.append(clip_path)
            log(f"    KB clip: {clip_path.name} ({clip_path.stat().st_size//1024}KB)")
        except Exception as e:
            log(f"    Ken Burns error: {e}")
        # Replicate rate limit: burst of 1 on low-credit accounts, 6/min overall.
        # Wait 11s between shots to stay safely under the burst threshold.
        if idx < len(shot_list):
            time.sleep(11)

    return clips


def generate_kling_multi_shot(brief: dict, slot: int) -> tuple[Path | None, dict]:
    """
    Generate visual track — B-roll footage only. AI image generation disabled.

    Provider chain:
      1. footage_sourcer (yt-dlp, BROLL_SEARCH_QUERIES, random segment)
      2. assets/broll_cache/ local fallback (if yt-dlp fails)
    """
    sys.path.insert(0, str(SCRIPTS_DIR)); sys.path.insert(0, str(AGENTS_DIR))

    try:
        import footage_sourcer
        log("  Visual: B-roll mode (AI generation bypassed)")
        _niche = brief.get("niche", "tech_ai") if brief else "tech_ai"
        footage_path = footage_sourcer.source_footage(
            niche=_niche,
            output_dir=OUTPUT_DIR,
        )
        if footage_path and footage_path.exists() and footage_path.stat().st_size > 100_000:
            log(f"  B-roll sourced: {footage_path.name} ({footage_path.stat().st_size // 1024}KB)")
            return footage_path, {"visual_source": "broll_footage", "shot_list": []}
        else:
            log("  footage_sourcer returned nothing")
    except Exception as e:
        log(f"  footage_sourcer error: {e}")

    # Cache fallback — footage_sourcer already tries the cache internally,
    # but call it directly here as a safety net.
    try:
        import footage_sourcer as _fs
        cache_path = _fs.get_cache_fallback(OUTPUT_DIR)
        if cache_path and cache_path.exists():
            log(f"  Using broll_cache fallback: {cache_path.name}")
            return cache_path, {"visual_source": "broll_cache", "shot_list": []}
    except Exception as e:
        log(f"  Cache fallback error: {e}")

    log("ABORT: No footage available (yt-dlp failed, broll_cache empty)")
    return None, {}

# ---------------------------------------------------------------------------
# Step 2: Audio generation via ElevenLabs
# ---------------------------------------------------------------------------
def generate_audio(brief: dict, slot: int) -> tuple[Path | None, dict]:
    """Generate voiceover via ElevenLabs eleven_v3 with word-level alignment timestamps.
    Returns (audio_path, alignment_data) — alignment fed into caption_engine for exact timing."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key:
        log("ELEVENLABS_API_KEY not set")
        return None, {}

    import requests, base64

    script_text = brief.get("full_script_text", "")
    if not script_text:
        script = brief.get("script", {})
        script_text = " ".join([
            script.get("intrusion", ""),
            script.get("weight", ""),
            script.get("mechanism", ""),
            script.get("proof", ""),
            script.get("edge", ""),
            script.get("move", ""),
        ]).strip()

    voice_settings = brief.get("voice_settings", {})
    stability  = voice_settings.get("stability",  0.40)
    similarity = voice_settings.get("similarity", 0.88)
    style      = voice_settings.get("style",      0.70)
    speed      = voice_settings.get("speed",      0.92)
    # Use niche-specific voice_id from brief (set by creative_synthesis.py NICHE_VOICE_SETTINGS)
    voice_id   = voice_settings.get("voice_id", ELEVENLABS_VOICE_ID)

    # Strip SSML tags from plain-text fallback (eleven_multilingual_v2 speaks them literally).
    # eleven_v3 via /with-timestamps requires enable_ssml_parsing:true to interpret <break> tags
    # as silence rather than speaking the XML markup aloud.
    import re as _re
    script_text_clean = _re.sub(r'<[^>]+>', '', script_text).strip()

    log(f"Generating audio via eleven_v3 ({len(script_text.split())} words, voice={voice_id[:12]}...)")

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": script_text,          # full text with SSML tags
        "model_id": "eleven_v3",
        "enable_ssml_parsing": True,  # interpret <break> as silence, not spoken text
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity,
            "style": style,
            "use_speaker_boost": True,
            "speed": speed,
        },
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=90)
        if r.status_code == 200:
            data = r.json()
            audio_bytes = base64.b64decode(data["audio_base64"])
            audio_path = OUTPUT_DIR / f"audio_{TIMESTAMP}_slot{slot}.mp3"
            audio_path.write_bytes(audio_bytes)
            log(f"Audio saved: {audio_path.name} ({len(audio_bytes) // 1024} KB)")

            alignment = data.get("alignment", {})
            alignment_path = OUTPUT_DIR / f"audio_alignment_{TIMESTAMP}_slot{slot}.json"
            alignment_path.write_text(json.dumps(alignment))
            log(f"Alignment data saved: {alignment_path.name} ({len(alignment.get('characters', []))} chars)")
            return audio_path, alignment
        else:
            log(f"ElevenLabs v3 error {r.status_code}: {r.text[:200]}")
            log("Falling back to standard TTS endpoint (SSML stripped)...")
            url_fb = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            # eleven_multilingual_v2 does not support SSML — send clean text only
            fallback_payload = {
                **payload,
                "text": script_text_clean,
                "model_id": "eleven_multilingual_v2",
                "enable_ssml_parsing": False,
            }
            r2 = requests.post(url_fb,
                headers={**headers, "Accept": "audio/mpeg"},
                json=fallback_payload,
                timeout=60)
            if r2.status_code == 200:
                audio_path = OUTPUT_DIR / f"audio_{TIMESTAMP}_slot{slot}.mp3"
                audio_path.write_bytes(r2.content)
                log(f"Fallback audio saved: {audio_path.name}")
                return audio_path, {}
            log(f"Fallback also failed: {r2.status_code}")
            return None, {}
    except Exception as e:
        log(f"ElevenLabs request failed: {e}")
        return None, {}

def generate_ambience(ambience_type: str, duration_s: float, api_key: str) -> Path | None:
    """Generate ambient sound via ElevenLabs Sound Effects API."""
    import requests

    AMBIENCE_PROMPTS = {
        "server_hum":       "server room hum, data centre ambient noise, constant low electrical drone, cinematic",
        "rain_glass":       "heavy rain on glass window, distant thunder, night atmosphere, cinematic",
        "distant_traffic":  "distant city traffic at night, urban ambient, low rumble, cinematic",
    }
    prompt = AMBIENCE_PROMPTS.get(ambience_type, AMBIENCE_PROMPTS["server_hum"])

    log(f"Generating ambience: {ambience_type} ({duration_s:.1f}s)...")
    try:
        r = requests.post(
            "https://api.elevenlabs.io/v1/sound-generation",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={
                "text": prompt,
                "duration_seconds": min(duration_s, 22.0),  # API max 22s
                "prompt_influence": 0.5,
            },
            timeout=60,
        )
        if r.status_code == 200:
            path = OUTPUT_DIR / f"ambience_{TIMESTAMP}_{ambience_type}.mp3"
            path.write_bytes(r.content)
            log(f"Ambience saved: {path.name} ({len(r.content) // 1024} KB)")
            return path
        else:
            log(f"ElevenLabs SFX error {r.status_code}: {r.text[:200]}")
            return None
    except Exception as e:
        log(f"Ambience generation failed: {e}")
        return None


def mix_three_layer_audio(voice_path: Path, ambience_path: Path | None,
                          sub_bass_db: float, sub_bass_hz: int,
                          total_duration: float, slot: int,
                          bgm_volume: float = 0.12) -> Path | None:
    """Mix voice + ambience + sub-bass tone into final audio track via FFmpeg."""
    output_path = OUTPUT_DIR / f"mixed_audio_{TIMESTAMP}_slot{slot}.mp3"

    # Sub-bass sine tone expression
    sub_bass_linear = 10 ** (sub_bass_db / 20)
    sub_bass_expr = f"{sub_bass_linear:.4f}*sin(2*PI*{sub_bass_hz}*t)"

    if ambience_path and ambience_path.exists():
        # Three-layer mix: sub-bass + ambience (looped at bgm_volume) + voice
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"aevalsrc={sub_bass_expr}:s=44100:d={total_duration}",
            "-stream_loop", "-1", "-i", str(ambience_path),
            "-i", str(voice_path),
            "-filter_complex",
            (
                f"[0:a]volume=1.0[bass];"
                f"[1:a]volume={bgm_volume:.2f},atrim=duration={total_duration}[amb];"
                f"[2:a]volume=1.0[voice];"
                f"[bass][amb][voice]amix=inputs=3:duration=first:normalize=0[out]"
            ),
            "-map", "[out]",
            "-ar", "44100", "-ac", "1", "-b:a", "192k",
            str(output_path),
        ]
    else:
        # Two-layer mix: sub-bass + voice only
        cmd = [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-i", f"aevalsrc={sub_bass_expr}:s=44100:d={total_duration}",
            "-i", str(voice_path),
            "-filter_complex",
            "[0:a]volume=1.0[bass];[1:a]volume=1.0[voice];[bass][voice]amix=inputs=2:duration=first:normalize=0[out]",
            "-map", "[out]",
            "-ar", "44100", "-ac", "1", "-b:a", "192k",
            str(output_path),
        ]

    log(f"Mixing audio layers (sub-bass {sub_bass_hz}Hz @ {sub_bass_db}dB, ambience={'yes' if ambience_path else 'no'})...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            log(f"Audio mix failed: {result.stderr[-300:]}")
            return None
        log(f"Mixed audio saved: {output_path.name}")
        return output_path
    except Exception as e:
        log(f"Audio mix error: {e}")
        return None


# ---------------------------------------------------------------------------
# Step 3: FFmpeg render
# ---------------------------------------------------------------------------
def layer_hook_sfx(audio_path: Path, alignment_data: dict | None, slot: int) -> Path:
    """
    Layer a brief 880Hz sine-wave click SFX at the first 3 spoken word timestamps (#6).
    Creates a subtle audio pattern interrupt that fires on the hook's opening words.
    Returns a new mixed audio path, or the original if alignment is unavailable / ffmpeg fails.
    """
    if not alignment_data:
        log("SFX: WARNING — alignment_data absent, skipping hook click SFX layer")
        return audio_path
    try:
        sys.path.insert(0, str(SCRIPTS_DIR)); sys.path.insert(0, str(AGENTS_DIR))
        import caption_engine as _ce
        word_times = _ce._get_word_timestamps(alignment_data)
    except Exception as e:
        log(f"SFX: WARNING — word timestamp parse failed ({e}), skipping hook click SFX layer")
        return audio_path

    if not word_times:
        log("SFX: WARNING — no word timestamps extracted, skipping hook click SFX layer")
        return audio_path

    click_times = [wt[1] for wt in word_times[:3]]   # first 3 word start seconds
    n_clicks    = len(click_times)
    out_path    = OUTPUT_DIR / f"voice_sfx_{TIMESTAMP}_slot{slot}.mp3"

    # Build filter_complex: generate one 50ms 880Hz sine per click, delay it, mix with voice.
    # Each element in filter_parts is a single filter clause (no embedded semicolons).
    # ";".join() then produces a valid filter_complex string with exactly one ; between clauses.
    filter_parts = []
    for i, t_sec in enumerate(click_times):
        delay_ms = int(t_sec * 1000)
        filter_parts.append(f"[{i}:a]volume=0.12,adelay={delay_ms}[c{i}]")
    click_refs = "".join(f"[c{i}]" for i in range(n_clicks))
    # Voice label — separate clause, no embedded semicolon
    filter_parts.append(f"[{n_clicks}:a]volume=1.0[voice]")
    # Mix clause — separate clause
    filter_parts.append(f"{click_refs}[voice]amix=inputs={n_clicks + 1}:duration=longest:normalize=0[out]")

    cmd = [FFMPEG_BIN, "-y"]
    for _ in click_times:
        cmd.extend(["-f", "lavfi", "-i", "sine=frequency=880:duration=0.05"])
    cmd.extend(["-i", str(audio_path)])
    cmd.extend([
        "-filter_complex", ";".join(filter_parts),
        "-map", "[out]",
        "-ar", "44100", "-ac", "1", "-b:a", "192k",
        str(out_path),
    ])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1000:
            log(f"SFX: hook clicks layered at {[f'{t:.2f}s' for t in click_times]}")
            return out_path
        log(f"SFX: ffmpeg failed — {result.stderr[-120:]}")
    except Exception as e:
        log(f"SFX: error — {e}")
    return audio_path


def get_audio_duration(audio_path: Path) -> float:
    """Get audio duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return 75.0  # default

FONTS_DIR = str(Path.home() / "Library" / "Fonts")

# ffmpeg-full (with libass) is keg-only — prefer it over system ffmpeg for caption rendering
_FFMPEG_FULL_PATH = Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg")
FFMPEG_BIN = str(_FFMPEG_FULL_PATH) if _FFMPEG_FULL_PATH.exists() else "ffmpeg"

def _check_ffmpeg_filter(name: str) -> bool:
    """Return True if the preferred FFmpeg binary has the named filter compiled in.
    Uses ffmpeg-full (keg-only, includes libass) when available."""
    if name == "subtitles":
        r = subprocess.run([FFMPEG_BIN, "-buildconf"], capture_output=True, text=True, timeout=10)
        return "--enable-libass" in r.stdout
    r = subprocess.run(
        [FFMPEG_BIN, "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.01",
         "-vf", f"{name}=/dev/null", "-t", "0.01", "-f", "null", "-"],
        capture_output=True, text=True, timeout=10,
    )
    return "No such filter" not in r.stderr

LIBASS_AVAILABLE = _check_ffmpeg_filter("subtitles")

DRAWTEXT_AVAILABLE = _check_ffmpeg_filter("drawtext")

# ---------------------------------------------------------------------------
# Style Router — per-niche aesthetic, BGM, and caption configuration
# ---------------------------------------------------------------------------
BGM_CACHE_DIR = BASE_DIR / "assets" / "bgm_cache"

NICHE_STYLES = {
    "tech_ai": {
        "grade_filter": "eq=saturation=1.2:contrast=1.1",
        "vignette":     None,
        "noise":        None,
        "atempo":       1.1,
        "bgm_volume":   0.15,
        "bgm_subdir":   "tech",
        "caption_color": "&H00FFFF00",   # Cyan in ASS ABGR format
    },
    "dark_psychology": {
        "grade_filter": "eq=saturation=0.8:contrast=1.2",
        "vignette":     "vignette=PI/4",
        "noise":        None,
        "atempo":       1.0,
        "bgm_volume":   0.20,
        "bgm_subdir":   "psychology",
        "caption_color": "&H00FFFFFF",   # White in ASS ABGR format
    },
    "micro_mystery": {
        "grade_filter": "eq=brightness=-0.05:contrast=1.1",
        "vignette":     "vignette=PI/3",
        "noise":        "noise=alls=20:allf=t+u",
        "atempo":       0.95,
        "bgm_volume":   0.25,
        "bgm_subdir":   "mystery",
        "caption_color": "&H0000FFFF",   # Yellow in ASS ABGR format
    },
}

# Ensure BGM subdirectories exist
for _subdir in ("tech", "psychology", "mystery"):
    (BGM_CACHE_DIR / _subdir).mkdir(parents=True, exist_ok=True)

import random as _random

def get_niche_style(niche: str) -> dict:
    """Return style config for given niche, defaulting to tech_ai."""
    return NICHE_STYLES.get(niche, NICHE_STYLES["tech_ai"])


def pick_bgm(niche: str) -> "Path | None":
    """Randomly select a .mp3 from assets/bgm_cache/{subdir}/. Returns None if empty."""
    subdir  = NICHE_STYLES.get(niche, {}).get("bgm_subdir", "tech")
    bgm_dir = BGM_CACHE_DIR / subdir
    tracks  = list(bgm_dir.glob("*.mp3"))
    if not tracks:
        log(f"BGM: no tracks in {bgm_dir.name}/ — falling back to generated ambience")
        return None
    chosen = _random.choice(tracks)
    log(f"BGM selected: {chosen.name} ({niche})")
    return chosen


def preprocess_audio_tempo(voice_path: "Path", atempo: float, slot: int) -> "Path":
    """Apply atempo to voiceover. Returns processed path (or original if atempo==1.0 or fails)."""
    if abs(atempo - 1.0) < 0.001:
        return voice_path
    out_path = OUTPUT_DIR / f"voice_tempo_{TIMESTAMP}_slot{slot}.mp3"
    cmd = [
        FFMPEG_BIN, "-y", "-i", str(voice_path),
        "-filter:a", f"atempo={atempo:.2f}",
        "-b:a", "192k",
        str(out_path),
    ]
    log(f"Tempo: applying atempo={atempo:.2f} to voiceover...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 1000:
            log(f"Tempo: {out_path.name}")
            return out_path
        log(f"Tempo: failed — using original voice ({result.stderr[-150:]})")
    except Exception as _e:
        log(f"Tempo: error {_e} — using original voice")
    return voice_path


def patch_ass_caption_color(ass_path: "Path", primary_color: str) -> bool:
    """Replace PrimaryColour in all ASS Style lines. primary_color is ASS ABGR hex."""
    try:
        import re as _re
        content = ass_path.read_text(encoding="utf-8")
        # ASS Style: Name,Font,Size,PrimaryColour,... — patch only PrimaryColour (4th field)
        patched, count = _re.subn(
            r'(?m)^(Style: [^,\n]+,[^,\n]+,[^,\n]+,)(&H[0-9A-Fa-f]{8})',
            lambda m: m.group(1) + primary_color,
            content,
        )
        if count:
            ass_path.write_text(patched, encoding="utf-8")
            log(f"Captions: primary colour → {primary_color} ({count} style(s) patched)")
            return True
        log("Captions: no Style lines found to colour-patch")
    except Exception as _e:
        log(f"Captions: colour patch failed — {_e}")
    return False

def generate_captions(brief: dict, alignment_data: dict, slot: int) -> Path | None:
    """
    Generate niche-styled karaoke ASS subtitle file via caption_engine v4.
    Passes niche so caption_engine applies the correct font/colour/highlight.
    Returns path to .ass file, or None on failure.
    """
    try:
        sys.path.insert(0, str(SCRIPTS_DIR)); sys.path.insert(0, str(AGENTS_DIR))
        import caption_engine
        script_text = brief.get("full_script_text") or brief.get("assembled_script", "")
        niche       = brief.get("niche", "tech_ai")
        ass_content, meta = caption_engine.generate_ass(
            script_text, alignment_data,
            niche=niche,
        )
        ass_path = OUTPUT_DIR / f"captions_{TIMESTAMP}_slot{slot}.ass"
        ass_path.write_text(ass_content, encoding="utf-8")
        log(f"Captions: {ass_path.name} | {meta.get('total_events', 0)} blocks "
            f"| {meta.get('timing_mode', '?')} | font={meta.get('style_font', '?')}")
        return ass_path
    except Exception as e:
        log(f"Caption engine failed: {e} — subtitles omitted")
        return None

def append_fca_warning_to_ass(ass_path: Path, brief: dict) -> bool:
    """Inject a persistent FCA risk warning into the ASS subtitle file.
    Uses libass (the same renderer as captions) — drawtext is not compiled into this FFmpeg build.
    Returns True if the warning was injected, False if skipped (no regulated affiliate)."""
    affiliate = brief.get("affiliate", {})
    if isinstance(affiliate, str):
        return False
    name = affiliate.get("name", "")
    if not name or name == "[AFFILIATE]":
        return False

    # ASS style: small, semi-transparent black box, white text, bottom-centre
    # BackColour &H33000000 = black at ~80% opacity (ASS alpha: 0=opaque, FF=transparent)
    style_line = (
        "Style: FCAWarning,Montserrat,22,&H00FFFFFF,&H0000FFFF,&H00000000,&H33000000,"
        "0,0,0,0,100,100,0,0,3,10,0,2,20,20,20,1"
    )
    # Dialogue spanning full video length — FCA FG24/1 requires warning throughout
    event_line = (
        "Dialogue: 0,0:00:00.00,9:59:59.99,FCAWarning,,0,0,0,,"
        "Capital at risk. Tax treatment depends on individual circumstances."
    )

    content = ass_path.read_text()
    # Insert style after last Style: line
    lines = content.splitlines()
    insert_style_at = 0
    insert_event_at = 0
    for i, line in enumerate(lines):
        if line.startswith("Style:"):
            insert_style_at = i + 1
        if line.startswith("Dialogue:"):
            insert_event_at = i  # insert before first dialogue
            break

    if insert_style_at:
        lines.insert(insert_style_at, style_line)
        # Re-find event insertion index after shift
        for i, line in enumerate(lines):
            if line.startswith("Dialogue:"):
                insert_event_at = i
                break
    if insert_event_at:
        lines.insert(insert_event_at, event_line)

    ass_path.write_text("\n".join(lines) + "\n")
    return True


def render_video(brief: dict, visual_path: Path | None, audio_path: Path,
                 slot: int, alignment_data: dict | None = None,
                 niche_style: dict | None = None) -> Path | None:
    """FFmpeg render pipeline: B-roll/dark bg → niche grade → ASS captions → output."""
    output_path = OUTPUT_DIR / f"assembled_{TIMESTAMP}_slot{slot}.mp4"
    final_path  = OUTPUT_DIR / f"post_{TIMESTAMP}_slot{slot}.mp4"

    audio_duration = get_audio_duration(audio_path)
    total_duration = audio_duration + 0.5   # 0.5s black at start

    log(f"Rendering: duration={total_duration:.1f}s, visual={'Kling' if visual_path else 'dark background'}")

    if visual_path and visual_path.exists():
        video_input = ["-stream_loop", "-1", "-i", str(visual_path)]
        video_source = "[0:v]"
    else:
        video_input = ["-f", "lavfi", "-i", f"color=c=0x0d0d0d:s=1080x1920:d={total_duration}"]
        video_source = "[0:v]"

    # Generate ASS captions from brief + word-level alignment
    ass_path = generate_captions(brief, alignment_data or {}, slot)

    # Niche style — caption_engine already wrote niche colours directly into the ASS file.
    # patch_ass_caption_color is a safety override; not needed when caption_engine v4 is used.
    style = niche_style or get_niche_style(brief.get("niche", "tech_ai"))

    # Load A/B-tested editing params from evolution_engine (#3 micro-editing evolution)
    evo_params  = load_evolution_params()
    zoom_factor = evo_params.get("zoom_factor", 0.15)

    # Build niche-specific filtergraph: zoom-in → grade → [vignette] → [noise] → ASS subtitles
    # Pattern interrupt: scale zooms in over first 2s, magnitude = zoom_factor (#4)
    scale_filter = (
        f"{video_source}scale="
        f"w='trunc(1080*(1+{zoom_factor:.3f}*min(t/2\\,1))/2)*2':"
        f"h='trunc(1920*(1+{zoom_factor:.3f}*min(t/2\\,1))/2)*2':"
        f"eval=frame,crop=1080:1920"
    )
    log(f"  Zoom factor: {zoom_factor:.3f} (from evolution_params)")
    grade_filter = style.get("grade_filter", "eq=saturation=1.0:contrast=1.0")
    filters = [scale_filter, grade_filter]
    if style.get("vignette"):
        filters.append(style["vignette"])
    if style.get("noise"):
        filters.append(style["noise"])

    niche_label = brief.get("niche_label", brief.get("niche", "?"))
    log(f"  Style route:  {niche_label} | grade={grade_filter[:40]} | vignette={style.get('vignette') or 'off'} | noise={style.get('noise') or 'off'}")

    if ass_path and ass_path.exists():
        if LIBASS_AVAILABLE:
            safe_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
            filters.append(f"subtitles='{safe_ass}':fontsdir='{FONTS_DIR}'")
            log(f"  ASS subtitles: {ass_path.name}")
        else:
            log("  Subtitles: SKIPPED (libass not compiled — reinstall ffmpeg)")
    else:
        log("  Subtitles: none (caption engine unavailable)")

    # Visual CTA overlay: "Follow for More" for final 3.5 seconds (#7)
    if DRAWTEXT_AVAILABLE:
        cta_start = max(0.0, total_duration - 3.5)
        cta_filter = (
            f"drawtext=text='Follow for More':"
            f"fontsize=64:fontcolor=white:bordercolor=black:borderw=3:"
            f"x=(w-text_w)/2:y=h-150:"
            f"enable='gte(t,{cta_start:.1f})'"
        )
        filters.append(cta_filter)
        log(f"  CTA overlay: 'Follow for More' from t={cta_start:.1f}s")
    else:
        log("  CTA overlay: SKIPPED (drawtext not available)")

    vf_chain = ",".join(filters)

    cmd = [
        FFMPEG_BIN, "-y",
        *video_input,                        # input 0: B-roll video (video stream only)
        "-itsoffset", "0.5",                 # 0.5s black at start
        "-i", str(audio_path),               # input 1: voice/mixed audio
        "-map", "0:v:0",                     # video ONLY from B-roll — never its audio
        "-map", "1:a:0",                     # audio ONLY from voice track
        "-t", str(total_duration),
        "-vf", vf_chain,
        "-r", "30",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(output_path),
    ]

    log(f"FFmpeg: {' '.join(cmd[:8])}...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log(f"FFmpeg failed: {result.stderr[-500:]}")
            return None
    except Exception as e:
        log(f"FFmpeg error: {e}")
        return None

    # Strip metadata — use full path for cron compatibility
    _exiftool = "/opt/homebrew/bin/exiftool"
    if not Path(_exiftool).exists():
        import shutil as _shutil
        _exiftool = _shutil.which("exiftool") or "exiftool"
    stripped = subprocess.run(
        [_exiftool, "-all=", "-overwrite_original", str(output_path)],
        capture_output=True, text=True,
    )
    log(f"Metadata stripped: {stripped.stdout.strip()}")

    # Rename to final
    shutil.move(str(output_path), str(final_path))
    log(f"Render complete: {final_path.name}")
    return final_path

# ---------------------------------------------------------------------------
# Step 4: Quality check
# ---------------------------------------------------------------------------

def cleanup_slot_intermediates(slot: int, final_video: Path) -> dict:
    """
    Delete every intermediate file produced during this slot's render.
    Keeps only the final post_*.mp4 and the production manifest.

    Targets (all use module-level TIMESTAMP, so one glob catches everything):
      audio_*_slot{N}.mp3          — raw ElevenLabs voice
      audio_alignment_*_slot{N}.json
      voice_tempo_*_slot{N}.mp3   — atempo-adjusted voice
      mixed_audio_*_slot{N}.mp3   — voice + BGM + sub-bass
      voice_sfx_*_slot{N}.mp3     — SFX-layered final audio
      ambience_*.mp3               — ElevenLabs ambient sound
      bgm_*.mp3                    — downloaded / normalised BGM
      captions_*_slot{N}.ass       — ASS subtitle file
      footage_*.mp4                — raw B-roll download
      vertical_*.mp4  (non-final) — resized B-roll
      cache_fallback_*.mp4         — broll_cache copy
      assembled_*_slot{N}.mp4      — pre-rename render intermediate
    """
    deleted_bytes = 0
    deleted_files = []
    failed_files  = []

    # All files this slot wrote share TIMESTAMP in their name.
    # Walk OUTPUT_DIR and delete anything with TIMESTAMP except the final post file.
    for f in list(OUTPUT_DIR.iterdir()):
        if not f.is_file():
            continue
        if f == final_video:
            continue
        # Only touch files that belong to this render (contain TIMESTAMP)
        if TIMESTAMP not in f.name:
            continue
        # Never delete another slot's final post file
        if f.name.startswith("post_") and f.suffix == ".mp4":
            continue
        try:
            size = f.stat().st_size
            f.unlink()
            deleted_bytes += size
            deleted_files.append(f.name)
        except Exception as e:
            failed_files.append(f"{f.name}: {e}")

    mb = deleted_bytes / (1024 * 1024)
    log(f"Cleanup slot {slot}: deleted {len(deleted_files)} files, freed {mb:.1f} MB")
    for name in deleted_files:
        log(f"  rm {name}")
    for err in failed_files:
        log(f"  WARN cleanup failed: {err}")

    return {"deleted": len(deleted_files), "freed_mb": round(mb, 1), "errors": len(failed_files)}


def quality_check(video_path: Path, brief: dict) -> dict:
    """Verify output against brief. Return pass/fail for each criterion."""
    results = {}

    # File size — must be >5MB (catches silent black screen renders) and <50MB (TikTok limit)
    size_mb = video_path.stat().st_size / 1024 / 1024
    results["file_size_mb"] = round(size_mb, 2)
    results["size_ok"] = 5 <= size_mb < 50

    # Resolution + duration via ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-show_format", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode != 0:
            log(f"Quality check: ffprobe failed (exit {probe.returncode}) — defaulting to 1920x1080: {probe.stderr[:200]}")
            raise ValueError(f"ffprobe non-zero exit: {probe.returncode}")
        data = json.loads(probe.stdout)
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        w, h = video_stream.get("width", 0), video_stream.get("height", 0)
        duration = float(data.get("format", {}).get("duration", 0))

        results["resolution"] = f"{w}x{h}"
        results["resolution_ok"] = (w == 1080 and h == 1920)
        results["duration_s"] = round(duration, 1)
        results["duration_ok"] = 10 <= duration <= 60   # covers short(15s) + long(30s) A/B variants
        if not results["duration_ok"]:
            log(f"  [WARN] Duration {duration:.1f}s outside 10-60s target")
    except Exception as e:
        log(f"Quality check probe error: {e}")
        results["probe_error"] = str(e)

    # Log results
    for k, v in results.items():
        icon = "OK" if v is True else ("FAIL" if v is False else "INFO")
        log(f"  [{icon}] {k}: {v}")

    return results

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot",  type=int, default=1, choices=list(range(1, 11)))
    parser.add_argument("--niche", type=str, default=None,
                        choices=["tech_ai", "dark_psychology", "micro_mystery"],
                        help="Override niche from brief (used by blitz_10.sh)")
    args = parser.parse_args()
    slot = args.slot

    load_env()

    log("=" * 60)
    log(f"AGENT 3: PRODUCTION (SLOT {slot})")
    log("=" * 60)

    brief = load_creative_brief(slot)

    # CLI --niche overrides whatever the brief says (ensures blitz_10 routing is enforced)
    if args.niche:
        brief["niche"] = args.niche
        log(f"Niche override: {args.niche} (CLI)")

    niche       = brief.get("niche", "tech_ai")
    niche_style = get_niche_style(niche)
    niche_labels = {"tech_ai": "Tech / AI Hacks", "dark_psychology": "Dark Psychology", "micro_mystery": "Micro-Mysteries"}
    log(f"Niche:       {niche_labels.get(niche, niche)}")

    # Compliance check — ensure disclosures are present before render
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import compliance_injector
        brief = compliance_injector.inject(brief)
        injected = brief.get("compliance_injected", [])
        if injected:
            log(f"Compliance: injected {injected}")
        else:
            log("Compliance: all disclosures present")
    except ImportError:
        log("WARNING: compliance_injector not found — skipping compliance check")

    # Load directives for sound profile
    directives = load_directives()
    sound_profile = directives.get("sound_profile", {})
    acoustic_profile = directives.get("acoustic_profile", {})

    # Determine sound profile for this slot (A/B test assigns per slot until winner locked)
    slot_sound = {
        1: {"sub_bass_db": -18, "ambience_type": "server_hum"},
        2: {"sub_bass_db": -22, "ambience_type": "rain_glass"},
        3: {"sub_bass_db": -20, "ambience_type": "distant_traffic"},
        4: {"sub_bass_db": -18, "ambience_type": "server_hum"},
        5: {"sub_bass_db": -22, "ambience_type": "rain_glass"},
    }.get(slot, {})

    # Use locked values from directives if available, else slot A/B defaults
    sub_bass_db = sound_profile.get("sub_bass_db", slot_sound.get("sub_bass_db", -18))
    sub_bass_hz = sound_profile.get("sub_bass_hz", 40)
    ambience_type = sound_profile.get("ambience_type", slot_sound.get("ambience_type", "server_hum"))

    # Step 1: Audio — eleven_v3 with word-level timestamps (auto-retry x3, 30s wait)
    # MUST run before visual generation so we never spend visual API credits on broken audio.
    audio_result = _retry_call(
        generate_audio, brief, slot,
        max_attempts=3, wait_secs=30, label="ElevenLabs",
        result_ok=lambda r: r is not None and r[0] is not None,
    )
    if not audio_result:
        log("ABORT: Audio generation failed after 3 attempts")
        sys.exit(1)
    audio_path, alignment_data = audio_result

    # Step 1 pre-check: Validate raw voice file before ANY mixing (BGM/SFX cannot mask silence)
    _voice_size = audio_path.stat().st_size if audio_path and audio_path.exists() else 0
    if _voice_size < 10_000:
        log(f"ABORT: Raw voice file too small ({_voice_size} bytes < 10 KB) — likely silent/corrupt ElevenLabs response")
        sys.exit(1)
    _vol_probe = subprocess.run(
        ["ffmpeg", "-i", str(audio_path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True
    )
    _vol_match = re.search(r"max_volume:\s*([-\d.]+)\s*dB", _vol_probe.stderr)
    if _vol_match:
        _max_vol_db = float(_vol_match.group(1))
        if _max_vol_db < -40.0:
            log(f"ABORT: Raw voice file is silent (max_volume={_max_vol_db:.1f} dB < -40 dB) — aborting before visual API call")
            sys.exit(1)
        log(f"Voice pre-check PASS: max_volume={_max_vol_db:.1f} dB, size={_voice_size // 1024} KB")
    else:
        log("Voice pre-check WARNING: could not parse volumedetect output — proceeding")

    # Step 2: Visual — multi-shot prompt engine + Kling 3.0
    # Audio is validated above — safe to spend visual API credits now.
    visual_path, prompt_metadata = generate_kling_multi_shot(brief, slot)
    if not visual_path:
        log("ABORT: All visual providers failed (insufficient credits or API error) — refusing to distribute black screen")
        sys.exit(1)

    # Step 2a: Apply niche voiceover tempo
    atempo = niche_style.get("atempo", 1.0)
    audio_path = preprocess_audio_tempo(audio_path, atempo, slot)

    # Step 2b: BGM — source_bgm() tries yt-dlp niche queries then bgm_cache fallback.
    # Fallback to ElevenLabs ambience only if both fail.
    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    audio_duration = get_audio_duration(audio_path)
    bgm_volume     = niche_style.get("bgm_volume", 0.15)
    ambience_path  = None
    try:
        sys.path.insert(0, str(SCRIPTS_DIR)); sys.path.insert(0, str(AGENTS_DIR))
        import footage_sourcer as _fs
        ambience_path = _fs.source_bgm(niche, OUTPUT_DIR)
        if ambience_path:
            log(f"BGM sourced via footage_sourcer: {ambience_path.name}")
    except Exception as _e:
        log(f"footage_sourcer.source_bgm failed: {_e}")
    if not ambience_path and elevenlabs_key:
        ambience_path = generate_ambience(ambience_type, audio_duration + 0.5, elevenlabs_key)

    mixed_audio = mix_three_layer_audio(
        audio_path, ambience_path, sub_bass_db, sub_bass_hz,
        audio_duration + 0.5, slot, bgm_volume=bgm_volume,
    )
    if mixed_audio:
        log(f"Using mixed audio: {mixed_audio.name}")
        audio_path = mixed_audio
        audio_duration = get_audio_duration(audio_path)  # re-read after mix — duration may change
        log(f"Post-mix audio duration: {audio_duration:.2f}s")
    else:
        log("WARNING: Audio mix failed — rendering with voice only")

    # Step 2c: SFX — layer 880Hz click at first 3 hook word timestamps (#6)
    audio_path = layer_hook_sfx(audio_path, alignment_data, slot)

    # Step 3: Render — niche style routing + real ASS caption timing (auto-retry x3, 30s wait)
    final_video = _retry_call(
        render_video, brief, visual_path, audio_path, slot, alignment_data, niche_style,
        max_attempts=3, wait_secs=30, label="FFmpeg render",
        result_ok=lambda r: r is not None,
    )
    if not final_video:
        log("ABORT: Render failed after 3 attempts")
        sys.exit(1)

    # Step 4: Quality check
    qc = quality_check(final_video, brief)

    # Capture evolution params used this run for auditor correlation (#3)
    evo_params_used = load_evolution_params()

    # Write manifest — includes prompt_metadata for quality_mirror genome scoring
    manifest = {
        "timestamp": NOW.isoformat(),
        "slot": slot,
        "sound_profile": {"sub_bass_db": sub_bass_db, "sub_bass_hz": sub_bass_hz, "ambience_type": ambience_type},
        "asymmetry": brief.get("asymmetry"),
        "affiliate": brief.get("affiliate"),
        "script": brief.get("script"),
        "caption_text": brief.get("caption_text"),
        "visual_direction": brief.get("visual_direction"),
        "voice_settings": brief.get("voice_settings"),
        "visual_source": (
            prompt_metadata.get("visual_source", "")
            or ("footage_sourcer" if prompt_metadata.get("footage_path") else
                ("Flux+KB" if visual_path and "_flux_" in visual_path.name else
                 ("dark_background" if not visual_path else "AI")))
        ),
        "audio": str(audio_path),
        "video": str(final_video),
        "quality_check": qc,
        "prompt_metadata": prompt_metadata,
        "audio_alignment":        bool(alignment_data),
        "ffmpeg_evolution_params": {
            "zoom_factor":     evo_params_used.get("zoom_factor", 0.15),
            "ssml_break_secs": evo_params_used.get("ssml_break_secs", 0.8),
            "prompt_version":  evo_params_used.get("prompt_version", 1),
        },
        "script_variant": brief.get("script_variant", "unknown"),
    }
    manifest_path = LOGS_DIR / f"production_manifest_{TIMESTAMP}_slot{slot}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    log(f"Manifest saved: {manifest_path.name}")

    # Archive copy in ads_ready_for_review/ for record-keeping
    review_path = ADS_READY_DIR / final_video.name
    shutil.copy(str(final_video), str(review_path))
    log(f"Archived:       {review_path.name}")

    log("\n--- PRODUCTION SUMMARY ---")
    log(f"Slot:        {slot}")
    log(f"Niche:       {brief.get('niche_label', brief.get('niche', '?'))}")
    log(f"Visual:      {'B-roll' if visual_path else 'dark background'}")
    log(f"Audio:       {audio_path.name}")
    log(f"Video:       {final_video.name}")
    log(f"Size:        {qc.get('file_size_mb', '?')} MB")
    log(f"Resolution:  {qc.get('resolution', '?')}")
    log(f"Duration:    {qc.get('duration_s', '?')}s")
    log(f"All checks:  {'PASS' if all(v is True for k, v in qc.items() if k.endswith('_ok')) else 'PARTIAL'}")
    log(f"Shots:       {len(prompt_metadata.get('shot_list', []))} (genome gen {prompt_metadata.get('genome_generation', '—')})")
    log(f"Alignment:   {'real timestamps' if alignment_data else 'estimated (fallback)'}")
    log("=" * 60)

    # ── Step 5: Final QA Gate — pre-upload integrity firewall ────────────────
    # Mathematically verifies audio presence, volume, duration, and file size.
    # A silent or corrupted render is quarantined here and never reaches Buffer.
    log("\n[QA GATE] Running pre-upload integrity checks...")
    QUARANTINE_DIR = OUTPUT_DIR / "quarantine"
    QUARANTINE_DIR.mkdir(exist_ok=True)

    try:
        sys.path.insert(0, str(AGENTS_DIR))
        import final_qa_gate as _qa
        qa_result = _qa.run_qa_gate(final_video)
    except Exception as _qe:
        log(f"[QA GATE] Import error: {_qe} — running as subprocess")
        qa_result = None
        _r = subprocess.run(
            [sys.executable, str(AGENTS_DIR / "final_qa_gate.py"), str(final_video), "--json"],
            capture_output=True, text=True, timeout=120,
        )
        try:
            qa_result = json.loads(_r.stdout)
        except Exception:
            qa_result = {"passed": False, "checks": [{"name": "subprocess", "passed": False,
                          "detail": _r.stderr[-200:]}]}

    for chk in qa_result.get("checks", []):
        icon = "PASS" if chk["passed"] else "FAIL"
        log(f"  [{icon}] {chk['name']}: {chk['detail']}")

    if not qa_result.get("passed", False):
        log("\n!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        log("  QA FAILED: Silent/Corrupted Render — UPLOAD ABORTED")
        log("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
        quarantine_path = QUARANTINE_DIR / final_video.name
        shutil.move(str(final_video), str(quarantine_path))
        log(f"  Quarantined: {quarantine_path}")
        log("  Slot will not be published. Investigate quarantine/ before next run.")
        manifest["qa_gate"] = {"passed": False, "checks": qa_result.get("checks", [])}
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(json.dumps(manifest, indent=2))
        sys.exit(1)

    log("[QA GATE] PASS — all checks cleared. Proceeding to upload.")
    manifest["qa_gate"] = {"passed": True, "checks": qa_result.get("checks", [])}
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Auto-publish via distribute.py
    distribute_script = SCRIPTS_DIR / "distribute.py"
    if not distribute_script.exists():
        log(f"ERROR: distribute.py not found at {distribute_script} — publish ABORTED. Move or restore the file.")
        sys.exit(1)
    log(f"Auto-publishing: distribute.py --slot {slot}")
    try:
        dist_result = subprocess.run(
            [sys.executable, str(distribute_script), "--slot", str(slot)],
            capture_output=True, text=True, timeout=300,
            env={**os.environ},
        )
        if dist_result.returncode == 0:
            log("distribute.py: SUCCESS")
        else:
            log(f"distribute.py: FAILED (exit {dist_result.returncode})")
            if dist_result.stderr:
                log(dist_result.stderr.strip()[-300:])
    except Exception as _e:
        log(f"distribute.py error: {_e}")

    # ── Step 6: Garbage collection ────────────────────────────────────────────
    # Delete all intermediates for this slot (B-roll, audio, ASS captions, mixes).
    # The final post_*.mp4 and production manifest are the only survivors.
    log("\n[GC] Running post-upload garbage collection...")
    gc_result = cleanup_slot_intermediates(slot, final_video)
    manifest["garbage_collection"] = gc_result
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()
