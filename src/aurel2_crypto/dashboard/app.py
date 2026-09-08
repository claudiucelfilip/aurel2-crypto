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
CHART_DATA_FILE = DATA_DIR / "chart_data.json"
EQUITY_CURVE_FILE = DATA_DIR / "equity_curve.json"
RUN_STATE_FILE = DATA_DIR / "run_state.json"

PERIOD_DAYS = {
    "1d": 1,
    "1w": 7,
    "1m": 30,
    "6m": 180,
    "1y": 365,
    "5y": 1825,
    "all": None,
}
DEFAULT_PERIOD = "all"


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


def get_run_state() -> dict | None:
    if not RUN_STATE_FILE.exists():
        return None
    try:
        return json.loads(RUN_STATE_FILE.read_text())
    except Exception:
        return None


def get_equity_curve() -> list[dict]:
    if not EQUITY_CURVE_FILE.exists():
        return []
    try:
        return json.loads(EQUITY_CURVE_FILE.read_text())
    except Exception:
        return []


_btc_cache: dict = {"ts": 0.0, "since": 0.0, "candles": []}
BTC_CACHE_TTL_SEC = 900


def get_btc_hourly(since_ts: float) -> list[tuple[float, float]]:
    """(timestamp_sec, close) hourly BTC/USDT candles from `since_ts`, cached 15 min."""
    now = time.time()
    c = _btc_cache
    if c["candles"] and c["since"] <= since_ts and now - c["ts"] < BTC_CACHE_TTL_SEC:
        return c["candles"]
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True})
        cursor = int(since_ts * 1000)
        rows: list[tuple[float, float]] = []
        while cursor < now * 1000:
            batch = ex.fetch_ohlcv("BTC/USDT", "1h", since=cursor, limit=1000)
            if not batch:
                break
            rows.extend((b[0] / 1000, float(b[4])) for b in batch)
            cursor = batch[-1][0] + 3600_000
        _btc_cache.update(ts=now, since=since_ts, candles=rows)
        return rows
    except Exception:
        return c["candles"]  # stale-or-empty beats a broken dashboard


def btc_benchmark_for(points: list[dict], starting_equity: float, started_ts: float) -> list[float | None]:
    """BTC buy-and-hold equity (same starting cash, bought at run start) aligned to each snapshot."""
    candles = get_btc_hourly(started_ts - 3600)
    if not candles or starting_equity <= 0:
        return []
    from bisect import bisect_right
    times = [t for t, _ in candles]

    def price_at(ts: float) -> float | None:
        i = bisect_right(times, ts) - 1
        return candles[i][1] if i >= 0 else None

    p0 = price_at(started_ts)
    if not p0:
        return []
    out = []
    for p in points:
        px = price_at(float(p.get("timestamp") or 0))
        out.append(round(starting_equity * px / p0, 2) if px else None)
    return out


