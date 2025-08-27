import os
import sys
import threading
import json
from ast import literal_eval

from dotenv import load_dotenv

# Prefer local SDK at ../acp-python over installed site-packages
#_ACPROOT = os.path.dirname(os.path.dirname(__file__))  # .../acp
#_SDK_DIR = os.path.join(os.path.dirname(_ACPROOT), "acp-python")  # sibling to acp
#if _SDK_DIR not in sys.path:
#    sys.path.insert(0, _SDK_DIR)

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG


# Make operari-server and operari-server/data importable to reuse modules
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
OPERARI_ROOT = os.path.join(BASE_DIR, "operari-server")
OPERARI_DATA = os.path.join(OPERARI_ROOT, "data")
for p in (OPERARI_ROOT, OPERARI_DATA):
    if p not in sys.path:
        sys.path.append(p)
from acp.common.schemas import TradeRequest
from data.crew.tools.tokenTools import TokenTransactionTool
import csv


load_dotenv(override=True)


def _parse_service_requirement(sr):
    if isinstance(sr, dict):
        return sr
    try:
        return json.loads(sr)
    except Exception:
        try:
            return literal_eval(sr)
        except Exception:
            return {}


# Resolve token address and decimals from symbol or address.
# Fallbacks:
# - 'ETH' maps to Base canonical ETH address with 18 decimals
# - If already an address (0x...), assume 18 decimals unless found in CSV
_TOKENS_CACHE = None
_TOKENS_CSV_PATH = os.path.join(OPERARI_ROOT, "tokens.csv")


