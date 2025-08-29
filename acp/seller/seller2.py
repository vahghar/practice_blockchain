import os
import sys
from web3 import Web3
import threading
import json
from ast import literal_eval
import logging
from dotenv import load_dotenv

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG
from virtuals_acp.models import IDeliverable, GenericPayload, PayloadType, FundResponsePayload


# Make operari-server and operari-server/data importable to reuse modules
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
OPERARI_ROOT = os.path.join(BASE_DIR, "operari-server")
OPERARI_DATA = os.path.join(OPERARI_ROOT, "data")
for p in (OPERARI_ROOT, OPERARI_DATA):
    if p not in sys.path:
        sys.path.append(p)
from acp.common.schemas import TradeRequest
from data.crew.tools.tokenTools import TokenTransactionTool
from data.utils import check_token_approval, approve_unlimited
import csv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


def execute_swap_transaction(tx_data, private_key, rpc_url):
    """
    Execute the actual swap transaction on-chain.
    Returns the transaction hash on success, None on failure.
    """
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not web3.is_connected():
            print("[SELLER] Failed to connect to RPC")
            return None
        
        account = web3.eth.account.from_key(private_key)
        wallet_address = account.address
        nonce = web3.eth.get_transaction_count(wallet_address)

        tx_value = int(tx_data.get('value') or '0')
        tx_gas = int(tx_data.get('gas') or '200000')
        
        transaction = {
            'to': web3.to_checksum_address(tx_data['to']),
            'data': tx_data['data'],
            'value': tx_value,
            'gas': tx_gas,
            'gasPrice': web3.eth.gas_price,
            'nonce': nonce,
            'chainId': 8453
        }
        
        print(f"[SELLER] Executing swap transaction for: {wallet_address}")
        signed_txn = web3.eth.account.sign_transaction(transaction, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        print(f"[SELLER] Transaction sent: {tx_hash.hex()}")
        
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            print(f"[SELLER] Swap successful! Gas used: {receipt.gasUsed}")
            return tx_hash.hex()
        else:
            print(f"[SELLER] Swap failed! Transaction reverted")
            return None
            
    except Exception as e:
        print(f"[SELLER] Swap execution error: {e}")
        return None

def execute_approval_transaction(approval_data, private_key, rpc_url):
    """
    Execute token approval transaction if needed.
    Returns True on success, False on failure.
    """
    try:
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        account = web3.eth.account.from_key(private_key)
        nonce = web3.eth.get_transaction_count(account.address)
        
        approval_tx = {
            'to': web3.to_checksum_address(approval_data['to']),
            'data': approval_data['data'],
            'value': 0,
            'gas': int(approval_data.get('gas', '100000')),
            'gasPrice': web3.to_wei(float(approval_data.get('gasPriceGwei', '0.1')), 'gwei'),
            'nonce': nonce
        }
        
        print("[SELLER] Executing approval transaction...")
        signed_txn = web3.eth.account.sign_transaction(approval_tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            print("[SELLER] Approval successful!")
            return True
        else:
            print("[SELLER] Approval failed!")
            return False
            
    except Exception as e:
        print(f"[SELLER] Approval execution error: {e}")
        return False

def seller():
    env = EnvSettings()
    
    designated_wallet_private_key = os.getenv("TEST_WALLET_PRIVATE_KEY")
    if designated_wallet_private_key is None:
        raise ValueError("DESIGNATED_WALLET_PRIVATE_KEY is not set")
    
    def on_new_task(job: ACPJob, memo_to_sign=None):
        print(f"[SELLER] on_new_task: phase={job.phase} job_id={getattr(job, 'id', None)} memos={len(job.memos)}")
        
        if job.phase == ACPJobPhase.REQUEST:
            print("[SELLER] REQUEST received. Checking memos for NEGOTIATION transition...")
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.NEGOTIATION:
                    print("[SELLER] Accepting request -> moving to NEGOTIATION")
                    test_wallet_address=os.getenv("TEST_WALLET_ADDRESS")
                    '''payload = IDeliverable(
                        type="object",
                        value={
                            "walletAddress": test_wallet_address
                        }
                    )'''
                    payload = GenericPayload(
                        type=PayloadType.FUND_RESPONSE,
                        data=FundResponsePayload(
                            walletAddress=test_wallet_address,
                            reporting_api_endpoint="YOUR_API_ENDPOINT_HERE"
                        )
                    )
                    job.respond(True, payload=payload)
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
                
                # --- The key change: Use the designated wallet address for the swap. ---
                web3 = Web3()
                account = web3.eth.account.from_key(designated_wallet_private_key)
                recipient = account.address
    
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

                # --- NEW LOGIC: DIRECTLY EXECUTE TRANSACTIONS ---
                rpc_url = os.getenv("BASE_MAINNET_RPC_URL")
                KYBER_ROUTER_ADDRESS = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"
                '''
                # Check and grant approval
                is_approved = check_token_approval(
                    token_address=sell_addr,
                    wallet_address=recipient,
                    spender_address=KYBER_ROUTER_ADDRESS
                )

                if not is_approved:
                    print(f"[SELLER] Token {sell_addr} not approved for spender {KYBER_ROUTER_ADDRESS}. Approving now...")
                    try:
                        approve_unlimited(
                            token_address=sell_addr,
                            wallet_address=recipient,
                            spender_address=KYBER_ROUTER_ADDRESS,
                            private_key=designated_wallet_private_key
                        )
                        print("[SELLER] Approval transaction successful.")
                    except Exception as e:
                        raise RuntimeError(f"Approval transaction failed: {e}")
                else:
                    print(f"[SELLER] Token {sell_addr} already approved for spender.")
                '''
                # Execute the swap transaction
                print("[SELLER] Executing swap transaction...")
                tx_hash = execute_swap_transaction(tx_data, designated_wallet_private_key, rpc_url)
    
                if tx_hash:
                    delivery_data = IDeliverable(
                        type="object",
                        value={
                            "status": "SUCCESS",
                            "message": "Swap completed.",
                            "transaction_hash": tx_hash,
                            "metadata": {
                                "sellToken": tr.fromToken,
                                "buyToken": tr.toToken,
                                "sellAmount": tr.amount
                            }
                        }
                    )
                    job.deliver(delivery_data)
                    print(f"[SELLER] Delivered successful swap status. Tx hash: {tx_hash}")
                else:
                    delivery_data = IDeliverable(
                        type="object",
                        value={
                            "status": "FAILURE",
                            "message": "Swap execution failed on-chain.",
                            "metadata": {
                                "sellToken": tr.fromToken,
                                "buyToken": tr.toToken,
                                "sellAmount": tr.amount
                            }
                        }
                    )
                    job.deliver(delivery_data)
                    print("[SELLER] Delivered failed swap status.")
    
            except Exception as e:
                print(f"[SELLER] Error during transaction phase: {e}")
                err_payload = IDeliverable(
                    type="object",
                    value={
                        "error": "QUOTE_OR_BUILD_FAILED",
                        "message": str(e),
                    },
                )
                job.deliver(err_payload)
                return

    # The following code should be at the same indentation level as the on_new_task definition
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
