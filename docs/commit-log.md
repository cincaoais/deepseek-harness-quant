# Commit log: branch `feature/us-malaysia-markets`

The US / Bursa Malaysia work, branched from `master` at `9f72ab7` (v1.0.9). Five commits were made on the Mac. **Phase 5 is in the working tree, uncommitted**, for the agent on the Windows PC to commit (steps below). Remote: `origin` = `git@github.com:cincaoais/deepseek-harness-quant.git`; nothing on this branch has been pushed.

| # | Commit | Phase | Files |
|---|---|---|---|
| 1 | `688c067` | Phase 1: data layer | 23 files, +1824 / −1 |
| 2 | `7cffea9` | Phases 2–3: validation and decision cards | 22 files, +1761 / −44 |
| 3 | `9d8fbcf` | Phase 4a: holdings and page sections | 20 files, +677 / −33 |
| 4 | `ef05955` | Phases 4b/4c: timing tests, value factors, AI personas | 34 files, +1435 / −87 |
| 5 | `dc2304b` | Fix: control-page HARNESS chat routes | 4 files, +98 / −54 |
| 6 | *to commit on Windows* | Phase 5: moomoo order desk | 21 files (list below) |

The five commits carry the Mac's git identity (`Johannes-Tang <johannes@snapdec.com>`). Step 3 below rewrites them to yours.

## Steps for the agent on the Windows PC

1. Unzip `deepseek-harness-quant.zip`. Then unzip `deepseek-harness-quant-data.zip` **into the repo folder** (the one containing `AGENTS.md`); it adds `data\cache\markets\`. Open a terminal in the repo folder.
2. Check the state:
   ```bat
   git status
   git log --oneline master..HEAD
   ```
   Expect the five commits above, and the Phase 5 files below as modified (M) or untracked (A).
   - If `git status` lists every file as modified, the cause is line-ending or file-mode settings, not real changes. Run `git config core.autocrlf false` and `git config core.filemode false`, then check again.
3. Set your identity for this repo. Optionally, rewrite the five commits to it while the tree is clean:
   ```bat
   git config user.name "Your Name"
   git config user.email "you@example.com"
   git stash -u
   git rebase master --exec "git commit --amend --no-edit --reset-author"
   git stash pop
   ```
   - `--reset-author` sets the author dates to now. To keep the original dates, use `--author="Your Name <you@example.com>"` instead of `--reset-author`; it's easiest in Git Bash.
   - The rewrite gives the commits new hashes.
4. Commit Phase 5 with the file list and message below.
5. Verify: `python -m pip install -r requirements.txt`, then `python -m pytest tests`. Expect **91 passed**.
6. Push when ready: `git push -u origin feature/us-malaysia-markets`. This needs your SSH key or HTTPS credentials on that PC.

## Phase 5 commit (pending)

Files:

```
M  .gitignore
M  README.md
M  backtest/market_rules.py
M  config/markets.yaml.example
M  deck/deck_server.py
M  docs/RnD-US-Malaysia.md
M  docs/markets-data.md
M  requirements.txt
M  scripts/market_daily.cmd
M  strategy/market_positions.py
M  ui_v2/components/market_desk.js
M  ui_v2/components/market_views.js
M  ui_v2/pages/pitch.html
A  broker/__init__.py
A  broker/moomoo_broker.py
A  config/broker.yaml.example
A  docs/commit-log.md
A  docs/windows-setup.md
A  strategy/market_orders.py
A  tests/test_market_orders.py
A  ui_v2/components/market_orders.js
```

Message:

```
Add moomoo order desk for US / Bursa Malaysia (phase 5)

- config/broker.yaml(.example): preview (default, never sends) / paper
  (US only; moomoo has no Bursa paper trading) / live, OpenD address,
  per-market caps, kill-switch file, RiskAgent limits
- broker/moomoo_broker.py: OpenD trade context (probed first, the SDK
  otherwise retries a dead port forever), account resolution, account,
  positions, place / query / cancel orders, fees
- strategy/market_orders.py: buy drafts only from approved cards not
  held or ordered, sell drafts only from exit alerts; limit price on the
  tick grid (rules.tick_sizes); risk check (kill switch, price age,
  orders per day, buy caps, buying power, sellable shares, RiskAgent);
  confirmed send; sync books fills in the holdings ledger once (partial
  sells close that part); reconcile ledger vs moomoo; kill switch
  cancels working orders; JSON-lines log
