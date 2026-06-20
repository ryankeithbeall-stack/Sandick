/* ─────────────────────────────────────────────────────────────
   SANDICK vault — front end logic
   - Basket + price data mirror config/sandick.basket.json + prices.example.json
   - allocate() is a faithful JS port of sandick.allocator.build_plan
   - Vault / queue / admin are a local demo state machine (no chain calls)
   ───────────────────────────────────────────────────────────── */

// ── data (mirrors config/) ──────────────────────────────────
const LETTERS = ['S', 'A', 'N', 'D', 'I', 'C', 'K'];
const LETTER_COLORS = {
  S: '#ff2d2d', A: '#2f7fe0', N: '#c8f032', D: '#2f7fe0',
  I: '#2a4cf0', C: '#2a4cf0', K: '#2bb4f0',
};

const BASKET = {
  name: 'SANDICK',
  dex: 'tradexyz',
  assets: [
    { company: 'SanDisk',     ticker: 'SNDK', coin: 'SNDK',   sz_decimals: 2 },
    { company: 'Astera Labs', ticker: 'ALAB', coin: 'ALAB',   sz_decimals: 2 },
    { company: 'Nebius',      ticker: 'NBIS', coin: 'NBIS',   sz_decimals: 2 },
    { company: 'Dell',        ticker: 'DELL', coin: 'DELL',   sz_decimals: 2 },
    { company: 'Intel',       ticker: 'INTC', coin: 'INTC',   sz_decimals: 1 },
    { company: 'CoreWeave',   ticker: 'CRWV', coin: 'CRWV',   sz_decimals: 2 },
    { company: 'Kioxia',      ticker: '285A', coin: 'KIOXIA', sz_decimals: 2 },
  ],
};

const EXAMPLE_PRICES = {
  SNDK: 50.0, ALAB: 80.0, NBIS: 50.0, DELL: 120.0,
  INTC: 22.0, CRWV: 140.0, KIOXIA: 13.0,
};

const N = BASKET.assets.length;
const EQUAL_WEIGHT = 1 / N;

// ── chain mode (config-gated) ───────────────────────────────
// When window.SANDICK_CONFIG.chain.enabled is true we wire the depositor and
// admin surfaces to a deployed SandickVault via chain.js; otherwise the app
// runs the self-contained demo state machine below. See frontend/config.js.
const CHAIN_CFG = (typeof window !== 'undefined' && window.SANDICK_CONFIG && window.SANDICK_CONFIG.chain) || null;
const LIVE = !!(CHAIN_CFG && CHAIN_CFG.enabled);

// ── allocation math (port of allocator.build_plan, equal-weight) ──
function roundSize(rawSize, szDecimals) {
  const factor = 10 ** szDecimals;
  return Math.floor(rawSize * factor) / factor;
}

function allocate({ prices, capital, leverage, side }) {
  // equal weight => gross_notional = capital * leverage
  const grossNotional = capital * leverage;
  const orders = BASKET.assets.map((asset) => {
    const price = prices[asset.coin];
    const w = EQUAL_WEIGHT;
    const targetNotional = w * grossNotional;
    const size = roundSize(targetNotional / price, asset.sz_decimals);
    const notional = size * price;
    return {
      asset, side, price, leverage, targetWeight: w,
      targetNotional, size, notional, margin: notional / leverage,
      actualWeight: 0,
    };
  });
  const total = orders.reduce((s, o) => s + o.notional, 0) || 1;
  orders.forEach((o) => { o.actualWeight = o.notional / total; });

  const deployedMargin = orders.reduce((s, o) => s + o.margin, 0);
  return {
    orders,
    grossNotional: orders.reduce((s, o) => s + o.notional, 0),
    deployedMargin,
    residualCash: capital - deployedMargin,
  };
}

// ── formatting ──────────────────────────────────────────────
const fmtUsd = (n, d = 2) =>
  '$' + n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtNum = (n, d = 2) =>
  n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const fmtPct = (n) => (n * 100).toFixed(2) + '%';

// ── tiny DOM helpers ────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let toastTimer;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

// ── app state ───────────────────────────────────────────────
const state = {
  connected: false,
  isManager: false,
  prices: { ...EXAMPLE_PRICES },
  side: 'long',
  // vault demo state
  navPerShare: 1.0427,
  totalAssets: 248_500,
  shareSupply: 238_330,
  walletUsdc: 10_000,
  yourShares: 0,
  queue: [],
};

