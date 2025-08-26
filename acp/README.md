# ACP Buy/Sell Service (Operari Wrapper)

Purpose: Wrap Operari's existing trade service behind ACP buyer↔seller flows so users can ask a Butler agent to buy/sell coins and Operari executes the job and charges via ACP.

## Flow

1. User → Butler: "Buy 10 VIRTUAL"
2. Butler (Buyer agent): Creates ACP job with a Buy/Sell service_requirement
3. Seller service (Operari adapter): Responds, validates, quotes, executes/prepares swap
4. Delivery: Results returned via ACP deliverables to Buyer → User

## Repos & Boundaries

- This `acp/` repo hosts ACP-facing logic only:
  - `buyer/` Butler agent orchestration (VirtualsACP)
  - `seller/` Adapter that wraps Operari trade logic and exposes a reporting API
  - `common/` Schemas and helpers
- Operari trade logic remains in `operari-server/`
  - Option A: Seller adapter calls Operari HTTP endpoints (recommended boundary)
  - Option B: Import Operari modules directly (tighter coupling)

## Service Requirement Schema (input)

- fromToken (address or symbol)
- toToken (address or symbol)
- amount (human units)
- side ("buy" | "sell")
- slippageBps (int, default 100)
- recipient (address, defaults to buyer wallet)
- chain ("base")
- notes (optional)

## Deliverable Schema (output)

- quote (string summary)
- non_custodial_bundle (optional):
  - approvalData (optional)
  - transactionData (to, data, value, gas)
- custodial_result (optional):
  - txHashes (list)
  - receipts (opaque data)
  - actualOutAmount
- meta: minOut, priceImpact, fees

## Run

1) Create env

Copy `.env.example` → `.env` and fill values.

2) Install deps

```
pip install -r acp/requirements.txt
```

3) Start Seller reporting API (optional but recommended)

```
python -m acp.seller.reporting_api
```

4) Run Seller adapter (as an ACP seller process)

```
python -m acp.seller.adapter
```

5) Run Buyer (Butler)

```
python -m acp.buyer.butler
```

## Notes

- Start with non-custodial delivery (return tx bundle), then add custodial execution.
- Enforce token allowlist and slippage caps in the seller adapter.
- Pricing exposed via NEGOTIATION as `job.price`.
- For integration, expose Operari trade APIs that the seller adapter can call.