- Holdings: fills tagged manual / paper / live; partial close
- /api/markets/orders (GET reads files only; POST send needs
  confirm: true, cancel, sync, kill); Orders section on the US / MY tab
- market_daily.cmd syncs orders (skipped in preview)
- Local config copies git-ignored; docs: orders guide, Windows
  checklist, commit log; tests against a fake moomoo connection

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
```

Commands (cmd):

```bat
git add .gitignore README.md backtest/market_rules.py config/markets.yaml.example deck/deck_server.py docs/RnD-US-Malaysia.md docs/markets-data.md requirements.txt scripts/market_daily.cmd strategy/market_positions.py ui_v2/components/market_desk.js ui_v2/components/market_views.js ui_v2/pages/pitch.html broker/__init__.py broker/moomoo_broker.py config/broker.yaml.example docs/commit-log.md docs/windows-setup.md strategy/market_orders.py tests/test_market_orders.py ui_v2/components/market_orders.js
git status
git commit
```

Paste the message above in the editor `git commit` opens. Before committing, `git status` should show nothing else staged. In particular `config/broker.yaml`, `config/markets.yaml` and any `.credentials.yaml` must not be staged; they are git-ignored.

## What each commit contains

### 1. `688c067` Add US / Bursa Malaysia data layer (phase 1)

- `config/markets.yaml(.example)`: per-market providers, symbol formats, universe seeds and filters, SEC concepts, FRED series.
- `data/providers`: Yahoo, EODHD (inactive until a key is set), Nasdaq Trader, SEC EDGAR company facts, FRED.
- `data/market_update.py`:
  - symbol master;
  - incremental bars with an overlap re-adjustment check, closed sessions only;
  - SEC facts and macro series.
- `data/market_store.py`: per-market stores and point-in-time fact lookup.
- `/api/markets` coverage endpoint.
- Docs: R&D report, data-layer guide, workflow diagram. Offline tests.

Files: `README.md`, `config/markets.yaml.example`, `config/params.yaml.example`, `data/market_store.py`, `data/market_update.py`, `data/markets.py`, `data/providers/{__init__,base,eodhd,fred,nasdaqtrader,sec_edgar,yahoo}.py`, `deck/deck_server.py`, `docs/RnD-US-Malaysia.md`, `docs/markets-data.md`, `docs/workflow.{png,svg}`, `requirements.txt`, `tests/{conftest,test_market_store,test_markets,test_providers}.py`.

### 2. `7cffea9` Add US / Bursa Malaysia validation and decision cards (phases 2–3)

- **Rules and costs** in config (`backtest/market_rules.py`): lots, the Bursa ±30% limit, moomoo/Bursa fees with stamp-duty rounding and caps.
- **Data additions:**
  - splits and dividends;
  - Bursa symbol master from the Yahoo screener;
  - share counts (Yahoo, and SEC frames where they agree) and daily turnover. ADRs and multi-class tickers get no US turnover;
  - exchange sessions only, which drops Bursa holiday placeholder bars.
- **Validation:** portfolio backtest (next-open fills, lots, per-order costs, halts) and the nine-step validation with grading; `/api/markets/validation`.
- **Decision cards** from validated factors (`strategy/market_pitch.py`), with sizing, stop, evidence and the L0 regime.
- **Decisions and tracking:** separate decision store and forward tracking (`strategy/market_track.py`); buy list = approved cards; `/api/markets/desk`, `POST /api/markets/decide`.
- "US / MY" tab on `/pitch`; `scripts/market_daily.cmd` for Task Scheduler.

Files: `backtest/{market_bt,market_rules,market_validation}.py`, `config/markets.yaml.example`, `data/{market_store,market_update,markets}.py`, `data/providers/{eodhd,sec_edgar,yahoo}.py`, `deck/deck_server.py`, `docs/{RnD-US-Malaysia,markets-data}.md`, `factors/market_factors.py`, `scripts/market_daily.cmd`, `strategy/{market_pitch,market_track}.py`, `tests/{test_market_bt,test_market_desk,test_market_store}.py`, `ui_v2/components/market_desk.js`, `ui_v2/pages/pitch.html`.

### 3. `9d8fbcf` Add US / Bursa Malaysia holdings and page sections (phase 4a)

- **Holdings ledger**, kept apart from the A-share portfolio (`strategy/market_positions.py`):
  - record fills, close, delete;
  - marks at the latest close, restated for splits;
  - fees default to the schedule.
- **Exit alerts** from `risk/stop_rules.py` (hard stop, drawdown from peak, take-profit), plus the validated rebalance exit.
- **Pages:** monthly equity curves in the validation reports; US / MY sections on the portal, report, holdings, factors, backtest and data pages (`ui_v2/components/market_views.js`); Record fill on the buy list.
- **APIs:** `/api/markets/overview` and `/api/markets/positions` (GET, POST). `market_daily.cmd` prints holdings and exit alerts.

Files: `backtest/market_validation.py`, `config/markets.yaml.example`, `data/markets.py`, `deck/deck_server.py`, `docs/{RnD-US-Malaysia,markets-data}.md`, `scripts/market_daily.cmd`, `strategy/{market_overview,market_pitch,market_positions}.py`, `tests/test_market_positions.py`, `ui_v2/components/{market_desk,market_views}.js`, `ui_v2/pages/{backtest,data,factors,holdings,pitch,portal,report}.html`.

### 4. `ef05955` Add US / Bursa Malaysia timing tests, value factors and AI personas (phases 4b/4c)

- **Timing tests** (`backtest/market_timing.py`):
  - L0 regime overlay against buy-and-hold, with a sensitivity grid. L0 lowered Sharpe in both markets, so `timing.gate_new_positions` is off for US/MY;
  - calendar windows (turn of month, pre-holiday, January, year end, Chinese New Year, Hari Raya) on the benchmark and on an equal-weight universe filtered by lagged liquidity.
- **US value/quality factors** from SEC frames: `book_to_price`, `earnings_yield`, `roa`, `asset_growth` (90-day lag, single-ticker filers).
- **US universe filters:** turnover only for single-ticker 10-Q filers; partnerships excluded.
- **Malaysian data:** macro from Bank Negara (OPR, USD/MYR) and OpenDOSM (CPI); optional PN17/GN3 and Shariah list filters.
- **AI personas:**
  - personas in `config/market_personas.yaml(.example)`;
  - `strategy/llm_client.py` calls OpenAI-compatible chat completions, DeepSeek by default;
  - decisions are tracked per persona; daily consult; Ask / Consult all on the US / MY tab.
- Skills updated with US / Bursa Malaysia sections.

Files: `assets/skills/{alpha-gpt-factor-mining,backtest-acceptance,factor-mining-workflow}/SKILL.md`, `backtest/{market_bt,market_timing,market_validation}.py`, `config/lists/{my_pn17_gn3,my_shariah}.csv.example`, `config/market_personas.yaml.example`, `config/markets.yaml.example`, `data/{market_store,market_update,markets}.py`, `data/providers/{__init__,base,bnm,fred,opendosm}.py`, `deck/deck_server.py`, `docs/{RnD-US-Malaysia,markets-data}.md`, `factors/market_factors.py`, `harness/home/skills/backtest-acceptance/SKILL.md`, `scripts/market_daily.cmd`, `strategy/{llm_client,market_personas,market_pitch,market_track}.py`, `tests/{test_market_personas,test_market_store,test_market_timing,test_providers}.py`, `ui_v2/components/{market_desk,market_views}.js`.

### 5. `dc2304b` Fix control-page HARNESS chat routes (A-share chat and persona chat)

The control page calls `/quantapi/*` and `/niuapi/*`, which the mounted plugin did not serve, so the chat never connected.

- The plugin now registers each handler under both its own path and the page's path.
- Delete, clear, restore and unlock only hide sessions in the panel (`harness/home/quantapi_archived.json`, git-ignored).
- The control page clears the "thinking" marker once a reply arrives.

Verified keyless against a local streaming stand-in.

Files: `.gitignore`, `docs/markets-data.md`, `harness/home/profiles/web/plugins/dsq-quant-bridge.js`, `ui_v2/pages/control.html`.
