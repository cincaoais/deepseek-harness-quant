/* ui_v2/components/market_orders.js — US / MY order desk on the /pitch "US / MY" tab.
   Data: GET /api/markets/orders?market=ID (mode, kill switch, drafts with risk checks, orders, reconcile).
   Actions: POST /api/markets/orders {action: send (with confirm: true) | cancel | sync | kill}.
   Every order needs a quantity prompt and a confirm; preview mode never sends. Needs market_views.js.
   Usage: LW.marketOrders.mount(slot, marketId). */
(function () {
  'use strict';
  window.LW = window.LW || {};
  var fmt = LW.marketViews.fmt, esc = fmt.esc, num = fmt.num, toast = fmt.toast;
  var MODE_COLORS = { preview: '#64748b', paper: '#0ea5e9', live: '#dc2626' };

  function renderHeader(d) {
    var s = d.snapshot, ctx = d.context;
    var mode = '<b style="color:' + MODE_COLORS[d.mode] + '">' + esc(d.mode.toUpperCase()) + '</b>';
    var kill = d.is_kill_switch_on
      ? '<b style="color:#dc2626">kill switch ON</b> <button class="v2-btn mo-kill" data-on="0">Release</button>'
      : 'kill switch off <button class="v2-btn mo-kill" data-on="1">Kill switch</button>';
    var account = s ? 'moomoo ' + esc(s.env) + ' at ' + esc(s.as_of) + ' · equity ' + num(s.account.total_assets, 0) +
      ' · buying power ' + num(s.account.power, 0) + ' ' + esc(d.currency) : 'no moomoo snapshot yet (Sync reads the account)';
    var notes = [d.note, d.has_config ? '' : 'config/broker.yaml not found: using the template (preview)']
      .filter(Boolean).map(function (n) { return '<div>⚠ ' + esc(n) + '</div>'; }).join('');
    return '<h2>Orders <span class="tag">' + esc(d.provider) + ' · mode ' + mode + ' · ' + ctx.orders_today + ' sent today</span></h2>' +
      '<div class="mini" style="line-height:1.9">' + kill + ' · <button class="v2-btn mo-sync">Sync</button> ' + account +
      ' · sizing on ' + esc(ctx.equity_source) + '</div>' +
      (notes ? '<div class="mini" style="color:#f59e0b;margin-top:6px">' + notes + '</div>' : '');
  }

  function renderDrafts(d) {
    var rows = d.drafts.map(function (x) {
      var r = x.risk, failed = r.checks.filter(function (c) { return !c.is_ok; });
      var verdict = '<b style="color:' + (r.decision === 'reject' ? '#dc2626' : r.decision === 'reduce' ? '#f59e0b' : '#16a34a') +
        '">' + esc(r.decision) + '</b>' + (failed.length ? '<div class="mini">' + failed.map(function (c) {
          return esc(c.name) + ': ' + esc(c.detail);
        }).join('<br>') + '</div>' : '');
      var canSend = d.mode !== 'preview' && r.decision !== 'reject';
      return '<tr><td><b>' + esc(x.side.toUpperCase()) + '</b></td><td>' + esc(x.code) + '</td><td>' + esc(x.name) + '</td><td>' +
        num(r.qty, 4) + (r.qty !== x.qty ? ' <span class="mini">of ' + num(x.qty, 4) + '</span>' : '') + '</td><td>' +
        num(x.limit_price, 4) + '<div class="mini">ref ' + num(x.ref_price, 4) + ' · ' + esc(x.ref_date) + '</div></td><td>' +
        num(r.value, 0) + '</td><td>' + num(x.est_fees, 2) + '</td><td class="mini">' + esc(x.source) + '</td><td>' + verdict +
        '</td><td><button class="v2-btn mo-send" data-key="' + esc(x.key) + '"' + (canSend ? '' : ' disabled') + '>Send</button></td></tr>';
    }).join('');
    return '<h3 style="margin:12px 0 6px">Drafts <span class="tag">approved cards not held · exit alerts on held positions</span></h3>' +
      (rows ? '<table><thead><tr><th>Side</th><th>Code</th><th>Name</th><th>Qty</th><th>Limit</th><th>Value</th><th>Est. fees</th>' +
        '<th>Source</th><th>Risk</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>'
        : '<div class="mini" style="opacity:.6">Nothing to order: approve cards above, or wait for exit alerts.</div>');
  }

  function renderOrders(d) {
    if (!d.orders.length) return '';
    var rows = d.orders.map(function (o) {
      var fill = o.dealt_qty ? num(o.dealt_qty, 4) + ' @ ' + num(o.dealt_avg_price, 4) : '–';
      var booked = o.fill_position_id ? 'booked ' + esc(o.fill_position_id) : (o.fill_error ? 'not booked: ' + esc(o.fill_error) : '');
      var working = o.status === 'working' || o.status === 'partial';
      return '<tr><td class="mini">' + esc(o.sent_at) + '</td><td>' + esc(o.env) + '</td><td>' + esc(o.side.toUpperCase()) + '</td><td>' +
        esc(o.code) + '</td><td>' + num(o.qty, 4) + '</td><td>' + num(o.limit_price, 4) + '</td><td><b>' + esc(o.status) + '</b>' +
        (o.broker_status ? ' <span class="mini">' + esc(o.broker_status) + '</span>' : '') + '</td><td>' + fill + '</td><td class="mini">' +
        esc(o.message || '') + ' ' + booked + '</td><td>' +
        (working ? '<button class="v2-btn mo-cancel" data-id="' + esc(o.id) + '">Cancel</button>' : '') + '</td></tr>';
    }).join('');
    return '<h3 style="margin:12px 0 6px">Sent orders <span class="tag">DAY limit orders · Sync books fills in holdings</span></h3>' +
      '<table><thead><tr><th>Sent</th><th>Env</th><th>Side</th><th>Code</th><th>Qty</th><th>Limit</th><th>Status</th><th>Filled</th>' +
      '<th>Note</th><th></th></tr></thead><tbody>' + rows + '</tbody></table>';
  }

  function renderReconcile(d) {
    if (!d.snapshot) return '';
    var rows = d.reconcile.map(function (r) {
      return '<tr style="' + (r.is_match ? '' : 'color:#dc2626;font-weight:600') + '"><td>' + esc(r.code) + '</td><td>' +
        num(r.ledger, 4) + '</td><td>' + num(r.broker, 4) + '</td><td>' + num(r.diff, 4) + '</td></tr>';
    }).join('');
    return '<h3 style="margin:12px 0 6px">Reconcile <span class="tag">holdings ledger (' + esc(d.env) + ') vs moomoo</span></h3>' +
      (rows ? '<table><thead><tr><th>Code</th><th>Ledger</th><th>moomoo</th><th>Diff</th></tr></thead><tbody>' + rows + '</tbody></table>'
        : '<div class="mini" style="opacity:.6">No positions on either side.</div>');
  }

  function render(slot, market) {
    LW.api.get('/api/markets/orders?market=' + encodeURIComponent(market), { bypass: true }).then(function (d) {
      if (!d || !d.ok) { slot.innerHTML = '<div class="v2-card mini">' + esc((d && d.message) || 'orders unavailable') + '</div>'; return; }
      slot._orders = d;
      slot.innerHTML = '<div class="v2-card">' + renderHeader(d) + renderDrafts(d) + renderOrders(d) + renderReconcile(d) + '</div>';
    }).catch(function (e) { slot.innerHTML = '<div class="v2-card mini">' + esc(e) + '</div>'; });
  }

  function post(slot, market, body, done) {
    body.market = market;
    return fetch('/api/markets/orders', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (res) {
      toast(res.ok ? done(res) : (res.message || 'failed'));
      render(slot, market);
    }).catch(function (e) { toast('server unreachable: ' + e); });
  }

  function send(slot, market, key) {
    var d = slot._orders, x = d.drafts.filter(function (r) { return r.key === key; })[0];
    if (!x) return;
    var label = d.mode.toUpperCase() + ' ' + x.side.toUpperCase() + ' ' + x.code + ' limit ' + x.limit_price + ' ' + d.currency;
    var qty = window.prompt(label + '\nQuantity (at most ' + x.risk.qty + ')', x.risk.qty);
    if (!qty) return;
    var warning = d.mode === 'live' ? '\n\nThis is the REAL moomoo account.' : '';
    if (!window.confirm('Send ' + label + ' × ' + qty + ' (DAY order)?' + warning)) return;
    post(slot, market, { action: 'send', key: key, qty: Number(qty), confirm: true }, function (res) { return res.message; });
  }

  function mount(slot, market) {
    if (!slot) return;
    if (!slot._moBound) {
      slot._moBound = true;
      slot.addEventListener('click', function (ev) {
        var m = slot._market, b;
        if ((b = ev.target.closest('.mo-send'))) send(slot, m, b.getAttribute('data-key'));
        if ((b = ev.target.closest('.mo-cancel')) && window.confirm('Cancel this order at moomoo?'))
          post(slot, m, { action: 'cancel', id: b.getAttribute('data-id') }, function (res) { return 'order ' + res.order.status; });
        if (ev.target.closest('.mo-sync'))
          post(slot, m, { action: 'sync' }, function (res) { return res.changed.length + ' orders changed · ' + res.fills.length + ' fills booked'; });
        if ((b = ev.target.closest('.mo-kill'))) {
          var on = b.getAttribute('data-on') === '1';
          if (window.confirm(on ? 'Engage the kill switch? Nothing is sent and working orders are cancelled.' : 'Release the kill switch?'))
            post(slot, m, { action: 'kill', is_on: on }, function (res) {
              return 'kill switch ' + (res.is_kill_switch_on ? 'ON' : 'off') + (res.errors.length ? ' · ' + res.errors.join('; ') : '');
            });
        }
      });
    }
    slot._market = market;
    slot.innerHTML = '<div class="v2-card mini" style="opacity:.6">Loading orders…</div>';
    render(slot, market);
  }

  LW.marketOrders = { mount: mount };
})();
