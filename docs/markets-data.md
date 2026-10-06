# US and Bursa Malaysia data layer

Phase 1 of [RnD-US-Malaysia.md](RnD-US-Malaysia.md). Fetches and stores US and Malaysian market data next to the A-share data. The A-share system is not touched; US/MY scanning, backtests and UI come in later phases.

## What it does

| Data | Market | Source (default) | Key needed |
|---|---|---|---|
| Daily bars (split- and dividend-adjusted) | US, MY | Yahoo (`yfinance`) | no |
| Daily bars incl. delisted tickers | US, MY | EODHD (switch in config) | `EODHD_API_KEY` |
| Symbol master | US | Nasdaq Trader directory files | no |
| Symbol master incl. delisted | US, MY | EODHD | `EODHD_API_KEY` |
| Fundamentals, point-in-time | US | SEC EDGAR company facts | `SEC_USER_AGENT` (your name + email) |
| Macro series | US | FRED (keyless CSV) | no |

Everything is configured in `config/markets.yaml` (copy from `config/markets.yaml.example`; the template is used when the copy is missing). Data goes to `<cache dir>/markets/us/` and `<cache dir>/markets/my/`; the cache dir is the same one the A-share data uses (`LWQUANT_CACHE_DIR`, else `data.cache_dir` in `config/params.yaml`, else `data/cache`).

## Setup (Windows)

```bat
pip install -r requirements.txt
copy config\markets.yaml.example config\markets.yaml
setx SEC_USER_AGENT "Your Name your@email.com"
```

Instead of `setx`, keys can go in `config/params.yaml` under `data.api_keys` (see `params.yaml.example`). Environment variables win.

## Commands

```bat
python data\market_update.py --market US                        :: seed list (config) + benchmark, bars only
python data\market_update.py --market US --fundamentals --macro :: + SEC facts + FRED series
python data\market_update.py --market MY                        :: Bursa seed list + FBM KLCI
python data\market_update.py --market US --symbols              :: refresh the US symbol master
python data\market_update.py --market US --universe all --limit 500
python data\market_update.py --status                           :: coverage of every market (JSON)
```

- First run downloads full history from `history_start` (2010); later runs are incremental.
- `--universe all` uses the symbol master after the name filters in config (~11,400 US stocks and ETFs). Without `--limit` that is a long first download; Yahoo may throttle.
- SEC facts are fetched at most once per `refresh_days` (7) per company; one company file is 1–4 MB.
- A summary of each run is written to `output/market_update_<market>.json`. The web server exposes all of it at `http://127.0.0.1:8787/api/markets`.

## Scheduling with Windows Task Scheduler (Malaysia time)

The US closes at 04:00 MYT (05:00 when the US is on winter time); Bursa closes at 17:00.

```bat
schtasks /Create /TN "DSHQuant-US-Update" /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 07:00 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\repo\data\market_update.py\" --market US --macro"
schtasks /Create /TN "DSHQuant-MY-Update" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\repo\data\market_update.py\" --market MY"
schtasks /Create /TN "DSHQuant-US-Fundamentals" /SC WEEKLY /D SUN /ST 09:00 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\repo\data\market_update.py\" --market US --bars --fundamentals"
```

Running on a holiday is harmless: nothing new is stored.

## Data conventions

- **Symbols:** `<code>.<market>`, e.g. `AAPL.US`, `BRK.B.US`, `0166.MY` (leading zeros kept), benchmarks `SPY.US`, `KLCI.MY`.
- **Bars** use the existing `DailyCache` schema in `bars.db`:
  - `adjust='qfq'`: split- and dividend-adjusted (use for returns and backtests);
  - `adjust='split'`: split-adjusted only (use with dividends for withholding-tax modelling);
  - `amount` = close × volume in the market's currency; `turn` is empty until shares outstanding are joined (Phase 2).
- **Only closed sessions are stored** (exchange calendars XNYS / XKLS), so an intraday bar never lands in the database.
- **Re-adjustment:** each incremental run re-downloads a 10-day overlap. If stored adjusted closes no longer match (new split or dividend), that symbol's full history is refetched.
- **Point-in-time fundamentals:** `data.market_store.get_fact_asof(market, code, concept, asof, duration)` returns the latest fact filed strictly before `asof`. `duration` is `"instant"`, `"quarter"` or `"annual"`.

## Switching to EODHD

1. Set `EODHD_API_KEY` (env var or `params.yaml`).
2. In `config/markets.yaml` set `providers.bars: eodhd` and/or `providers.symbols: eodhd` for the market.
3. Delete that market's `bars.db` once, so history is rebuilt from one source.

The adapter converts EODHD's raw prices to the same split-adjusted basis Yahoo uses; prices for AAPL around its 2020 split match Yahoo exactly.

## Known limits

- **Yahoo has no delisted tickers** (survivorship bias). Fine for exploring, not for final backtests; that is what EODHD is for.
- **FRED keyless CSV is the latest vintage.** `CPIAUCSL` and `UNRATE` get revised, so their history has look-ahead; market series (yields, VIX, spreads) do not.
- **Malaysia has no fundamentals or macro source yet**; Bank Negara and OpenDOSM adapters are planned.
- SEC company facts cover US filers only; ETFs and most foreign ADRs return no facts.

## Tests

```bat
python -m pytest tests
```

Offline unit tests (no network) for symbol mapping, provider parsers, point-in-time lookup and the re-adjustment logic.
