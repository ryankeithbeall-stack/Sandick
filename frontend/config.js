/* SANDICK front-end runtime config.
 *
 * The app runs in a self-contained DEMO mode by default (no chain calls).
 * To point it at a deployed BasketVault on HyperEVM testnet, fill in the
 * addresses below and set `chain.enabled = true`. See chain.js + README.md.
 *
 * NOTE: testnet sign-off is not complete — leave chain disabled until the
 * vault is deployed and its immutables are verified on chainid 998.
 */
window.SANDICK_CONFIG = {
  chain: {
    enabled: false,
    chainId: 998,                  // HyperEVM testnet
    rpcUrl: '',                    // e.g. https://rpc.hyperliquid-testnet.xyz/evm
    factoryAddress: '',            // deployed VaultFactory (the platform)
    vaultAddress: '',              // flagship BasketVault (SANDICK) for the detail view
    usdcAddress: '',               // vault underlying (USDC, 6dp)
    explorer: '',                  // optional block-explorer base url
  },
};
