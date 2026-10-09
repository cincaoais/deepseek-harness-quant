# Windows checklist: US / Bursa Malaysia, AI personas and moomoo orders

Everything on the branch `feature/us-malaysia-markets` was built and tested on a Mac **without** a DeepSeek key or a moomoo account: tests use a fake moomoo connection, and the personas and HARNESS chat were dry-run against local stand-ins. These steps are what's left for you on the Windows PC, in order. Details per feature: [markets-data.md](markets-data.md). Background: [RnD-US-Malaysia.md](RnD-US-Malaysia.md).

## 1. Get the repo onto the PC

- [ ] Unzip `deepseek-harness-quant.zip`. It holds the repo with its git history, on branch `feature/us-malaysia-markets`, with Phase 5 still uncommitted.
- [ ] Unzip `deepseek-harness-quant-data.zip` **into the repo folder** (the one containing `AGENTS.md`). It adds `data\cache\markets\`, the US and MY stores (about 7 GB unpacked). Without it, download the data again (step 4).
- [ ] Commit Phase 5, and optionally rewrite the earlier commits to your git identity: follow [commit-log.md](commit-log.md).
- [ ] Left out of the zip because they hold Mac-only binaries: `.venv` (step 2 reinstalls the packages) and `harness\node_modules`. For the latter, install Node.js 18+ and run `harness\install.cmd` before starting HARNESS.

## 2. Python, packages and tests

- [ ] Install Python 3.12, 64-bit, from python.org. Tick "Add python.exe to PATH".
- [ ] Install packages, from the repo folder:
  ```bat
  python -m pip install -r requirements.txt
  ```
  This includes `moomoo-api` for orders. `scipy`, which the risk check needs, comes in through `quantstats`/`vectorbt`.
- [ ] Run the tests. Expect **91 passed**, with no network or accounts needed:
  ```bat
  python -m pytest tests
  ```
- [ ] `scripts\market_daily.cmd` calls plain `python`. If you use a virtual environment instead of the global Python, the scheduled task must activate it first (see step 7).

## 3. Local config files

None of these are committed; they are git-ignored. Each has a `.example` template next to it.

```bat
copy config\markets.yaml.example config\markets.yaml
copy config\market_personas.yaml.example config\market_personas.yaml
copy config\broker.yaml.example config\broker.yaml
copy config\lists\my_pn17_gn3.csv.example config\lists\my_pn17_gn3.csv
copy config\lists\my_shariah.csv.example config\lists\my_shariah.csv
setx SEC_USER_AGENT "Your Name your@email.com"
```

- [ ] `SEC_USER_AGENT` must be your real name and email (SEC rule). Open a new terminal after `setx`.
- [ ] Fill the PN17/GN3 list from Bursa's announcements when you want those counters excluded. The Shariah list is optional.

## 4. Data, validation and cards (first time)

```bat
python data\market_update.py --market US --symbols --universe all --stocks-only --shares --macro
python data\market_update.py --market US --universe all --stocks-only --frames
python data\market_update.py --market MY --symbols --universe all --shares --macro
python backtest\market_validation.py --market US
python backtest\market_validation.py --market MY
python backtest\market_timing.py --market US
python backtest\market_timing.py --market MY
python strategy\market_pitch.py --market US
python strategy\market_pitch.py --market MY
```

- [ ] Start the web app (`启动.cmd`). On `/pitch`, the "US / MY" tab should show cards for both markets, and `/data` should show coverage.

## 5. DeepSeek API key: personas and HARNESS chat

Built and dry-run tested without a key. The live test with your key happens here.

- [ ] Put the key in `harness\home\.credentials.yaml` (git-ignored):
  ```yaml
  DEEPSEEK_API_KEY: sk-...
  ```
  `接入API.cmd` writes the same file. An environment variable `DEEPSEEK_API_KEY` also works and takes priority. Don't paste the key into chats or commit it.
- [ ] Check that the model id in `config\market_personas.yaml` (`llm.model: deepseek-v4-flash`) is on DeepSeek's current model list for your account. Change it if not.
- [ ] Persona test from the terminal:
  ```bat
  python strategy\market_personas.py --market US --consult --persona risk_officer
  python strategy\market_personas.py --market US --consult
  ```
  Expect one reply per persona and "N calls recorded".
- [ ] Persona test in the browser: `/pitch` → US / MY → **AI personas** → **Ask**, then **Consult all**. Each reply should:
  - end with a JSON block of decisions;
  - name only codes from the cards or holdings;
  - have no buys from the Risk Officer.
  
  Calls appear under "Today's calls" and start a track record.
- [ ] If it fails, read the message on the page or terminal:
  - **401**: wrong key.
  - **402**: no balance.
  - **400/404 mentioning the model**: model id.
  - **Timeout**: raise `llm.timeout_s`. Reasoning models can take a minute.
- [ ] HARNESS chat: start the app (`启动.cmd`), open **控制** (control page), and send one message in the A-share chat and one in the 牛散 persona chat. Replies should stream in. These routes were broken in v1.0.9 and fixed on this branch.
- [ ] Optional: OpenAI or z.ai GLM instead of DeepSeek. Change `llm.base_url`, `llm.model` and `llm.api_key_env` in `config\market_personas.yaml`, and put that key in the same credentials file.

## 6. moomoo orders (Phase 5)

The order desk is on `/pitch` → US / MY → **Orders**. Drafts come only from approved cards (buys) and exit alerts on held positions (sells). Every order needs **Send** → quantity → confirm. Nothing trades on its own.

`config\broker.yaml` sets the mode:

| Mode | What Send does |
|---|---|
| `preview` (default) | Never sends. Sync reads your **real** account read-only. |
| `paper` | Sends to the moomoo **paper** account. US only; MY falls back to preview because moomoo has no Bursa paper trading. |
| `live` | Sends to the real account. You can set it per market under `markets.US.mode` / `markets.MY.mode`. |

### 6.1 Account and OpenD

- [ ] moomoo MY account with US and Bursa trading enabled. Fund it only when you go live.
- [ ] In the moomoo app, enable **Paper Trading** for US stocks.
- [ ] Download **OpenD** for Windows (the GUI version) from moomoo's OpenAPI page, install it, and log in with your moomoo ID.
- [ ] Accept the API agreement / questionnaire when OpenD asks; it's needed once. Keep OpenD running whenever you sync or send.
- [ ] Leave OpenD's API address at `127.0.0.1` port `11111` (matches `opend` in `broker.yaml`). Check it's listening:
  ```bat
  python -c "import socket; socket.create_connection(('127.0.0.1', 11111), 3); print('OpenD is up')"
  ```
- [ ] List your accounts. If the desk picks the wrong one, put its `acc_id` under `markets.<US|MY>.acc_id` in `broker.yaml`:
  ```bat
  python -c "from moomoo import *; c = OpenSecTradeContext(filter_trdmarket=TrdMarket.US, security_firm=SecurityFirm.FUTUMY); print(c.get_acc_list()); c.close()"
  ```

### 6.2 Read-only check (mode: preview)

- [ ] With `mode: preview`, open Orders and press **Sync**. The header should show your real equity and buying power. **Reconcile** compares the holdings ledger (manual entries) with moomoo positions. Nothing is sent in preview.

### 6.3 US paper test (mode: paper)

- [ ] Set `mode: paper` in `config\broker.yaml`.
- [ ] Approve one or two US cards (**Buy**). Drafts appear in Orders, each with a risk verdict:
  - **approve**;
  - **reduce**: cut by a cap, with the reason shown;
  - **reject**: Send disabled.
- [ ] **Send** one order. It's a DAY limit order at the reference close +1%.
  - Sent before the US open, it waits; the market opens 21:30 MYT (US summer time) or 22:30 (US winter time).
- [ ] After the open, press **Sync**. Expect:
  - order status **filled**;
  - "booked …" in the Note column;
  - the position on `/holdings` with a **paper** badge.
- [ ] Cancel test: send an order outside market hours, then **Cancel** it before the open.
- [ ] Kill switch test: **Kill switch** should cancel working orders, and drafts show *reject*. Then **Release**.
- [ ] Exit test: when a paper holding gets an exit alert, a SELL draft appears. Send it, then Sync; the position closes. Exits are not cut by the buy caps.
- [ ] After a sync, Reconcile should show no red rows.
- [ ] Logs: `logs\market_orders.log` (sends, fills, syncs, kill switch) and `logs\risk_audit.log`.
- [ ] Not verifiable without an account; please check:
  - the paper account shows `US` in `trdmarket_auth`;
  - paper orders are accepted through the API with `FUTUMY`;
  - whether `order_fee_query` works on paper. If not, fees fall back to the schedule estimate, which is fine;
  - the fill date (`updated_time`) lands on the right trading day.

### 6.4 Going live

- [ ] US small live: set `markets.US.mode: live` and keep `MY` on `preview`. Keep `max_order_value` small at first.
- [ ] Unlock trading. Either:
  - press **Unlock Trade** in the OpenD window each session (simplest, and nothing is stored); or
  - store the trade password as an environment variable: `setx MOOMOO_TRADE_PASSWORD "..."`.
- [ ] MY live with one lot: set `markets.MY.mode: live` and keep `max_order_qty: 100`. Ticks, the ±30% limit and 100-share lots are handled.
  - Still to confirm: whether moomoo accepts Bursa orders placed the evening before. If not, send them in the 08:30–09:00 pre-open.
- [ ] Raise caps (`max_order_value`, `max_daily_value`, `max_order_qty`) only after a week of clean reconciles.

### 6.5 Daily routine

- **Morning (after the US close):** the scheduled `market_daily.cmd US` updates data, rebuilds cards and syncs moomoo orders and fills. The sync is skipped in preview mode and needs OpenD running; if OpenD is down it prints "sync failed" and the run carries on.
- **Evening:** review the cards, approve, check drafts and risk verdicts, Send.
- **Malaysia:** `market_daily.cmd MY` after 18:00; send Bursa orders for the next morning.

## 7. Schedule the daily runs

From [markets-data.md](markets-data.md#scheduling-with-windows-task-scheduler-malaysia-time); replace `C:\path\to\repo`:

```bat
schtasks /Create /TN "DSHQuant-US-Daily" /SC WEEKLY /D TUE,WED,THU,FRI,SAT /ST 07:00 ^
  /TR "C:\path\to\repo\scripts\market_daily.cmd US"
schtasks /Create /TN "DSHQuant-MY-Daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 ^
  /TR "C:\path\to\repo\scripts\market_daily.cmd MY"
```

- [ ] With a virtual environment, use `/TR "cmd /c C:\path\to\repo\.venv\Scripts\activate.bat && C:\path\to\repo\scripts\market_daily.cmd US"`.
- [ ] Re-run the validation and timing scripts monthly, or after changing factors.

## What was verified on the Mac

- 91 offline tests, including the order desk against a fake moomoo connection that returns SDK-shaped replies:
  - a paper buy sent, filled and booked;
  - a stop-loss sell past the buy caps, partially filled, then cancelled (that part closes, the rest stays open);
  - kill switch;
  - refusals: preview, MY paper, kill switch, stale prices;
  - reconcile;
  - OpenD not running (clear error instead of the SDK's endless retry).
- The Orders section driven in headless Chrome against the fake connection: drafts, Send, Sync, fills booked into holdings, reconcile, kill switch, MY preview.
- Personas and HARNESS chat: keyless dry runs through local stand-ins for the DeepSeek API.
- **Not verified:** a real OpenD and moomoo account, a real DeepSeek key, Windows-specific paths and console encoding.
