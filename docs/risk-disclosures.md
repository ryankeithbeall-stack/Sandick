# SANDICK vault — risk disclosures

> This document is part of the project's defensive/educational material. It is
> **not** investment advice, a solicitation, or an offer. Do not deposit funds
> you cannot afford to lose.

## Status

- **Unaudited.** The contracts have a test suite (Python + ethereumjs) but have
  **not** had an independent security audit. Do not deploy with real funds
  before an audit.
- **Testnet only.** The live round-trip on HyperEVM/HyperCore (chainid 998) is
  not yet signed off. Several immutables (USDC system address, decimal scaling,
  `perpDexIndex`, HIP-3 asset ids) are **unverified inputs** that must be
  confirmed on testnet first.

## Market risk

- The basket is **leveraged perpetual futures**. Losses can exceed the basket's
  spot exposure and, in adverse moves, approach **total loss** of deposited
  capital.
- Equal weighting is a strategy choice, not a hedge. All seven names are in the
  same AI / data-center / storage theme and are **highly correlated** — they can
  draw down together.
- Funding rates, liquidation, and slippage on HIP-3 markets all affect NAV.

## Smart-contract & integration risk

- Bugs in the vault, the HyperCore integration, or the NAV reader could misprice
  shares or lock funds.
- **NAV-pricing risk:** share price is derived from on-chain reads
  (`accountMarginSummary` precompile + balances). A stale, manipulated, or
  misconfigured read misprices deposits and redemptions. This path warrants
  specific review.
- **Async settlement risk:** CoreWriter actions are delayed and can fail
  silently. The system mitigates this by confirming via reads and by making
  fulfillment permissionless, but liveness still depends on liquidity and the
  manager bridging funds back.

## Manager / operational risk

- The manager **cannot withdraw your funds**, but a compromised or negligent
  manager key can still trade the basket poorly within the allow-list and caps.
- Owner controls (pause, manager rotation, allow-list, order caps) are
  trusted-but-bounded: the owner can halt deposits/trading but **cannot** seize
  deposits or block exits.
- Redemption liveness depends on idle-liquidity buffers and timely bridging.

## What the controls do and don't do

| Control | Protects against | Does **not** |
|--------|------------------|--------------|
| Custody = contract | Theft by manager/owner | Bad trading losses |
| Manager allow-list | Trading un-vetted assets | Losses on allowed assets |
| Pause | Runaway deposits/trading | Trapping exits (exits stay open) |
| Order notional caps | A key churning the book | Normal-size bad trades |
| Permissionless fulfill | Manager blocking exits | Illiquidity delaying exits |

By interacting with these contracts you acknowledge these risks.
