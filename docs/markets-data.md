# US and Bursa Malaysia: data, validation, decision cards and holdings

Phases 1–4a of [RnD-US-Malaysia.md](RnD-US-Malaysia.md). Fetches and stores US and Malaysian market data next to the A-share data, runs the project's nine-step factor validation with each market's own trading rules and costs, and turns the surviving factors into decision cards on the `/pitch` page ("US / MY" tab). The A-share system is not touched.

## What it does

| Data | Market | Source (default) | Key needed |
|---|---|---|---|
| Daily bars (split- and dividend-adjusted) | US, MY | Yahoo (`yfinance`) | no |
| Daily bars incl. delisted tickers | US, MY | EODHD (switch in config) | `EODHD_API_KEY` |
| Splits and dividends | US, MY | Yahoo (with the bars) | no |
| Symbol master | US | Nasdaq Trader directory files | no |
| Symbol master | MY | Yahoo screener (exchange KLS, ~1,085 counters) | no |
| Symbol master incl. delisted | US, MY | EODHD | `EODHD_API_KEY` |
| Share counts → daily turnover | US | Yahoo history (~2015+), SEC frames for earlier years | `SEC_USER_AGENT` |
| Share counts → daily turnover | MY | Yahoo history (2023+) | no |
| Fundamentals, point-in-time | US | SEC EDGAR company facts | `SEC_USER_AGENT` (your name + email) |
| Equity, assets, net income for every filer | US | SEC frames (`--frames`, one request per period) | `SEC_USER_AGENT` |
| Macro series | US | FRED (keyless CSV) | no |
| OPR, USD/MYR, CPI | MY | Bank Negara OpenAPI, OpenDOSM | no |

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
python data\market_update.py --market US --symbols --universe all --stocks-only --shares
python data\market_update.py --market MY --symbols --universe all --shares
python data\market_update.py --market MY --full                  :: re-download full history
python data\market_update.py --status                           :: coverage of every market (JSON)
```

- First run downloads full history from `history_start` (2010); later runs are incremental.
- `--stocks-only` leaves ETFs out of `--universe all`. The full US stock universe (~5,700 names since 2010) takes roughly 9 GB of disk.
- `--shares` fetches share counts (Yahoo per symbol, refreshed every `refresh_days`; SEC one request per quarter) and then recomputes turnover. Every bar update also recomputes turnover from the stored counts.
- `--universe all` uses the symbol master after the name filters in config (~11,400 US stocks and ETFs). Without `--limit` that is a long first download; Yahoo may throttle.
- SEC facts are fetched at most once per `refresh_days` (7) per company; one company file is 1–4 MB.
- A summary of each run is written to `output/market_update_<market>.json`. The web server exposes all of it at `http://127.0.0.1:8787/api/markets`.

## Scheduling with Windows Task Scheduler (Malaysia time)

The US closes at 04:00 MYT (05:00 when the US is on winter time); Bursa closes at 17:00. `scripts\market_daily.cmd <market>` updates the data and then builds that day's decision cards.

```bat
schtasks /Create /TN "DSHQuant-US-Daily" /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 07:00 ^
  /TR "C:\path\to\repo\scripts\market_daily.cmd US"
schtasks /Create /TN "DSHQuant-MY-Daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 ^
  /TR "C:\path\to\repo\scripts\market_daily.cmd MY"
schtasks /Create /TN "DSHQuant-US-Fundamentals" /SC WEEKLY /D SUN /ST 09:00 ^
  /TR "\"C:\path\to\python.exe\" \"C:\path\to\repo\data\market_update.py\" --market US --universe all --stocks-only --shares --frames"
```

Running on a holiday is harmless: nothing new is stored. Re-run `backtest\market_validation.py` and `backtest\market_timing.py` monthly (or after changing factors) so the cards' evidence stays current.

## Data conventions

