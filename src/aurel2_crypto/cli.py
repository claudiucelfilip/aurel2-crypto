"""CLI entry point for Aurel2-Crypto."""

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import structlog
import typer

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
)

app = typer.Typer(name="aurel2-crypto", help="Systematic crypto trading system")


@app.command()
def backtest(
    start: str = typer.Option("2021-01-01", help="Start date (YYYY-MM-DD)"),
    end: str = typer.Option(None, help="End date (YYYY-MM-DD), defaults to today"),
    capital: float = typer.Option(10000.0, help="Initial capital in USDT"),
    strategy: str = typer.Option("all", help="Strategy: momentum, carry, all"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a historical backtest."""
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end) if end else date.today()

    typer.echo(f"Backtest: {start_date} to {end_date}, capital=${capital:,.0f}")
    typer.echo(f"Strategy: {strategy}")

    from aurel2_crypto.core.assets import CARRY_ASSETS, ASSET_REGISTRY, get_all_symbols
    from aurel2_crypto.data.providers.binance import BinanceDataProvider
    from aurel2_crypto.engine.backtest_combined import run_combined_backtest

    provider = BinanceDataProvider()
    prices = provider.get_multi_ohlcv(
        symbols=get_all_symbols(), timeframe="1d",
        start_date=datetime.combine(start_date, datetime.min.time()),
        end_date=datetime.combine(end_date, datetime.min.time()),
    )
    perp_symbols = [ASSET_REGISTRY[a].perp_symbol for a in CARRY_ASSETS]
    funding_rates = provider.get_multi_funding_rates(
        symbols=perp_symbols,
        start_date=datetime.combine(start_date, datetime.min.time()),
        end_date=datetime.combine(end_date, datetime.min.time()),
    )

    result = run_combined_backtest(
        prices, funding_rates, start_date, end_date, capital,
        carry_pct=0.30, momentum_pct=0.70,
    )
    result.print_summary()


@app.command()
def live(
    paper: bool = typer.Option(True, "--paper/--live", help="Paper or live trading"),
):
    """Run live trading daemon (24/7)."""
    from aurel2_crypto.config.settings import Settings
    from aurel2_crypto.live.daemon import CryptoDaemon

    settings = Settings()
    settings.binance_testnet = paper
    daemon = CryptoDaemon(settings)
    asyncio.run(daemon.start())


@app.command()
def dashboard(
    host: str = typer.Option("0.0.0.0", help="Dashboard host"),
    port: int = typer.Option(8081, help="Dashboard port"),
):
    """Run the web dashboard."""
    import uvicorn
    from aurel2_crypto.dashboard.app import app as dashboard_app

    typer.echo(f"Starting dashboard on {host}:{port}")
    uvicorn.run(dashboard_app, host=host, port=port)


@app.command()
def status():
    """Show current daemon status."""
    heartbeat_file = Path.home() / ".aurel2-crypto" / "heartbeat.json"
    if not heartbeat_file.exists():
        typer.echo("Daemon not running (no heartbeat file)")
        return

    import time
    data = json.loads(heartbeat_file.read_text())
    age = int(time.time() - data.get("timestamp", 0))
    status = "HEALTHY" if age < 300 else "STALE"

    typer.echo(f"Status: {status} (last seen {age}s ago)")
    typer.echo(f"Testnet: {data.get('testnet', True)}")
    typer.echo(f"Connected: {data.get('connected', False)}")
    typer.echo(f"Momentum holding: {data.get('momentum_holding', 'None')}")
    typer.echo(f"Carry positions: {data.get('carry_positions', {})}")
    typer.echo(f"Errors: {data.get('error_count', 0)}")


if __name__ == "__main__":
    app()