// ═══════════════════════════════════════════════════════════
//  Basket grid
// ═══════════════════════════════════════════════════════════
function renderBasket() {
  const grid = $('#basketGrid');
  grid.innerHTML = BASKET.assets.map((a, i) => {
    const L = LETTERS[i];
    const color = LETTER_COLORS[L];
    return `
      <article class="bcard">
        <span class="bcard__bar" style="background:${color}"></span>
        <span class="bcard__letter" style="color:${color}">${L}</span>
        <h3 class="bcard__company">${a.company}</h3>
        <div class="bcard__tickers">${a.ticker} <span class="bcard__coin">${a.coin}</span></div>
        <div class="bcard__foot">
          <span><span class="k">Weight</span> <span class="v">${fmtPct(EQUAL_WEIGHT)}</span></span>
          <span><span class="k">sz</span> <span class="v">${a.sz_decimals}d</span></span>
        </div>
      </article>`;
  }).join('');
}

// ═══════════════════════════════════════════════════════════
//  Calculator
// ═══════════════════════════════════════════════════════════
function renderPriceInputs() {
  const wrap = $('#priceInputs');
  wrap.innerHTML = BASKET.assets.map((a) => `
    <div class="price-row">
      <label for="px-${a.coin}">${a.coin}</label>
      <input id="px-${a.coin}" type="number" min="0" step="0.01"
             value="${state.prices[a.coin]}" data-coin="${a.coin}" />
    </div>`).join('');
  $$('#priceInputs input').forEach((inp) =>
    inp.addEventListener('input', (e) => {
      const v = parseFloat(e.target.value);
      state.prices[e.target.dataset.coin] = Number.isFinite(v) && v > 0 ? v : 0;
      runCalc();
    }));
}

function runCalc() {
  const capital = Math.max(0, parseFloat($('#capital').value) || 0);
  const leverage = parseFloat($('#leverage').value) || 1;
  $('#levVal').textContent = leverage.toFixed(1) + '×';

  // guard: any non-positive price -> show message
  const bad = BASKET.assets.some((a) => !(state.prices[a.coin] > 0));
  const body = $('#planBody');
  if (capital <= 0 || bad) {
    body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:28px">Enter a positive capital and prices to build a plan.</td></tr>`;
    $('#calcSummary').innerHTML = '';
    return;
  }

  const plan = allocate({ prices: state.prices, capital, leverage, side: state.side });

  body.innerHTML = plan.orders.map((o) => `
    <tr>
      <td>${o.asset.ticker}</td>
      <td>${o.asset.coin}</td>
      <td><span class="tag-side ${o.side}">${o.side.toUpperCase()}</span></td>
      <td class="num">${fmtUsd(o.price)}</td>
      <td class="num">${fmtNum(o.size)}</td>
      <td class="num">${fmtUsd(o.notional)}</td>
      <td class="num">${fmtUsd(o.margin)}</td>
      <td class="num">${fmtPct(o.actualWeight)}</td>
    </tr>`).join('');

  $('#calcSummary').innerHTML = `
    <div class="sumcell"><div class="k">Gross notional</div><div class="v">${fmtUsd(plan.grossNotional)}</div></div>
    <div class="sumcell"><div class="k">Deployed margin</div><div class="v">${fmtUsd(plan.deployedMargin)}</div></div>
    <div class="sumcell"><div class="k">Residual cash</div><div class="v">${fmtUsd(plan.residualCash)}</div></div>
    <div class="sumcell"><div class="k">Leverage</div><div class="v">${leverage.toFixed(1)}×</div></div>`;
}

function wireCalc() {
  $('#capital').addEventListener('input', runCalc);
  $('#leverage').addEventListener('input', runCalc);
  $$('#sideSeg .seg__btn').forEach((b) =>
    b.addEventListener('click', () => {
      $$('#sideSeg .seg__btn').forEach((x) => x.classList.remove('is-active'));
      b.classList.add('is-active');
      state.side = b.dataset.side;
      runCalc();
    }));
  $('#resetPrices').addEventListener('click', () => {
    state.prices = { ...EXAMPLE_PRICES };
    renderPriceInputs();
    runCalc();
    toast('Example prices restored');
  });
}

// ═══════════════════════════════════════════════════════════
//  Wallet connect
// ═══════════════════════════════════════════════════════════
function setConnected(on) {
  state.connected = on;
  const btn = $('#connectBtn');
  btn.classList.toggle('is-connected', on);
  $('#connectLabel').textContent = on ? '0xA1c…9bE4' : 'Connect wallet';
  refreshVault();
  if (!on) { state.isManager = false; refreshAdmin(); }
}

