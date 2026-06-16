"""
OgaAI Action Backend
Integrates with:
  - cowriesys/airtime  — Airtime & data top-up API (all 4 Nigerian networks)
  - tomiiide/nigerian-banks — Nigerian bank codes dataset
"""

import hashlib
import hmac
import base64
import json
import os
import uuid
import httpx
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .intent_parser import ParseResult, Intent


# ─── Nigerian Banks (bundled from tomiiide/nigerian-banks) ──────────────────

_BANKS_PATH = Path(__file__).parent.parent / "data" / "nigerian_banks.json"
_banks_cache: Optional[list[dict]] = None


def get_banks() -> list[dict]:
    global _banks_cache
    if _banks_cache is None:
        with open(_BANKS_PATH) as f:
            _banks_cache = json.load(f)
    return _banks_cache


def find_bank(query: str) -> Optional[dict]:
    """Fuzzy-find a bank by name or code."""
    q = query.strip().lower()
    for bank in get_banks():
        if q == bank["code"] or q in bank["name"].lower():
            return bank
    return None


# ─── Cowrie airtime API client ───────────────────────────────────────────────
# Reference: cowriesys/airtime — HMAC-SHA256 authenticated REST API

class CowrieClient:
    BASE_URL = "https://api.cowriesys.com:8021"

    def __init__(self, client_id: str, client_key: str):
        self.client_id = client_id
        # The API requires the key to be base64-decoded before use
        self.client_key = base64.b64decode(client_key)

    def _sign(self, nonce: str, query_string: str) -> str:
        """HMAC-SHA256(nonce + query_string) → base64."""
        message = (nonce + query_string).encode()
        sig = hmac.new(self.client_key, message, hashlib.sha256).digest()
        return base64.b64encode(sig).decode()

    def _headers(self, nonce: str, query_string: str) -> dict:
        return {
            "ClientId":  self.client_id,
            "Nonce":     nonce,
            "Signature": self._sign(nonce, query_string),
        }

    async def check_balance(self) -> dict:
        nonce = str(uuid.uuid4())
        qs = f"clientId={self.client_id}"
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.BASE_URL}/Balance",
                params={"clientId": self.client_id},
                headers=self._headers(nonce, qs),
                timeout=15,
            )
            return r.json()

    async def buy_airtime(
        self, network: str, phone: str, amount: float, xref: str
    ) -> dict:
        nonce = str(uuid.uuid4())
        params = {
            "network": network,
            "msisdn":  phone,
            "amount":  int(amount),
            "xref":    xref,
        }
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.BASE_URL}/Credit",
                params=params,
                headers=self._headers(nonce, qs),
                timeout=15,
            )
            return r.json()

    async def buy_data(
        self, network: str, phone: str, amount: float, xref: str
    ) -> dict:
        nonce = str(uuid.uuid4())
        params = {
            "network": network,
            "msisdn":  phone,
            "amount":  int(amount),
            "xref":    xref,
        }
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{self.BASE_URL}/Data",
                params=params,
                headers=self._headers(nonce, qs),
                timeout=15,
            )
            return r.json()


def _get_cowrie() -> Optional[CowrieClient]:
    cid = os.getenv("COWRIE_CLIENT_ID")
    ckey = os.getenv("COWRIE_CLIENT_KEY")
    if cid and ckey:
        return CowrieClient(cid, ckey)
    return None


# ─── Action result ────────────────────────────────────────────────────────────

@dataclass
class ActionResult:
    success: bool
    message: str
    data: dict | None = None


# ─── Action handlers ──────────────────────────────────────────────────────────

async def handle_check_balance(parsed: ParseResult, user_id: str) -> ActionResult:
    cowrie = _get_cowrie()
    if cowrie:
        try:
            resp = await cowrie.check_balance()
            balance = resp.get("balance", "N/A")
            return ActionResult(
                success=True,
                message=f"Oga, your Cowrie wallet balance na ₦{balance:,.2f}. E don do!",
                data=resp,
            )
        except Exception as e:
            return ActionResult(success=False, message=f"Ehh, I no fit check balance now: {e}")

    # Demo mode when no API credentials configured
    return ActionResult(
        success=True,
        message="Abeg, your account balance na ₦12,500.00 — demo mode. Configure COWRIE_CLIENT_ID for live data.",
    )


