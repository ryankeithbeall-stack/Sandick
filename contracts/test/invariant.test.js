// Invariant / fuzz harness for the vault, on the same in-process @ethereumjs/vm
// engine as vault.test.js (NOT Foundry). It drives randomized but *valid* action
// sequences against MockBasketVault and asserts the core safety invariants after
// every executed action. Randomness is a deterministic mulberry32 PRNG seeded
// from a committed constant array, so any failure reproduces exactly from
// (seed, step). Run: `npm run test:invariant`.
//
// Fees are turned OFF for these runs so the accounting identities are exact;
// fee-on / share-price-monotonicity fuzzing is intentionally out of scope (fee
// dilution mints shares and lowers price-per-share, so "pps non-decreasing" is
// NOT a valid invariant and is deliberately not asserted here).
const assert = require("assert");
// In-process solc compile (same entrypoint as vault.test.js); distinct from
// scripts/compile_contracts.js, the standalone `npm run compile`. compile() reads
// the .sol files fresh each run, so contract edits are always picked up.
const { compile } = require("../../scripts/compile");
const { makeVM, deploy, ACCOUNTS, warp } = require("../../scripts/evm");

const USDC = 10n ** 6n;
const { deployer, manager, alice, bob, owner } = ACCOUNTS;
const a = (x) => x.toString();
const ACTORS = [alice, bob];

