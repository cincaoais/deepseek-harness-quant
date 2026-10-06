/* ui_v2/components/market_views.js — US / Bursa Malaysia views shared by the portal, holdings,
   factors, backtest, data and report pages, plus formatting helpers (LW.marketViews.fmt).
   Data: /api/markets, /api/markets/overview, /api/markets/positions, /api/markets/validation.
   Usage: LW.marketViews.mount('element-id', 'overview' | 'holdings' | 'validation' | 'curves' | 'coverage')
   or LW.marketViews.render(element, view) as a tab loader. */
(function () {
  'use strict';
  window.LW = window.LW || {};
  var SYMBOL = { USD: 'US$', MYR: 'RM' };
  var SERIES = ['#60a5fa', '#f59e0b', '#a78bfa', '#34d399', '#f472b6', '#22d3ee', '#e879f9', '#facc15'];
  var ACTIVE = 'background:#e4e4e7;color:#111114', INACTIVE = 'background:#2a2a30;color:#d4d4d8';
  var selected = null;

  var fmt = {
    esc: function (s) {
      return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
      });
    },
    num: function (v, digits) { return v == null ? '–' : (+v).toLocaleString(undefined, { maximumFractionDigits: digits }); },
    color: function (v) { return v > 0 ? '#22c55e' : (v < 0 ? '#ef4444' : 'inherit'); },   // these markets show gains in green
    pct: function (v) {
      if (v == null) return '<span style="opacity:.5">–</span>';
      return '<span style="color:' + fmt.color(v) + '">' + (v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%</span>';
    },
    money: function (v, currency) {
      if (v == null) return '–';
      return (v < 0 ? '−' : '') + (SYMBOL[currency] || currency + ' ') + Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
    },
    pnl: function (v, currency) { return '<span style="color:' + fmt.color(v) + '">' + (v > 0 ? '+' : '') + fmt.money(v, currency) + '</span>'; },
    toast: function (msg) { if (LW.decide && LW.decide.toast) LW.decide.toast(msg); else window.alert(msg); }
  };

  function card(title, tag, body) {
    return '<div class="v2-card"><h2>' + title + (tag ? ' <span class="tag">' + tag + '</span>' : '') + '</h2>' + body + '</div>';
  }
  function table(head, rows) {
    return '<table><thead><tr>' + head.map(function (h) { return '<th>' + h + '</th>'; }).join('') +
      '</tr></thead><tbody>' + rows.join('') + '</tbody></table>';
  }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json(); });
  }

  /* Market buttons; picking one re-renders the view with that market. */
  function withMarket(box, draw) {
    LW.api.get('/api/markets').then(function (d) {
      var markets = (d && d.results) || [];
      if (!markets.length) { box.innerHTML = card('US / Malaysia', '', '<div class="mini">No markets in config/markets.yaml.</div>'); return; }
      selected = selected || markets[0].id;
      var market = markets.filter(function (m) { return m.id === selected; })[0] || markets[0];
      box.innerHTML = '<div style="display:flex;gap:6px;margin-top:14px">' + markets.map(function (m) {
        return '<button class="v2-btn mv-market" data-market="' + fmt.esc(m.id) + '" style="' +
          (m.id === market.id ? ACTIVE : INACTIVE) + '">' + fmt.esc(m.name) + '</button>';
      }).join('') + '</div><div class="mv-body"></div>';
      box.querySelectorAll('.mv-market').forEach(function (b) {
        b.onclick = function () { selected = b.getAttribute('data-market'); withMarket(box, draw); };
      });
      draw(box.querySelector('.mv-body'), market);
    }).catch(function (e) { box.innerHTML = '<div class="mini">' + fmt.esc(e) + '</div>'; });
  }

  function overview(box) {
    LW.api.get('/api/markets/overview', { bypass: true }).then(function (d) {
      var rows = ((d && d.results) || []).map(function (m) {
        var r = m.regime || {}, b = m.book || {}, grades = m.validation.grades || {};
        return '<tr><td><b>' + fmt.esc(m.name) + '</b></td><td>' + fmt.esc(m.data.last_date || '–') +
          (m.data.is_current ? '' : ' <span style="color:#f59e0b">stale</span>') + '</td><td>' +
          (r.state ? fmt.esc(r.state) + (r.can_trade ? '' : ' · <span style="color:#f59e0b">no new positions</span>') : '–') +
          '</td><td>' + m.cards.count + (m.cards.signal_date ? ' · ' + fmt.esc(m.cards.signal_date) : '') + '</td><td>' + m.buy_list +
          '</td><td>' + b.open + '/' + (b.slots || '–') + ' · ' + fmt.money(b.value, m.currency) + '</td><td>' + fmt.pnl(b.unrealized || 0, m.currency) +
          '</td><td>' + (b.alerts ? '<b style="color:#f59e0b">' + b.alerts + '</b>' : '0') + '</td><td class="mini">' +
          Object.keys(grades).map(function (g) { return grades[g] + ' ' + g; }).join(' · ') + '</td></tr>';
      });
      box.innerHTML = card('US / Malaysia', '<a href="/pitch" style="color:inherit">cards on /pitch → US / MY</a>',
        table(['Market', 'Data', 'L0 regime', 'Cards', 'Buy list', 'Holdings', 'Unrealized', 'Exit alerts', 'Validation'], rows));
    }).catch(function (e) { box.innerHTML = '<div class="mini">' + fmt.esc(e) + '</div>'; });
  }

  function holdings(box) {
    withMarket(box, function (body, market) {
      var cur = market.currency;
      LW.api.get('/api/markets/positions?market=' + market.id, { bypass: true }).then(function (d) {
        var s = d.summary;
        var openRows = d.open.map(function (p) {
          var m = p.marks || {};
          var alerts = p.alerts.map(function (a) {
            return '<span class="tag-ot" title="' + fmt.esc(a.detail) + '" style="color:#f59e0b">' + fmt.esc(a.rule) + '</span>';
          }).join(' ');
          return '<tr><td>' + fmt.esc(p.code) + '</td><td>' + fmt.esc(p.name) + '</td><td>' + fmt.num(m.shares, 4) + '</td><td>' +
            fmt.num(m.entry, 4) + '</td><td>' + fmt.esc(p.entry_date) + '</td><td>' + fmt.num(m.last, 4) + ' <span class="mini">' +
            fmt.esc(m.last_date || '') + '</span></td><td>' + fmt.money(m.value, cur) + '</td><td>' + fmt.pnl(m.pnl, cur) + ' ' + fmt.pct(m.pnl_pct) +
            '</td><td>' + (m.sessions_held == null ? '–' : m.sessions_held) + '</td><td>' + (alerts || '–') + '</td><td>' +
            '<button class="v2-btn mv-close" data-id="' + p.id + '" data-last="' + (m.last || '') + '">Close</button> ' +
            '<button class="v2-btn mv-delete" data-id="' + p.id + '">Delete</button></td></tr>';
        });
        var closedRows = d.closed.map(function (p) {
          return '<tr><td>' + fmt.esc(p.code) + '</td><td>' + fmt.esc(p.entry_date) + ' → ' + fmt.esc(p.exit_date) + '</td><td>' +
            fmt.num(p.entry_price, 4) + ' → ' + fmt.num(p.exit_price, 4) + '</td><td>' + fmt.pnl(p.realized, cur) + ' ' +
            fmt.pct(p.realized_pct) + '</td><td>' + fmt.esc(p.exit_reason) + '</td></tr>';
        });
        body.innerHTML = card(fmt.esc(market.name) + ' holdings', s.open + '/' + (s.slots || '–') + ' positions',
          '<div class="mini" style="line-height:1.9">Cost ' + fmt.money(s.cost, cur) + ' · value ' + fmt.money(s.value, cur) +
          ' · unrealized ' + fmt.pnl(s.unrealized, cur) + ' · realized ' + fmt.pnl(s.realized, cur) + ' · exit alerts ' + s.alerts +
          ' · marked at the latest close; dividends not booked</div>' +
          (openRows.length ? table(['Code', 'Name', 'Shares', 'Entry', 'Bought', 'Last', 'Value', 'P&L', 'Sessions', 'Exit alerts', ''], openRows)
            : '<div class="mini" style="opacity:.6;margin-top:6px">No open positions.</div>') +
          '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;align-items:center">' +
          '<input class="mv-f" name="code" placeholder="Code (e.g. AAPL / 1155)" style="width:150px">' +
          '<input class="mv-f" name="shares" placeholder="Shares" style="width:90px">' +
          '<input class="mv-f" name="price" placeholder="Fill price" style="width:90px">' +
          '<input class="mv-f" name="date" placeholder="Date (default today)" style="width:150px">' +
          '<input class="mv-f" name="fees" placeholder="Fees (default estimate)" style="width:160px">' +
          '<button class="v2-btn mv-open">Record fill</button></div>') +
          (closedRows.length ? card('Closed', closedRows.length + ' positions',
            table(['Code', 'Held', 'Price', 'Realized', 'Reason'], closedRows)) : '');
        body.querySelector('.mv-open').onclick = function () {
          var rec = { market: market.id, action: 'open' };
          body.querySelectorAll('.mv-f').forEach(function (i) { if (i.value.trim()) rec[i.name] = i.value.trim(); });
          post('/api/markets/positions', rec).then(function (r) {
            fmt.toast(r.ok ? 'Recorded ' + r.position.code : r.message);
            if (r.ok) holdings(box);
          });
        };
        body.querySelectorAll('.mv-close').forEach(function (b) {
          b.onclick = function () {
            var price = window.prompt('Sell price', b.getAttribute('data-last'));
            if (!price) return;
            post('/api/markets/positions', { market: market.id, action: 'close', id: b.getAttribute('data-id'), price: price })
              .then(function (r) { fmt.toast(r.ok ? 'Closed ' + r.position.code : r.message); if (r.ok) holdings(box); });
          };
        });
        body.querySelectorAll('.mv-delete').forEach(function (b) {
          b.onclick = function () {
            if (!window.confirm('Delete this entry? Use Close for a real sale.')) return;
            post('/api/markets/positions', { market: market.id, action: 'delete', id: b.getAttribute('data-id') })
              .then(function (r) { fmt.toast(r.ok ? 'Deleted' : r.message); if (r.ok) holdings(box); });
          };
        });
      }).catch(function (e) { body.innerHTML = '<div class="mini">' + fmt.esc(e) + '</div>'; });
    });
  }

  function getReport(market) {
    return LW.api.get('/api/markets/validation').then(function (d) {
      return ((d && d.results) || []).filter(function (r) { return r.market === market.id; })[0];
    });
  }

  function validation(box) {
    withMarket(box, function (body, market) {
      getReport(market).then(function (r) {
        if (!r) { body.innerHTML = card('Validation', '', '<div class="mini">No report yet: python backtest/market_validation.py --market ' + market.id + '</div>'); return; }
        var names = Object.keys(r.factors), p = r.params;
        var rows = names.map(function (n) {
          var f = r.factors[n], pf = f.portfolio, ic = f.ic;
          var gradeColor = { valid: '#22c55e', watch: '#f59e0b', invalid: '#ef4444' }[f.grade];
          return '<tr><td><b>' + fmt.esc(n) + '</b> <span class="mini">' + (f.sign > 0 ? 'high' : 'low') + '</span></td>' +
            '<td style="color:' + gradeColor + '" title="' + fmt.esc(f.reasons.join('; ')) + '">' + f.grade + '</td><td>' + fmt.pct(pf.annual_return) +
            '</td><td>' + fmt.num(pf.sharpe, 2) + '</td><td>' + fmt.pct(pf.max_drawdown) + '</td><td>' + fmt.pct(pf.excess_annual) +
            '</td><td>' + fmt.num(ic.mean, 3) + ' (' + fmt.num(ic.t_stat, 1) + ')</td><td>' + fmt.pct(f.holdout.portfolio.annual_return) +
            '</td><td>' + fmt.money(f.capacity, r.currency) + '</td><td class="mini">' + (f.duplicate_of ? 'duplicate of ' + fmt.esc(f.duplicate_of) : '') +
            '</td></tr>';
        });
        var years = {};
        names.forEach(function (n) { Object.keys(r.factors[n].excess_by_year).forEach(function (y) { years[y] = 1; }); });
        years = Object.keys(years).sort();
        var yearRows = names.map(function (n) {
          var e = r.factors[n].excess_by_year;
          return '<tr><td>' + fmt.esc(n) + '</td>' + years.map(function (y) { return '<td>' + fmt.pct(e[y]) + '</td>'; }).join('') + '</tr>';
        });
        body.innerHTML = card(fmt.esc(market.name) + ' factor validation', fmt.esc(r.generated_at) + ' · after costs',
          '<div class="mini" style="line-height:1.9">' + p.universe_codes + ' stocks · from ' + fmt.esc(p.start) + ' · holdout from ' +
          fmt.esc(p.holdout_start) + ' · top ' + p.top_n + ', rebalance every ' + p.rebalance_days + ' sessions · benchmark ' +
          fmt.esc(r.benchmark.code) + ' ' + fmt.pct(r.benchmark.annual_return) + '/yr' +
          r.data_notes.map(function (n) {
            var warn = /biased|boundary/.test(n);
            return '<div' + (warn ? ' style="color:#f59e0b">⚠ ' : '>') + fmt.esc(n) + '</div>';
          }).join('') + '</div>' +
          table(['Factor', 'Grade', 'Annual', 'Sharpe', 'Max DD', 'vs benchmark', 'IC (t)', 'Holdout', 'Capacity', ''], rows)) +
          card('Excess return by year', 'portfolio minus ' + fmt.esc(r.benchmark.code),
            '<div style="overflow-x:auto">' + table(['Factor'].concat(years), yearRows) + '</div>');
      });
    });
  }

  function chart(series, width, height) {
    var all = [];
    series.forEach(function (s) { s.points.forEach(function (p) { all.push(p[1]); }); });
    var months = series[0].points.map(function (p) { return p[0]; });
    var lo = Math.log(Math.min.apply(null, all)), hi = Math.log(Math.max.apply(null, all));
    var x = function (i) { return 40 + i / Math.max(months.length - 1, 1) * (width - 50); };
    var y = function (v) { return 10 + (hi - Math.log(v)) / Math.max(hi - lo, 1e-9) * (height - 30); };
    var index = {};
    months.forEach(function (m, i) { index[m] = i; });
    var lines = series.map(function (s) {
      var pts = s.points.filter(function (p) { return index[p[0]] != null; })
        .map(function (p) { return x(index[p[0]]).toFixed(1) + ',' + y(p[1]).toFixed(1); }).join(' ');
      return '<polyline fill="none" stroke="' + s.color + '" stroke-width="' + (s.dashed ? 2 : 1.6) + '"' +
        (s.dashed ? ' stroke-dasharray="5 4"' : '') + ' points="' + pts + '"/>';
    }).join('');
    var ticks = months.filter(function (m) { return m.slice(5) === '01'; }).map(function (m) {
      return '<text x="' + x(index[m]) + '" y="' + (height - 4) + '" fill="#71717a" font-size="10" text-anchor="middle">' + m.slice(0, 4) + '</text>';
    }).join('');
    return '<svg viewBox="0 0 ' + width + ' ' + height + '" style="width:100%;height:auto">' + ticks + lines + '</svg>';
  }

  function curves(box) {
    withMarket(box, function (body, market) {
      getReport(market).then(function (r) {
        if (!r) { body.innerHTML = card('Validated portfolios', '', '<div class="mini">No report yet: python backtest/market_validation.py --market ' + market.id + '</div>'); return; }
        var start = r.params.start.slice(0, 7);
        var bench = r.benchmark.nav_monthly || [];
        var series = [{ name: r.benchmark.code, color: '#a1a1aa', dashed: true, points: bench }];
        Object.keys(r.factors).forEach(function (n, i) {
          var f = r.factors[n];
          if (!f.nav_monthly || f.start.slice(0, 7) !== start) return;
          series.push({ name: n + ' · ' + f.grade, color: SERIES[i % SERIES.length], points: f.nav_monthly });
        });
        if (!bench.length) { body.innerHTML = card('Validated portfolios', '', '<div class="mini">Re-run the validation to add equity curves.</div>'); return; }
        var legend = series.map(function (s) {
          var last = s.points.length ? s.points[s.points.length - 1][1] : null;
          return '<span style="margin-right:14px;white-space:nowrap"><span style="display:inline-block;width:14px;height:3px;background:' + s.color +
            ';vertical-align:middle;margin-right:5px"></span>' + fmt.esc(s.name) + ' ×' + fmt.num(last, 2) + '</span>';
        }).join('');
        body.innerHTML = card(fmt.esc(market.name) + ' · validated portfolios vs ' + fmt.esc(r.benchmark.code),
          'growth of 1 since ' + fmt.esc(r.params.start) + ', log scale, after costs',
          chart(series, 900, 320) + '<div class="mini" style="line-height:2;margin-top:6px">' + legend + '</div>' +
          '<div class="mini" style="opacity:.7">Turnover factors start later (share-count boundary) and are in the validation table on the factors page. Run: python backtest/market_validation.py --market ' + market.id + '</div>');
      });
    });
  }

  function coverage(box) {
    LW.api.get('/api/markets', { bypass: true }).then(function (d) {
      var rows = ((d && d.results) || []).map(function (m) {
        var t = m.turnover || {}, sh = m.shares || {}, f = m.fundamentals || {}, sm = m.symbol_master || {};
        return '<tr><td><b>' + fmt.esc(m.name) + '</b></td><td>' + m.bars.symbols + ' · ' + fmt.num(m.bars.rows, 0) + ' rows</td><td>' +
          fmt.esc(m.bars.first_date) + ' → ' + fmt.esc(m.bars.last_date) + (m.bars.is_current ? '' : ' <span style="color:#f59e0b">stale</span>') +
          '</td><td>' + (sm.count || 0) + '</td><td>' + (sh.companies || 0) + '</td><td>' + (t.codes != null ? t.codes + (t.not_domestic ? ' (' + t.not_domestic + ' foreign/multi-class excluded)' : '') : '–') +
          '</td><td>' + (f.companies || 0) + ' · ' + fmt.num(f.facts, 0) + ' facts</td><td>' + (m.macro || []).length + '</td><td>' +
          fmt.esc(m.providers.bars) + (m.is_survivorship_safe ? '' : ' <span style="color:#f59e0b">(no delisted)</span>') + '</td></tr>';
      });
      box.innerHTML = card('US / Malaysia data', 'python data/market_update.py · docs/markets-data.md',
        table(['Market', 'Daily bars', 'Range', 'Symbol master', 'Share counts', 'Turnover stocks', 'SEC facts', 'Macro series', 'Bars source'], rows));
    }).catch(function (e) { box.innerHTML = '<div class="mini">' + fmt.esc(e) + '</div>'; });
  }

  var VIEWS = { overview: overview, holdings: holdings, validation: validation, curves: curves, coverage: coverage };
  LW.marketViews = {
    fmt: fmt,
    render: function (box, view) { VIEWS[view](box); },
    mount: function (id, view) { var box = document.getElementById(id); if (box) VIEWS[view](box); },
    buttonStyle: function (isActive) { return isActive ? ACTIVE : INACTIVE; }
  };
})();
