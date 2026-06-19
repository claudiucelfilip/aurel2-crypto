# WORKTREE_HANDOFF.md

owner: Lurch / Codex
created_utc: 2026-06-18T12:04:13Z
resolved_utc: 2026-06-19T20:10:23Z
repo: /Users/claudiu/vps-root/aurel2-crypto
service: Aurel2 Crypto
purpose: Historical handoff for dirty Aurel2 Crypto state found during the Gatekeeper dirty-runtime check.

## Current Classification

active_in_runtime: yes
money_or_trading_adjacent: yes

## Resolved Changes

- `README.md`: documents preferred `AUREL2_CRYPTO_*` environment variables and legacy aliases.
- `src/aurel2_crypto/config/settings.py`: adds `pydantic` alias handling for preferred and legacy env vars.
- `src/aurel2_crypto/broker/binance.py`: avoids unsupported Binance demo SAPI balance calls, values non-USDT spot holdings, reports free USDT as cash, and preserves futures margin/PnL accounting.
- `.gitignore`: ignores local `.claude/` assistant state.

## Tests / Evidence

- 2026-06-19 Claudiu asked to address dirty repos.
- 2026-06-19 `python3 -m py_compile src/aurel2_crypto/broker/binance.py src/aurel2_crypto/config/settings.py` passed on Dumbo.
- 2026-06-19 `git diff --check` passed on Dumbo.

## Required Handling

No active cleanup is required for this handoff after the source commit. Keep
local `.claude/` state ignored and do not delete it as part of repo cleanup.

## Next Check

due_utc: none
owner: Lurch / Gatekeeper