def get_live_progress(period: str = DEFAULT_PERIOD) -> dict | None:
    """Compile live testnet/demo run progress from equity curve + run state.

    `period` filters the displayed window: 1d/1w/1m/6m/1y/5y/all.
    Run-level stats (starting equity, days running) always reflect the full run;
    window stats (peak, drawdown, current) reflect the chosen period.
    """
    run_state = get_run_state()
    curve = get_equity_curve()
    if not run_state or not curve:
        return None

    if period not in PERIOD_DAYS:
        period = DEFAULT_PERIOD

    # Filter curve by period
    days = PERIOD_DAYS[period]
    if days is not None:
        cutoff = time.time() - days * 86400
        windowed = [p for p in curve if float(p.get("timestamp") or 0) >= cutoff]
        # Always keep at least the most recent point so the chart isn't empty
        if not windowed:
            windowed = curve[-1:]
    else:
        windowed = curve

    starting_equity = float(run_state.get("starting_equity") or 0)
    current_equity = float(curve[-1].get("equity") or 0)
    pnl = current_equity - starting_equity
    pnl_pct = (pnl / starting_equity * 100) if starting_equity > 0 else 0.0

    started_ts = float(run_state.get("started_timestamp") or 0)
    days_running = (time.time() - started_ts) / 86400 if started_ts else 0.0

    # Window-scoped peak / drawdown
    window_peak = max((float(p.get("equity") or 0) for p in windowed), default=0.0)
    drawdown_pct = ((window_peak - current_equity) / window_peak * 100) if window_peak > 0 else 0.0

    # Window P/L (vs first point in window)
    window_start_equity = float(windowed[0].get("equity") or 0) if windowed else starting_equity
    window_pnl = current_equity - window_start_equity
    window_pnl_pct = (window_pnl / window_start_equity * 100) if window_start_equity > 0 else 0.0

    # Period exceeds the actual run length → we're showing all available data
    period_exceeds_run = days is not None and days > days_running

    # BTC buy-and-hold with the same starting cash, for the chart + alpha stat
    btc_curve = btc_benchmark_for(windowed, starting_equity, started_ts) if started_ts else []
    btc_final = next((v for v in reversed(btc_curve) if v is not None), None)
    alpha = (current_equity - btc_final) if btc_final is not None else None

    # Build the list of period options worth offering. Hide periods that would
    # show the exact same data as the next-shorter one (e.g. don't offer 1Y
    # when the run is only 6 days old — it's identical to ALL).
    available_periods: list[str] = []
    for key, key_days in PERIOD_DAYS.items():
        if key_days is None:
            available_periods.append(key)  # always offer ALL
            continue
        # Offer this period if its window is shorter than the run, OR if it's
        # the first period that fully contains the run (so the user has a
        # natural "1W"-style label even on a young run).
        if key_days < days_running:
            available_periods.append(key)
        elif not any(
            PERIOD_DAYS[p] is not None and PERIOD_DAYS[p] >= days_running
            for p in available_periods
        ):
            available_periods.append(key)

    return {
        "started_at": run_state.get("started_at"),
        "starting_equity": starting_equity,
        "current_equity": current_equity,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "window_pnl": window_pnl,
        "window_pnl_pct": window_pnl_pct,
        "window_start_equity": window_start_equity,
        "days_running": days_running,
        "peak_equity": window_peak,
        "drawdown_pct": drawdown_pct,
        "snapshot_count": len(windowed),
        "total_snapshots": len(curve),
        "testnet": run_state.get("testnet", True),
        "period": period,
        "period_exceeds_run": period_exceeds_run,
        "available_periods": available_periods,
        "dates": [p.get("iso", "") for p in windowed],
        "equity": [p.get("equity", 0) for p in windowed],
        "btc_benchmark": btc_curve,
        "btc_final": btc_final,
        "alpha": alpha,
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, period: str = DEFAULT_PERIOD):
    if period not in PERIOD_DAYS:
        period = DEFAULT_PERIOD

    heartbeat = get_heartbeat()
    trades = get_trade_journal()

    carry_active = []
    momentum_holding = None
    if heartbeat:
        for asset, active in heartbeat.get("carry_positions", {}).items():
            if active:
                carry_active.append(asset.upper())
        momentum_holding = heartbeat.get("momentum_holding")

    chart_data = None
    if CHART_DATA_FILE.exists():
        try:
            chart_data = json.loads(CHART_DATA_FILE.read_text())
        except Exception:
            pass

    live_progress = get_live_progress(period)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "heartbeat": heartbeat,
            "trades": trades,
            "carry_active": carry_active,
            "momentum_holding": momentum_holding,
            "chart_data": chart_data,
            "live_progress": live_progress,
            "period": period,
        },
    )


@app.get("/api/status")
async def api_status():
    return get_heartbeat() or {"status": "offline"}


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    return get_trade_journal(limit)


@app.get("/api/chart")
async def api_chart():
    if not CHART_DATA_FILE.exists():
        return {"error": "No chart data. Run generate_chart_data.py first."}
    try:
        return json.loads(CHART_DATA_FILE.read_text())
    except Exception:
        return {"error": "Failed to load chart data"}


@app.get("/api/live-progress")
async def api_live_progress(period: str = DEFAULT_PERIOD):
    progress = get_live_progress(period)
    return progress or {"error": "No live equity data yet — daemon hasn't recorded a snapshot."}