function wireConnect() {
  $('#connectBtn').addEventListener('click', () => {
    if (LIVE) return Live.connect();
    setConnected(!state.connected);
    toast(state.connected ? 'Wallet connected (demo)' : 'Wallet disconnected');
  });
}

// ═══════════════════════════════════════════════════════════
//  Vault — stats, deposit, redeem, queue
// ═══════════════════════════════════════════════════════════
function refreshVault() {
  // LIVE: kick off async on-chain reads (which update state + re-render when they
  // land); always paint immediately from current state so the UI stays responsive.
  if (LIVE) Live.refreshVault();
  renderVault();
}

function renderVault() {
  $('#navPerShare').textContent = fmtUsd(state.navPerShare, 4);
  // Demo shows a canned all-time delta; live computes it from the genesis 1.0000.
  const deltaPct = LIVE ? (state.navPerShare - 1) * 100 : 4.27;
  $('#navDelta').textContent = `${deltaPct >= 0 ? '+' : ''}${deltaPct.toFixed(2)}% all-time`;
  $('#navDelta').className = 'stat__d ' + (deltaPct >= 0 ? 'up' : 'down');
  $('#totalAssets').textContent = fmtUsd(state.totalAssets, 0);
  $('#shareSupply').textContent = fmtNum(state.shareSupply, 0);

  const shares = state.connected ? state.yourShares : 0;
  $('#yourShares').textContent = fmtNum(shares);
  $('#yourValue').textContent = state.connected
    ? fmtUsd(shares * state.navPerShare) + ' value' : '—';
  $('#walletUsdc').textContent = fmtNum(state.walletUsdc);
  $('#redeemHolding').textContent = fmtNum(shares);

  const canDeposit = state.connected;
  $('#depositBtn').disabled = !canDeposit;
  $('#depositBtn').textContent = canDeposit ? 'Deposit USDC' : 'Connect wallet to deposit';
  const canRedeem = state.connected && shares > 0;
  $('#redeemSyncBtn').disabled = !canRedeem;
  $('#redeemAsyncBtn').disabled = !canRedeem;

  updateDepositPreview();
  updateRedeemPreview();
  renderQueue();
}

function updateDepositPreview() {
  const amt = parseFloat($('#depositAmt').value) || 0;
  const shares = amt / state.navPerShare;
  $('#depositPreview').innerHTML = amt > 0 ? `
    <div class="row"><span>You deposit</span><span>${fmtUsd(amt)}</span></div>
    <div class="row"><span>You receive</span><span>${fmtNum(shares)} SAND-LP</span></div>
    <div class="row"><span>Share price</span><span>${fmtUsd(state.navPerShare, 4)}</span></div>`
    : `<span class="muted">Enter an amount to preview shares.</span>`;
}

function updateRedeemPreview() {
  const shares = parseFloat($('#redeemAmt').value) || 0;
  const usdc = shares * state.navPerShare;
  $('#redeemPreview').innerHTML = shares > 0 ? `
    <div class="row"><span>You redeem</span><span>${fmtNum(shares)} SAND-LP</span></div>
    <div class="row"><span>You receive</span><span>≈ ${fmtUsd(usdc)}</span></div>
    <div class="row"><span>Sync needs idle USDC; else use the queue.</span><span></span></div>`
    : `<span class="muted">Enter shares to preview proceeds.</span>`;
}

function renderQueue() {
  const list = $('#queueList');
  if (!state.queue.length) {
    list.innerHTML = `<li class="queue__empty">No pending requests.</li>`;
    return;
  }
  list.innerHTML = state.queue.map((q) => `
    <li class="qitem">
      <div class="qitem__main">
        <span class="qitem__amt">${fmtNum(q.shares)} SAND-LP</span>
        <span class="qitem__meta">≈ ${fmtUsd(q.shares * state.navPerShare)} · #${q.id}</span>
      </div>
      ${q.status === 'claimable'
        ? `<button class="btn btn--primary" data-claim="${q.id}">Claim</button>`
        : `<span class="qstatus pending">pending</span>`}
    </li>`).join('');
  $$('#queueList [data-claim]').forEach((b) =>
    b.addEventListener('click', () => claim(parseInt(b.dataset.claim, 10))));
}

