/* SANDICK — on-chain layer (optional, config-gated).
 *
 * A thin viem wrapper over the deployed SandickVault for the eventual live
 * wiring. It is intentionally decoupled from app.js's demo state machine: import
 * it and call these helpers from the UI once `config.js` has `chain.enabled`.
 *
 * Read path  : totalAssets / totalSupply / sharePrice / balanceOf / queue state.
 * Write path : approve + deposit, requestRedeem, redeem, claim — via the user's
 *              injected wallet (window.ethereum) on HyperEVM.
 *
 * Async-aware by design: writes return the tx hash, but callers must CONFIRM by
 * re-reading state (CoreWriter actions settle later and can fail silently) —
 * never treat a receipt as success for a Core action.
 *
 * Usage (ES module):
 *   import { SandickChain } from './chain.js';
 *   const chain = await SandickChain.connect(window.SANDICK_CONFIG.chain);
 *   const nav = await chain.totalAssets();
 */

// Minimal ABI: only the methods the front end touches.
export const VAULT_ABI = [
  { type: 'function', stateMutability: 'view', name: 'totalAssets', inputs: [], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'totalSupply', inputs: [], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'decimals', inputs: [], outputs: [{ type: 'uint8' }] },
  { type: 'function', stateMutability: 'view', name: 'asset', inputs: [], outputs: [{ type: 'address' }] },
  { type: 'function', stateMutability: 'view', name: 'paused', inputs: [], outputs: [{ type: 'bool' }] },
  { type: 'function', stateMutability: 'view', name: 'balanceOf', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'convertToAssets', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'convertToShares', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'maxRedeem', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'pendingRedeemShares', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'claimableAssets', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'deposit', inputs: [{ type: 'uint256' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'requestRedeem', inputs: [{ type: 'uint256' }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'redeem', inputs: [{ type: 'uint256' }, { type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'claim', inputs: [], outputs: [] },
];

export const ERC20_ABI = [
  { type: 'function', stateMutability: 'view', name: 'balanceOf', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'allowance', inputs: [{ type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'approve', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [{ type: 'bool' }] },
];

const VIEM_CDN = 'https://esm.sh/viem@2';

export class SandickChain {
  constructor({ publicClient, walletClient, account, cfg, viem }) {
    this.publicClient = publicClient;
    this.walletClient = walletClient;
    this.account = account;
    this.cfg = cfg;
    this.viem = viem;
  }

  /** Connect public + (optional) wallet clients. Requires cfg.enabled + addresses. */
  static async connect(cfg) {
    if (!cfg || !cfg.enabled) throw new Error('chain disabled in config.js');
    if (!cfg.rpcUrl || !cfg.vaultAddress) throw new Error('rpcUrl and vaultAddress required');
    const viem = await import(VIEM_CDN);
    const chain = {
      id: cfg.chainId,
      name: 'HyperEVM',
      nativeCurrency: { name: 'HYPE', symbol: 'HYPE', decimals: 18 },
      rpcUrls: { default: { http: [cfg.rpcUrl] } },
    };
    const publicClient = viem.createPublicClient({ chain, transport: viem.http(cfg.rpcUrl) });

    let walletClient, account;
    if (typeof window !== 'undefined' && window.ethereum) {
      walletClient = viem.createWalletClient({ chain, transport: viem.custom(window.ethereum) });
      [account] = await walletClient.requestAddresses();
    }
    return new SandickChain({ publicClient, walletClient, account, cfg, viem });
  }

  _read(functionName, args = []) {
    return this.publicClient.readContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName, args,
    });
  }

  // ---- reads ----
  totalAssets() { return this._read('totalAssets'); }
  totalSupply() { return this._read('totalSupply'); }
  paused() { return this._read('paused'); }
  balanceOf(addr) { return this._read('balanceOf', [addr]); }
  convertToAssets(shares) { return this._read('convertToAssets', [shares]); }
  pendingRedeemShares(addr) { return this._read('pendingRedeemShares', [addr]); }
  claimableAssets(addr) { return this._read('claimableAssets', [addr]); }

  /** NAV per share scaled to 1e18 (shares and assets may differ in decimals). */
  async sharePrice() {
    const [assets, supply] = await Promise.all([this.totalAssets(), this.totalSupply()]);
    if (supply === 0n) return 0n;
    return (assets * (10n ** 18n)) / supply;
  }

  // ---- writes (require a wallet) ----
  _assertWallet() {
    if (!this.walletClient || !this.account) throw new Error('no wallet connected');
  }

  async approveUsdc(amount) {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.usdcAddress, abi: ERC20_ABI, functionName: 'approve',
      args: [this.cfg.vaultAddress, amount], account: this.account,
    });
  }

  async deposit(amount, receiver = this.account) {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName: 'deposit',
      args: [amount, receiver], account: this.account,
    });
  }

  async requestRedeem(shares) {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName: 'requestRedeem',
      args: [shares], account: this.account,
    });
  }

  async claim() {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName: 'claim',
      args: [], account: this.account,
    });
  }
}
