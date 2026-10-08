"""Provider price table for preflight estimates and cost reporting.

Source + verification status per row lives in docs/02-PROVIDERS-AND-COSTS.md (researched 2026-10-08).
V = read on vendor page, S = vendor docs quoted in search, 3P = third-party only. Re-verify before launch.
All amounts USD micros (1e-6 USD). Credits: 1 credit = $0.01 of provider list cost (commercial decision).
"""

PRICES = {
    # LLM per 1M tokens (V)
    "llm:claude-opus-5-5": {"in": 4_000_000, "out": 20_000_000},
    "llm:claude-sonnet-5-5": {"in": 2_000_000, "out": 10_000_000},
    "llm:claude-haiku-5-5": {"in": 100_000, "out": 500_000},
    # per output second of video
    "video:veo-3.1": 400_000,  # with audio, V
    "video:veo-3.1-fast": 120_000,  # V
    "video:veo-3.1-lite": 50_000,  # V (720p, no audio)
    "video:kling-3": 140_000,  # 3P
    "video:runway-gen-4.5": 250_000,  # 3P
    "video:studio_preview_local": 0,
    # lip-sync per second
    "lipsync:sync-lipsync-2-pro": 83_000,  # S
    "lipsync:latentsync-selfhost": 9_000,  # est. RunPod L40S
    "lipsync:viseme_local": 0,
    # TTS per 1K characters
    "tts:elevenlabs-v3": 100_000,  # S
    "tts:elevenlabs-flash": 50_000,  # S
    "tts:google-chirp3-hd": 30_000,  # V
    "tts:espeak_local": 0,
    # image per image
    "image:nano-banana-2": 67_000,  # V
    "image:nano-banana-pro": 134_000,  # V
    "image:portrait_local": 0,
    # music per 30 s clip
    "music:lyria-3": 40_000,  # V
    "music:procedural_local": 0,
}

# Premium-route shadow estimate shown next to the local cost so creators/finance can see unit economics.
PREMIUM_ROUTE = {"video": "video:veo-3.1-fast", "lipsync": "lipsync:sync-lipsync-2-pro",
                 "tts": "tts:elevenlabs-v3", "image": "image:nano-banana-2", "music": "music:lyria-3"}
BUDGET_ROUTE = {"video": "video:veo-3.1-lite", "lipsync": "lipsync:latentsync-selfhost",
                "tts": "tts:elevenlabs-flash", "image": "image:nano-banana-2", "music": "music:lyria-3"}
RETAKE_FACTOR = 2.5  # generations per kept second (researched assumption)

LOCAL_COMPUTE_MICROS_PER_RENDER_SECOND = 300  # CPU time on a ~$0.10/h vCPU, rounded up


def llm_cost_micros(model: str, tin: int, tout: int) -> int:
    p = PRICES.get(f"llm:{model}")
    if not p:
        return 0
    return (tin * p["in"] + tout * p["out"]) // 1_000_000


def micros_to_credits(m: int) -> int:
    return -(-m // 10_000)  # ceil to 1 credit = $0.01


def route_estimate(route: dict, *, seconds: float, chars: int, images: int) -> dict:
    lines = {
        "video": PRICES[route["video"]] * seconds * RETAKE_FACTOR,
        "lipsync": PRICES[route["lipsync"]] * seconds,
        "tts": PRICES[route["tts"]] * chars / 1000,
        "image": PRICES[route["image"]] * images,
        "music": PRICES[route["music"]] * max(1, round(seconds / 30)),
    }
    total = int(sum(lines.values()))
    return {"line_items_usd": {k: round(v / 1e6, 2) for k, v in lines.items()}, "total_usd": round(total / 1e6, 2)}