let queueId = 1;
function deposit() {
  if (LIVE) return Live.deposit();
  const amt = parseFloat($('#depositAmt').value) || 0;
  if (amt <= 0) return toast('Enter a deposit amount');
  if (amt > state.walletUsdc) return toast('Insufficient USDC balance');
  const shares = amt / state.navPerShare;
  state.walletUsdc -= amt;
  state.yourShares += shares;
  state.totalAssets += amt;
  state.shareSupply += shares;
  refreshVault();
  toast(`Deposited ${fmtUsd(amt)} → ${fmtNum(shares)} shares`);
}

function redeemSync() {
  if (LIVE) return Live.redeemSync();
  const shares = parseFloat($('#redeemAmt').value) || 0;
  if (shares <= 0) return toast('Enter shares to redeem');
  if (shares > state.yourShares) return toast('You don’t hold that many shares');
  const usdc = shares * state.navPerShare;
  state.yourShares -= shares;
  state.walletUsdc += usdc;
  state.totalAssets -= usdc;
  state.shareSupply -= shares;
  $('#redeemAmt').value = 0;
  refreshVault();
  toast(`Redeemed ${fmtNum(shares)} shares → ${fmtUsd(usdc)}`);
}

function requestRedeem() {
  if (LIVE) return Live.requestRedeem();
  const shares = parseFloat($('#redeemAmt').value) || 0;
  if (shares <= 0) return toast('Enter shares to redeem');
  if (shares > state.yourShares) return toast('You don’t hold that many shares');
  state.yourShares -= shares;           // shares escrowed by the vault
  state.shareSupply -= shares;
  const id = queueId++;
  state.queue.push({ id, shares, status: 'pending' });
  $('#redeemAmt').value = 0;
  refreshVault();
  toast(`Redeem request #${id} queued — settling on Core…`);
  // simulate the async CoreWriter delay -> claimable
  setTimeout(() => {
    const q = state.queue.find((x) => x.id === id);
    if (q) { q.status = 'claimable'; renderQueue(); toast(`Request #${id} is claimable`); }
  }, 4000);
}

function claim(id) {
  if (LIVE) return Live.claim();
  const idx = state.queue.findIndex((x) => x.id === id);
  if (idx === -1) return;
  const q = state.queue[idx];
  const usdc = q.shares * state.navPerShare;
  state.walletUsdc += usdc;
  state.totalAssets -= usdc;
  state.queue.splice(idx, 1);
  refreshVault();
  toast(`Claimed ${fmtUsd(usdc)} from request #${id}`);
}

function wireVault() {
  $$('#actionSeg .seg__btn').forEach((b) =>
    b.addEventListener('click', () => {
      $$('#actionSeg .seg__btn').forEach((x) => x.classList.remove('is-active'));
      b.classList.add('is-active');
      $$('.action-pane').forEach((p) =>
        p.classList.toggle('is-hidden', p.dataset.pane !== b.dataset.action));
    }));
  $('#depositAmt').addEventListener('input', updateDepositPreview);
  $('#redeemAmt').addEventListener('input', updateRedeemPreview);
  $('[data-max="deposit"]').addEventListener('click', () => {
    $('#depositAmt').value = state.walletUsdc.toFixed(2); updateDepositPreview();
  });
  $('[data-max="redeem"]').addEventListener('click', () => {
    $('#redeemAmt').value = state.yourShares.toFixed(2); updateRedeemPreview();
  });
  $('#depositBtn').addEventListener('click', deposit);
  $('#redeemSyncBtn').addEventListener('click', redeemSync);
  $('#redeemAsyncBtn').addEventListener('click', requestRedeem);
}

// ═══════════════════════════════════════════════════════════
//  Admin panel
// ═══════════════════════════════════════════════════════════
function refreshAdmin() {
  $('#adminLock').classList.toggle('is-hidden', state.isManager);
  $('#adminBody').classList.toggle('is-hidden', !state.isManager);
  if (state.isManager) {
    $('#adminAssets').innerHTML = BASKET.assets.map((a) => `
      <li>
        <span>${a.company} <span class="coin">${a.coin}</span></span>
        <span class="w">${fmtPct(EQUAL_WEIGHT)}</span>
      </li>`).join('');
  }
}

function adminLog(msg, ok = false) {
  const log = $('#adminLog');
  const t = new Date().toLocaleTimeString('en-US', { hour12: false });
  const div = document.createElement('div');
  div.className = 'line';
  div.innerHTML = `<span class="t">[${t}]</span> <span class="${ok ? 'ok' : ''}">${msg}</span>`;
  log.prepend(div);
}

