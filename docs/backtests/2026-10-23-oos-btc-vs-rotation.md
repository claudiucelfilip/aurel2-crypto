# 2026-10-23 — BTC-only momentum vs 5-coin rotation, out of sample, plus the paper run

Reruns `scripts/backtest_oos.py` (committed 2026-09 without its results) and compares with the
testnet paper run that has been live since 2026-04-02.

## Paper run (Binance demo, $10k start, the live 5-coin rules)

| | 2026-04-02 → 2026-10-23 |
|---|---|
| Paper equity | $10,000 → $9,043 (−9.6%), max DD −23.4%, invested 125 of 199 days |
| BTC buy-and-hold (to 10-17) | +21.7%, max DD −28.7% |
| ETH buy-and-hold (to 10-17) | +16.7%, max DD −35.4% |
| BTC > 140-day average else cash | +7.1%, max DD −10.9% |

8 trailing stops fired in 6.5 months; 6 of them on LINK/AVAX/SOL. Two AVAX stops in October took
the account from its $11.3k peak to $9.0k.

## Backtest (daily engine, Monday rebalance, 0.1% fee, 6% idle yield)

Caveat on "out of sample": the universe {BTC, ETH, SOL, AVAX, LINK} was chosen in 2026 knowing
which alts survived. SOL/AVAX have no data before Aug/Sep 2020, LINK before 2019. The 2017–2020
rotation numbers are survivorship-biased, not a genuine holdout.

    btc: data from 2017-08-17
    eth: data from 2017-08-17
    sol: data from 2020-08-11
    avax: data from 2020-09-22
    link: data from 2019-01-16
    
    === OUT-OF-SAMPLE 2017-09 -> 2020-12 | BTC B&H 59,822  CAGR 71.1%  maxDD -83.2% ===
    variant                     lb      final    CAGR   maxDD  trd    vsBTC
    BTC-only momentum           14    112,098  106.5%  -37.0%   77   +87.4%
    BTC-only momentum           21    116,520  109.0%  -43.4%   71   +94.8%
    BTC-only momentum           28     96,749   97.6%  -72.5%   53   +61.7%
    BTC-only momentum           42    152,549  126.6%  -61.4%   37  +155.0%
    BTC-only momentum           56     73,141   81.7%  -60.3%   35   +22.3%
    rotation, no stop           14    380,198  198.0%  -58.6%  113  +535.5%
    rotation, no stop           21    330,494  185.7%  -57.1%  105  +452.5%
    rotation, no stop           28    217,509  152.0%  -80.2%   75  +263.6%
    rotation, no stop           42    303,094  178.4%  -75.5%   57  +406.7%
    rotation, no stop           56    292,493  175.4%  -68.5%   49  +388.9%
    rotation +10% stop (live)   14    390,957  200.5%  -62.3%  145  +553.5%
    rotation +10% stop (live)   21    213,537  150.6%  -64.2%  147  +257.0%
    rotation +10% stop (live)   28    224,657  154.5%  -78.4%  127  +275.5%
    rotation +10% stop (live)   42    345,032  189.4%  -67.5%  109  +476.8%
    rotation +10% stop (live)   56    191,294  142.5%  -70.1%  107  +219.8%
    
    === IN-SAMPLE 2021-01 -> 2026-09 | BTC B&H 29,391  CAGR 20.7%  maxDD -76.6% ===
    variant                     lb      final    CAGR   maxDD  trd    vsBTC
    BTC-only momentum           14     31,032   21.9%  -46.7%  145    +5.6%
    BTC-only momentum           21     27,713   19.5%  -55.7%  123    -5.7%
    BTC-only momentum           28     47,722   31.4%  -33.4%  107   +62.4%
    BTC-only momentum           42     44,294   29.7%  -45.8%   85   +50.7%
    BTC-only momentum           56     85,543   45.5%  -37.9%   65  +191.1%
    rotation, no stop           14    330,525   84.3%  -67.4%  259 +1024.6%
    rotation, no stop           21     55,553   34.9%  -86.8%  209   +89.0%
    rotation, no stop           28    170,327   64.1%  -77.3%  185  +479.5%
    rotation, no stop           42     23,541   16.1%  -88.1%  159   -19.9%
    rotation, no stop           56     63,973   38.3%  -83.7%  147  +117.7%
    rotation +10% stop (live)   14    244,829   74.9%  -61.0%  309  +733.0%
    rotation +10% stop (live)   21     49,328   32.2%  -77.4%  271   +67.8%
    rotation +10% stop (live)   28     85,214   45.4%  -65.6%  263  +189.9%
    rotation +10% stop (live)   42     15,212    7.6%  -84.9%  259   -48.2%
    rotation +10% stop (live)   56     32,993   23.2%  -77.8%  253   +12.3%
    
    === FULL 2017-09 -> 2026-09 | BTC B&H 178,304  CAGR 37.5%  maxDD -83.2% ===
    variant                     lb      final    CAGR   maxDD  trd    vsBTC
    BTC-only momentum           14    397,093   50.2%  -46.7%  221  +122.7%
    BTC-only momentum           21    368,618   48.9%  -55.7%  193  +106.7%
    BTC-only momentum           28    527,048   54.9%  -72.5%  159  +195.6%
    BTC-only momentum           42    771,326   61.6%  -61.4%  121  +332.6%
    BTC-only momentum           56    714,222   60.2%  -60.3%   99  +300.6%
    rotation, no stop           14 14,316,384  123.1%  -67.4%  373 +7929.2%
    rotation, no stop           21  2,095,830   80.4%  -86.8%  313 +1075.4%
    rotation, no stop           28  4,229,104   95.0%  -80.2%  259 +2271.8%
    rotation, no stop           42    814,505   62.6%  -88.1%  215  +356.8%
    rotation, no stop           56  1,922,292   78.7%  -83.7%  197  +978.1%
    rotation +10% stop (live)   14 10,904,634  116.5%  -62.3%  455 +6015.7%
    rotation +10% stop (live)   21  1,202,426   69.7%  -77.4%  417  +574.4%
    rotation +10% stop (live)   28  2,185,348   81.3%  -78.4%  389 +1125.6%
    rotation +10% stop (live)   42    599,142   57.1%  -84.9%  367  +236.0%
    rotation +10% stop (live)   56    648,368   58.5%  -77.8%  361  +263.6%
    
    === calendar years (28d lookback), return % ===
    year     BTC B&H   BTC-mom  rotation
    2018        -72%      -56%      -54%
    2019        +89%      +58%     +157%
    2020       +302%     +369%     +838%
    2021        +58%     +108%    +1044%
    2022        -65%      -15%      -64%
    2023       +154%      +77%     +511%
    2024       +112%      +61%       -4%
    2025         -7%       -5%      -16%
    2026         -3%       -1%      -18%

## Verdict

- The 5-coin rotation has lost money three calendar years running (2024 −4%, 2025 −16%,
  2026 −18%) while BTC did +112%, −7%, −3%. The paper run confirms it live. Its in-sample
  edge is lookback-fragile (28d +64% CAGR vs 42d +16%; with the stop, 45% vs 8%). Treat it as dead.
- BTC-only momentum beats BTC buy-and-hold in every window for 4 of 5 lookbacks and cuts the
  bear years (2018 −56% vs −72%, 2022 −15% vs −65%), at the cost of lagging in up years. This is
  the same finding as the aurel3 Track J BTC 140-day trend test (hold-out +39pp vs SPY, DD 41%).
- The 10% stop adds trades and never adds return on BTC (see `scripts/backtest_btc_only.py`).
- Max drawdowns are 33–88% in every variant. Whatever is run, size it as a satellite.

Recommendation: drop the alts and the stop; run BTC-only trend (28–56d momentum or 140d average,
daily close, exit to USDT) on paper, then decide on real money.

Journal bug noted: `daemon.py` records `stop_loss` twice per stop (intent before the sell, fill
after). One sell happens; the dashboard shows each stop twice.
