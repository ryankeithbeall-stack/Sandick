// Deploy HyperCoreReader + SandickVault to HyperEVM from a deploy-config JSON.
//
// Dry-run by default (prints the constructor args). To actually deploy, set the
// env vars below and pass --execute:
//   RPC_URL          HyperEVM RPC (testnet chainid 998)
//   PRIVATE_KEY      deployer key
//   VAULT_OWNER      governance address (owner)
//   VAULT_MANAGER    strategy/trade-only address (manager)
//   USDC_ADDRESS     EVM USDC ERC-20 used as the vault asset
//
//   node scripts/deploy.js config/deploy.json --execute
const fs = require("fs");
const { ethers } = require("ethers");
const { compile } = require("./compile");

function loadConfig(path) {
  return JSON.parse(fs.readFileSync(path, "utf8"));
}

function readerArgs(cfg) {
  return [cfg.marginSummaryPrecompile, cfg.perpDexIndex];
}

function vaultArgs(cfg, env, readerAddr) {
  return [
    env.USDC_ADDRESS,
    env.VAULT_MANAGER,
    env.VAULT_OWNER,
    readerAddr,
    cfg.usdcSystemAddress,
    cfg.usdcCoreTokenIndex,
    cfg.coreScale,
    cfg.tif,
  ];
}

async function main() {
  const args = process.argv.slice(2);
  const execute = args.includes("--execute");
  const cfgPath = args.find((a) => !a.startsWith("--")) || "config/deploy.json";
  const cfg = loadConfig(cfgPath);
  const env = process.env;
  const { artifacts } = compile();

  const assetIds = Object.values(cfg.assetIds || {});
  console.log(`Deploy config: ${cfgPath} (${cfg.network})`);
  console.log(`  Basket ${cfg.basket} on dex ${cfg.dex} (perpDexIndex ${cfg.perpDexIndex})`);
  console.log(`  HyperCoreReader(${readerArgs(cfg).join(", ")})`);
  console.log(`  SandickVault: usdcSystemAddress=${cfg.usdcSystemAddress} coreScale=${cfg.coreScale} tif=${cfg.tif}`);
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

  const vaultFactory = new ethers.ContractFactory(
    artifacts.SandickVault.abi, artifacts.SandickVault.bytecode, wallet
  );
  const vault = await vaultFactory.deploy(...vaultArgs(cfg, env, readerAddr));
  await vault.waitForDeployment();
  const vaultAddr = await vault.getAddress();
  console.log(`Deployed SandickVault at ${vaultAddr}`);

  for (const id of assetIds) {
    await (await vault.setAllowedAsset(id, true)).wait();
    console.log(`  allowed asset ${id}`);
  }

  const manifest = { network: cfg.network, reader: readerAddr, vault: vaultAddr, assetIds };
  fs.writeFileSync("deploy.manifest.json", JSON.stringify(manifest, null, 2) + "\n");
  console.log("Wrote deploy.manifest.json");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