def _load_tokens_csv():
    global _TOKENS_CACHE
    if _TOKENS_CACHE is not None:
        return _TOKENS_CACHE
    cache = {}
    try:
        with open(_TOKENS_CSV_PATH, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            # Expect header like: Token,Full Name,Contract Address,decimals
            next(reader, None)
            for row in reader:
                if len(row) < 4:
                    continue
                sym = str(row[0]).strip()
                addr = str(row[2]).strip()
                try:
                    dec = int(str(row[3]).strip())
                except Exception:
                    dec = 18
                if sym:
                    cache[sym.upper()] = {"address": addr, "decimals": dec}
    except Exception as e:
        print(f"[SELLER] Warning: failed to load tokens.csv at {_TOKENS_CSV_PATH}: {e}")
    _TOKENS_CACHE = cache
    return _TOKENS_CACHE


def _resolve_token(value: str):
    """
    Returns tuple (address, decimals).
    Accepts symbol (e.g., 'USDC', 'ETH') or address (0x...).
    """
    if not value:
        raise ValueError("Token value is empty")
    v = str(value).strip()
    # Base canonical ETH (WETH) address used by TokenTransactionTool to skip approvals
    ETH_ADDR = "0x4200000000000000000000000000000000000006"
    if v.lower() == "eth":
        return ETH_ADDR, 18
    if v.startswith("0x") and len(v) == 42:
        # Try to enrich decimals from CSV if present by matching address
        tokens = _load_tokens_csv()
        for sym, info in tokens.items():
            if info.get("address", "").lower() == v.lower():
                return info.get("address"), int(info.get("decimals", 18))
        return v, 18
    # Symbol path
    tokens = _load_tokens_csv()
    info = tokens.get(v.upper())
    if not info:
        raise ValueError(f"Unknown token symbol '{v}'. Please use address or add to tokens.csv")
    return info.get("address"), int(info.get("decimals", 18))


def seller():
    env = EnvSettings()

    def on_new_task(job: ACPJob, memo_to_sign=None):
        print(f"[SELLER] on_new_task: phase={job.phase} job_id={getattr(job, 'id', None)} memos={len(job.memos)}")
        
        if job.phase == ACPJobPhase.REQUEST:
            print("[SELLER] REQUEST received. Checking memos for NEGOTIATION transition...")
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.NEGOTIATION:
                    print("[SELLER] Accepting request -> moving to NEGOTIATION")
                    job.respond(True)
                    break
        
        elif job.phase == ACPJobPhase.TRANSACTION:
            print("[SELLER] TRANSACTION received. Preparing quote/tx bundle and moving to EVALUATION...")
            
            # Find the ORIGINAL memo with trade data (not payment confirmation)
            original_trade_memo = None
            for memo in job.memos:
                if memo.content and memo.content.strip().startswith('{') and 'side' in memo.content:
                    original_trade_memo = memo
                    break
            
            if not original_trade_memo:
                print("[SELLER] ERROR: Could not find original trade request memo")
                err_payload = IDeliverable(
                    type="object",
                    value={
                        "error": "MISSING_TRADE_DATA",
                        "message": "Original trade request data not found in memos",
                    },
                )
                job.deliver(err_payload)
                return
            
            # Find the EVALUATION memo to respond to
            evaluation_memo = None
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.EVALUATION:
                    evaluation_memo = memo
                    break
            
            if not evaluation_memo:
                print("[SELLER] ERROR: Could not find evaluation memo")
                err_payload = IDeliverable(
                    type="object",
                    value={
                        "error": "MISSING_EVALUATION_MEMO",
                        "message": "Evaluation memo not found",
                    },
                )
                job.deliver(err_payload)
                return
            
            try:
                print(f"[DEBUG] Reading from original trade memo: {original_trade_memo.content}")
                requirements = _parse_service_requirement(original_trade_memo.content)
                tr = TradeRequest.from_dict(requirements)
                
                # Resolve tokens and decimals
                sell_addr, sell_dec = _resolve_token(tr.fromToken)
                buy_addr, _ = _resolve_token(tr.toToken)
                recipient = tr.recipient or env.SELLER_AGENT_WALLET_ADDRESS

                # Build using Operari internal tool (KyberSwap)
                tool = TokenTransactionTool()
                tool_resp_raw = tool._run(
                    buy_token=buy_addr,
                    sell_token=sell_addr,
                    sell_amount=str(tr.amount),
                    wallet_address=recipient,
                    sell_token_decimals=int(sell_dec),
                )

                # Parse tool response
                tool_resp = json.loads(tool_resp_raw) if isinstance(tool_resp_raw, str) else tool_resp_raw
                if "error" in tool_resp:
                    raise RuntimeError(tool_resp.get("error"))

                tx_section = tool_resp.get("transaction", {})
                tx_data = tx_section.get("transactionData") or tx_section.get("transaction") or {}
                needs_approval = bool(tx_section.get("needsApproval"))
                approval_data = tx_section.get("approvalData") or {}

                meta = {
                    "quoteSource": "KyberSwap via TokenTransactionTool",
                    "sellAmount": tx_section.get("sellAmount"),
                    "sellToken": tx_section.get("sellToken"),
                    "buyToken": tx_section.get("buyToken"),
                    "gasPriceGwei": tx_data.get("gasPriceGwei"),
                    "totalGas": tx_data.get("totalGas"),
                    "gasUsd": tx_data.get("gasUsd"),
                    "needsApproval": needs_approval,
                }
                summary = f"{tr.side.upper()} {tr.amount} {tr.fromToken} -> {tr.toToken} on {tr.chain}"

                bundle = {
                    "transactionData": tx_data,
                }
                if needs_approval and approval_data:
                    bundle["approvalData"] = approval_data

                delivery_data = IDeliverable(
                    type="object",
                    value={
                        "quote": summary,
                        "non_custodial_bundle": bundle,
                        "meta": meta,
                    },
                )
                print(f"[SELLER] Delivering non-custodial bundle (preview): {str(delivery_data.model_dump())[:200]}...")
                job.deliver(delivery_data)
                
            except Exception as e:
                from virtuals_acp.models import IDeliverable
                err_payload = IDeliverable(
                    type="object",
                    value={
                        "error": "QUOTE_OR_BUILD_FAILED",
                        "message": str(e),
                    },
                )
                print(f"[SELLER] Error building delivery: {e}")
                job.deliver(err_payload)  # Now passing an IDeliverable instance

    if env.WHITELISTED_WALLET_PRIVATE_KEY is None:
        raise ValueError("WHITELISTED_WALLET_PRIVATE_KEY is not set")
    if env.SELLER_ENTITY_ID is None:
        raise ValueError("SELLER_ENTITY_ID is not set")

    # Allow overriding the RPC via .env without touching SDK
    rpc_override = os.getenv("BASE_MAINNET_RPC_URL")
    config = replace(BASE_MAINNET_CONFIG, rpc_url=rpc_override) if rpc_override else BASE_MAINNET_CONFIG

    print("[SELLER] Using config:", {
        "chain_env": config.chain_env,
        "rpc_url": config.rpc_url,
        "contract": config.contract_address,
    })
    print("[SELLER] Agent:", env.SELLER_AGENT_WALLET_ADDRESS, "Entity:", env.SELLER_ENTITY_ID)

    VirtualsACP(
        wallet_private_key=env.WHITELISTED_WALLET_PRIVATE_KEY,
        agent_wallet_address=env.SELLER_AGENT_WALLET_ADDRESS,
        on_new_task=on_new_task,
        entity_id=env.SELLER_ENTITY_ID,
        config=config,
    )

    print("Waiting for new task...")
    threading.Event().wait()


if __name__ == "__main__":
    seller()