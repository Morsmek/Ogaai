"""
OgaAI — WhatsApp Chatbot that speaks Nigerian Pidgin
FastAPI application: webhook + landing page
"""

import os
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from .intent_parser import parse
from .actions import dispatch, ActionResult
from .response_engine import build_reply

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ogaai")

WHATSAPP_TOKEN    = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN      = os.getenv("VERIFY_TOKEN", "ogaai_verify_2024")
WA_API_URL        = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OgaAI starting up — e don ready!")
    yield
    logger.info("OgaAI shutting down.")


app = FastAPI(
    title="OgaAI",
    description="WhatsApp AI assistant that speaks Nigerian Pidgin",
    version="1.0.0",
    lifespan=lifespan,
)

_base = os.path.dirname(os.path.dirname(__file__))
app.mount("/static", StaticFiles(directory=os.path.join(_base, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(_base, "templates"))


# ─── WhatsApp webhook verification (GET) ─────────────────────────────────────

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = dict(request.query_params)
    mode      = params.get("hub.mode")
    token     = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=challenge, media_type="text/plain")

    raise HTTPException(status_code=403, detail="Verification failed")


# ─── WhatsApp message handler (POST) ─────────────────────────────────────────

@app.post("/webhook")
async def receive_message(request: Request):
    body = await request.json()
    logger.debug("Incoming webhook: %s", body)

    try:
        entry   = body["entry"][0]
        changes = entry["changes"][0]
        value   = changes["value"]

        if "messages" not in value:
            return JSONResponse({"status": "no_messages"})

        msg     = value["messages"][0]
        user_id = msg["from"]
        text    = msg.get("text", {}).get("body", "")

        if not text:
            return JSONResponse({"status": "non_text_ignored"})

        logger.info("User %s: %s", user_id, text)

        # Pipeline: parse → action → response
        parsed        = parse(text)
        action_result = await dispatch(parsed, user_id)
        reply         = await build_reply(parsed, action_result, text)

        await _send_whatsapp(user_id, reply)
        logger.info("Reply to %s: %s", user_id, reply[:80])

    except (KeyError, IndexError):
        pass  # malformed / status callbacks — silently accept

    return JSONResponse({"status": "ok"})


async def _send_whatsapp(to: str, message: str) -> None:
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        logger.warning("WhatsApp credentials not set — skipping send")
        return
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            WA_API_URL,
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": message},
            },
        )
        if r.status_code != 200:
            logger.error("WhatsApp send failed %s: %s", r.status_code, r.text)


# ─── Landing page ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "whatsapp_configured": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID),
        "cowrie_configured":   bool(os.getenv("COWRIE_CLIENT_ID")),
        "llm_configured":      bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")),
    }


# ─── Chat API (webapp) ───────────────────────────────────────────────────────

@app.post("/chat")
async def chat(request: Request):
    """Web chat: POST {"message": "...", "session_id": "..."} → {"reply": "..."}"""
    body       = await request.json()
    text       = body.get("message", "").strip()
    session_id = body.get("session_id", "web_user")
    if not text:
        return JSONResponse({"reply": "Abeg type something na!", "intent": "UNKNOWN"})
    parsed = parse(text)
    action = await dispatch(parsed, session_id)
    reply  = await build_reply(parsed, action, text)
    return {
        "reply":      reply,
        "intent":     parsed.intent,
        "confidence": parsed.confidence,
        "flags":      parsed.flags,
    }


# ─── Test endpoint (dev/debug) ────────────────────────────────────────────────

@app.post("/test")
async def test_parse(request: Request):
    """Debug: POST {"message": "..."} → full parse details."""
    body   = await request.json()
    text   = body.get("message", "")
    parsed = parse(text)
    action = await dispatch(parsed, "test_user")
    reply  = await build_reply(parsed, action, text)
    return {
        "input":      text,
        "intent":     parsed.intent,
        "amount":     parsed.amount,
        "network":    parsed.network,
        "phone":      parsed.phone,
        "flags":      parsed.flags,
        "confidence": parsed.confidence,
        "reply":      reply,
    }
