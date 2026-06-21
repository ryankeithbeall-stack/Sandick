/* SANDICK — on-chain layer (optional, config-gated).
 *
 * A thin viem wrapper over the deployed BasketVault for the eventual live
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
 *   import { ApertureChain } from './chain.js';
 *   const chain = await ApertureChain.connect(window.APERTURE_CONFIG.chain);
 *   const nav = await chain.totalAssets();
 */

// The Order struct submitBasket expects (mirrors BasketVaultBase.Order).
const ORDER_COMPONENTS = [
  { name: 'assetId', type: 'uint32' },
  { name: 'isBuy', type: 'bool' },
  { name: 'limitPx', type: 'uint64' },
  { name: 'sz', type: 'uint64' },
  { name: 'reduceOnly', type: 'bool' },
];

// Minimal ABI: only the methods the front end touches.
export const VAULT_ABI = [
  // ---- depositor reads ----
  { type: 'function', stateMutability: 'view', name: 'totalAssets', inputs: [], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'totalSupply', inputs: [], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'decimals', inputs: [], outputs: [{ type: 'uint8' }] },
  { type: 'function', stateMutability: 'view', name: 'name', inputs: [], outputs: [{ type: 'string' }] },
  { type: 'function', stateMutability: 'view', name: 'symbol', inputs: [], outputs: [{ type: 'string' }] },
  { type: 'function', stateMutability: 'view', name: 'asset', inputs: [], outputs: [{ type: 'address' }] },
  { type: 'function', stateMutability: 'view', name: 'paused', inputs: [], outputs: [{ type: 'bool' }] },
  { type: 'function', stateMutability: 'view', name: 'balanceOf', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'convertToAssets', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'convertToShares', inputs: [{ type: 'uint256' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'maxRedeem', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'pendingRedeemShares', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'claimableAssets', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  // redemption-liveness backstop
  { type: 'function', stateMutability: 'view', name: 'managerIsDark', inputs: [], outputs: [{ type: 'bool' }] },
  { type: 'function', stateMutability: 'view', name: 'redemptionDeficit', inputs: [], outputs: [{ type: 'uint256' }] },
  // fees
  { type: 'function', stateMutability: 'view', name: 'managementFeeBps', inputs: [], outputs: [{ type: 'uint16' }] },
  { type: 'function', stateMutability: 'view', name: 'performanceFeeBps', inputs: [], outputs: [{ type: 'uint16' }] },
  { type: 'function', stateMutability: 'view', name: 'exitFeeBps', inputs: [], outputs: [{ type: 'uint16' }] },
  { type: 'function', stateMutability: 'view', name: 'feeRecipient', inputs: [], outputs: [{ type: 'address' }] },
  // ---- role reads (admin gating) ----
  { type: 'function', stateMutability: 'view', name: 'owner', inputs: [], outputs: [{ type: 'address' }] },
  { type: 'function', stateMutability: 'view', name: 'manager', inputs: [], outputs: [{ type: 'address' }] },
  { type: 'function', stateMutability: 'view', name: 'allowedAsset', inputs: [{ type: 'uint32' }], outputs: [{ type: 'bool' }] },
  // ---- depositor writes ----
  { type: 'function', stateMutability: 'nonpayable', name: 'deposit', inputs: [{ type: 'uint256' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'requestRedeem', inputs: [{ type: 'uint256' }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'cancelRedeemRequest', inputs: [{ type: 'uint256' }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'redeem', inputs: [{ type: 'uint256' }, { type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'claim', inputs: [], outputs: [] },
  // fulfillRedeem is permissionless once idle liquidity exists (redemption liveness).
  { type: 'function', stateMutability: 'nonpayable', name: 'fulfillRedeem', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [] },
  // permissionless backstop: pull USDC from Core (up to the deficit) if the manager went dark.
  { type: 'function', stateMutability: 'nonpayable', name: 'bridgeFromCoreForRedemptions', inputs: [{ type: 'uint256' }], outputs: [] },
  // ---- manager writes ----
  { type: 'function', stateMutability: 'nonpayable', name: 'submitBasket', inputs: [{ type: 'tuple[]', name: 'orders', components: ORDER_COMPONENTS }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'bridgeToCore', inputs: [{ type: 'uint256' }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'bridgeFromCore', inputs: [{ type: 'uint256' }], outputs: [] },
  // ---- owner writes ----
  { type: 'function', stateMutability: 'nonpayable', name: 'setAllowedAsset', inputs: [{ type: 'uint32' }, { type: 'bool' }], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'pause', inputs: [], outputs: [] },
  { type: 'function', stateMutability: 'nonpayable', name: 'unpause', inputs: [], outputs: [] },
];

// Minimal ABI for the VaultFactory (the platform): enumerate vaults, read the
// platform fee, and deploy new vaults.
export const FACTORY_ABI = [
  { type: 'function', stateMutability: 'view', name: 'vaultCount', inputs: [], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'allVaults', inputs: [], outputs: [{ type: 'address[]' }] },
  { type: 'function', stateMutability: 'view', name: 'protocolFeeBps', inputs: [], outputs: [{ type: 'uint16' }] },
  { type: 'function', stateMutability: 'view', name: 'protocolTreasury', inputs: [], outputs: [{ type: 'address' }] },
  {
    type: 'function', stateMutability: 'nonpayable', name: 'createVault',
    inputs: [
      { name: 'asset', type: 'address' },
      { name: 'name', type: 'string' },
      { name: 'symbol', type: 'string' },
      { name: 'manager', type: 'address' },
      {
        name: 'core', type: 'tuple', components: [
          { name: 'reader', type: 'address' },
          { name: 'usdcSystemAddress', type: 'address' },
          { name: 'usdcCoreTokenIndex', type: 'uint64' },
          { name: 'coreScale', type: 'uint256' },
          { name: 'tif', type: 'uint8' },
        ],
      },
    ],
    outputs: [{ type: 'address' }],
  },
];

export const ERC20_ABI = [
  { type: 'function', stateMutability: 'view', name: 'balanceOf', inputs: [{ type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'allowance', inputs: [{ type: 'address' }, { type: 'address' }], outputs: [{ type: 'uint256' }] },
  { type: 'function', stateMutability: 'view', name: 'decimals', inputs: [], outputs: [{ type: 'uint8' }] },
  { type: 'function', stateMutability: 'nonpayable', name: 'approve', inputs: [{ type: 'address' }, { type: 'uint256' }], outputs: [{ type: 'bool' }] },
];

const VIEM_CDN = 'https://esm.sh/viem@2';

export class ApertureChain {
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
    if (!cfg.rpcUrl) throw new Error('rpcUrl required');
    if (!cfg.vaultAddress && !cfg.factoryAddress) {
      throw new Error('vaultAddress or factoryAddress required');
    }
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
    return new ApertureChain({ publicClient, walletClient, account, cfg, viem });
  }

  _read(functionName, args = []) {
    return this.publicClient.readContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName, args,
    });
  }

  /** Read any vault by address (for the marketplace, not just cfg.vaultAddress). */
  _readAt(address, functionName, args = []) {
    return this.publicClient.readContract({ address, abi: VAULT_ABI, functionName, args });
  }

  /** Read the factory (the platform). Requires cfg.factoryAddress. */
  _readFactory(functionName, args = []) {
    if (!this.cfg.factoryAddress) throw new Error('factoryAddress required');
    return this.publicClient.readContract({
      address: this.cfg.factoryAddress, abi: FACTORY_ABI, functionName, args,
    });
  }

  // ---- platform (factory) ----
  vaultCount() { return this._readFactory('vaultCount'); }
  protocolFeeBps() { return this._readFactory('protocolFeeBps'); }
  protocolTreasury() { return this._readFactory('protocolTreasury'); }

  /** Enumerate every vault on the platform with its live stats. Returns
   *  [{ address, name, symbol, manager, asset, tvl, supply }] in raw units. */
  async listVaults() {
    const addrs = await this._readFactory('allVaults');
    return Promise.all(addrs.map(async (address) => {
      const [tvl, supply, name, symbol, manager, asset] = await Promise.all([
        this._readAt(address, 'totalAssets'),
        this._readAt(address, 'totalSupply'),
        this._readAt(address, 'name'),
        this._readAt(address, 'symbol'),
        this._readAt(address, 'manager'),
        this._readAt(address, 'asset'),
      ]);
      return { address, name, symbol, manager, asset, tvl, supply };
    }));
  }

  /** Deploy a new vault through the factory. `core` is the CoreParams tuple
   *  [reader, usdcSystemAddress, usdcCoreTokenIndex, coreScale, tif]. Returns the
   *  tx hash; read back via listVaults() once mined (the new vault is appended). */
  async createVault({ asset, name, symbol, manager, core }) {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.factoryAddress, abi: FACTORY_ABI, functionName: 'createVault',
      args: [asset, name, symbol, manager, core], account: this.account,
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
  managerIsDark() { return this._read('managerIsDark'); }
  redemptionDeficit() { return this._read('redemptionDeficit'); }

  /** Fee schedule in basis points: { management, performance, exit }. */
  async feeSchedule() {
    const [management, performance, exit] = await Promise.all([
      this._read('managementFeeBps'), this._read('performanceFeeBps'), this._read('exitFeeBps'),
    ]);
    return { management, performance, exit };
  }
  owner() { return this._read('owner'); }
  manager() { return this._read('manager'); }
  allowedAsset(assetId) { return this._read('allowedAsset', [assetId]); }
  shareDecimals() { return this._read('decimals'); }

  /** Underlying (USDC) token decimals — usually 6. */
  usdcDecimals() {
    return this.publicClient.readContract({
      address: this.cfg.usdcAddress, abi: ERC20_ABI, functionName: 'decimals', args: [],
    });
  }

  /** Connected wallet's USDC balance (raw units). */
  usdcBalance(addr = this.account) {
    return this.publicClient.readContract({
      address: this.cfg.usdcAddress, abi: ERC20_ABI, functionName: 'balanceOf', args: [addr],
    });
  }

  /** Current USDC allowance the user has granted the vault (raw units). */
  usdcAllowance(addr = this.account) {
    return this.publicClient.readContract({
      address: this.cfg.usdcAddress, abi: ERC20_ABI, functionName: 'allowance',
      args: [addr, this.cfg.vaultAddress],
    });
  }

  /** NAV per share scaled to 1e18 (shares and assets may differ in decimals). */
  async sharePrice() {
    const [assets, supply] = await Promise.all([this.totalAssets(), this.totalSupply()]);
    if (supply === 0n) return 0n;
    return (assets * (10n ** 18n)) / supply;
  }

  /** True when the connected account is the vault manager (case-insensitive). */
  async isManager() {
    if (!this.account) return false;
    const m = await this.manager();
    return m.toLowerCase() === this.account.toLowerCase();
  }

  /** True when the connected account is the vault owner. */
  async isOwner() {
    if (!this.account) return false;
    const o = await this.owner();
    return o.toLowerCase() === this.account.toLowerCase();
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

  /** Synchronous redeem (needs idle liquidity; reverts otherwise — use queue). */
  async redeem(shares, receiver = this.account, owner = this.account) {
    return this._write('redeem', [shares, receiver, owner]);
  }

  async requestRedeem(shares) { return this._write('requestRedeem', [shares]); }
  async cancelRedeemRequest(shares) { return this._write('cancelRedeemRequest', [shares]); }
  async claim() { return this._write('claim', []); }

  /** Permissionless once idle USDC exists: settle another holder's queued request. */
  async fulfillRedeem(owner, shares) { return this._write('fulfillRedeem', [owner, shares]); }

  /** Redemption-liveness backstop: if managerIsDark(), anyone may pull USDC from
   *  Core back to the vault (capped at redemptionDeficit()) so exits can settle. */
  async bridgeFromCoreForRedemptions(amount) { return this._write('bridgeFromCoreForRedemptions', [amount]); }

  // ---- manager writes ----
  /** Submit basket orders. `orders` = [{ assetId, isBuy, limitPx, sz, reduceOnly }]
   *  with limitPx/sz already 1e8-scaled (see sandick.onchain.plan_to_onchain_orders).
   *  CoreWriter settles later and can fail silently — CONFIRM by re-reading NAV /
   *  positions; do not treat the receipt as success. */
  async submitBasket(orders) { return this._write('submitBasket', [orders]); }
  async bridgeToCore(amount) { return this._write('bridgeToCore', [amount]); }
  async bridgeFromCore(amount) { return this._write('bridgeFromCore', [amount]); }

  // ---- owner writes ----
  async setAllowedAsset(assetId, ok) { return this._write('setAllowedAsset', [assetId, ok]); }
  async pause() { return this._write('pause', []); }
  async unpause() { return this._write('unpause', []); }

  _write(functionName, args) {
    this._assertWallet();
    return this.walletClient.writeContract({
      address: this.cfg.vaultAddress, abi: VAULT_ABI, functionName, args, account: this.account,
    });
  }
}
