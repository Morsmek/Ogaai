"""
OgaAI Response Engine
Fast path: templated Pidgin replies.
LLM path (priority order): DeepSeek → Anthropic Claude → OpenAI GPT-4o-mini
Set whichever key(s) you have in .env — first available wins.
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


async def _call_openai_compat(
    url: str, api_key: str, model: str, user_message: str
) -> str:
    """Generic OpenAI-compatible chat completions call (works for DeepSeek, OpenAI, etc.)"""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={
                    "model": model,
                    "max_tokens": 300,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",   "content": user_message},
                    ],
                },
            )
            data = r.json()
            return data["choices"][0]["message"]["content"]
    except Exception:
        return ""


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


async def _llm_reply(user_message: str) -> str:
    """Try DeepSeek → Claude → OpenAI in order; first key that works wins."""

    # 1. DeepSeek (OpenAI-compatible, cheapest)
    key = os.getenv("DEEPSEEK_API_KEY")
    if key:
        reply = await _call_openai_compat(
            "https://api.deepseek.com/v1/chat/completions",
            key, "deepseek-chat", user_message,
        )
        if reply:
            return reply

    # 2. Anthropic Claude Haiku
    reply = await _call_claude(user_message)
    if reply:
        return reply

    # 3. OpenAI GPT-4o-mini
    key = os.getenv("OPENAI_API_KEY")
    if key:
        reply = await _call_openai_compat(
            "https://api.openai.com/v1/chat/completions",
            key, "gpt-4o-mini", user_message,
        )
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