const ADMIN_ACTIONS = {
  discover: () => { adminLog('Discovering HIP-3 assets across perp dexes…'); setTimeout(() => adminLog(`Found ${N} matching coins on dex “${BASKET.dex}”.`, true), 700); },
  build:    () => { adminLog('Building equal-weight basket…'); setTimeout(() => adminLog('Saved config/sandick.basket.json (7 assets, 14.29% each).', true), 700); },
  submit:   () => { adminLog('Encoding submitBasket → CoreWriter…'); setTimeout(() => adminLog('Order intents sent. Poll reads to confirm fills.', true), 900); },
  rebalance:() => { adminLog('Computing deltas back to target weight…'); setTimeout(() => adminLog('Rebalance orders queued (reduce-only aware).', true), 900); },
  bridge:   () => { adminLog('Bridging USDC EVM ⇄ Core (spot ⇄ perp)…'); setTimeout(() => adminLog('Bridge submitted; settles over the next blocks.', true), 1000); },
};

function wireAdmin() {
  $('#adminUnlock').addEventListener('click', () => {
    if (LIVE) return Live.unlockAdmin();
    if (!state.connected) setConnected(true);
    state.isManager = true;
    refreshAdmin();
    toast('Manager access unlocked (demo)');
    adminLog('Authenticated as vault manager.', true);
  });
  $$('[data-admin]').forEach((b) =>
    b.addEventListener('click', () => {
      const action = b.dataset.admin;
      if (LIVE) return Live.admin(action);
      ADMIN_ACTIONS[action]?.();
    }));
}

// ═══════════════════════════════════════════════════════════
//  Live chain mode — wires the demo handlers to a deployed vault (chain.js)
// ═══════════════════════════════════════════════════════════
let chain = null;                       // SandickChain instance (lazy)
const dec = { asset: 6, share: 18 };    // token decimals, read on connect

const toNum = (bi, d) => Number(bi) / 10 ** d;       // raw units -> human (display)
const toUnits = (human, d) => chain.viem.parseUnits(String(human), d);  // human -> raw

