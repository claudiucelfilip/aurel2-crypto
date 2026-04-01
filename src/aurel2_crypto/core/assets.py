"""Crypto asset registry for Aurel2-Crypto."""

from aurel2_crypto.core.models import Asset, AssetCategory, CryptoAsset

ASSET_REGISTRY: dict[CryptoAsset, Asset] = {
    CryptoAsset.BTC: Asset(
        symbol="BTC/USDT",
        name="Bitcoin",
        asset_id=CryptoAsset.BTC,
        category=AssetCategory.CRYPTO,
        perp_symbol="BTC/USDT:USDT",
    ),
    CryptoAsset.ETH: Asset(
        symbol="ETH/USDT",
        name="Ethereum",
        asset_id=CryptoAsset.ETH,
        category=AssetCategory.CRYPTO,
        perp_symbol="ETH/USDT:USDT",
    ),
    CryptoAsset.SOL: Asset(
        symbol="SOL/USDT",
        name="Solana",
        asset_id=CryptoAsset.SOL,
        category=AssetCategory.CRYPTO,
        perp_symbol="SOL/USDT:USDT",
    ),
    CryptoAsset.AVAX: Asset(
        symbol="AVAX/USDT",
        name="Avalanche",
        asset_id=CryptoAsset.AVAX,
        category=AssetCategory.CRYPTO,
        perp_symbol="AVAX/USDT:USDT",
    ),
    CryptoAsset.LINK: Asset(
        symbol="LINK/USDT",
        name="Chainlink",
        asset_id=CryptoAsset.LINK,
        category=AssetCategory.CRYPTO,
        perp_symbol="LINK/USDT:USDT",
    ),
    CryptoAsset.USDT: Asset(
        symbol="USDT",
        name="Tether USD",
        asset_id=CryptoAsset.USDT,
        category=AssetCategory.STABLECOIN,
        perp_symbol=None,
    ),
}

# Momentum strategy trades these (excludes stablecoin)
MOMENTUM_ASSETS = [
    CryptoAsset.BTC,
    CryptoAsset.ETH,
    CryptoAsset.SOL,
    CryptoAsset.AVAX,
    CryptoAsset.LINK,
]

# Carry strategy uses these (most liquid perps)
CARRY_ASSETS = [
    CryptoAsset.BTC,
    CryptoAsset.ETH,
]

# Pairs trading uses these
PAIRS_ASSETS = (CryptoAsset.BTC, CryptoAsset.ETH)


def get_asset(asset_id: CryptoAsset) -> Asset:
    """Get an asset by its ID."""
    return ASSET_REGISTRY[asset_id]


def get_tradeable_assets() -> list[Asset]:
    """Get all tradeable crypto assets (excludes stablecoin)."""
    return [
        asset for asset in ASSET_REGISTRY.values()
        if asset.category == AssetCategory.CRYPTO
    ]


def get_all_symbols() -> list[str]:
    """Get all spot trading pair symbols."""
    return [
        asset.symbol
        for asset in ASSET_REGISTRY.values()
        if asset.category == AssetCategory.CRYPTO
    ]


def get_all_perp_symbols() -> list[str]:
    """Get all perpetual futures symbols."""
    return [
        asset.perp_symbol
        for asset in ASSET_REGISTRY.values()
        if asset.perp_symbol is not None
    ]
