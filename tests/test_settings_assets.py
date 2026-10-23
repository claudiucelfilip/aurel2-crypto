"""Momentum universe comes from settings; BTC-only, no stop, 56d by default since 2026-10-23."""

from aurel2_crypto.config.settings import Settings
from aurel2_crypto.core.models import CryptoAsset
from aurel2_crypto.strategies.momentum import ShortTermMomentumStrategy


def test_defaults_are_btc_only_no_stop():
    s = Settings(_env_file=None)
    assert s.momentum_asset_list == [CryptoAsset.BTC]
    assert s.momentum_lookback_days == 56
    assert s.trailing_stop_pct == 0.0


def test_asset_list_parses_csv_and_drops_usdt():
    s = Settings(_env_file=None, AUREL2_CRYPTO_MOMENTUM_ASSETS="btc, ETH,usdt")
    assert s.momentum_asset_list == [CryptoAsset.BTC, CryptoAsset.ETH]


def test_strategy_takes_settings_universe():
    s = Settings(_env_file=None, AUREL2_CRYPTO_MOMENTUM_ASSETS="BTC")
    strat = ShortTermMomentumStrategy(lookback_days=s.momentum_lookback_days, assets=s.momentum_asset_list)
    assert strat.assets == [CryptoAsset.BTC]