- **Symbols:** `<code>.<market>`, e.g. `AAPL.US`, `BRK.B.US`, `0166.MY` (leading zeros kept), benchmarks `SPY.US`, `KLCI.MY`.
- **Bars** use the existing `DailyCache` schema in `bars.db`:
  - `adjust='qfq'`: split- and dividend-adjusted (use for returns and backtests);
  - `adjust='split'`: split-adjusted only (use with dividends for withholding-tax modelling);
  - `amount` = close × volume in the market's currency;
  - `turn` = daily turnover % = volume / shares outstanding, both on today's split basis.
- **Only closed sessions are stored** (exchange calendars XNYS / XKLS), so an intraday bar never lands in the database.
- **Re-adjustment:** each incremental run re-downloads a 10-day overlap. If stored adjusted closes no longer match (new split or dividend), that symbol's full history is refetched.
- **Share counts** are usable from `available_from` = count date + `lag_days` (Yahoo 7, SEC 35 because frames have no filing date). Per stock, Yahoo counts are used; SEC counts extend them into earlier years only for single-ticker companies whose SEC and Yahoo counts agree within 0.8–1.25 where they overlap (this rejects ADRs and share-class mismatches). Dates before the first count reuse it, so turnover factors are only validated from `shares.reliable_from` (US 2016, MY mid-2023).
- **US turnover only for domestic single-ticker companies** (`shares.domestic_only`): a company counts as domestic when it appeared in one of SEC's quarterly net-income frames in the last two years (only 10-Q filers report quarterly; ADRs and other foreign issuers file 20-F). Tickers sharing a company (GOOG/GOOGL, BRK.A/BRK.B) get no turnover; preferred-share tickers outside the universe don't count. Market cap (for book-to-price, earnings yield) is traded value ÷ turnover, so it follows the same rule.
- **Partnerships are excluded** from the US universe by name (`L.P.`, `limited partnership`): non-US holders suffer 10% withholding on gross sale proceeds.
- **Malaysian list filters** (`universe.list_filters`): put Bursa's PN17/GN3 list in `config/lists/my_pn17_gn3.csv` to exclude distressed companies, or require `config/lists/my_shariah.csv` for a Shariah-only universe (templates in `config/lists/*.example`). A missing file means no filter. Today's list is applied to history too, so validation with a list filter has look-ahead.
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

## Factor validation (nine-step chain)

```bat
python backtest\market_validation.py --market US
python backtest\market_validation.py --market MY --factors turn_20 lowvol_60
```

| Step | What it does here |
|---|---|
| 1 Scope | every stock with stored bars, minus ETFs and the benchmark; per day: price ≥ `min_price`, 20-day median traded value ≥ `min_dollar_volume`, ≥ `min_history_days` of history |
| 2 Generate | factors from `factors/market_factors.py` (turn_20, mom_12_1, amihud_20, max_ret_20) and `factors/factor_engine.py` (lowvol_60, mom_20, near_high_250, …), signed per config |
| 3 IC screen | rank IC of the score vs the return from the next open to the open `rebalance_days` later, on each rebalance date |
| 4 Duplicate gate | factors whose scores correlate ≥ 0.7: the one with the lower \|ICIR\| is marked duplicate |
| 5 Portfolio (T+1) | top `top_n` equal weight, rebalanced every `rebalance_days`: signal at close, trade at next open, per-order costs, board lots, open-at-limit-up not buyable |
| 6 By year / holdout | calendar-year and excess returns; in-sample vs `holdout_start` onwards |
| 7 Orthogonality | highest correlation of daily excess returns with another factor |
| 8 Capacity | capital at which each position stays within `participation` of the 10th-percentile traded value of the picks |
| 9 Archive | `output/market_validation/<market>_<time>.json` and `latest_<market>.json`; served at `/api/markets/validation` |

Grades follow `harness/home/skills/backtest-acceptance`:

| Grade | Rule |
|---|---|
| valid | annual return > 0 after costs, Sharpe > 0.5, holdout annual return > 0, positive IC, not a duplicate |
| watch | positive but any valid condition fails, **or the bars have no delisted stocks** (Yahoo) |
| invalid | annual return ≤ 0 after costs, or IC ≤ 0 in the configured direction |

