"""CLI entry point for Aurel2-Crypto."""

from datetime import date, datetime

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
    strategy: str = typer.Option("all", help="Strategy: momentum, carry, pairs, all"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Run a historical backtest."""
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end) if end else date.today()

    typer.echo(f"Backtest: {start_date} to {end_date}, capital=${capital:,.0f}")
    typer.echo(f"Strategy: {strategy}")
    typer.echo("(backtest engine not yet implemented)")


@app.command()
def live(
    paper: bool = typer.Option(True, "--paper/--live", help="Paper or live trading"),
):
    """Run live trading daemon (24/7)."""
    mode = "paper (testnet)" if paper else "LIVE"
    typer.echo(f"Starting live trading daemon in {mode} mode...")
    typer.echo("(live daemon not yet implemented)")


@app.command()
def monitor():
    """Run the health monitoring watchdog."""
    typer.echo("Starting monitor...")
    typer.echo("(monitor not yet implemented)")


@app.command()
def status():
    """Show current portfolio status."""
    typer.echo("(status not yet implemented)")


if __name__ == "__main__":
    app()
