// In-process EVM tests for the vault trust/accounting logic.
// Runs the actual compiled bytecode on @ethereumjs/vm — exercises shares, NAV,
// withdrawal caps, and the trade-only manager restrictions.
const assert = require("assert");
const { compile } = require("../../scripts/compile");
const { makeVM, deploy, ACCOUNTS, warp } = require("../../scripts/evm");

const SEVEN_DAYS = 7n * 24n * 60n * 60n;

const USDC = 10n ** 6n;
const { deployer, manager, alice, bob, owner } = ACCOUNTS;
const a = (x) => x.toString(); // address hex

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
    const vault = await deploy(vm, artifacts.MockSandickVault, [
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
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0]);
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 0n);
    await ms.send(deployer, "setAccountValue", [1234n * USDC]);
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 1234n * USDC);
    await ms.send(deployer, "setAccountValue", [-5n * USDC]); // underwater -> 0
    assert.equal(await reader.call("accountEquityUsd", [a(alice)]), 0n);
  });

  await test("production SandickVault NAV = idle + reader equity", async () => {
    const vm = await makeVM();
    const usdc = await deploy(vm, artifacts.MockERC20, ["USD Coin", "USDC", 6]);
    const ms = await deploy(vm, artifacts.MockMarginSummary, []);
    const reader = await deploy(vm, artifacts.HyperCoreReader, [a(ms.address), 0]);
    const vault = await deploy(vm, artifacts.SandickVault, [
      a(usdc.address), a(manager), a(owner), a(reader.address),
      a(alice) /*dummy usdc system addr*/, 1, 1, 3,
    ]);
    await usdc.send(deployer, "mint", [a(alice), 1_000_000n * USDC]);
    await usdc.send(alice, "approve", [a(vault.address), 1n << 255n]);
    await vault.send(alice, "deposit", [1000n * USDC, a(alice)]); // idle 1000, equity 0
    assert.equal(await vault.call("totalAssets"), 1000n * USDC);
    await ms.send(deployer, "setAccountValue", [500n * USDC]); // simulate Core equity
    assert.equal(await vault.call("totalAssets"), 1500n * USDC);
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
    const v = await deploy(usdc.vm, artifacts.MockSandickVault, [
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

  await test("manager has no path to extract funds", async () => {
    const names = artifacts.MockSandickVault.abi
      .filter((x) => x.type === "function")
      .map((x) => x.name);
    assert.ok(!names.includes("rescue") && !names.includes("sweep"));
    assert.ok(names.includes("bridgeToCore") && names.includes("bridgeFromCore"));
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