With Yahoo bars nothing can grade above "watch": survivorship bias inflates returns. Switching bars to EODHD lifts that cap.

Runtime on the full universes: US (~5,700 stocks) about 2 minutes and ~7 GB of RAM at peak; Malaysia (~1,070 counters) under 30 seconds. Results for 2026-10-06 are summarised in [RnD-US-Malaysia.md §10](RnD-US-Malaysia.md).

Rules and costs per market live in `config/markets.yaml` → `rules` (lot size, price limit) and `costs` (commission, platform fee, clearing, stamp duty with rounding and caps, sell-side fee, slippage), implemented in `backtest/market_rules.py`.

## Timing hypotheses (L0 regime, calendar)

```bat
python backtest\market_timing.py --market US
python backtest\market_timing.py --market MY
```

- **L0 overlay:** the benchmark held at 1 − cash ratio of the regime state machine (decided at the close, applied next session; cash earns nothing), vs buy-and-hold, with a sensitivity grid. Verdict helps / mixed / hurts. `timing.gate_new_positions` decides whether L0 blocks buys on the cards; it is off for US and MY because the overlay lowered Sharpe in both (see RnD §10).
- **Calendar windows:** turn of month, pre-holiday, January, year end and named holidays (`calendar_windows.named_holidays`, approximate dates snapped to the exchange closure). Mean daily return inside vs outside, Welch t, share of years agreeing, holdout gap. Graded on the benchmark (no survivorship) and an equal-weight universe (capped at "watch").
- Results: `output/market_validation/timing_<market>.json`, `/api/markets/timing`, shown under the validation table on the factors page.

## Decision cards ("US / MY" tab on /pitch)

```bat
python strategy\market_pitch.py --market US
python strategy\market_pitch.py --market MY
```

- **Score:** each tradable stock (validation filters) gets its percentile rank on every factor in `config/markets.yaml` → `pitch.factors`; the weighted mean (0–100) ranks the cards. Factors graded "invalid" in the latest validation are dropped and shown as dropped.
- **Card:** factor percentiles, last close, position = `capital / positions` rounded to board lots (US fractional) after costs, estimated cost, stop at `stop_loss_pct` below the close. Header: the market's L0 regime (`strategy/timing.py` on SPY / FBM KLCI) and data warnings.
- **Decisions:** Buy / Drop / Undo on the tab call `POST /api/markets/decide`; only codes on the latest cards are accepted. The **buy list is the approved cards** (same rule as A-shares: no second recommendation path). A buy while L0 says "no new positions" asks for confirmation.
- **Kept apart from A-shares:** US/MY decisions and tracking live in `output/market_desk/`, not in the A-share holdings (≤5 positions) or the A-share forward pools, which read A-share prices.
- **Forward tracking:** every card enters `auto_pitch`; approved buys move to `human_select`. Each run fills T+1/5/20/60 returns measured from the open after the signal.
- **There is no order routing yet**: place approved orders yourself at the next session open (moomoo), then press **Record fill** on the buy list. Broker automation is Phase 5.

APIs: `/api/markets/desk?market=US` (cards, decisions, buy list, tracking), `POST /api/markets/decide` `{market, code, action: buy|drop|undo}`.

## Holdings and exit checks

```bat
python strategy\market_positions.py --market US     :: holdings, P&L and exit alerts (also run by market_daily.cmd)
```

- **Ledger:** `output/market_desk/<market>_positions.json`, separate from the A-share portfolio. Record a fill from the buy list ("Record fill") or on the holdings page (code, shares, price, optional date and fees). Fees default to the broker/exchange schedule without the backtest's slippage. Close a position with its sell price; Delete only fixes a mistaken entry.
- **Marks:** latest unadjusted close. A split after entry restates entry price and shares on today's basis, so P&L stays right. Dividends are not booked yet.
- **Exit alerts** (alerts only, you decide): `risk/stop_rules.py` with `positions.stop_rules` from config: hard stop (`stop_loss_pct`, also the stop shown on cards), drawdown from the peak close, take-profit levels; plus the **rebalance exit** the validation tested: after `rebalance_days` sessions a stock that is no longer on the cards should be sold.

