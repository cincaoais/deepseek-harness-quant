/* ui_v2/components/market_desk.js — US / Bursa Malaysia decision desk (the /pitch "US / MY" tab).
   Data: /api/markets (market list) and /api/markets/desk?market=ID (cards, decisions, buy list, tracking).
   Actions: POST /api/markets/decide {market, code, action}; the buy list is the approved cards only;
   "Record fill" posts the executed buy to /api/markets/positions. Needs market_views.js loaded first.
   Usage: LW.marketDesk.load(box) as a tab loader. */
(function () {
  'use strict';
  window.LW = window.LW || {};
  var current = null;
  var fmt = LW.marketViews.fmt, esc = fmt.esc, num = fmt.num, pct = fmt.pct, toast = fmt.toast;

  function renderHeader(d) {
    var c = d.cards, r = c.regime || {};
    var gate = r.is_gate === false
      ? 'informational only (tested: ' + esc(r.verdict || 'not yet') + ')'
      : (r.can_trade ? 'new positions allowed' : '<b style="color:#f59e0b">no new positions (L0)</b>');
    var regime = r.state ? esc(r.state) + ' · cash ' + Math.round((r.cash_ratio || 0) * 100) + '% · ' + gate : 'n/a';
    var warn = (c.warnings || []).map(function (w) { return '<div>⚠ ' + esc(w) + '</div>'; }).join('');
    return '<div class="v2-card"><h2>' + esc(d.name) + ' <span class="tag">' + esc(d.currency) + '</span></h2>' +
      '<div class="mini" style="line-height:1.9">Signal ' + esc(c.signal_date) + ' close → trade at the next session open · ' +
      'L0 regime (' + esc(r.benchmark || '') + '): ' + regime + ' · ' + c.eligible + ' tradable stocks scored</div>' +
      (warn ? '<div class="mini" style="color:#f59e0b;margin-top:6px">' + warn + '</div>' : '') + '</div>';
  }

  function renderBuyList(d) {
    var rows = d.buy_list.map(function (c) {
      return '<tr><td>' + esc(c.code) + '</td><td>' + esc(c.name) + '</td><td>' + num(c.position.shares, 4) +
        '</td><td>' + num(c.position.value, 0) + '</td><td>' + num(c.price, 4) + '</td><td>' + num(c.stop.price, 4) +
        '</td><td><button class="v2-btn md-fill" data-code="' + esc(c.code) + '" data-shares="' + c.position.shares +
        '" data-price="' + c.price + '">Record fill</button></td></tr>';
    }).join('');
    return '<div class="v2-card"><h2>Buy list <span class="tag">approved cards · ' + d.buy_list.length + ' · record the fill once bought</span></h2>' +
      (rows ? '<table><thead><tr><th>Code</th><th>Name</th><th>Shares</th><th>Value</th><th>Last close</th><th>Stop</th><th></th></tr></thead><tbody>' +
        rows + '</tbody></table>' : '<div class="mini" style="opacity:.6">Nothing approved for this card date.</div>') + '</div>';
  }

  function renderCards(d) {
    var c = d.cards, done = {};
    d.decisions.forEach(function (x) { done[x.code] = x.action; });
    var names = c.factors.map(function (f) { return f.name; });
    var head = '<tr><th>#</th><th>Code</th><th>Name</th><th>Score</th>' +
      names.map(function (n) { return '<th>' + esc(n) + ' pct</th>'; }).join('') +
      '<th>Close</th><th>Shares</th><th>Value</th><th>Est. cost</th><th>Stop</th><th></th></tr>';
    var rows = c.cards.map(function (x) {
      var act = done[x.code]
        ? '<b>' + (done[x.code] === 'buy' ? 'Bought' : 'Dropped') + '</b> <button class="v2-btn md-act" data-act="undo" data-code="' + esc(x.code) + '">Undo</button>'
        : '<button class="v2-btn v2-btn-buy md-act" data-act="buy" data-code="' + esc(x.code) + '">Buy</button> ' +
          '<button class="v2-btn md-act" data-act="drop" data-code="' + esc(x.code) + '">Drop</button>';
      return '<tr><td>' + x.rank + '</td><td>' + esc(x.code) + '</td><td>' + esc(x.name) + '</td><td><b>' + x.score + '</b></td>' +
        names.map(function (n) { return '<td>' + num(x.factors[n], 1) + '</td>'; }).join('') +
        '<td>' + num(x.price, 4) + '</td><td>' + num(x.position.shares, 4) + '</td><td>' + num(x.position.value, 0) +
        '</td><td>' + num(x.position.est_cost, 2) + '</td><td>' + num(x.stop.price, 4) + '</td><td>' + act + '</td></tr>';
    }).join('');
    return '<div class="v2-card"><h2>Cards <span class="tag">top ' + c.cards.length + ' by composite score</span></h2>' +
      '<table><thead>' + head + '</thead><tbody>' + rows + '</tbody></table></div>';
  }

  function renderEvidence(c) {
    var row = function (f, skipped) {
      var e = f.evidence || {};
      return '<tr><td>' + esc(f.name) + '</td><td>' + (f.sign > 0 ? 'high' : 'low') + '</td><td>' + (skipped ? 'dropped · ' : '') +
        esc(e.grade || 'not validated') + '</td><td>' + pct(e.annual) + '</td><td>' + num(e.sharpe, 2) + '</td><td>' +
        num(e.ic, 3) + '</td><td>' + pct(e.holdout_annual) + '</td></tr>';
    };
    return '<div class="v2-card"><h2>Factor evidence <span class="tag">latest validation, after costs</span></h2>' +
      '<table><thead><tr><th>Factor</th><th>Prefers</th><th>Grade</th><th>Annual</th><th>Sharpe</th><th>IC</th><th>Holdout</th></tr></thead><tbody>' +
      c.factors.map(function (f) { return row(f, false); }).join('') +
      (c.skipped_factors || []).map(function (f) { return row(f, true); }).join('') + '</tbody></table></div>';
  }

  function renderTrack(track) {
    var horizons = ['t1', 't5', 't20', 't60'];
    var rows = ['auto_pitch', 'human_select'].map(function (pool) {
      var t = track[pool] || {};
      return '<tr><td>' + pool + '</td><td>' + (t.entries || 0) + '</td>' + horizons.map(function (h) {
        var s = t[h];
        return '<td>' + (s ? pct(s.mean) + ' · win ' + Math.round(s.win_rate * 100) + '% (' + s.n + ')' : '–') + '</td>';
      }).join('') + '</tr>';
    }).join('');
    return '<div class="v2-card"><h2>Forward tracking <span class="tag">from the next open after each signal</span></h2>' +
      '<table><thead><tr><th>Pool</th><th>Entries</th><th>T+1</th><th>T+5</th><th>T+20</th><th>T+60</th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  function renderDesk(box) {
    var body = box.querySelector('#md-body');
    body.innerHTML = '<div class="mini" style="opacity:.6">Loading ' + esc(current) + '…</div>';
    LW.api.get('/api/markets/desk?market=' + encodeURIComponent(current), { bypass: true }).then(function (d) {
      if (!d || !d.ok) { body.innerHTML = '<div class="mini">' + esc((d && d.message) || 'desk unavailable') + '</div>'; return; }
      box._desk = d;
      if (!d.cards) {
        body.innerHTML = '<div class="v2-card"><div class="mini">No cards yet. Run <code>python strategy/market_pitch.py --market ' +
          esc(current) + '</code>.</div></div>';
        return;
      }
      body.innerHTML = renderHeader(d) + renderBuyList(d) + renderCards(d) + '<div id="md-personas"></div>' +
        renderEvidence(d.cards) + renderTrack(d.track);
      loadPersonas(box);
    }).catch(function (e) { body.innerHTML = '<div class="mini">' + esc(e) + '</div>'; });
  }

  function decide(box, code, action) {
    var regime = (box._desk && box._desk.cards && box._desk.cards.regime) || {};
    if (action === 'buy' && regime.state && regime.is_gate !== false && !regime.can_trade &&
        !window.confirm('L0 regime is "' + regime.state + '": no new positions. Record this buy anyway?')) return;
    fetch('/api/markets/decide', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: current, code: code, action: action })
    }).then(function (r) { return r.json(); }).then(function (res) {
      toast(res.ok ? code + ' · ' + action : (res.message || 'failed'));
      renderDesk(box);
    }).catch(function (e) { toast('server unreachable: ' + e); });
  }

  function renderPersonas(p) {
    var llm = p.llm || {};
    var keyNote = llm.is_key_set ? 'key set' : '<b style="color:#f59e0b">no key: add ' + esc('DEEPSEEK_API_KEY') +
      ' to harness/home/.credentials.yaml (or set it in config/market_personas.yaml → llm)</b>';
    var rows = p.personas.map(function (x) {
      var calls = x.decisions.map(function (d) {
        return '<span class="tag-ot" title="' + esc(d.reason) + '">' + esc(d.code) + ' ' + esc(d.action) + '</span>';
      }).join(' ') || '<span class="mini" style="opacity:.6">–</span>';
      var last = x.last_turns.filter(function (t) { return t.role === 'assistant'; }).pop();
      var reply = last ? '<details><summary class="mini">' + esc(last.ts) + ' · ' + esc(last.content.slice(0, 90)) + '…</summary>' +
        '<div class="mini" style="white-space:pre-wrap;line-height:1.6;margin-top:6px">' + esc(last.content) + '</div></details>'
        : '<span class="mini" style="opacity:.6">not consulted yet</span>';
      var t = x.track || {}, t20 = t.t20, t5 = t.t5;
      var record = t.entries ? t.entries + ' buys · T+5 ' + (t5 ? pct(t5.mean) : '–') + ' · T+20 ' + (t20 ? pct(t20.mean) : '–')
        : '<span class="mini" style="opacity:.6">no buys yet</span>';
      return '<tr><td><b>' + esc(x.name) + '</b> <span class="mini">' + esc(x.tag) + (x.can_buy ? '' : ' · never buys') + '</span></td><td>' +
        calls + '</td><td style="max-width:420px">' + reply + '</td><td class="mini">' + record + '</td><td>' +
        '<button class="v2-btn md-ask" data-persona="' + esc(x.id) + '">Ask</button></td></tr>';
    }).join('');
    return '<div class="v2-card"><h2>AI personas <span class="tag">' + esc(llm.model || '') + ' · ' + keyNote + '</span></h2>' +
      '<div class="mini" style="line-height:1.8">Investing styles, not real people. They see today\'s cards, your holdings and the evidence; ' +
      'their calls are tracked to measure them and never become orders. <button class="v2-btn md-consult-all">Consult all</button> ' +
      '<span class="md-persona-status"></span></div>' +
      '<table><thead><tr><th>Persona</th><th>Today\'s calls</th><th>Last reply</th><th>Track record</th><th></th></tr></thead><tbody>' +
      rows + '</tbody></table></div>';
  }

  function loadPersonas(box) {
    var slot = box.querySelector('#md-personas');
    if (!slot) return;
    LW.api.get('/api/markets/personas?market=' + encodeURIComponent(current), { bypass: true }).then(function (p) {
      if (p && p.ok) { slot.innerHTML = renderPersonas(p); box._personas = p.personas; }
    }).catch(function () { slot.innerHTML = ''; });
  }

  function consult(box, persona, text) {
    var status = box.querySelector('.md-persona-status');
    if (status) status.textContent = 'asking ' + persona + '… (reasoning models can take a minute)';
    return fetch('/api/markets/persona_chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: current, persona: persona, text: text || '' })
    }).then(function (r) { return r.json(); }).then(function (res) {
      toast(res.ok ? persona + ': ' + res.recorded + ' calls recorded' : (res.message || 'failed'));
      return res;
    });
  }

  function recordFill(box, b) {
    var price = window.prompt('Fill price for ' + b.getAttribute('data-code'), b.getAttribute('data-price'));
    if (!price) return;
    var shares = window.prompt('Shares bought', b.getAttribute('data-shares'));
    if (!shares) return;
    fetch('/api/markets/positions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: current, action: 'open', code: b.getAttribute('data-code'), shares: shares, price: price })
    }).then(function (r) { return r.json(); }).then(function (res) {
      toast(res.ok ? 'Recorded ' + res.position.code + ' in holdings' : (res.message || 'failed'));
    }).catch(function (e) { toast('server unreachable: ' + e); });
  }

  function load(box) {
    LW.api.get('/api/markets').then(function (d) {
      var markets = (d && d.results) || [];
      if (!markets.length) { box.innerHTML = '<div class="mini">No markets in config/markets.yaml.</div>'; return; }
      current = current || markets[0].id;
      box.innerHTML = '<div id="md-switch" style="display:flex;gap:6px;margin-bottom:6px"></div><div id="md-body"></div>';
      var sw = box.querySelector('#md-switch');
      sw.innerHTML = markets.map(function (m) {
        var on = m.id === current;
        return '<button class="v2-btn md-market" data-market="' + esc(m.id) + '" style="' +
          LW.marketViews.buttonStyle(on) + '">' + esc(m.name) + ' · ' + esc(m.bars.last_date || 'no data') + '</button>';
      }).join('');
      if (!box._mdBound) {
        box._mdBound = true;
        box.addEventListener('click', function (ev) {
          var m = ev.target.closest('.md-market');
          if (m) { current = m.getAttribute('data-market'); load(box); return; }
          var b = ev.target.closest('.md-act');
          if (b) decide(box, b.getAttribute('data-code'), b.getAttribute('data-act'));
          var f = ev.target.closest('.md-fill');
          if (f) recordFill(box, f);
          var ask = ev.target.closest('.md-ask');
          if (ask) {
            var q = window.prompt('Question for ' + ask.getAttribute('data-persona') + ' (empty = daily review)', '');
            if (q !== null) consult(box, ask.getAttribute('data-persona'), q).then(function () { renderDesk(box); });
          }
          if (ev.target.closest('.md-consult-all')) {
            (box._personas || []).reduce(function (chain, x) {
              return chain.then(function () { return consult(box, x.id, ''); });
            }, Promise.resolve()).then(function () { renderDesk(box); });
          }
        });
      }
      renderDesk(box);
    }).catch(function (e) { box.innerHTML = '<div class="mini">' + esc(e) + '</div>'; });
  }

  LW.marketDesk = { load: load };
})();
