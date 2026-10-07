# R&D: running DeepSeek HARNESS Quant on US and Bursa Malaysia stocks

Status: research only, no code changed. Researched 2026-10-06 for a Malaysian user on a Windows PC.
Items marked **UNVERIFIED** could not be confirmed from an official source and need a test before you rely on them.

---

## 0. Summary

| Question | Answer |
|---|---|
| Can the current system run on US / Malaysian stocks as-is? | No. Data feeds, stock codes, price-limit rules, calendar, costs and the opportunity scanner all assume China A-shares. |
| How much carries over? | The backtest core, risk agent, stop rules, factor-pool lifecycle, regime timing and most price/volume factors port with small changes. |
| What does not carry over? | Every China-only dataset (Dragon-Tiger list, social-security holdings, chip distribution, limit-up lists, auction data), the 2,492-line `scan.py`, the short-term "tech line", the China calendar/policy hooks, the six investor personas. |
| Do the A-share backtest results transfer? | **No.** `turn_low` and every factor must be re-validated per market with the existing 9-step process. |
| Recommended approach | Add a market layer (`config/markets.yaml`) and build a lean US/MY pipeline that reuses the market-agnostic modules, rather than generalising every A-share code path. |
| Broker | Moomoo MY OpenAPI (OpenD on Windows): live US + Malaysian trading; paper trading for US only. |
| LLM | Not DeepSeek-only. OpenAI, z.ai (GLM), Anthropic, Google, OpenRouter and more are built in; it's a settings change. |
| Which market first | **US.** Better data, survivorship-free sources, paper trading available. Malaysia second. |

---

## 1. How the markets differ

| | China A-shares (current) | US | Bursa Malaysia |
|---|---|---|---|
| Settlement | T+1, **cannot sell shares bought today** | T+1 since 28 May 2024; same-day selling allowed | T+2 (since 29 Apr 2019); same-day round trip only via broker-granted contra |
| Daily price limit | ±10% main board, ±20% ChiNext/STAR, ±30% BSE | **None**. LULD bands (±5% for S&P 500/R1000 >$3, ±10% others) cause 5-min pauses, not closes at the limit | **Static ±30%** of reference (±30 sen below RM1), +400% on listing day; dynamic ±8% from last trade intraday |
| Market circuit breaker | — | S&P 500 −7% / −13% (15-min halt), −20% (closed for day) | FBM KLCI −10% / −15% / −20% |
| Lot size | 100 | 1 share (fractional available) | **100** (odd lots trade on a separate market) |
| Tick size | 0.01 CNY | $0.01 | 0.5 sen < RM1; 1 sen RM1–9.99; 2 sen RM10–99.98; 10 sen ≥ RM100 |
| Hours (local) | 09:30–11:30, 13:00–15:00 | 09:30–16:00 ET | 09:00–12:30, 14:30–16:45 + pre-close 16:45–16:50 + trading-at-last 16:50–17:00 |
| Hours (MYT) | same as China (both UTC+8) | **21:30–04:00** (US summer time) / **22:30–05:00** (US winter time) | same as local |
| Auctions | 09:15–09:25 open, 14:57–15:00 close | Opening/closing cross; imbalance data is paid (Nasdaq TotalView, NYSE via Massive add-on $49/mo) | Pre-open 08:30 and 14:00; pre-close 16:45–16:50 |
| Short selling | margin list only | Allowed; Reg SHO Rule 201 restricts after a −10% day | RSS (borrow first) / IDSS (intraday), approved list only, broker-dependent |
| "ST" (distressed) flag | ST / *ST | No equivalent; exclude OTC, partnerships (PTPs), penny stocks | **PN17** (Main) / **GN3** (ACE); exclude **LEAP** board |
| Retail cost per side | ~0.026% + 0.05% + stamp | moomoo MY ≈ **0.16%** on a $5k order (incl. Malaysian stamp duty 0.1%); IBKR ≈ $1/order | brokerage 0.03% + RM3 (moomoo) + clearing 0.03% (cap RM1,000) + stamp duty RM1 per RM1,000 rounded up (cap RM1,000); **ETFs: no stamp duty until end-2028** |
| Tax for a Malaysian resident | — | **30% dividend withholding** (no US–Malaysia treaty); no US tax on capital gains; **US estate tax above US$60k** of US-situs assets; W-8BEN still needed | Foreign-sourced income exempt for individuals 2022–2036 if taxed at source; 2% dividend tax applies to Malaysian-company dividends only |
| Day-trading rule | n/a | FINRA PDT ($25k) rule replaced, effective 4 Jun 2026 (brokers may phase in until 20 Oct 2027) | contra at broker's discretion |
| Benchmark | CSI 300 (`000300.SH`) | S&P 500 (`^GSPC` / SPY), Nasdaq-100 (QQQ), Russell 2000 (IWM) | FBM KLCI (`^KLSE`, Yahoo history from 1993) |
| ETFs | many | very many, liquid | 13 listed; **only 0828EA (TradePlus Shariah Gold, ~RM310k/day) has meaningful liquidity**. 0820EA no longer tracks the KLCI (renamed FTSE4Good, 1 Oct 2025) |
| Shariah screen | — | — | SC list, published **May and November**; methodology changed Nov 2025 (single 5% benchmark), older lists not comparable |
| Up/down colours | **red = up**, green = down | green = up | green = up |

