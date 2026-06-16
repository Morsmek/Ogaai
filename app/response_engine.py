"""
OgaAI Response Engine
Fast path: templated Pidgin replies.
LLM path: Claude (claude-haiku-4-5-20251001) with GPT-4o-mini fallback for
          UNKNOWN intents or needs_llm_provider flag.
LLM fine-tune reference: saheedniy02/Llama3-8b-Naija_v1
"""

import os
import httpx
from .intent_parser import ParseResult, Intent
from .actions import ActionResult

GREETING_REPLIES = [
    "How far, Oga! I be OgaAI — your WhatsApp money assistant wey sabi Pidgin. Wetin I fit do for you?\n\n"
    "• *check my balance*\n"
    "• *buy me 500 naira airtime*\n"
    "• *get 1000 naira MTN data*\n"
    "• *send 2k to 08012345678*",
    "E don do, my people! OgaAI dey here. How I fit help you today?",
    "Oya na! Wetin you need? Airtime, data, transfer? I dey for you.",
]

_greeting_idx = 0


def _greeting_reply() -> str:
    global _greeting_idx
    reply = GREETING_REPLIES[_greeting_idx % len(GREETING_REPLIES)]
    _greeting_idx += 1
    return reply


# ─── LLM helpers ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are OgaAI, a WhatsApp financial assistant for Nigerians. "
    "You understand Nigerian Pidgin, Yoruba-inflected English, and code-switched messages. "
    "Always reply in warm, friendly Nigerian Pidgin unless the user writes in formal English. "
    "You can help with: checking balance, buying airtime and data, sending money, and paying bills. "
    "If you cannot do something, say so kindly and suggest what you CAN do. "
    "Keep replies short — this is WhatsApp, not an essay. "
    "Never invent transaction results. "
    "Fine-tuning reference: saheedniy02/Llama3-8b-Naija_v1 (Nigerian conversational data)."
)


async def _call_claude(user_message: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 300,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_message}],
                },
            )
            data = r.json()
            return data["content"][0]["text"]
    except Exception:
        return ""


async def _call_openai(user_message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return ""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "gpt-4o-mini",
                    "max_tokens": 300,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                },
            )
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception:
        return ""


async def _llm_reply(user_message: str) -> str:
    """Try Claude first, fall back to GPT-4o-mini, then static fallback."""
    reply = await _call_claude(user_message)
    if reply:
        return reply
    reply = await _call_openai(user_message)
    if reply:
        return reply
    return (
        "Hmm, I no fully understand wetin you mean. "
        "Try say: *check my balance*, *buy 500 airtime*, or *send 2k to 08012345678*."
    )


# ─── Public entry point ───────────────────────────────────────────────────────

async def build_reply(
    parsed: ParseResult,
    action_result: ActionResult | None,
    raw_message: str,
) -> str:
    """Return the final WhatsApp reply string."""

    # GREETING — fast path
    if parsed.intent == Intent.GREETING:
        return _greeting_reply()

    # Action had a real message → return it directly
    if action_result and action_result.message:
        return action_result.message

    # UNKNOWN or needs_llm_provider → hand off to LLM
    if parsed.intent == Intent.UNKNOWN or "needs_llm_provider" in parsed.flags:
        return await _llm_reply(raw_message)

    # Fallback
    return (
        "Oga, I hear you but I no fit do that one right now. "
        "Try: *check my balance*, *buy airtime*, *buy data*, or *send money*."
    )