## Pages

| Page | US / MY section |
|---|---|
| `/` portal, `/report` | one row per market: data date, L0 regime, cards, buy list, holdings value and P&L, exit alerts, validation grades |
| `/pitch` | "US / MY" tab: cards, buy / drop / undo, buy list with Record fill, factor evidence, forward tracking |
| `/holdings` | market switch: open positions with P&L and exit alerts, record / close / delete, closed positions |
| `/factors` | "US / MY" tab: validation table (grade, return, Sharpe, drawdown, excess, IC, holdout, capacity) and excess return by year |
| `/backtest` | equity curves of the validated factor portfolios vs SPY / KLCI (log scale, after costs) |
| `/data` | coverage per market: bars, symbol master, share counts, turnover, SEC facts, macro, bars source |

APIs: `/api/markets/overview`, `/api/markets/positions?market=US` (GET), `POST /api/markets/positions` `{market, action: open|close|delete, …}`.

## AI personas (US / MY)

```bat
python strategy\market_personas.py --market US --consult            :: daily review by every persona (also in market_daily.cmd)
python strategy\market_personas.py --market MY --consult --persona risk_officer
```

- **Personas:** five investing styles in `config/market_personas.yaml` (template `.example`): Trend Rider, Quality Compounder, Defensive Income, Value Contrarian (US) and a Risk Officer that never buys. They are styles, not real people.
- **LLM:** the Python server calls an OpenAI-compatible `/chat/completions` endpoint directly (`llm` block: DeepSeek by default; OpenAI or z.ai GLM by changing `base_url` and `model`). The key is read from the environment, `config/params.yaml → data.api_keys` or `harness/home/.credentials.yaml` (where `接入API.cmd` writes it). Without a key the consult is skipped and nothing else is affected.
- **What a persona sees:** its system prompt (style, rules, the exact JSON format) plus a fresh snapshot: L0 regime and its tested verdict, data caveats, validated calendar effects, each card's factor percentiles (100 = best in the tested direction) with the other validated factors as unscored context, traded value, holdings with exit alerts, and the factor evidence. About 1,100 tokens per consult.
- **Decisions:** the last JSON object in the reply is parsed (code fences and bare tickers are accepted); only codes on the cards or in holdings are kept, and buys from personas that cannot buy are rejected. Buy calls enter the forward pool as `persona_select`, per persona, so each style builds a track record. They never become orders.
- **On the "US / MY" tab of /pitch:** today's calls per persona, the last reply, the track record, **Ask** (a follow-up keeps the last six turns) and **Consult all**.
- APIs: `GET /api/markets/personas?market=US`, `POST /api/markets/persona_chat {market, persona, text}` (empty text = daily review), `POST /api/markets/persona_record {market, persona, text}` (record a reply produced elsewhere).

Why not through HARNESS: personas need no tools or agent loops (the HARNESS bridge already disables tools for them), so one direct request is simpler and testable without Node.js. HARNESS stays for agentic work. The A-share control chat and 牛散 persona chat (control page) do go through HARNESS. In v1.0.9 they could not work: the page called `/quantapi/*` and `/niuapi/*`, which the mounted plugin (`harness/home/profiles/web/plugins/dsq-quant-bridge.js`) did not serve. The plugin now answers both its own `/quant/*` paths and the page's paths, and adds the page's delete / clear / restore / unlock actions, which only hide sessions in the control panel (`harness/home/quantapi_archived.json`) and never delete HARNESS sessions. Verified keyless: HARNESS pointed at a local streaming stand-in (`DEEPSEEK_BASE_URL`, placeholder key), driven through the real HARNESS app and control page.

## Tests

```bat
python -m pytest tests
```

Offline unit tests (no network) for symbol mapping, provider parsers, point-in-time lookup, re-adjustment, turnover, trading costs, lot rounding, price limits, next-open execution and grading.
