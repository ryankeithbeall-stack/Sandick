// In-process EVM tests for the vault trust/accounting logic.
// Runs the actual compiled bytecode on @ethereumjs/vm — exercises shares, NAV,
// withdrawal caps, and the trade-only manager restrictions.
const assert = require("assert");
const { ethers } = require("ethers");
const { compile } = require("../../scripts/compile");
const { makeVM, deploy, at, etch, ACCOUNTS, warp } = require("../../scripts/evm");

const SEVEN_DAYS = 7n * 24n * 60n * 60n;

const USDC = 10n ** 6n;
const { deployer, manager, alice, bob, owner } = ACCOUNTS;
const a = (x) => x.toString(); // address hex

// CoreWriter system contract address (HyperCoreActions.CORE_WRITER). NOTE: this
// is byte-identical to ACCOUNTS.alice (0x33..33), so the CoreWriter fixture must
// NOT use alice as an actor or system address — it uses bob + a distinct 0x66.. .
const CORE_WRITER = "0x" + "33".repeat(20);
const USDC_SYS = "0x" + "66".repeat(20); // dummy USDC system address (bridge sink)
const _abi = ethers.AbiCoder.defaultAbiCoder();

// Split a CoreWriter payload (version | uint24 actionId | abi.encode(args)).
function decodeAction(dataHex) {
  const hex = dataHex.startsWith("0x") ? dataHex.slice(2) : dataHex;
  return {
    version: parseInt(hex.slice(0, 2), 16),
    actionId: parseInt(hex.slice(2, 8), 16),
    args: "0x" + hex.slice(8),
  };
}

let passed = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`  ok  ${name}`);
    passed++;
  } catch (e) {
    console.error(`FAIL  ${name}\n      ${e.message}`);
    process.exitCode = 1;
  }
}

