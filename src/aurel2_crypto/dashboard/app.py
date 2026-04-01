"""FastAPI dashboard for Aurel2-Crypto."""

import json
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Aurel2-Crypto Dashboard")

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

DATA_DIR = Path.home() / ".aurel2-crypto"
HEARTBEAT_FILE = DATA_DIR / "heartbeat.json"
TRADE_JOURNAL_FILE = DATA_DIR / "trade_journal.json"


def get_heartbeat() -> dict | None:
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        data = json.loads(HEARTBEAT_FILE.read_text())
        data["seconds_ago"] = int(time.time() - data.get("timestamp", 0))
        data["status"] = "healthy" if data["seconds_ago"] < 300 else "stale"
        return data
    except Exception:
        return None


def get_trade_journal(limit: int = 50) -> list[dict]:
    if not TRADE_JOURNAL_FILE.exists():
        return []
    try:
        trades = json.loads(TRADE_JOURNAL_FILE.read_text())
        return list(reversed(trades[-limit:]))
    except Exception:
        return []


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    heartbeat = get_heartbeat()
    trades = get_trade_journal()

    carry_active = []
    momentum_holding = None
    if heartbeat:
        for asset, active in heartbeat.get("carry_positions", {}).items():
            if active:
                carry_active.append(asset.upper())
        momentum_holding = heartbeat.get("momentum_holding")

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "heartbeat": heartbeat,
            "trades": trades,
            "carry_active": carry_active,
            "momentum_holding": momentum_holding,
        },
    )


@app.get("/api/status")
async def api_status():
    return get_heartbeat() or {"status": "offline"}


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return get_trade_journal(limit)