async def handle_buy_airtime(parsed: ParseResult, user_id: str) -> ActionResult:
    if "missing_amount" in parsed.flags:
        return ActionResult(
            success=False,
            message="How much airtime you want buy? E.g. *buy me 500 naira airtime*",
        )

    network = parsed.network or "MTN"
    phone = parsed.phone or user_id
    amount = parsed.amount or 0
    xref = str(uuid.uuid4())[:16]

    cowrie = _get_cowrie()
    if cowrie:
        try:
            resp = await cowrie.buy_airtime(network, phone, amount, xref)
            if resp.get("status") == "00":
                return ActionResult(
                    success=True,
                    message=f"E don land! ₦{amount:,.0f} {network} airtime don reach {phone}. Enjoy!",
                    data=resp,
                )
            return ActionResult(
                success=False,
                message=f"The thing no work: {resp.get('message', 'Unknown error')}",
                data=resp,
            )
        except Exception as e:
            return ActionResult(success=False, message=f"Wahala dey: {e}")

    return ActionResult(
        success=True,
        message=f"[DEMO] ₦{amount:,.0f} {network} airtime ordered for {phone}. Set COWRIE_CLIENT_ID to go live.",
    )


async def handle_buy_data(parsed: ParseResult, user_id: str) -> ActionResult:
    if "missing_amount" in parsed.flags:
        return ActionResult(
            success=False,
            message="How much data you want? E.g. *get me 1000 naira MTN data*",
        )
    if "missing_network" in parsed.flags:
        return ActionResult(
            success=False,
            message="Which network you dey use? MTN, Airtel, Glo, or 9mobile?",
        )

    network = parsed.network or "MTN"
    phone = parsed.phone or user_id
    amount = parsed.amount or 0
    xref = str(uuid.uuid4())[:16]

    cowrie = _get_cowrie()
    if cowrie:
        try:
            resp = await cowrie.buy_data(network, phone, amount, xref)
            if resp.get("status") == "00":
                return ActionResult(
                    success=True,
                    message=f"Data don load! ₦{amount:,.0f} {network} data don reach {phone}.",
                    data=resp,
                )
            return ActionResult(
                success=False,
                message=f"The thing no work: {resp.get('message', 'Unknown error')}",
                data=resp,
            )
        except Exception as e:
            return ActionResult(success=False, message=f"Wahala dey: {e}")

    return ActionResult(
        success=True,
        message=f"[DEMO] ₦{amount:,.0f} {network} data ordered for {phone}. Set COWRIE_CLIENT_ID to go live.",
    )


async def handle_send_money(parsed: ParseResult, user_id: str) -> ActionResult:
    if "missing_amount" in parsed.flags:
        return ActionResult(
            success=False,
            message="How much you wan send? E.g. *send 2k to 08012345678*",
        )
    if "missing_recipient_phone" in parsed.flags:
        return ActionResult(
            success=False,
            message="Who you wan send am to? Abeg gimme their phone number or account number.",
        )

    amount = parsed.amount or 0
    recipient = parsed.phone or "unknown"
    return ActionResult(
        success=True,
        message=(
            f"To confirm: send ₦{amount:,.0f} to {recipient}?\n"
            "Reply *YES* to confirm or *NO* to cancel.\n"
            "_(Bank transfer coming fully live in V2)_"
        ),
    )


async def handle_pay_bill(parsed: ParseResult, user_id: str) -> ActionResult:
    return ActionResult(
        success=False,
        message=(
            "Bill payment dey come for V2, Oga!\n"
            "For now e-go cover:\n"
            "• Electricity (NEPA/PHCN prepaid)\n"
            "• DSTV / GOtv\n"
            "• Water bills\n\n"
            "Follow us make you know when e ready."
        ),
    )


# ─── Dispatcher ───────────────────────────────────────────────────────────────

async def dispatch(parsed: ParseResult, user_id: str) -> ActionResult:
    handlers = {
        Intent.CHECK_BALANCE: handle_check_balance,
        Intent.BUY_AIRTIME:   handle_buy_airtime,
        Intent.BUY_DATA:      handle_buy_data,
        Intent.SEND_MONEY:    handle_send_money,
        Intent.PAY_BILL:      handle_pay_bill,
    }
    handler = handlers.get(parsed.intent)
    if handler:
        return await handler(parsed, user_id)
    # GREETING and UNKNOWN are handled by the response engine
    return ActionResult(success=False, message="")
