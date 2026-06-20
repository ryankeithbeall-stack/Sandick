// In-process EVM tests for the vault trust/accounting logic.
// Runs the actual compiled bytecode on @ethereumjs/vm — exercises shares, NAV,
// withdrawal caps, and the trade-only manager restrictions.
const assert = require("assert");
const { compile } = require("../../scripts/compile");
const { makeVM, deploy, ACCOUNTS } = require("../../scripts/evm");

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