Engine implications:
- Keep **next-day-open execution** for both markets (it's conservative and already built).
- US: remove the limit-up / one-price-bar (一字板) filter; add halt flags, minimum price and minimum dollar-volume screens.
- Malaysia: keep a limit filter but at ±30%; add **lot rounding (100)** and **tick rounding** (the 0.5 sen tick is 5–10% of a penny stock's price).
- Costs, lot size, limits, sessions and benchmark go into `config/markets.yaml`, not code (AGENTS.md rule).
- Annualisation: 252 days (US), ~245 (MY).
- Dividend strategies on US stocks: model the 30% withholding (a 3% yield loses ~0.9%/yr). Backtest with unadjusted prices + dividends × 0.7, not adjusted close.

---

## 2. What happens to each A-share feature

| Current feature | US | Malaysia | Action |
|---|---|---|---|
| `turn_low` core (low turnover) | turnover = volume / shares outstanding (needs **historical** shares outstanding) | same | Port, then re-validate. The A-share result (≈+16%/yr) does not transfer |
| Limit-up/down family, one-price-bar filter | not applicable | ±30% limit rarely hit | Drop for US; simple flag for MY |
| T+1 sell lock | none | none | Nothing to port; keep next-day-open fills |
| Dragon-Tiger list (龙虎榜) | Form 4 insider trades (Finnhub free / SEC), 13F (45-day lag), FINRA short interest (twice monthly) | substantial-shareholder announcements (Bursa site blocks scraping) | New US factors; skip for MY at first |
| Call-auction strength | opening imbalance data is paid | not available via known APIs | Drop |
| Social-security fund, margin, chip-distribution data | none | none | Drop |
| ST flag | exclude OTC/PTPs/penny stocks | PN17/GN3 list (current only, no history) | `UniverseFilter` per market |
| Calendar hooks (Spring Festival, Two Sessions…) | candidates: turn-of-month, FOMC weeks, earnings season, January | candidates: Chinese New Year, Hari Raya, Budget day, year-end window dressing | Treat as **hypotheses**; they must pass the 9-step test |
| Policy score (China EPU, social financing) | US EPU (FRED), rates | OPR, FX (BNM OpenAPI), CPI/GDP (OpenDOSM) | Same idea, new data |
| ETF mapping | rich universe | meaningless (illiquid) | Enable for US only |
| Shariah filter | n/a | new | Twice-yearly point-in-time membership table |
| Fundamentals timing (China disclosure deadlines) | EDGAR `filed` / FMP `acceptedDate` | quarterly-report announcement date (no provider documents PIT for Bursa) | Per-market lag rule |
| 牛散 personas | rewrite | rewrite | Persona prompts currently embed A-share codes and speculation styles |
| Red-up / green-down UI | green up | green up | UI colour from market config |

---

## 3. Data sources

### What your existing keys can do

| Provider | US | Malaysia | Notes |
|---|---|---|---|
| **FMP** | Good: adjusted/unadjusted EOD, statements with `filingDate`/`acceptedDate`, `/stable/delisted-companies`, historical S&P 500 constituents, insider trades; 13F on Ultimate | Exchange `KLS` exists, but **global coverage needs the Ultimate plan (US$99/mo)**; `.KL` fundamentals and delisted coverage **UNVERIFIED** | Starter ($19) is US-only, 5 yrs; Premium ($49) 30+ yrs; free = 250 calls/day. Personal-use licence |
| **Finnhub** (free) | quotes, basic financials, financials-as-reported with filing dates, insider transactions + sentiment, earnings calendar/surprises, SEC filings, news | symbol master list only (`/stock/symbol?exchange=KL`) | Candles, dividends, institutional ownership, index constituents are **paid**. 60 calls/min |
| **yfinance** | adjusted prices, fast | `.KL` prices in MYR, splits/bonus issues recorded, `^KLSE` from 1993 | Unofficial, personal use, rate-limited. **Delisted tickers disappear** (e.g. 5014.KL) → survivorship bias. Prototype only |
| **Moomoo OpenAPI** | quotes (US LV3 free "during promotion"), trading | trading supported; **API quotes for MY: docs contradict each other (UNVERIFIED)** | **History quota: 100 symbols / 7 days** below HKD 10k assets → not a data source |

### Recommended additions

| Job | Recommendation | Cost |
|---|---|---|
| EOD prices, **both markets, incl. delisted** | **EODHD** EOD plan: US + KLSE (`.KLSE`, 1,076 tickers), delisted flag | $19.99/mo (fundamentals $59.99, all-in-one $99.99); free tier 20 calls/day to test |
| US research-grade, survivorship-free | **Sharadar** (point-in-time fundamentals, delisted, S&P 500 membership) | $69/mo full history ($499/yr) |
| US alternative | Massive (Polygon.io, renamed Oct 2025): keeps delisted tickers | $29–$199/mo |
| US fundamentals, point-in-time | **SEC EDGAR** XBRL `companyfacts` (each fact has its filing date) | free (10 req/s, User-Agent required) |
| US short interest | **FINRA** short interest (twice monthly) + daily short-sale volume | free |
| US macro | **FRED / ALFRED** (vintage data = point-in-time) | free key |
| MY macro & FX | **BNM OpenAPI** (OPR, daily FX), **OpenDOSM** (`api.data.gov.my`) | free, no key |
| MY Shariah list / PN17-GN3 | SC Shariah list (May/Nov), Bursa PN17/GN3 page | free, manual download |
| Symbol masters | Nasdaq Trader `nasdaqlisted.txt` / `otherlisted.txt`; Finnhub `exchange=KL` | free |

Suggested stack:
- **Start, free:** yfinance for prototyping both markets. Finnhub free for US insider data and the MY symbol list. SEC EDGAR, FRED, BNM and OpenDOSM.
- **First paid upgrade:** EODHD EOD ($19.99) for survivorship-free prices in both markets. Test KLSE delisted depth on the free tier first.
- **If US becomes the main focus:** Sharadar ($69/mo) or FMP Premium.
- **Bursa official data** (historical packages, announcements feed) is licensed and paid; not needed to start.

### Calls to run with your own keys (to settle the UNVERIFIED items)

```text
FMP     /stable/profile?symbol=1155.KL
FMP     /stable/historical-price-eod/full?symbol=1155.KL
FMP     /stable/delisted-companies
Finnhub /stock/symbol?exchange=KL
Moomoo  OpenD → get_market_snapshot(['MY.1155'])  and  request_history_kline('MY.1155')
EODHD   /api/eod/1155.KLSE   and   /api/exchange-symbol-list/KLSE?delisted=1
```

---

## 4. Broker

| Broker | US via API | Bursa via API | Paper trading | Notes |
|---|---|---|---|---|
| **Moomoo MY** (SC licence eCMSL/A0397/2024) | **yes** | **yes** (code format `MY.1155`) | **US yes, MY no** | OpenD gateway runs on Windows; Python package `moomoo-api`; one-time API questionnaire; `unlock_trade` before live orders; `place_order` 15 per 30 s; order types: limit, market, stop, stop-limit, trailing |
| Webull MY OpenAPI (launched 7 Jul 2026) | yes | no | — | US stocks and ETFs only |
| Interactive Brokers | yes (`ib_async` + TWS/IB Gateway) | Bursa offered since Aug 2024, but **reportedly not to Malaysian residents (UNVERIFIED)** | yes | Malaysia is an accepted country; ~$1/order US; API market data ~$10/mo (waived at $30 commissions) |
| Tiger | — | — | — | Not available to Malaysian residents |
| Alpaca | — | — | — | Malaysia not named; UNVERIFIED |

Design rule to keep (it matches AGENTS.md): the broker adapter only converts **cards you approved on `/pitch`** into orders, after the RiskAgent check. No auto-trading path. Start with Moomoo **US paper**; for Malaysia (no paper) use the internal `paper_account` first, then live with one lot.

---

## 5. LLM: not DeepSeek-only

The bundled HARNESS runtime (`@deepseek-ai/dsh` 0.1.0-rc.6) contains two adapters:
- `dsh-llm-deepseek` → route `deepseek-official` (current default);
- `dsh-llm-pi-ai` → multi-provider, **mounted but dormant** until `settings.yaml` has an `llm-pi-ai:` section. Built-in catalog includes `openai`, `zai`, `anthropic`, `google`, `openrouter`, `groq`, `mistral`, `moonshotai`, `xai`, `deepseek` and others. Custom OpenAI-compatible gateways are configuration only.

`harness/home/settings.yaml` (not yet tested here):

```yaml
agent-default-model:
  provider: openai          # or: zai, deepseek-official
  model: gpt-5              # or: glm-5.1, deepseek-v4-flash
llm-pi-ai:
  providers:
    openai:
      apiKeyEnv: OPENAI_API_KEY
    zai:
      apiKeyEnv: ZAI_API_KEY
      baseURL: https://api.z.ai/api/paas/v4/   # pay-as-you-go
```

`harness/home/.credentials.yaml`:

```yaml
DEEPSEEK_API_KEY: sk-...
OPENAI_API_KEY: sk-...
ZAI_API_KEY: ...
```

Caveats:
- The built-in `zai` entry points at the **GLM Coding Plan** endpoint (`https://api.z.ai/api/coding/paas/v4`). A normal pay-as-you-go key uses `https://api.z.ai/api/paas/v4/` (confirmed in z.ai docs), hence the override above.
- `接入API.cmd` / `setup_api.cmd` only accept keys starting with `sk-` and only write `DEEPSEEK_API_KEY`. Edit the credentials file by hand for other providers.
- The live persona bridge (`harness/home/profiles/web/plugins/dsq-quant-bridge.js`) uses the default model, so switching works. The unused copy at `harness/plugins/dsq-quant-bridge/index.js` hard-codes `deepseek-chat`.

---

## 6. Architecture plan

### Recommended shape

Keep the A-share path untouched. Add:

1. **`config/markets.yaml` (+ `.example`)**, one block per market:
   `id`, `suffix`, `currency`, `timezone`, `sessions`, `calendar` (exchange_calendars `XNYS` / `XKLS`, check), `lot_size`, `tick_table`, `price_limit`, `settlement_days`, `cost_model`, `benchmark`, `universe_filter` (min price, min dollar volume, exclude OTC/LEAP/PN17), `annualization_days`, `up_color`.
2. **Provider adapters** (`data/providers/`): one interface (bars, corporate actions, fundamentals with filing dates, universe incl. delisted, calendar, realtime quote) with implementations for EODHD, FMP, yfinance, Finnhub, SEC EDGAR, FRED, BNM. Selected per market in config.
3. **Canonical symbols** as strings: `AAPL.US`, `1155.MY` (keep leading zeros like `0166`). Store a separate `market` column instead of guessing exchange from a numeric prefix (30+ sites do that today).
4. **Per-market databases** (`bars_us.db`, `bars_my.db`) with the same schema plus `market`; compute `turn` at ingest as volume / shares outstanding; store canonical units (no 亿/万元/千元).
5. **`MarketRules` / `CostModel` / `UniverseFilter`** objects read from config; used by `bt_engine`, `bt_runner`, `paper_account`, risk.
6. **A lean scan** for US/MY: reuse `factor_engine`, factor-pool lifecycle and Pitch card format, but do not port `scan.py`'s China-specific branches (limit-up, Dragon-Tiger, auction, external factor CSVs).
7. **Broker adapter** (`broker/moomoo.py`): `submit(approved_cards)`, `positions()`, `cancel()`, paper/live switch in config, RiskAgent gate before submit.

### Reusable with little change
`factors/factor_engine.py`, `factors/classic_indicators.py`, `factors/pool/*` (benchmark only), `backtest/bt_report.py`, the period-backtest core in `backtest/bt_runner.py`, `risk/risk_agent.py`, `risk/stop_rules.py`, `risk/factor_attribution.py`, `risk/position_stop_check.py`, `strategy/timing.py`, `strategy/stock_state.py`, `strategy/style_timing.py`, `strategy/ranking.py`, `strategy/portfolio_builder.py`, `strategy/pool_manager.py`, `strategy/paper_tracker.py`, `factors/fscore.py` and `risk/beneish.py` formulas (field mapping), `ui_v2/core/api.js`.

### Hardest coupling points (from the code inventory)
1. Provider layer and the incremental/qfq price-adjustment pipeline (history is never re-adjusted, which breaks on US splits).
2. External factor-pool CSVs built outside this repo (~65 factors, many China-only).
3. Symbol identity: `[:6]` joins, prefix→exchange guessing, indices stored in `daily_bar` distinguished by `sh.` prefix.
4. Fundamentals timing: China disclosure deadlines, YTD-differencing, non-calendar fiscal years in US/MY.
5. `factors/opportunities/scan.py` (2,492 lines mixing limits, calendar, auction and external signals).
6. Calendar: no calendar table; "complete day" = ≥4,000 stocks; weekdays assumed; no timezone handling.
7. Benchmark `SH.000300` hard-coded in ~10 places; CNY units (亿/万元) in code, config and UI.
8. Cost model hard-coded (`0.00026+0.0005+0.001`) in `bt_engine.py:48`, `bt_runner.py:365`, `paper_account.py:27-29`; `backtest.commission_bps/stamp_tax/slippage` in `params.yaml` are never read.

---

## 7. Daily schedule on Windows (MYT)

| Market | Close (MYT) | Run EOD pipeline | Review on `/pitch` | Orders execute |
|---|---|---|---|---|
| US | 04:00 (US summer time, Mar–Nov) / 05:00 (US winter time) | ~06:30–07:30 MYT, after EOD data is published | during your day | at the US open, 21:30 / 22:30 MYT |
| Malaysia | 17:00 | ~18:00–18:30 MYT | evening | next day, pre-open 08:30, open 09:00 |

Windows Task Scheduler (`schtasks`) works for this; the repo ships the scripts but doesn't register the jobs.

---

## 8. Phased roadmap

| Phase | Work | Done when |
|---|---|---|
| 0 | Run the existing system in demo mode on Windows; switch LLM if wanted; run the test calls in §3; install OpenD and confirm Moomoo API quote rights | Demo dashboard works; you know your FMP plan and Moomoo MY quote access |
| 1 ✅ | `config/markets.yaml`, provider adapters (Yahoo, Nasdaq Trader, SEC EDGAR, FRED; EODHD ready but inactive), per-market DBs, trading calendars, `/api/markets` — see [markets-data.md](markets-data.md) | US and MY bars stored per market; SEC facts readable point-in-time |
| 2 ✅ | `MarketRules` / `CostModel` (`backtest/market_rules.py`), turnover from share counts, US/MY portfolio backtest and nine-step validation (`backtest/market_validation.py`) | Done 2026-10-06; results in §10. Nothing graded above "watch" while bars lack delisted stocks |
| 3 ✅ | US/MY decision cards (`strategy/market_pitch.py`), separate decision store and forward tracking (`strategy/market_track.py`), "US / MY" tab on `/pitch` | Done 2026-10-06; cards from factors not graded invalid; buy list = approved cards |
| 4a ✅ | US/MY holdings ledger with split-aware marks and exit alerts (`strategy/market_positions.py`); US/MY sections on portal, holdings, factors, backtest, data and report pages; USD/MYR, green-up | Done 2026-10-06 |
| 4b/4c ✅ | US/MY personas (config + snapshot + decision tracking), skills updated for US/MY; L0 and calendar tests, US value/quality factors from SEC frames, Malaysian macro (BNM, OpenDOSM), PN17/GN3 and Shariah list filters | Done 2026-10-07; personas call the LLM directly (dry-run tested, live test pending a key); results in §10 |
| 5 | Moomoo adapter: US paper → small live; MY live with one lot | Approved cards turn into orders only after RiskAgent check |

---

## 9. Watchlist to look at

US (beyond the index ETFs):

| Group | Tickers |
|---|---|
| Mega-cap tech | AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AVGO |
| Semiconductors | AMD, TSM (ADR), ASML (ADR), MU, QCOM, ARM |
| Software / internet | ORCL, CRM, ADBE, NOW, NFLX, PLTR, UBER, SHOP |
| Financials | JPM, BRK.B, V, MA, GS, BAC |
| Healthcare | LLY, UNH, JNJ, ABBV, ISRG, NVO (ADR) |
| Consumer | COST, WMT, KO, PEP, MCD, NKE, DIS |
| Energy / industrials | XOM, CVX, CAT, GE, BA |
| ETFs beyond SPY/QQQ | VTI, IWM, SMH, XLK, SCHD, GLD, TLT |

Two points to raise with a tax adviser: dividend ETFs (e.g. SCHD) lose 30% of dividends to withholding; US-domiciled holdings above US$60k carry US estate-tax exposure. Ireland-domiciled UCITS ETFs are the usual alternative (not verified here, and Moomoo MY may not offer them).

Malaysia (codes confirmed 2026-10-06):

| Company | Ticker | Company | Ticker |
|---|---|---|---|
| Maybank | 1155.KL | Public Bank | 1295.KL |
| CIMB | 1023.KL | Hong Leong Bank | 5819.KL |
| RHB | 1066.KL | Tenaga Nasional | 5347.KL |
| YTL Power | 6742.KL | Petronas Gas | 6033.KL |
| Petronas Chemicals | 5183.KL | Press Metal | 8869.KL |
| IHH Healthcare | 5225.KL | Gamuda | 5398.KL |
| Sunway | 5211.KL | Inari Amertron | 0166.KL |
| CelcomDigi | 6947.KL | Maxis | 6012.KL |
| Telekom Malaysia | 4863.KL | Axiata | 6888.KL |
| MR DIY | 5296.KL | Nestlé Malaysia | 4707.KL |
| Kuala Lumpur Kepong | 2445.KL | IOI Corp | 1961.KL |
| SD Guthrie | 5285.KL | Sime Darby | 4197.KL |
| Genting | 3182.KL | | |

---

## 10. Phase 2 results (2026-10-06, Yahoo data, after costs)

Setup: top 20 equal weight, rebalanced every 40 trading days; signal at close, trade at next open; moomoo MY / Bursa costs; US fractional shares, Bursa 100-share lots; liquidity floor US $5 price / $5M daily value, MY RM0.20 / RM200k. US 5,710 stocks; MY 1,069 counters. Holdout = 2023 onwards.

**US** (2012–2026; SPY +15.1%/yr, Sharpe 0.94, max drawdown −34%):

| Factor | Annual | Sharpe | vs SPY | IC (t) | Holdout | Grade |
|---|---|---|---|---|---|---|
| turn_20 low turnover (from 2016) | +11.1% | 0.65 | −4.1% | 0.020 (1.1) | +13.5% | watch |
| near_high_250 | +12.1% | 0.68 | −2.9% | 0.026 (1.3) | +23.2% | watch |
| mom_12_1 | +11.8% | 0.48 | −3.1% | 0.018 (1.0) | +22.0% | watch |
| max_ret_20 (avoid lottery stocks) | +7.9% | 0.62 | −7.1% | 0.034 (2.2) | +7.4% | watch |
| lowvol_60 | +5.8% | 0.51 | −9.2% | 0.030 (1.4) | +4.0% | watch (duplicate of max_ret_20) |
| mom_20 reversal | +7.6% | 0.38 | −7.3% | 0.014 (1.0) | +14.7% | watch |
| amihud_20 illiquidity | −6.1% | 0.03 | −21.1% | −0.027 | −15.7% | invalid |

**Malaysia** (2012–2026; FBM KLCI +0.4%/yr, Sharpe 0.09, max drawdown -36%; 29 exchange-holiday placeholder dates removed):

| Factor | Annual | Sharpe | vs KLCI | IC (t) | Holdout | Grade |
|---|---|---|---|---|---|---|
| near_high_250 | +25.6% | 1.41 | +25.1% | 0.149 (9.7) | +15.1% | watch |
| mom_12_1 | +11.3% | 0.53 | +10.7% | 0.078 (5.5) | +5.0% | watch |
| amihud_20 | +6.4% | 0.41 | +5.8% | -0.030 (-2.5) | +4.2% | invalid (IC wrong sign) |
| lowvol_60 | +3.7% | 0.59 | +3.2% | 0.105 (5.5) | +1.7% | watch |
| turn_20 low turnover (from mid-2023 only) | +2.1% | 0.30 | -3.2% | 0.091 (2.7) | +2.1% | watch |
| max_ret_20 | -0.5% | -0.01 | -1.0% | 0.069 (4.3) | -2.3% | invalid |
| mom_20 reversal | -9.8% | -0.27 | -10.3% | -0.042 (-3.1) | -7.8% | invalid |

What this says:

- **The A-share core does not carry over as-is.** In the US, low turnover behaves like a defensive tilt (ahead of SPY in 2016, 2018, 2022, behind in bull years) with an insignificant IC; no simple 20-stock factor portfolio beat SPY after costs. In Malaysia, turnover can only be tested from mid-2023, which is too short to judge.
- **Malaysia rewards trend-following, not reversal**: stocks near their 52-week high and 12-month winners lead by a wide margin; short-term reversal (strong in A-shares) loses. Low volatility is a steady, modest edge (IC t-stat 5.5).
- **Read every number as an upper bound.** Yahoo has no delisted stocks, which flatters all factors and especially trend factors. Bursa capacity is small (≈RM0.2–0.4M at 5% of traded value), fine for a retail account but the 0.2% slippage assumption may be light for small counters.
- **Next decisive step is survivorship-free data** (EODHD ~US$20/month covers both markets): rerun the same command and see which factors keep their edge.

### Phase 4c additions (2026-10-06)

**L0 regime overlay** (benchmark held at 1 − cash ratio, decided at the close, applied next session; 9-setting sensitivity grid):

| | Buy and hold | With L0 | Best of grid | Verdict |
|---|---|---|---|---|
| US (SPY) | +15.1%/yr, Sharpe 0.94, max DD −34% | +6.0%, 0.67, −15% | Sharpe 0.91 | hurts |
| Malaysia (KLCI) | +0.4%/yr, Sharpe 0.09, max DD −36% | −0.6%, −0.10, −17% | Sharpe 0.01 | hurts |

L0 halves drawdowns but costs more return than it saves in both markets, so `timing.gate_new_positions` is off for US/MY: the regime is shown on the cards as information, not a block.

**Calendar windows** (mean daily return inside vs outside the window):

- Malaysia **year end** (last 5 sessions of December): KLCI +25 bp/day vs −0.1 bp, t = 4.0, consistent across years and in the holdout → **valid** (the only one). Equal-weight t = 4.8, capped at watch.
- Malaysia after Chinese New Year: equal-weight t = 3.7 (watch); KLCI not significant.
- US pre-holiday: equal-weight t = 2.4, SPY t = 1.1 (watch). Turn of month, January and US year end: nothing.
- The equal-weight universe is picked on the previous 20 sessions' traded value (no same-day selection). Ten windows × two series were tested, so a single |t| near 2 can be luck; the Malaysian year-end result is strong enough to survive that.

**US value / quality factors** (SEC frames used 90 days after period end; market cap = traded value ÷ turnover, so US domestic single-ticker companies only, from 2016):

| Factor | Annual | Sharpe | vs SPY | IC (t) | Holdout | Grade |
|---|---|---|---|---|---|---|
| book_to_price | +20.5% | 0.74 | +5.3% | 0.004 (0.2) | +34.4% | watch |
| earnings_yield | +11.8% | 0.53 | −3.4% | 0.025 (1.4) | +23.3% | watch |
| roa (from 2012) | +10.2% | 0.56 | −4.8% | 0.020 (2.0) | +9.0% | watch |
| asset_growth (low; from 2012) | +9.9% | 0.46 | −5.1% | 0.005 (0.6) | +8.6% | watch |

Book-to-price beats SPY but its IC is near zero and its worst drawdown is −66%: a few deep-value names drive it, not a broad signal. ROA has the more reliable cross-sectional signal. None replaces the card factors yet.

---

## 11. Open items to verify

- Your FMP plan (Malaysia needs Ultimate).
- Moomoo MY API: Malaysian quotes and historical bars (docs contradict).
- EODHD: depth of KLSE delisted history.
- IBKR: whether Malaysian residents can trade Bursa stocks.
- Bursa tick sizes and price-limit details for ETFs/warrants (read from summaries of the 2025 Trading Manual).
- `exchange_calendars` coverage of `XKLS` holidays (Hari Raya dates move each year).

---

## Sources (main)

- US settlement T+1: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/new-t1-settlement-cycle-what-investors-need-know-investor-bulletin
- LULD: https://www.luldplan.com/ · circuit breakers: https://www.investor.gov/introduction-investing/investing-basics/glossary/stock-market-circuit-breakers
- PDT replacement: https://www.finra.org/rules-guidance/notices/26-10
- SEC fee 2026: https://www.finra.org/rules-guidance/notices/information-notice-20260317
- US treaties list: https://www.irs.gov/businesses/international-businesses/united-states-income-tax-treaties-a-to-z
- US estate tax for non-residents: https://www.irs.gov/individuals/international-taxpayers/some-nonresidents-with-us-assets-must-file-estate-tax-returns
- Bursa sessions: https://www.bursamalaysia.com/trade/trading_resources/equities/trading_sessions
- Bursa board lot / tick: https://www.bursamalaysia.com/trade/trading_resources/equities/board_lot
- Bursa costs: https://www.bursamalaysia.com/trade/trading_resources/equities/transaction_costs
- Stamp duty remission: https://www.ey.com/en_my/technical/tax-alerts/remission-of-stamp-duty-on-contract-notes-for-the-trading-of-listed-shares-or-stocks
- Bursa price limits reinstated: https://www.sc.com.my/resources/media/media-release/sc-and-bursa-malaysia-to-end-temporary-revision-of-market-management-and-control-mechanisms
- Shariah list: https://www.sc.com.my/development/icm/shariah-compliant-securities
- Moomoo OpenAPI: https://openapi.moomoo.com/moomoo-api-doc/en/intro/intro.html · quote rights: https://openapi.moomoo.com/moomoo-api-doc/en/intro/authority.html
- moomoo MY fees: https://www.moomoo.com/my/support/topic9_136
- FMP plans: https://site.financialmodelingprep.com/pricing-plans · Finnhub: https://finnhub.io/pricing
- EODHD: https://eodhd.com/pricing · KLSE: https://eodhd.com/exchange/KLSE
- Sharadar: https://sharadar.com/subscribe · Massive: https://massive.com/pricing
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- BNM OpenAPI: https://api.bnm.gov.my · OpenDOSM: https://developer.data.gov.my
- IBKR Bursa: https://www.interactivebrokers.com/en/trading/bursa-malaysia.php
- Webull MY OpenAPI: https://www.prnewswire.com/apac/news-releases/webull-malaysia-launches-openapi-allowing-investors-to-trade-us-stocks-and-etfs-via-plain-language-ai-302818258.html
- z.ai API base URL: https://docs.z.ai/guides/overview/quick-start