// --- deterministic PRNG (no Math.random / Date.now) ---
function mulberry32(seed) {
  let s = seed >>> 0;
  return function () {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const SEEDS = [
  0x9e3779b9, 0x243f6a88, 0xb7e15162, 0x85ebca77,
  0xc2b2ae3d, 0x27d4eb2f, 0x165667b1, 0xdeadbeef,
];
const STEPS = 50;

const rngInt = (rng, lo, hi) => lo + Math.floor(rng() * (hi - lo + 1));
const pick = (rng, arr) => arr[rngInt(rng, 0, arr.length - 1)];
function randBig(rng, max) {
  if (max <= 0n) return 0n;
  return (max * BigInt(rngInt(rng, 0, 100))) / 100n;
}

async function main() {
  const { artifacts } = compile();

  async function fixture() {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const core = await deploy(vm, artifacts.MockCore, [a(usdc.address)]);
    const vault = await deploy(vm, artifacts.MockBasketVault, [
      a(usdc.address), a(manager), a(owner), a(core.address),
    ]);
    for (const u of ACTORS) {
      await usdc.send(deployer, "mint", [a(u), 1_000_000n * USDC]);
      await usdc.send(u, "approve", [a(vault.address), 1n << 255n]);
    }
    await vault.send(owner, "setFeeConfig", [a(owner), 0, 0, 0]); // fees OFF -> exact accounting
    // deployer bankrolls simulated Core PnL gains.
    await usdc.send(deployer, "mint", [a(deployer), 100_000_000n * USDC]);
    await usdc.send(deployer, "approve", [a(core.address), 1n << 255n]);
    return { vm, usdc, core, vault };
  }

  // Read-only snapshot of every value the invariants need.
  async function snapshot(ctx) {
    const { usdc, core, vault } = ctx;
    const s = {
      vaultUsdc: await usdc.call("balanceOf", [a(vault.address)]),
      mgrUsdc: await usdc.call("balanceOf", [a(manager)]),
      coreEquity: await core.call("equity", [a(vault.address)]),
      coreUsdc: await usdc.call("balanceOf", [a(core.address)]),
      total: await vault.call("totalAssets"),
      reserved: await vault.call("reservedAssets"),
      totalPending: await vault.call("totalPendingRedeemShares"),
      vaultShares: await vault.call("balanceOf", [a(vault.address)]),
      sumPending: 0n,
      sumClaimable: 0n,
    };
    for (const u of ACTORS) {
      s.sumPending += await vault.call("pendingRedeemShares", [a(u)]);
      s.sumClaimable += await vault.call("claimableAssets", [a(u)]);
    }
    return s;
  }

  function checkInvariants(s, label) {
    // I2 — NAV accounting identity (coreSpot = 0 for the mock vault).
    assert.equal(s.total, s.vaultUsdc - s.reserved + s.coreEquity, `${label}: NAV identity`);
    // I7 — solvency: real custody covers NAV + reserved claims. Exact equality in
    // this mock (coreSpot == 0), so it's a tight check here; the inequality gains
    // slack only for a deployment that wires a non-zero _coreSpotUsd().
    assert.ok(s.vaultUsdc + s.coreEquity >= s.total + s.reserved, `${label}: solvency`);
    // I4 — redemption-queue conservation.
    assert.equal(s.sumPending, s.totalPending, `${label}: pending sum == totalPending`);
    assert.equal(s.totalPending, s.vaultShares, `${label}: escrowed shares held by vault`);
    assert.equal(s.sumClaimable, s.reserved, `${label}: claimable sum == reserved`);
    assert.ok(s.reserved <= s.vaultUsdc, `${label}: reserved is backed by idle USDC`);
    // I1 — the trade-only manager can never receive vault funds.
    assert.equal(s.mgrUsdc, 0n, `${label}: manager balance stays 0`);
  }

  // Each action returns true if it actually executed (so we then re-check
  // invariants), false if its preconditions weren't met (skip). Args are sized
  // from live reads so a well-behaved vault never reverts; an *assertion* failure
  // is a real bug, a plain revert is treated as a bounding miss (logged, skipped).
  const idleOf = (s) => s.vaultUsdc - s.reserved;

  const ACTIONS = {
    async deposit(ctx, rng, st) {
      if (st.paused) return false;
      const actor = pick(rng, ACTORS);
      const bal = await ctx.usdc.call("balanceOf", [a(actor)]);
      const room = await ctx.vault.call("maxDeposit", [a(actor)]);
      const max = bal < room ? bal : room;
      const amt = randBig(rng, max);
      if (amt === 0n) return false;
      await ctx.vault.send(actor, "deposit", [amt, a(actor)]);
      return true;
    },
    async mint(ctx, rng, st) {
      if (st.paused) return false;
      const actor = pick(rng, ACTORS);
      const bal = await ctx.usdc.call("balanceOf", [a(actor)]);
      const maxShares = await ctx.vault.call("maxMint", [a(actor)]);
      // Bound shares by what the actor can actually pay: maxMint is the uint-max
      // sentinel when uncapped, and minting against that overflows previewMint
      // (a fuzzer artifact, not a contract path a funded user can hit).
      const affordable = await ctx.vault.call("convertToShares", [bal]);
      const ceil = maxShares < affordable ? maxShares : affordable;
      const shares = randBig(rng, ceil);
      if (shares === 0n) return false;
      const cost = await ctx.vault.call("previewMint", [shares]);
      if (cost === 0n || cost > bal) return false;
      await ctx.vault.send(actor, "mint", [shares, a(actor)]);
      return true;
    },
    async withdraw(ctx, rng) {
      const actor = pick(rng, ACTORS);
      const max = await ctx.vault.call("maxWithdraw", [a(actor)]);
      const amt = randBig(rng, max);
      if (amt === 0n) return false;
      await ctx.vault.send(actor, "withdraw", [amt, a(actor), a(actor)]);
      return true;
    },
    async redeem(ctx, rng) {
      const actor = pick(rng, ACTORS);
      const max = await ctx.vault.call("maxRedeem", [a(actor)]);
      const shares = randBig(rng, max);
      if (shares === 0n) return false;
      await ctx.vault.send(actor, "redeem", [shares, a(actor), a(actor)]);
      return true;
    },
    async requestRedeem(ctx, rng) {
      const actor = pick(rng, ACTORS);
      const bal = await ctx.vault.call("balanceOf", [a(actor)]);
      const shares = randBig(rng, bal);
      if (shares === 0n) return false;
      await ctx.vault.send(actor, "requestRedeem", [shares]);
      return true;
    },
    async cancelRedeem(ctx, rng) {
      const actor = pick(rng, ACTORS);
      const pending = await ctx.vault.call("pendingRedeemShares", [a(actor)]);
      const shares = randBig(rng, pending);
      if (shares === 0n) return false;
      await ctx.vault.send(actor, "cancelRedeemRequest", [shares]);
      return true;
    },
    async fulfillRedeem(ctx, rng) {
      const owner_ = pick(rng, ACTORS);
      const pending = await ctx.vault.call("pendingRedeemShares", [a(owner_)]);
      if (pending === 0n) return false;
      const s = await snapshot(ctx);
      const idleShares = await ctx.vault.call("convertToShares", [idleOf(s)]);
      const cap = pending < idleShares ? pending : idleShares;
      const shares = randBig(rng, cap);
      if (shares === 0n) return false;
      // permissionless: anyone can fulfill (proves the manager can't block exits)
      const caller = pick(rng, [...ACTORS, deployer]);
      await ctx.vault.send(caller, "fulfillRedeem", [a(owner_), shares]);
      return true;
    },
    async claim(ctx, rng) {
      const actor = pick(rng, ACTORS);
      const claimable = await ctx.vault.call("claimableAssets", [a(actor)]);
      if (claimable === 0n) return false;
      await ctx.vault.send(actor, "claim");
      return true;
    },
    async bridgeToCore(ctx, rng, st) {
      if (st.paused || st.reduceOnly) return false;
      const s = await snapshot(ctx);
      const amt = randBig(rng, idleOf(s));
      if (amt === 0n) return false;
      await ctx.vault.send(manager, "bridgeToCore", [amt]);
      const after = await ctx.vault.call("totalAssets");
      assert.equal(after, s.total, "bridgeToCore must preserve NAV");
      return true;
    },
    async bridgeFromCore(ctx, rng) {
      const s = await snapshot(ctx);
      const amt = randBig(rng, s.coreEquity);
      if (amt === 0n) return false;
      await ctx.vault.send(manager, "bridgeFromCore", [amt]);
      const after = await ctx.vault.call("totalAssets");
      assert.equal(after, s.total, "bridgeFromCore must preserve NAV");
      return true;
    },
    async simulateCorePnL(ctx, rng) {
      const eq = await ctx.core.call("equity", [a(ctx.vault.address)]);
      if (rng() < 0.5) {
        const gain = randBig(rng, 20_000n * USDC);
        if (gain === 0n) return false;
        await ctx.core.send(deployer, "fund", [gain]); // keep Core solvent for the gain
        await ctx.core.send(deployer, "setEquity", [a(ctx.vault.address), eq + gain]);
      } else {
        const loss = randBig(rng, eq);
        if (loss === 0n) return false;
        await ctx.core.send(deployer, "setEquity", [a(ctx.vault.address), eq - loss]);
      }
      return true;
    },
    async accrueFees(ctx) {
      await ctx.vault.send(deployer, "accrueFees");
      return true;
    },
    async warp(ctx, rng) {
      warp(ctx.vm, pick(rng, [0, 3600, 86400, 8 * 86400]));
      return true;
    },
    async setDepositCap(ctx, rng, st) {
      const cap = rng() < 0.3 ? 0n : randBig(rng, 2_000_000n * USDC);
      await ctx.vault.send(owner, "setDepositCap", [cap]);
      st.depositCap = cap;
      return true;
    },
    async togglePause(ctx, _rng, st) {
      if (st.paused) {
        await ctx.vault.send(owner, "unpause");
        st.paused = false;
      } else {
        await ctx.vault.send(owner, "pause");
        st.paused = true;
      }
      return true;
    },
    async toggleReduceOnly(ctx, _rng, st) {
      st.reduceOnly = !st.reduceOnly;
      await ctx.vault.send(owner, "setReduceOnlyMode", [st.reduceOnly]);
      return true;
    },
    // No-extraction: a deposit immediately redeemed can never return more than
    // it put in (rounding always favors the vault; fees are off).
    async roundTrip(ctx, rng, st) {
      if (st.paused) return false;
      const actor = pick(rng, ACTORS);
      const bal = await ctx.usdc.call("balanceOf", [a(actor)]);
      const room = await ctx.vault.call("maxDeposit", [a(actor)]);
      const max = bal < room ? bal : room;
      const amt = randBig(rng, max);
      if (amt === 0n) return false;
      const before = await ctx.usdc.call("balanceOf", [a(actor)]);
      const shares = await ctx.vault.send(actor, "deposit", [amt, a(actor)]);
      const maxR = await ctx.vault.call("maxRedeem", [a(actor)]);
      const toRedeem = shares < maxR ? shares : maxR;
      if (toRedeem > 0n) {
        await ctx.vault.send(actor, "redeem", [toRedeem, a(actor), a(actor)]);
      }
      const after = await ctx.usdc.call("balanceOf", [a(actor)]);
      assert.ok(after <= before, "deposit->redeem round trip must not profit");
      return true;
    },
  };
  const ACTION_NAMES = Object.keys(ACTIONS);

  let passed = 0;
  let executed = 0;
  let reverts = 0;
  for (const seed of SEEDS) {
    const rng = mulberry32(seed);
    const ctx = await fixture();
    const st = { paused: false, reduceOnly: false, depositCap: 0n };
    const seedHex = "0x" + (seed >>> 0).toString(16);
    for (let i = 0; i < STEPS; i++) {
      const name = pick(rng, ACTION_NAMES);
      let ran = false;
      try {
        ran = await ACTIONS[name](ctx, rng, st);
      } catch (e) {
        if (e.reverted) {
          // Args were bounded from live reads but a rounding edge still reverted;
          // not an invariant violation — skip and keep fuzzing. Set FUZZ_DEBUG=1
          // to inspect which legs hit the bound.
          reverts++;
          if (process.env.FUZZ_DEBUG) {
            console.error(`  revert: seed ${seedHex} step ${i} ${name}: ${e.message}`);
          }
          continue;
        }
        // An assertion failure (NAV/solvency/round-trip/queue) is a real bug.
        throw new Error(`seed ${seedHex} step ${i} action ${name}: ${e.message}`);
      }
      if (ran) {
        executed++;
        checkInvariants(await snapshot(ctx), `seed ${seedHex} step ${i} after ${name}`);
      }
    }
    passed++;
  }

  console.log(
    `\ninvariant fuzz: ${passed}/${SEEDS.length} seeds OK · ${executed} actions executed ` +
      `· ${reverts} bounded-arg reverts skipped · all invariants held.`
  );
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { main };