async function main() {
  const { artifacts } = compile();

  async function fixture() {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const core = await deploy(vm, artifacts.MockCore, [a(usdc.address)]);
    const vault = await deploy(vm, artifacts.MockBasketVault, [
      a(usdc.address),
      a(manager),
      a(owner),
      a(core.address),
    ]);
    for (const u of [alice, bob]) {
      await usdc.send(deployer, "mint", [a(u), 1_000_000n * USDC]);
      await usdc.send(u, "approve", [a(vault.address), (1n << 255n)]);
    }
    // Base accounting tests run with fees OFF; dedicated fee tests re-enable them.
    await vault.send(owner, "setFeeConfig", [a(owner), 0, 0, 0]);
    return { vm, usdc, core, vault };
  }

  await test("deposit mints shares and is redeemable 1:1 before PnL", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    assert.equal(await vault.call("totalAssets"), 1000n * USDC);
    const shares = await vault.call("balanceOf", [a(alice)]);
    assert.equal(await vault.call("previewRedeem", [shares]), 1000n * USDC);
  });

  await test("two depositors get proportional shares", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(bob, "deposit", [3000n * USDC, a(bob)]);
    const sa = await vault.call("balanceOf", [a(alice)]);
    const sb = await vault.call("balanceOf", [a(bob)]);
    assert.equal(sb, sa * 3n);
  });

  await test("bridgeToCore preserves NAV; withdraw caps to idle liquidity", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [900n * USDC]);
    assert.equal(await vault.call("totalAssets"), 1000n * USDC);
    assert.equal(await vault.call("maxWithdraw", [a(alice)]), 100n * USDC);
  });

  await test("PnL on Core raises share price; redeemer collects gains", async () => {
    const { usdc, core, vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [1000n * USDC]);
    await core.send(deployer, "setEquity", [a(vault.address), 1200n * USDC]);
    await usdc.send(deployer, "mint", [a(deployer), 200n * USDC]);
    await usdc.send(deployer, "approve", [a(core.address), 200n * USDC]);
    await core.send(deployer, "fund", [200n * USDC]);
    assert.equal(await vault.call("totalAssets"), 1200n * USDC);
    await vault.send(manager, "bridgeFromCore", [1200n * USDC]);
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "redeem", [shares, a(alice), a(alice)]);
    // ERC-4626 virtual shares round redemptions down in the vault's favor, so
    // the depositor receives the ~$200 gain minus at most a few wei.
    const bal = await usdc.call("balanceOf", [a(alice)]);
    const ideal = 1_000_200n * USDC;
    assert.ok(bal <= ideal && ideal - bal <= 10n, `got ${bal}, ideal ${ideal}`);
  });

  await test("only manager can trade; non-manager reverts", async () => {
    const { vault } = await fixture();
    await assert.rejects(() => vault.send(alice, "bridgeToCore", [1n]));
    await assert.rejects(() =>
      vault.send(alice, "submitBasket", [[[1, true, 100n, 100n, false]]])
    );
  });

  await test("manager can only trade allow-listed assets", async () => {
    const { vault } = await fixture();
    const order = [7, true, 5_000_000_000n, 100_000_000n, false];
    await assert.rejects(() => vault.send(manager, "submitBasket", [[order]]));
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await vault.send(manager, "submitBasket", [[order]]);
    assert.equal(await vault.call("submittedCount"), 1n);
  });

  await test("owner can rotate the manager; trading rights follow", async () => {
    const { vault } = await fixture();
    assert.equal(a(await vault.call("manager")), a(manager));

    // only the owner may rotate, and never to the zero address
    const ZERO = "0x" + "00".repeat(20);
    await assert.rejects(() => vault.send(alice, "setManager", [a(bob)]));
    await assert.rejects(() => vault.send(owner, "setManager", [ZERO]));

    await vault.send(owner, "setManager", [a(bob)]);
    assert.equal(a(await vault.call("manager")), a(bob));

    // trading rights move with the role: new manager in, old manager out
    const order = [7, true, 5_000_000_000n, 100_000_000n, false];
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await assert.rejects(() => vault.send(manager, "submitBasket", [[order]]));
    await vault.send(bob, "submitBasket", [[order]]);
    assert.equal(await vault.call("submittedCount"), 1n);
  });

  await test("async redeem: request -> bridge -> fulfill (permissionless) -> claim", async () => {
    const { usdc, vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [1000n * USDC]); // no idle liquidity
    const shares = await vault.call("balanceOf", [a(alice)]);

    // escrow the shares
    await vault.send(alice, "requestRedeem", [shares]);
    assert.equal(await vault.call("balanceOf", [a(alice)]), 0n);
    assert.equal(await vault.call("pendingRedeemShares", [a(alice)]), shares);

    // can't fulfill without idle liquidity
    await assert.rejects(() => vault.send(bob, "fulfillRedeem", [a(alice), shares]));

    // manager brings funds back; now anyone can fulfill
    await vault.send(manager, "bridgeFromCore", [1000n * USDC]);
    await vault.send(bob, "fulfillRedeem", [a(alice), shares]); // permissionless
    assert.equal(await vault.call("claimableAssets", [a(alice)]), 1000n * USDC);
    assert.equal(await vault.call("totalAssets"), 0n); // reserved excluded from NAV

    // claim pays out
    await vault.send(alice, "claim");
    assert.equal(await usdc.call("balanceOf", [a(alice)]), 1_000_000n * USDC);
    assert.equal(await vault.call("reservedAssets"), 0n);
  });

  await test("async redeem: cancel returns escrowed shares", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "requestRedeem", [shares]);
    await vault.send(alice, "cancelRedeemRequest", [shares]);
    assert.equal(await vault.call("balanceOf", [a(alice)]), shares);
    assert.equal(await vault.call("pendingRedeemShares", [a(alice)]), 0n);
  });

  await test("reserved assets are protected from sync withdrawals", async () => {
    const { usdc, vault } = await fixture();
    // alice deposits and queues a full redemption, fulfilled from idle
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    const aShares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "requestRedeem", [aShares]);
    await vault.send(bob, "fulfillRedeem", [a(alice), aShares]); // reserves alice's 1000
    // bob deposits fresh; his sync withdrawal must not dip into alice's reserve
    await vault.send(bob, "deposit", [500n * USDC, a(bob)]);
    assert.equal(await vault.call("maxWithdraw", [a(bob)]), 500n * USDC);
    // contract holds 1500 USDC but 1000 is reserved
    assert.equal(await usdc.call("balanceOf", [a(vault.address)]), 1500n * USDC);
    assert.equal(await vault.call("totalAssets"), 500n * USDC);
  });

  await test("HyperCoreReader returns accountValue (clamping negatives)", async () => {
    const vm = await makeVM();
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(sb.address), 0, 100]);
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 0n);
    await ms.send(deployer, "setAccountValue", [1234n * USDC]);
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 1234n * USDC);
    await ms.send(deployer, "setAccountValue", [-5n * USDC]); // underwater -> 0
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 0n);
  });

  await test("HyperCoreReader spotBalanceUsd scales spot-wei (8dp) down to asset 6dp", async () => {
    const vm = await makeVM();
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(sb.address), 0, 100]);
    assert.equal(await reader.call("spotBalanceUsd", [a(alice)]), 0n);   // empty spot account
    // 300 USDC parked in spot, expressed in HyperCore 8dp wei (300 * 1e8 = 3e10)
    await sb.send(deployer, "setTotal", [300n * 100n * USDC]);
    assert.equal(await reader.call("spotBalanceUsd", [a(alice)]), 300n * USDC); // -> 6dp
    // Sub-divisor dust truncates toward zero (conservative for NAV): 150 wei -> 1.
    await sb.send(deployer, "setTotal", [150n]);
    assert.equal(await reader.call("spotBalanceUsd", [a(alice)]), 1n);
  });

  await test("reader reverts (never silently 0) on a short/failed precompile read", async () => {
    const vm = await makeVM();
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    // Point each precompile at an address with NO code: the staticcall succeeds
    // but returns 0 bytes (< the required 96/128), which must REVERT — a silent 0
    // would misprice NAV. (bob is a plain EOA, no contract code.)
    const spotBad = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(bob), 0, 100]);
    await assert.rejects(spotBad.call("spotBalanceUsd", [a(alice)]));
    const marginBad = await deploy(vm, artifacts.HyperCoreReader, [a(bob), 0, a(sb.address), 0, 100]);
    await assert.rejects(marginBad.call("accountEquityUsd", [a(alice)]));
  });

  await test("production BasketVault NAV = idle + perp equity + spot balance", async () => {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(sb.address), 0, 100]);
    // BasketVault now takes a single VaultParams struct (one tuple arg).
    const vault = await deploy(vm, artifacts.BasketVault, [[
      a(usdc.address), "SANDICK Vault", "sSANDICK", a(manager), a(owner), a(reader.address),
      a(alice) /*dummy usdc system addr*/, 1, 1, 3,
      a(owner) /*protocolAdmin*/, a(owner) /*protocolTreasury*/, 0 /*protocolFeeBps*/,
    ]]);
    await usdc.send(deployer, "mint", [a(alice), 1_000_000n * USDC]);
    await usdc.send(alice, "approve", [a(vault.address), 1n << 255n]);
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]); // idle 1000, equity 0, spot 0
    assert.equal(await vault.call("totalAssets"), 1000n * USDC);
    await ms.send(deployer, "setAccountValue", [500n * USDC]); // simulate Core perp equity
    assert.equal(await vault.call("totalAssets"), 1500n * USDC);
    await sb.send(deployer, "setTotal", [200n * 100n * USDC]); // 200 USDC parked in spot (8dp)
    assert.equal(await vault.call("totalAssets"), 1700n * USDC); // idle 1000 + perp 500 + spot 200
  });

  await test("pause blocks deposits and trading but never blocks exits", async () => {
    const { usdc, vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(owner, "setAllowedAsset", [7, true]);

    // owner pauses
    await vault.send(owner, "pause");
    assert.equal(await vault.call("paused"), true);

    // deposits + manager trading are blocked while paused
    await assert.rejects(() => vault.send(bob, "deposit", [100n * USDC, a(bob)]));
    await assert.rejects(() => vault.send(manager, "bridgeToCore", [100n * USDC]));
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, true, 100n, 100n, false]]])
    );

    // exits stay open: alice can still withdraw her funds
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "redeem", [shares, a(alice), a(alice)]);
    assert.equal(await usdc.call("balanceOf", [a(alice)]), 1_000_000n * USDC);

    // non-owner cannot pause/unpause; owner can resume
    await assert.rejects(() => vault.send(alice, "unpause"));
    await vault.send(owner, "unpause");
    assert.equal(await vault.call("paused"), false);
    await vault.send(bob, "deposit", [100n * USDC, a(bob)]); // works again
  });

  await test("per-order notional cap rejects oversized legs", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setAllowedAsset", [7, true]);
    // cap a single leg's raw notional (limitPx * sz) at 1e12
    await vault.send(owner, "setOrderCaps", [1_000_000_000_000n, 0n, 0n]);

    // 200 * 100 = 20_000 <= cap -> ok
    await vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]]);
    assert.equal(await vault.call("submittedCount"), 1n);

    // 2e6 * 1e6 = 2e12 > cap -> revert, count unchanged
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, true, 2_000_000n, 1_000_000n, false]]])
    );
    assert.equal(await vault.call("submittedCount"), 1n);
  });

  await test("per-epoch notional cap accumulates and resets", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setAllowedAsset", [7, true]);
    // no per-order cap; epoch cap 1500 over a 1000s window
    await vault.send(owner, "setOrderCaps", [0n, 1500n, 1000n]);

    await vault.send(manager, "submitBasket", [[[7, true, 100n, 10n, false]]]); // 1000 used
    assert.equal(await vault.call("epochNotionalUsed"), 1000n);

    // another 1000 would total 2000 > 1500 -> revert
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, true, 100n, 10n, false]]])
    );
    // a 400 leg fits (1400 <= 1500)
    await vault.send(manager, "submitBasket", [[[7, true, 100n, 4n, false]]]);
    assert.equal(await vault.call("epochNotionalUsed"), 1400n);
  });

  // Set up a vault with a redemption deficit: most funds on Core, a queued
  // redemption that idle liquidity can't cover. Returns alice's escrowed shares.
  async function deficitFixture() {
    const f = await fixture();
    const { vault } = f;
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [950n * USDC]); // idle 50, core 950
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "requestRedeem", [shares]); // escrow -> deficit ~950
    return { ...f, shares };
  }

  await test("setDepositCap is owner-only", async () => {
    const { vault } = await fixture();
    await assert.rejects(vault.send(manager, "setDepositCap", [1000n * USDC]));
    await assert.rejects(vault.send(alice, "setDepositCap", [1000n * USDC]));
    await vault.send(owner, "setDepositCap", [1000n * USDC]);
    assert.equal(await vault.call("depositCap"), 1000n * USDC);
  });

  await test("deposit cap limits deposits and reflects remaining room", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setDepositCap", [1000n * USDC]);
    assert.equal(await vault.call("maxDeposit", [a(alice)]), 1000n * USDC);
    await vault.send(alice, "deposit", [600n * USDC, a(alice)]);
    assert.equal(await vault.call("maxDeposit", [a(alice)]), 400n * USDC); // room shrinks
    await assert.rejects(vault.send(bob, "deposit", [500n * USDC, a(bob)])); // > room
    await vault.send(bob, "deposit", [400n * USDC, a(bob)]);                 // exactly fills
    assert.equal(await vault.call("totalAssets"), 1000n * USDC);
    assert.equal(await vault.call("maxDeposit", [a(alice)]), 0n);           // full
    await assert.rejects(vault.send(alice, "deposit", [1n, a(alice)]));     // nothing more
  });

  await test("deposit cap limits mint too (maxMint mirrors maxDeposit)", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setDepositCap", [500n * USDC]);
    const maxMint = await vault.call("maxMint", [a(alice)]);
    await assert.rejects(vault.send(alice, "mint", [maxMint + 1n, a(alice)])); // over room
    await vault.send(alice, "mint", [maxMint, a(alice)]);                      // fills to cap
    assert.ok((await vault.call("totalAssets")) <= 500n * USDC);
  });

  await test("deposit cap = 0 means uncapped", async () => {
    const { vault } = await fixture();
    assert.equal(await vault.call("depositCap"), 0n); // default
    await vault.send(alice, "deposit", [900_000n * USDC, a(alice)]); // large, allowed
    assert.equal(await vault.call("totalAssets"), 900_000n * USDC);
  });

  await test("deposit cap never blocks exits", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(owner, "setDepositCap", [1n]); // cap now far below NAV
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "redeem", [shares, a(alice), a(alice)]); // still exits
    assert.equal(await vault.call("balanceOf", [a(alice)]), 0n);
  });

  await test("redemption backstop is shut while the manager is active", async () => {
    const { vault } = await deficitFixture();
    assert.equal(await vault.call("managerIsDark"), false);
    const deficit = await vault.call("redemptionDeficit");
    assert.ok(deficit > 0n, `expected a deficit, got ${deficit}`);
    // non-manager can't force a bridge yet
    await assert.rejects(() => vault.send(bob, "bridgeFromCoreForRedemptions", [deficit]));
  });

  await test("redemption backstop opens after manager timeout; exit completes", async () => {
    const { usdc, vault, shares } = await deficitFixture();
    warp(vault.vm, SEVEN_DAYS + 1n);
    assert.equal(await vault.call("managerIsDark"), true);

    const deficit = await vault.call("redemptionDeficit");
    // anyone (bob) can now pull exactly the owed USDC back from Core
    await vault.send(bob, "bridgeFromCoreForRedemptions", [deficit]);

    // queued redemption can now be fulfilled (permissionless) and claimed
    await vault.send(bob, "fulfillRedeem", [a(alice), shares]);
    const before = await usdc.call("balanceOf", [a(alice)]);
    await vault.send(alice, "claim");
    const after = await usdc.call("balanceOf", [a(alice)]);
    assert.ok(after - before >= 999n * USDC, `alice recovered ${after - before}`);
  });

  await test("redemption backstop can never pull more than is owed", async () => {
    const { vault } = await deficitFixture();
    warp(vault.vm, SEVEN_DAYS + 1n);
    const deficit = await vault.call("redemptionDeficit");
    await assert.rejects(() =>
      vault.send(bob, "bridgeFromCoreForRedemptions", [deficit + 1n])
    );
    // zero amount is rejected too
    await assert.rejects(() => vault.send(bob, "bridgeFromCoreForRedemptions", [0n]));
  });

  await test("manager activity resets the backstop countdown", async () => {
    const { vault } = await deficitFixture();
    warp(vault.vm, SEVEN_DAYS - 100n);          // almost dark
    await vault.send(manager, "bridgeToCore", [1n * USDC]); // a heartbeat
    warp(vault.vm, 200n);                        // past the original deadline
    assert.equal(await vault.call("managerIsDark"), false);
    const deficit = await vault.call("redemptionDeficit");
    await assert.rejects(() => vault.send(bob, "bridgeFromCoreForRedemptions", [deficit]));
  });

  await test("owner can disable the redemption backstop", async () => {
    const { vault } = await deficitFixture();
    await vault.send(owner, "setManagerTimeout", [0n]);
    warp(vault.vm, 100n * SEVEN_DAYS);
    assert.equal(await vault.call("managerIsDark"), false);
    const deficit = await vault.call("redemptionDeficit");
    await assert.rejects(() => vault.send(bob, "bridgeFromCoreForRedemptions", [deficit]));
  });

  const YEAR = 365n * 24n * 60n * 60n;
  // Value of an account's shares, in USDC.
  const shareValue = (vault, who) =>
    vault.call("balanceOf", [a(who)]).then((s) => vault.call("convertToAssets", [s]));

  await test("fee defaults are set at deployment", async () => {
    const { usdc } = await fixture();
    const core = await deploy(usdc.vm, artifacts.MockCore, [a(usdc.address)]);
    const v = await deploy(usdc.vm, artifacts.MockBasketVault, [
      a(usdc.address), a(manager), a(owner), a(core.address),
    ]);
    assert.equal(await v.call("managementFeeBps"), 200n);
    assert.equal(await v.call("performanceFeeBps"), 1000n);
    assert.equal(await v.call("exitFeeBps"), 10n);
    assert.equal(a(await v.call("feeRecipient")), a(owner));
  });

  await test("fee config is owner-only and capped", async () => {
    const { vault } = await fixture();
    await assert.rejects(() => vault.send(alice, "setFeeConfig", [a(owner), 100, 100, 1]));
    await assert.rejects(() => vault.send(owner, "setFeeConfig", [a(owner), 600, 0, 0]));   // mgmt > 5%
    await assert.rejects(() => vault.send(owner, "setFeeConfig", [a(owner), 0, 4000, 0]));  // perf > 30%
    await assert.rejects(() => vault.send(owner, "setFeeConfig", [a(owner), 0, 0, 200]));   // exit > 1%
    const ZERO = "0x" + "00".repeat(20);
    await assert.rejects(() => vault.send(owner, "setFeeConfig", [ZERO, 0, 0, 0]));
  });

  await test("management fee accrues over time as dilution shares", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setFeeConfig", [a(owner), 200, 0, 0]); // 2%/yr, mgmt only
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);

    warp(vault.vm, YEAR);
    await vault.send(owner, "accrueFees", []);

    // ~2% of the $1000 NAV is now owned by the treasury (owner), the rest alice's.
    const treasury = await shareValue(vault, owner);
    const aliceVal = await shareValue(vault, alice);
    assert.ok(treasury >= 198n * USDC / 10n && treasury <= 202n * USDC / 10n, `treasury ${treasury}`);
    assert.ok(aliceVal >= 979n * USDC && aliceVal <= 981n * USDC, `alice ${aliceVal}`);
  });

  await test("performance fee charges only gains above the high-water mark", async () => {
    const { core, vault } = await fixture();
    await vault.send(owner, "setFeeConfig", [a(owner), 0, 1000, 0]); // 10% perf only
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [1000n * USDC]);
    await vault.send(owner, "accrueFees", []); // sets HWM at the $1000 baseline

    // +$200 of PnL on Core -> share price makes a new high
    await core.send(deployer, "setEquity", [a(vault.address), 1200n * USDC]);
    await vault.send(owner, "accrueFees", []);
    const treasury1 = await shareValue(vault, owner);
    assert.ok(treasury1 >= 199n * USDC / 10n && treasury1 <= 201n * USDC / 10n, `perf fee ${treasury1}`);

    // accruing again with no new high charges nothing more
    await vault.send(owner, "accrueFees", []);
    const treasury2 = await shareValue(vault, owner);
    assert.ok(treasury2 <= treasury1 + USDC / 100n, `no double charge: ${treasury1} -> ${treasury2}`);
  });

  await test("exit fee is retained in the vault for remaining holders", async () => {
    const { usdc, vault } = await fixture();
    await vault.send(owner, "setFeeConfig", [a(owner), 0, 0, 10]); // 0.1% exit only
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(bob, "deposit", [1000n * USDC, a(bob)]);

    const before = await usdc.call("balanceOf", [a(alice)]);
    const shares = await vault.call("balanceOf", [a(alice)]);
    await vault.send(alice, "redeem", [shares, a(alice), a(alice)]);
    const got = (await usdc.call("balanceOf", [a(alice)])) - before;

    // alice paid ~0.1% to exit; bob (the remaining holder) is now worth >$1000
    assert.ok(got < 1000n * USDC && got >= 9985n * USDC / 10n, `alice got ${got}`);
    const bobVal = await shareValue(vault, bob);
    assert.ok(bobVal > 1000n * USDC, `bob should gain the exit fee, got ${bobVal}`);
  });

  // --- Platform fee (the protocol's cut of every vault) ---

  await test("platform fee streams to the protocol treasury", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setFeeConfig", [a(owner), 0, 0, 0]); // operator fees off
    await vault.send(owner, "setProtocolFeeConfig", [a(bob), 100]); // 1%/yr platform fee -> bob
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);

    warp(vault.vm, YEAR);
    await vault.send(owner, "accrueFees", []);

    // ~1% of the $1000 NAV is now owned by the protocol treasury (bob).
    const treasury = await shareValue(vault, bob);
    assert.ok(treasury >= 99n * USDC / 10n && treasury <= 101n * USDC / 10n, `platform fee ${treasury}`);
    const aliceVal = await shareValue(vault, alice);
    assert.ok(aliceVal >= 989n * USDC && aliceVal <= 991n * USDC, `alice ${aliceVal}`);
  });

  await test("operator and platform fees stack (separate recipients)", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setFeeConfig", [a(alice), 200, 0, 0]); // 2% operator mgmt -> alice (operator treasury)
    await vault.send(owner, "setProtocolFeeConfig", [a(bob), 100]); // 1% platform -> bob
    // deposit from a fresh holder so the operator-treasury (alice) shares are isolated
    await vault.send(bob, "deposit", [1000n * USDC, a(bob)]);
    const start = await shareValue(vault, alice);

    warp(vault.vm, YEAR);
    await vault.send(owner, "accrueFees", []);

    const operatorGain = (await shareValue(vault, alice)) - start;
    assert.ok(operatorGain >= 198n * USDC / 10n && operatorGain <= 202n * USDC / 10n, `operator ${operatorGain}`);
  });

  await test("platform fee config is protocol-admin-only and capped", async () => {
    const { vault } = await fixture(); // mock: protocolAdmin == owner
    await assert.rejects(() => vault.send(alice, "setProtocolFeeConfig", [a(bob), 50])); // not admin
    await assert.rejects(() => vault.send(owner, "setProtocolFeeConfig", [a(bob), 300])); // > 2% cap
    const ZERO = "0x" + "00".repeat(20);
    await assert.rejects(() => vault.send(owner, "setProtocolFeeConfig", [ZERO, 50])); // treasury 0 w/ fee
    await vault.send(owner, "setProtocolFeeConfig", [a(bob), 200]); // exactly the cap is ok
    assert.equal(await vault.call("protocolFeeBps"), 200n);
  });

  // --- VaultFactory (the platform) ---

  async function factoryFixture() {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(sb.address), 0, 100]);
    // platform owner = owner, protocol treasury = bob, platform fee = 1%/yr
    const factory = await deploy(vm, artifacts.VaultFactory, [a(owner), a(bob), 100]);
    const core = [a(reader.address), a(alice) /*dummy usdc system*/, 1, 1, 3];
    // alice (a third-party operator) deploys her vault through the platform
    const vaultAddr = await factory.send(alice, "createVault", [
      a(usdc.address), "SANDICK Vault", "sSANDICK", a(manager), core,
    ]);
    const vault = at(vm, artifacts.BasketVault.abi, vaultAddr);
    return { vm, usdc, ms, sb, reader, factory, vault, vaultAddr, core };
  }

  await test("factory creates a vault, records it, and wires the platform fee", async () => {
    const { factory, vault, vaultAddr } = await factoryFixture();
    assert.equal(await factory.call("vaultCount"), 1n);
    assert.equal(await factory.call("isVault", [vaultAddr]), true);
    // creator is the operator/owner; platform keeps protocol-fee governance
    assert.equal(a(await vault.call("owner")), a(alice));
    assert.equal(a(await vault.call("manager")), a(manager));
    assert.equal(a(await vault.call("protocolAdmin")).toLowerCase(), a(factory.address).toLowerCase());
    assert.equal(a(await vault.call("protocolTreasury")), a(bob));
    assert.equal(await vault.call("protocolFeeBps"), 100n);
    const list = await vault.call("symbol");
    assert.equal(list, "sSANDICK");
  });

  await test("operator cannot touch the platform fee; the platform can", async () => {
    const { factory, vault, vaultAddr } = await factoryFixture();
    // alice owns the vault but is NOT the protocol admin -> cannot change the fee
    await assert.rejects(() => vault.send(alice, "setProtocolFeeConfig", [a(alice), 0]));
    // a random platform-owner impostor cannot drive the factory either
    await assert.rejects(() => factory.send(alice, "setVaultProtocolFee", [vaultAddr, a(bob), 50]));
    // the platform owner can, via the factory (which is the vault's protocolAdmin)
    await factory.send(owner, "setVaultProtocolFee", [vaultAddr, a(bob), 50]);
    assert.equal(await vault.call("protocolFeeBps"), 50n);
  });

  await test("factory tracks multiple vaults and enforces the fee cap", async () => {
    const { factory, core, usdc } = await factoryFixture();
    await factory.send(bob, "createVault", [
      a(usdc.address), "Second Vault", "sTWO", a(manager), core,
    ]);
    assert.equal(await factory.call("vaultCount"), 2n);
    const all = await factory.call("allVaults");
    assert.equal(all.length, 2);
    // default-fee setter is platform-owner-only and capped at 2%
    await assert.rejects(() => factory.send(alice, "setDefaultProtocolFee", [a(bob), 100]));
    await assert.rejects(() => factory.send(owner, "setDefaultProtocolFee", [a(bob), 300]));
    await factory.send(owner, "setDefaultProtocolFee", [a(bob), 150]);
    assert.equal(await factory.call("protocolFeeBps"), 150n);
  });

  await test("a factory-created vault charges the platform fee end-to-end", async () => {
    const { usdc, vault } = await factoryFixture();
    await usdc.send(deployer, "mint", [a(alice), 1_000_000n * USDC]);
    await usdc.send(alice, "approve", [a(vault.address), 1n << 255n]);
    // operator runs fee-free; only the 1% platform fee should accrue (to bob)
    await vault.send(alice, "setFeeConfig", [a(alice), 0, 0, 0]);
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);

    warp(vault.vm, YEAR);
    await vault.send(alice, "accrueFees", []);
    const platform = await shareValue(vault, bob);
    assert.ok(platform >= 99n * USDC / 10n && platform <= 101n * USDC / 10n, `platform fee ${platform}`);
  });

  await test("manager has no path to extract funds", async () => {
    const names = artifacts.MockBasketVault.abi
      .filter((x) => x.type === "function")
      .map((x) => x.name);
    assert.ok(!names.includes("rescue") && !names.includes("sweep"));
    assert.ok(names.includes("bridgeToCore") && names.includes("bridgeFromCore"));
  });

  // ---- Wren-derived safety controls: guardian, reduce-only, per-asset caps ----

  await test("guardian can pause + de-risk but has no fund/fee/manager power", async () => {
    const { vault } = await fixture();
    // owner-only to assign; the guardian cannot reassign itself
    await assert.rejects(() => vault.send(alice, "setGuardian", [a(bob)]));
    await vault.send(owner, "setGuardian", [a(bob)]);
    assert.equal(a(await vault.call("guardian")), a(bob));

    // guardian (bob) can pause and toggle wind-down mode
    await vault.send(bob, "pause");
    assert.equal(await vault.call("paused"), true);
    await vault.send(bob, "setReduceOnlyMode", [true]);
    assert.equal(await vault.call("reduceOnlyMode"), true);

    // ...but never unpause, fees, manager rotation, caps, or post-only
    await assert.rejects(() => vault.send(bob, "unpause"));
    await assert.rejects(() => vault.send(bob, "setFeeConfig", [a(bob), 0, 0, 0]));
    await assert.rejects(() => vault.send(bob, "setManager", [a(bob)]));
    await assert.rejects(() => vault.send(bob, "setOrderCaps", [0n, 0n, 0n]));
    await assert.rejects(() => vault.send(bob, "setAssetOrderCap", [7, 1n]));
    await assert.rejects(() => vault.send(bob, "setRequirePostOnly", [true]));

    await vault.send(owner, "unpause"); // owner always can
    assert.equal(await vault.call("paused"), false);
  });

  await test("guardian defaults to the owner; a random account cannot pause", async () => {
    const { vault } = await fixture();
    assert.equal(a(await vault.call("guardian")), a(owner));
    await assert.rejects(() => vault.send(alice, "pause"));
    await vault.send(owner, "pause"); // owner satisfies guardian-or-owner
    assert.equal(await vault.call("paused"), true);
  });

  await test("reduce-only mode: only shrinking legs trade; new margin blocked, exits open", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [500n * USDC]); // fund Core before de-risk
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await vault.send(owner, "setReduceOnlyMode", [true]);

    // exposure-increasing leg rejected; reduce-only leg passes
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]])
    );
    await vault.send(manager, "submitBasket", [[[7, false, 200n, 100n, true]]]);
    assert.equal(await vault.call("submittedCount"), 1n);

    // no new margin into Core...
    await assert.rejects(() => vault.send(manager, "bridgeToCore", [10n * USDC]));
    // ...but funds can still be pulled BACK from Core (exit path open)
    await vault.send(manager, "bridgeFromCore", [100n * USDC]);

    // turning it off restores normal trading
    await vault.send(owner, "setReduceOnlyMode", [false]);
    await vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]]);
    assert.equal(await vault.call("submittedCount"), 2n);
  });

  await test("per-asset order cap overrides the global cap", async () => {
    const { vault } = await fixture();
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await vault.send(owner, "setAllowedAsset", [8, true]);
    await vault.send(owner, "setOrderCaps", [1_000_000_000_000n, 0n, 0n]); // global 1e12
    await vault.send(owner, "setAssetOrderCap", [7, 1_000_000n]); // asset 7 tightened to 1e6

    // asset 7: 200*100=20_000 <= 1e6 ok; 2000*1000=2e6 > 1e6 revert
    await vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]]);
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, true, 2000n, 1000n, false]]])
    );
    // asset 8 has no per-asset cap -> falls back to the looser global 1e12
    await vault.send(manager, "submitBasket", [[[8, true, 2000n, 1000n, false]]]);
    assert.equal(await vault.call("submittedCount"), 2n);
  });

  await test("requirePostOnly is owner-gated and stored", async () => {
    const { vault } = await fixture();
    assert.equal(await vault.call("requirePostOnly"), false);
    await assert.rejects(() => vault.send(manager, "setRequirePostOnly", [true]));
    await vault.send(owner, "setRequirePostOnly", [true]);
    assert.equal(await vault.call("requirePostOnly"), true);
  });

  await test("manager cannot call any owner/guardian-gated setter", async () => {
    const { vault } = await fixture();
    const gated = [
      ["setManager", [a(manager)]],
      ["setGuardian", [a(manager)]],
      ["setFeeConfig", [a(manager), 0, 0, 0]],
      ["setManagerTimeout", [0n]],
      ["setAllowedAsset", [7, true]],
      ["setOrderCaps", [0n, 0n, 0n]],
      ["setAssetOrderCap", [7, 1n]],
      ["setRequirePostOnly", [true]],
      ["setReduceOnlyMode", [true]], // guardian-or-owner; manager is neither
      ["pause", []], // guardian-or-owner
      ["unpause", []], // owner
    ];
    for (const [fn, args] of gated) {
      await assert.rejects(() => vault.send(manager, fn, args), `${fn} should reject manager`);
    }
  });

  await test("recovery drill: de-risk, tighten, rotate; old manager loses all power", async () => {
    const { vault } = await fixture();
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]);
    await vault.send(manager, "bridgeToCore", [500n * USDC]);
    await vault.send(owner, "setAllowedAsset", [7, true]);

    // 1. force wind-down, 2. tighten the asset cap, 3. rotate the suspect key
    await vault.send(owner, "setReduceOnlyMode", [true]);
    await vault.send(owner, "setAssetOrderCap", [7, 1_000_000n]);
    await vault.send(owner, "setManager", [a(bob)]);

    // old manager is fully deauthorized
    await assert.rejects(() =>
      vault.send(manager, "submitBasket", [[[7, false, 100n, 100n, true]]])
    );
    await assert.rejects(() => vault.send(manager, "bridgeFromCore", [1n]));

    // new manager: only reduce-only legs within the tightened cap
    await assert.rejects(() =>
      vault.send(bob, "submitBasket", [[[7, true, 100n, 100n, false]]]) // not reduceOnly
    );
    await assert.rejects(() =>
      vault.send(bob, "submitBasket", [[[7, false, 2000n, 1000n, true]]]) // 2e6 > 1e6 cap
    );
    await vault.send(bob, "submitBasket", [[[7, false, 200n, 100n, true]]]);
    assert.equal(await vault.call("submittedCount"), 1n);
  });

  // ---- Production CoreWriter path: etch a recorder at 0x33..33 and assert the
  //      real HyperCoreActions wire bytes (covers BasketVault + HyperCoreActions
  //      which MockBasketVault otherwise bypasses). ----

  async function coreWriterFixture() {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const sb = await deploy(vm, artifacts.MockSpotBalance, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0, a(sb.address), 1, 1]);
    const vault = await deploy(vm, artifacts.BasketVault, [[
      a(usdc.address), "SANDICK Vault", "sSANDICK", a(manager), a(owner), a(reader.address),
      USDC_SYS, 1 /*usdcCoreTokenIndex*/, 1 /*coreScale*/, 3 /*tif IOC*/,
      a(owner), a(owner), 0,
    ]]);
    // Recorder lives at the CoreWriter precompile address.
    await etch(vm, CORE_WRITER, artifacts.MockCoreWriter.deployedBytecode);
    const cw = at(vm, artifacts.MockCoreWriter.abi, CORE_WRITER);
    // bob (NOT alice — alice == CoreWriter) funds the vault.
    await usdc.send(deployer, "mint", [a(bob), 1_000_000n * USDC]);
    await usdc.send(bob, "approve", [a(vault.address), 1n << 255n]);
    await vault.send(bob, "deposit", [1000n * USDC, a(bob)]);
    return { vm, usdc, vault, cw };
  }

  await test("CoreWriter: bridgeToCore emits usdClassTransfer(spot->perp)", async () => {
    const { vault, cw } = await coreWriterFixture();
    await vault.send(manager, "bridgeToCore", [500n * USDC]);
    assert.equal(await cw.call("callCount"), 1n);
    const { version, actionId, args } = decodeAction(await cw.call("lastData"));
    assert.equal(version, 1);
    assert.equal(actionId, 7); // USD class transfer
    const [ntl, toPerp] = _abi.decode(["uint64", "bool"], args);
    assert.equal(ntl, 500n * USDC); // coreScale 1
    assert.equal(toPerp, true);
  });

  await test("CoreWriter: submitBasket emits a limit order with the configured TIF", async () => {
    const { vault, cw } = await coreWriterFixture();
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]]);
    const { actionId, args } = decodeAction(await cw.call("lastData"));
    assert.equal(actionId, 1); // limit order
    const d = _abi.decode(
      ["uint32", "bool", "uint64", "uint64", "bool", "uint8", "uint128"], args
    );
    assert.equal(d[0], 7n); assert.equal(d[1], true);
    assert.equal(d[2], 200n); assert.equal(d[3], 100n);
    assert.equal(d[4], false); assert.equal(d[5], 3n); assert.equal(d[6], 0n); // tif IOC, no cloid
  });

  await test("CoreWriter: requirePostOnly forces ALO (tif=1) on submitted orders", async () => {
    const { vault, cw } = await coreWriterFixture();
    await vault.send(owner, "setAllowedAsset", [7, true]);
    await vault.send(owner, "setRequirePostOnly", [true]);
    await vault.send(manager, "submitBasket", [[[7, true, 200n, 100n, false]]]);
    const { args } = decodeAction(await cw.call("lastData"));
    const d = _abi.decode(
      ["uint32", "bool", "uint64", "uint64", "bool", "uint8", "uint128"], args
    );
    assert.equal(d[5], 1n); // tif coerced to ALO by requirePostOnly
  });

  await test("CoreWriter: bridgeFromCore emits usdClassTransfer THEN spotSend", async () => {
    const { vault, cw } = await coreWriterFixture();
    await vault.send(manager, "bridgeToCore", [500n * USDC]);   // call 0
    await vault.send(manager, "bridgeFromCore", [100n * USDC]); // calls 1,2
    assert.equal(await cw.call("callCount"), 3n);
    // perp -> spot
    const t = decodeAction(await cw.call("dataAt", [1n]));
    assert.equal(t.actionId, 7);
    const [ntl, toPerp] = _abi.decode(["uint64", "bool"], t.args);
    assert.equal(ntl, 100n * USDC); assert.equal(toPerp, false);
    // spot-send to the USDC system address
    const s = decodeAction(await cw.call("lastData"));
    assert.equal(s.actionId, 6);
    const [to, token, amountWei] = _abi.decode(["address", "uint64", "uint64"], s.args);
    assert.equal(to.toLowerCase(), USDC_SYS);
    assert.equal(token, 1n); assert.equal(amountWei, 100n * USDC);
  });

  console.log(`\n${passed} contract test(s) passed.`);
}

if (require.main === module) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}

module.exports = { main };