const Live = {
  /** Import + connect clients once; reads token decimals. Safe to call repeatedly. */
  async ensure() {
    if (chain) return chain;
    const mod = await import('./chain.js');
    chain = await mod.SandickChain.connect(CHAIN_CFG);
    [dec.asset, dec.share] = await Promise.all([chain.usdcDecimals(), chain.shareDecimals()]);
    return chain;
  },

  async init() {
    try {
      await Live.ensure();
      if (chain.account) await Live._afterConnect(false);
    } catch (e) {
      toast('Chain unavailable — check config.js (' + e.message + ')');
    }
    refreshVault();
  },

  async connect() {
    try {
      await Live.ensure();
      if (!chain.account) return toast('No wallet found (install a browser wallet)');
      await Live._afterConnect(true);
    } catch (e) {
      toast('Connect failed: ' + e.message);
    }
  },

  async _afterConnect(announce) {
    state.connected = true;
    const a = chain.account;
    $('#connectBtn').classList.add('is-connected');
    $('#connectLabel').textContent = a.slice(0, 5) + '…' + a.slice(-4);
    if (announce) toast('Wallet connected');
    await Live.refreshVault();
  },

  async refreshVault() {
    if (!chain) return;
    try {
      const [assets, supply] = await Promise.all([chain.totalAssets(), chain.totalSupply()]);
      state.totalAssets = toNum(assets, dec.asset);
      state.shareSupply = toNum(supply, dec.share);
      state.navPerShare = state.shareSupply > 0 ? state.totalAssets / state.shareSupply : 1;

      if (chain.account) {
        const [bal, usdc, pending, claimable] = await Promise.all([
          chain.balanceOf(chain.account),
          chain.usdcBalance(),
          chain.pendingRedeemShares(chain.account),
          chain.claimableAssets(chain.account),
        ]);
        state.yourShares = toNum(bal, dec.share);
        state.walletUsdc = toNum(usdc, dec.asset);
        state.queue = Live._queue(pending, claimable);
      }
    } catch (e) {
      toast('Read failed: ' + e.message);
    }
    renderVault();
  },

  /** Map the contract's per-account pending/claimable into the demo queue shape. */
  _queue(pending, claimable) {
    const q = [];
    const p = toNum(pending, dec.share);
    const c = toNum(claimable, dec.asset);
    if (p > 0) q.push({ id: 'pending', shares: p, status: 'pending' });
    if (c > 0) q.push({ id: 'claim', shares: c / (state.navPerShare || 1), status: 'claimable' });
    return q;
  },

  async _send(label, fn, { coreAction = false } = {}) {
    try {
      const hash = await fn();
      toast(`${label} sent…`);
      await chain.publicClient.waitForTransactionReceipt({ hash });
      // CoreWriter actions settle on later blocks and can fail silently — the
      // receipt only proves the EVM call, so we re-read state to reflect reality.
      if (coreAction) toast(`${label} mined — confirm via reads (Core settles later)`);
      else toast(`${label} confirmed`);
      await Live.refreshVault();
      return hash;
    } catch (e) {
      toast(`${label} failed: ${e.shortMessage || e.message}`);
      throw e;
    }
  },

  async deposit() {
    const amt = parseFloat($('#depositAmt').value) || 0;
    if (amt <= 0) return toast('Enter a deposit amount');
    const units = toUnits(amt, dec.asset);
    const allowance = await chain.usdcAllowance();
    if (allowance < units) {
      await Live._send('Approve', () => chain.approveUsdc(units));
    }
    await Live._send('Deposit', () => chain.deposit(units));
  },

  async redeemSync() {
    const shares = parseFloat($('#redeemAmt').value) || 0;
    if (shares <= 0) return toast('Enter shares to redeem');
    await Live._send('Redeem', () => chain.redeem(toUnits(shares, dec.share)));
    $('#redeemAmt').value = 0;
  },

  async requestRedeem() {
    const shares = parseFloat($('#redeemAmt').value) || 0;
    if (shares <= 0) return toast('Enter shares to redeem');
    await Live._send('Redeem request', () => chain.requestRedeem(toUnits(shares, dec.share)));
    $('#redeemAmt').value = 0;
  },

  async claim() {
    await Live._send('Claim', () => chain.claim());
  },

  // ---- admin ----
  async unlockAdmin() {
    try {
      await Live.ensure();
      if (!chain.account) { await Live.connect(); }
      const [mgr, own] = await Promise.all([chain.isManager(), chain.isOwner()]);
      if (!mgr && !own) return toast('Connected wallet is not the manager or owner');
      state.isManager = true;
      refreshAdmin();
      adminLog(`Authenticated as ${mgr ? 'manager' : 'owner'} (${chain.account.slice(0, 6)}…).`, true);
      toast('Admin access unlocked');
    } catch (e) {
      toast('Unlock failed: ' + e.message);
    }
  },

  async admin(action) {
    try {
      if (action === 'discover' || action === 'build') {
        adminLog(`“${action}” is an off-chain step — run: python -m sandick.admin ${action === 'discover' ? 'discover' : 'build-basket …'}`);
        return;
      }
      if (action === 'submit' || action === 'rebalance') {
        // Order encoding (HIP-3 asset ids + 1e8 px/sz) is owned by the Python
        // planner; the chain hook is ready (chain.submitBasket(orders)).
        adminLog(`“${action}” needs encoded orders from the planner: ` +
          `python -m sandick.exec_cli run … then submit via chain.submitBasket(orders).`);
        return;
      }
      if (action === 'bridge') {
        const dir = (window.prompt('Bridge direction: type "toCore" or "fromCore"', 'toCore') || '').trim();
        if (dir !== 'toCore' && dir !== 'fromCore') return adminLog('Bridge cancelled.');
        const amt = parseFloat(window.prompt('Amount of USDC to bridge:', '0') || '0');
        if (!(amt > 0)) return adminLog('Bridge cancelled (no amount).');
        const units = toUnits(amt, dec.asset);
        adminLog(`Bridging ${amt} USDC ${dir}…`);
        await Live._send(`Bridge ${dir}`,
          () => (dir === 'toCore' ? chain.bridgeToCore(units) : chain.bridgeFromCore(units)),
          { coreAction: true });
        adminLog(`Bridge ${dir} submitted; settles over the next blocks.`, true);
      }
    } catch (e) {
      adminLog('Action failed: ' + (e.shortMessage || e.message));
    }
  },
};

// ═══════════════════════════════════════════════════════════
//  Init
// ═══════════════════════════════════════════════════════════
function init() {
  renderBasket();
  renderPriceInputs();
  wireCalc();
  wireConnect();
  wireVault();
  wireAdmin();
  runCalc();
  if (LIVE) {
    document.title = 'SANDICK — live (' + (CHAIN_CFG.explorer ? 'testnet' : 'chain ' + CHAIN_CFG.chainId) + ')';
    Live.init();
  } else {
    refreshVault();
  }
  refreshAdmin();
}
document.addEventListener('DOMContentLoaded', init);
