/* SANDICK front-end runtime config.
 *
 * The app runs in a self-contained DEMO mode by default (no chain calls).
 * To point it at a deployed BasketVault on HyperEVM testnet, fill in the
 * addresses below and set `chain.enabled = true`. See chain.js + README.md.
 *
 * NOTE: testnet sign-off is not complete — leave chain disabled until the
 * vault is deployed and its immutables are verified on chainid 998.
 */
window.APERTURE_CONFIG = {
  chain: {
    enabled: false,
    chainId: 998,                  // HyperEVM testnet
    rpcUrl: '',                    // e.g. https://rpc.hyperliquid-testnet.xyz/evm
    factoryAddress: '',            // deployed VaultFactory (the platform)
    vaultAddress: '',              // flagship BasketVault (SANDICK) for the detail view
    usdcAddress: '',               // vault underlying (USDC, 6dp)
    explorer: '',                  // optional block-explorer base url
    // Platform-wide HyperCore immutables, shared by every vault on this chain.
    // Produced by `python -m sandick.deploy_config`. Required to launch a vault
    // from the UI (factory.createVault); leave blank to disable the launch flow.
    coreParams: {
      reader: '',                  // deployed HyperCoreReader address
      usdcSystemAddress: '',       // 0x20..<usdcCoreTokenIndex>
      usdcCoreTokenIndex: 0,
      coreScale: 1,
      tif: 3,                      // 1 ALO · 2 GTC · 3 IOC
    },
  },
};
