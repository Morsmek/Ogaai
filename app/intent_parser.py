"""
OgaAI Intent Parser
Inspired by LPMatrix/owo — Nigerian NLU intent parser.
Supports Nigerian Pidgin, Yoruba-inflected English, and code-switched commands.
"""

import re
from dataclasses import dataclass, field
from typing import Optional


# ─── Intent constants ────────────────────────────────────────────────────────

class Intent:
    CHECK_BALANCE = "CHECK_BALANCE"
    BUY_AIRTIME   = "BUY_AIRTIME"
    BUY_DATA      = "BUY_DATA"
    SEND_MONEY    = "SEND_MONEY"
    PAY_BILL      = "PAY_BILL"
    GREETING      = "GREETING"
    UNKNOWN       = "UNKNOWN"


# ─── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ParseResult:
    intent: str = Intent.UNKNOWN
    amount: Optional[float] = None
    currency: str = "NGN"
    recipient: Optional[str] = None
    bank: Optional[str] = None
    network: Optional[str] = None
    phone: Optional[str] = None
    language: str = "en-pidgin"
    confidence: float = 0.0
    flags: list = field(default_factory=list)
    raw: str = ""


# ─── Pidgin / multilingual normalisation ─────────────────────────────────────

PIDGIN_MAP = {
    r"\babeg\b":        "",          # polite prefix — strip
    r"\bpls\b":         "",
    r"\bplease\b":      "",
    r"\boya\b":         "",          # filler — strip
    r"\be don do\b":    "",
    r"\bwetin\b":       "what",
    r"\bdey\b":         "is",
    r"\bmy guy\b":      "my friend",
    r"\bmy people\b":   "my friend",
    r"\bbrother\b":     "my friend",
    r"\bsister\b":      "my friend",
    r"\bnaira\b":       "NGN",
    r"₦":               "NGN ",
    r"\bk\b":           "000",       # 2k → 2000 (handled separately)
}

# Yoruba keywords → English concept
YORUBA_MAP = {
    r"\bsan owo\b":     "pay",
    r"\bowo\b":         "money",
    r"\bina\b":         "electricity",
    r"\bomi\b":         "water",
    r"\bgba\b":         "collect",
    r"\ble\b":          "",
    r"\bpe\b":          "call",
    r"\bj[eẹ] k[ií] n\b": "let me",
    r"\bemi\b":         "i",
    r"\bmi\b":          "me",
}

NETWORK_ALIASES = {
    "mtn":      "MTN",
    "airtel":   "AIR",
    "air":      "AIR",
    "glo":      "GLO",
    "9mobile":  "ETI",
    "etisalat": "ETI",
    "9mob":     "ETI",
}

GREETING_TRIGGERS = {
    "how far", "howfar", "hafa", "how body", "wetin dey",
    "sup", "hi", "hello", "hey", "good morning", "good afternoon",
    "good evening", "oga", "how now",
}

BALANCE_TRIGGERS = [
    r"check.{0,20}balance",
    r"balance.{0,20}check",
    r"how much.{0,20}(i|my).{0,10}(get|have|dey)",
    r"(i|my).{0,10}(balance|account)",
    r"wetin.{0,20}dey.{0,20}account",
]

AIRTIME_TRIGGERS = [
    r"(buy|get|send|recharge).{0,20}airtime",
    r"airtime.{0,20}(for|of|worth)",
    r"top.{0,5}up",
    r"load.{0,5}airtime",
    r"(buy|get).{0,10}credit",
]

DATA_TRIGGERS = [
    r"(buy|get|subscribe|sub).{0,20}data",
    r"data.{0,20}(bundle|plan|sub)",
    r"(get|buy).{0,20}(mb|gb).{0,20}data",
    r"internet.{0,20}(data|plan|bundle)",
]

SEND_TRIGGERS = [
    r"(send|transfer|move).{0,20}(money|naira|NGN|cash|funds)",
    r"send\s+\d",
    r"transfer\s+\d",
    r"(give|pay).{0,20}(my|a)\s+(guy|friend|brother|sister)",
    r"(credit|fund).{0,20}(account|wallet)",
]

BILL_TRIGGERS = [
    r"(pay|settle).{0,20}(bill|light|electricity|water|dstv|gotv|startime)",
    r"san owo",
    r"recharge.{0,20}(prepaid|meter|nepa|phcn)",
    r"buy.{0,20}(unit|token).{0,20}(light|electricity|nepa|phcn)",
]


