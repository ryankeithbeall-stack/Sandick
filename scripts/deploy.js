// Deploy the platform (HyperCoreReader + VaultFactory) to HyperEVM and create the
// flagship vault through the factory, from a deploy-config JSON.
//
// The factory is the product: it deploys BasketVaults, stamps each with the
// platform's protocol fee + treasury, and stays each vault's protocolAdmin so the
// platform keeps earning from every hosted vault. The flagship (e.g. SANDICK) is
// just the first vault created here.
//
// Dry-run by default (prints the constructor/createVault args). To actually
// deploy, set the env vars below and pass --execute:
//   RPC_URL            HyperEVM RPC (testnet chainid 998)
//   PRIVATE_KEY        deployer key (also the initial vault owner; see VAULT_OWNER)
//   VAULT_OWNER        governance address the flagship vault is handed to
//   VAULT_MANAGER      strategy/trade-only address (manager)
//   USDC_ADDRESS       EVM USDC ERC-20 used as the vault asset
//   PLATFORM_OWNER     (optional) factory owner / platform governance [default VAULT_OWNER]
//   PROTOCOL_TREASURY  (optional) platform fee treasury [default VAULT_OWNER]
//   PROTOCOL_FEE_BPS   (optional) platform fee, bps/yr of NAV [default cfg.protocolFeeBps or 100]
//
//   node scripts/deploy.js config/deploy.json --execute
const fs = require("fs");
const { ethers } = require("ethers");
const { compile } = require("./compile");

function loadConfig(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function readerArgs(cfg) {
  // spotWeiToAssetDivisor is sourced from cfg.coreScale (single source of truth):
  // coreScale is the EVM->Core write-path multiplier; the spot read divides by the
  // same value on the Core->EVM path, so they can never diverge.
  return [
    cfg.marginSummaryPrecompile,
    cfg.perpDexIndex,
    cfg.spotBalancePrecompile,
    cfg.usdcCoreTokenIndex,
    cfg.coreScale,
  ];
}

function protocolFeeBps(cfg, env) {
  const raw = env.PROTOCOL_FEE_BPS ?? cfg.protocolFeeBps ?? 100;
  return Number(raw);
}

function factoryArgs(cfg, env) {
  return [
    env.PLATFORM_OWNER || env.VAULT_OWNER,
    env.PROTOCOL_TREASURY || env.VAULT_OWNER,
    protocolFeeBps(cfg, env),
  ];
}

// The immutable HyperCore wiring shared by every vault (the CoreParams struct).
function coreParams(cfg, readerAddr) {
  return [readerAddr, cfg.usdcSystemAddress, cfg.usdcCoreTokenIndex, cfg.coreScale, cfg.tif];
}

function vaultName(cfg, env) {
  return env.VAULT_NAME || `${cfg.basket} Vault`;
}

function vaultSymbol(cfg, env) {
  return env.VAULT_SYMBOL || `s${cfg.basket}`;
}

async function main() {
  const args = process.argv.slice(2);
  const execute = args.includes("--execute");
  const cfgPath = args.find((a) => !a.startsWith("--")) || "config/deploy.json";
  const cfg = loadConfig(cfgPath);
  const env = process.env;
  const { artifacts } = compile();

  const assetIds = Object.values(cfg.assetIds || {});
  const name = vaultName(cfg, env);
  const symbol = vaultSymbol(cfg, env);
  console.log(`Deploy config: ${cfgPath} (${cfg.network})`);
  console.log(`  Basket ${cfg.basket} on dex ${cfg.dex} (perpDexIndex ${cfg.perpDexIndex})`);
  console.log(`  HyperCoreReader(${readerArgs(cfg).join(", ")})`);
  console.log(`  VaultFactory(owner=${factoryArgs(cfg, env)[0]}, treasury=${factoryArgs(cfg, env)[1]}, feeBps=${protocolFeeBps(cfg, env)})`);
  console.log(`  createVault: name="${name}" symbol="${symbol}" usdcSystemAddress=${cfg.usdcSystemAddress} coreScale=${cfg.coreScale} tif=${cfg.tif}`);
  console.log(`  Allowed asset ids: ${assetIds.join(", ")}`);

  const haveEnv = env.RPC_URL && env.PRIVATE_KEY && env.VAULT_OWNER && env.VAULT_MANAGER && env.USDC_ADDRESS;
  if (!execute || !haveEnv) {
    console.log(
      `\n[dry-run] ${execute && !haveEnv ? "missing env vars; " : ""}not deploying. ` +
        `Set RPC_URL/PRIVATE_KEY/VAULT_OWNER/VAULT_MANAGER/USDC_ADDRESS and pass --execute.`
    );
    return;
  }

  const provider = new ethers.JsonRpcProvider(env.RPC_URL);
  const wallet = new ethers.Wallet(env.PRIVATE_KEY, provider);

  const readerFactory = new ethers.ContractFactory(
    artifacts.HyperCoreReader.abi, artifacts.HyperCoreReader.bytecode, wallet
  );
  const reader = await readerFactory.deploy(...readerArgs(cfg));
  await reader.waitForDeployment();
  const readerAddr = await reader.getAddress();
  console.log(`Deployed HyperCoreReader at ${readerAddr}`);

  const factoryFactory = new ethers.ContractFactory(
    artifacts.VaultFactory.abi, artifacts.VaultFactory.bytecode, wallet
  );
  const factory = await factoryFactory.deploy(...factoryArgs(cfg, env));
  await factory.waitForDeployment();
  const factoryAddr = await factory.getAddress();
  console.log(`Deployed VaultFactory at ${factoryAddr}`);

  // Create the flagship vault through the factory. The deployer wallet becomes
  // the vault owner so it can allow-list assets, then ownership is handed to
  // VAULT_OWNER below.
  const createTx = await factory.createVault(
    env.USDC_ADDRESS, name, symbol, env.VAULT_MANAGER, coreParams(cfg, readerAddr)
  );
  const receipt = await createTx.wait();
  let vaultAddr;
  for (const log of receipt.logs) {
    try {
      const parsed = factory.interface.parseLog(log);
      if (parsed && parsed.name === "VaultCreated") {
        vaultAddr = parsed.args.vault;
        break;
      }
    } catch (_) {
      /* not a factory event */
    }
  }
  if (!vaultAddr) throw new Error("VaultCreated event not found");
  console.log(`Created BasketVault at ${vaultAddr}`);

  const vault = new ethers.Contract(vaultAddr, artifacts.BasketVault.abi, wallet);
  for (const id of assetIds) {
    await (await vault.setAllowedAsset(id, true)).wait();
    console.log(`  allowed asset ${id}`);
  }

  // Hand the flagship vault to governance if the deployer isn't already it.
  if (env.VAULT_OWNER.toLowerCase() !== wallet.address.toLowerCase()) {
    await (await vault.transferOwnership(env.VAULT_OWNER)).wait();
    console.log(`  transferred vault ownership to ${env.VAULT_OWNER}`);
  }

  const manifest = {
    network: cfg.network,
    reader: readerAddr,
    factory: factoryAddr,
    vault: vaultAddr,
    name,
    symbol,
    protocolFeeBps: protocolFeeBps(cfg, env),
    assetIds,
  };
  fs.writeFileSync("deploy.manifest.json", JSON.stringify(manifest, null, 2) + "\n");
  console.log("Wrote deploy.manifest.json");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
