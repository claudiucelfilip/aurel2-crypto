# Aurel2 Crypto

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-dashboard-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-deployable-2496ED?logo=docker&logoColor=white)](https://www.docker.com/)
[![Binance](https://img.shields.io/badge/Binance-compatible-F0B90B?logo=binance&logoColor=black)](https://www.binance.com/)
[![Mode](https://img.shields.io/badge/mode-testnet%20first-555555)](#safety-note)
[![Scope](https://img.shields.io/badge/scope-momentum%20%2B%20carry-6B7280)](#strategy-stack)

Aurel2 Crypto is a systematic crypto trading research platform focused on combining directional momentum with yield-aware cash management and funding-carry opportunities. It is the crypto sibling of Aurel2, designed around Binance-compatible market data/execution, testnet-first operation, and a dashboard that makes both live run behavior and backtest context visible.

This repository is a public portfolio snapshot. It demonstrates architecture, research process, and trading-system ergonomics. It is not financial advice and is not a recommendation to trade these strategies.

## Strategy Stack

- **Momentum rotation**: weekly rotation into the strongest asset from a curated liquid crypto universe, with absolute-momentum and trailing-stop exits.
- **Funding carry**: evaluates cash-and-carry opportunities using spot/perpetual funding rates where the venue supports it.
- **Idle USDT yield**: models or uses yield on idle USDT between trades, so "cash" is treated as an active portfolio state rather than dead capital.
- **Testnet/demo support**: live daemon paths are designed to run safely in paper/testnet mode before any real execution.

## Why This Exists

Crypto systems fail in different ways from equity systems: 24/7 markets, exchange-specific APIs, funding mechanics, and noisy short-term regimes. Aurel2 Crypto isolates those concerns into a smaller codebase while keeping the same discipline as Aurel2:

- explicit strategy code
- reproducible backtests
- persistent daemon state
- transparent dashboards
- credentials kept outside source control

## Repository Map

| Path | Purpose |
|---|---|
| `src/aurel2_crypto/strategies/` | Momentum, funding-carry, and pairs strategy logic. |
| `src/aurel2_crypto/broker/` | Broker abstractions plus Binance/ccxt adapter code. |
| `src/aurel2_crypto/live/` | 24/7 daemon loop and runtime state handling. |
| `src/aurel2_crypto/dashboard/` | FastAPI dashboard and templates. |
| `src/aurel2_crypto/engine/` | Backtest engines for strategy variants. |
| `scripts/` | Backtests, chart generation, and deployment helpers. |
| `data/` | Public research/backtest artifacts for review. Runtime exchange state is ignored. |
| `docker/` | Docker Compose setup with placeholder environment configuration. |

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,dashboard]"
```

Run backtests:

```bash
python scripts/backtest_momentum.py
python scripts/backtest_combined.py
python scripts/generate_chart_data.py
```

Run local testnet/paper components:

```bash
aurel2-crypto live --paper
aurel2-crypto dashboard --host 127.0.0.1 --port 8081
```

## Dashboard

The dashboard is meant to answer operational questions quickly:

- Is the daemon alive?
- What is the current holding?
- What is the current equity curve for this run?
- How does the live/testnet run compare with historical backtests?
- What trades or actions happened recently?

Endpoints include:

- `GET /api/status`
- `GET /api/trades?limit=N`
- `GET /api/chart`
- `GET /api/live-progress?period=1d|1w|1m|6m|1y|5y|all`

## Runtime State

The daemon writes runtime state under `~/.aurel2-crypto/`:

| File | Purpose |
|---|---|
| `heartbeat.json` | Connection state, holding, high-water mark, and current equity. |
| `equity_curve.json` | Time series of equity snapshots for the live chart. |
| `run_state.json` | Run anchor with `started_at` and `starting_equity`. |
| `trade_journal.json` | Append-only record of order actions and reasoning. |

These files are runtime artifacts and are not intended to be committed.

## Configuration And Secrets

Use `docker/.env.example` as a template. Real Binance keys, notification topics, and live-mode flags belong in local environment files or deployment secrets, never in git.

Start with testnet/paper mode. Treat live exchange execution as a separate operational decision that requires independent review.

## Safety Note

This project touches market data and exchange APIs. It is research software, not a turnkey trading product. Validate every assumption, run in testnet first, and do not trade live capital based only on this repository.