def _normalise(text: str) -> str:
    """Apply Pidgin + Yoruba normalisations and return lowercased text."""
    t = text.lower().strip()

    # handle "Xk" amount shorthand before general strip (e.g. "2k" → "2000")
    t = re.sub(r"(\d+)\s*k\b", lambda m: str(int(m.group(1)) * 1000), t)

    for pattern, replacement in YORUBA_MAP.items():
        t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)
    for pattern, replacement in PIDGIN_MAP.items():
        t = re.sub(pattern, replacement, t, flags=re.IGNORECASE)

    # collapse multiple spaces
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_amount(text: str) -> Optional[float]:
    """Extract the first numeric amount from text (handles commas)."""
    m = re.search(r"[\d,]+(?:\.\d+)?", text)
    if m:
        try:
            return float(m.group().replace(",", ""))
        except ValueError:
            pass
    return None


def _extract_phone(text: str) -> Optional[str]:
    """Extract Nigerian phone number if present."""
    m = re.search(r"(?:0|\+?234)([789]\d{9})", text.replace(" ", ""))
    if m:
        digits = m.group(1)
        return f"0{digits}"
    return None


def _extract_network(text: str) -> Optional[str]:
    for alias, code in NETWORK_ALIASES.items():
        if re.search(rf"\b{alias}\b", text, re.IGNORECASE):
            return code
    return None


def _detect_language(raw: str) -> str:
    pidgin_words = {"abeg", "oya", "wetin", "dey", "e don", "na", "shey", "wahala"}
    yoruba_words = {"owo", "ina", "san", "gba", "emi", "jẹ", "kí"}
    words = set(raw.lower().split())
    if words & yoruba_words:
        return "yo"
    if words & pidgin_words:
        return "pcm"  # ISO 639-3 for Nigerian Pidgin
    return "en"


def _match_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ─── Public API ───────────────────────────────────────────────────────────────

def parse(text: str) -> ParseResult:
    """
    Parse a user message into a structured ParseResult.
    Mirrors the OWO result structure: intent + entities + flags.
    """
    result = ParseResult(raw=text, language=_detect_language(text))
    normalised = _normalise(text)
    flags: list[str] = []

    # ── GREETING ─────────────────────────────────────────────────────────────
    if any(t in normalised for t in GREETING_TRIGGERS):
        result.intent = Intent.GREETING
        result.confidence = 0.95
        return result

    # ── CHECK_BALANCE ─────────────────────────────────────────────────────────
    if _match_any(BALANCE_TRIGGERS, normalised):
        result.intent = Intent.CHECK_BALANCE
        result.confidence = 0.90
        return result

    # ── BUY_DATA (before airtime to avoid false match) ────────────────────────
    if _match_any(DATA_TRIGGERS, normalised):
        result.intent = Intent.BUY_DATA
        result.amount = _extract_amount(normalised)
        result.network = _extract_network(normalised) or _extract_network(text)
        result.phone = _extract_phone(text)
        if result.amount is None:
            flags.append("missing_amount")
        if result.network is None:
            flags.append("missing_network")
        result.confidence = 0.85 if not flags else 0.60
        result.flags = flags
        return result

    # ── BUY_AIRTIME ───────────────────────────────────────────────────────────
    if _match_any(AIRTIME_TRIGGERS, normalised):
        result.intent = Intent.BUY_AIRTIME
        result.amount = _extract_amount(normalised)
        result.network = _extract_network(normalised) or _extract_network(text)
        result.phone = _extract_phone(text)
        if result.amount is None:
            flags.append("missing_amount")
        result.confidence = 0.85 if not flags else 0.60
        result.flags = flags
        return result

    # ── SEND_MONEY ────────────────────────────────────────────────────────────
    if _match_any(SEND_TRIGGERS, normalised):
        result.intent = Intent.SEND_MONEY
        result.amount = _extract_amount(normalised)
        result.phone = _extract_phone(text)
        if result.amount is None:
            flags.append("missing_amount")
        if result.phone is None:
            flags.append("missing_recipient_phone")
        result.confidence = 0.80 if not flags else 0.55
        result.flags = flags
        return result

    # ── PAY_BILL ──────────────────────────────────────────────────────────────
    if _match_any(BILL_TRIGGERS, normalised):
        result.intent = Intent.PAY_BILL
        result.amount = _extract_amount(normalised)
        if result.amount is None:
            flags.append("missing_amount")
        result.confidence = 0.80 if not flags else 0.55
        result.flags = flags
        return result

    # ── UNKNOWN ───────────────────────────────────────────────────────────────
    flags.append("needs_llm_provider")
    result.flags = flags
    result.confidence = 0.0
    return result
