import os
import sys
import threading
import json
import time
from ast import literal_eval

from dotenv import load_dotenv
from web3 import Web3
import secrets

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG
from virtuals_acp.models import IDeliverable
from virtuals_acp.models import NegotiationPayload

job_designated_wallets = {}  # GLOBAL storage for wallets
job_trade_details = {}  

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

JOBS_DIR = "/tmp/acp_jobs"  # or use a proper data directory

def save_job_data(job_id, wallet_info, trade_details):
    """Save job data to file for monitor to read"""
    os.makedirs(JOBS_DIR, exist_ok=True)
    
    job_data = {
        "wallet_info": wallet_info,
        "trade_details": trade_details,
        "status": "waiting_for_funds",
        "created_at": time.time()
    }
    
    with open(f"{JOBS_DIR}/{job_id}.json", "w") as f:
        json.dump(job_data, f)
    
    print(f"[SELLER] Saved job data for {job_id}")

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

_TOKENS_CACHE = None
_TOKENS_CSV_PATH = os.path.join(OPERARI_ROOT, "tokens.csv")

def generate_new_wallet():
    """Generate a new Ethereum wallet for designated funds"""
    private_key = "0x" + secrets.token_hex(32)
    w3 = Web3()
    account = w3.eth.account.from_key(private_key)
    return {
        "address": account.address,
        "private_key": private_key
    }

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
    Returns tuple (address, decimals)
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
    global job_designated_wallets

    YOUR_TEST_WALLET = {
        "address": os.getenv("TEST_WALLET_ADDRESS"),
        "private_key": os.getenv("TEST_WALLET_PRIVATE_KEY")
    }
    # VALIDATION FIRST
    if env.WHITELISTED_WALLET_PRIVATE_KEY is None:
        raise ValueError("WHITELISTED_WALLET_PRIVATE_KEY is not set")
    if env.SELLER_ENTITY_ID is None:
        raise ValueError("SELLER_ENTITY_ID is not set")

    # Config definition (ONCE)
    rpc_override = os.getenv("BASE_MAINNET_RPC_URL")
    config = replace(BASE_MAINNET_CONFIG, rpc_url=rpc_override) if rpc_override else BASE_MAINNET_CONFIG

    print("[SELLER] Using config:", {
        "chain_env": config.chain_env,
        "rpc_url": config.rpc_url,
        "contract": config.contract_address,
    })
    print("[SELLER] Agent:", env.SELLER_AGENT_WALLET_ADDRESS, "Entity:", env.SELLER_ENTITY_ID)

    '''def on_new_task(job: ACPJob, memo_to_sign=None):
        print(f"[SELLER] on_new_task: phase={job.phase} job_id={getattr(job, 'id', None)} memos={len(job.memos)}")
        
        global job_designated_wallets, job_trade_details
        
        if job.phase == ACPJobPhase.REQUEST:
            print("[SELLER] REQUEST received. Checking memos for NEGOTIATION transition...")
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.NEGOTIATION:
                    designated_wallet = YOUR_TEST_WALLET
                    print(f"[SELLER] Generated designated wallet: {designated_wallet['address']}")
                    
                    job_designated_wallets[job.id] = designated_wallet
                    
                    job.respond(
                        accept=True,
                        payload={"walletAddress": designated_wallet['address']},  # ✅ Correct way
                        reason="Ready to process trade"
                    )
                    break
        
        elif job.phase == ACPJobPhase.TRANSACTION:
            print("[SELLER] TRANSACTION received. Preparing funds request...")
            
            original_trade_memo = None
            for memo in job.memos:
                if memo.content and memo.content.strip().startswith('{') and 'side' in memo.content:
                    original_trade_memo = memo
                    break
            
            if not original_trade_memo:
                print("[SELLER] ERROR: Could not find original trade request memo")
                return
            
            try:
                requirements = _parse_service_requirement(original_trade_memo.content)
                tr = TradeRequest.from_dict(requirements)

                # FIX: Get the wallet from storage instead of recreating it
                designated_wallet = job_designated_wallets.get(job.id)
                if not designated_wallet:
                    print(f"[SELLER] ERROR: No designated wallet found for job {job.id}")
                    return

                # Store trade details
                trade_details = {
                    'fromToken': tr.fromToken,
                    'toToken': tr.toToken, 
                    'amount': tr.amount,
                    'sell_decimals': 6
                }
                
                job_trade_details[job.id] = trade_details
                
                # FIXED: Save to file for monitor to read
                save_job_data(job.id, designated_wallet, trade_details)
                print(f"[SELLER] Registered trade details for job {job.id}")
                
                # Use the GLOBAL acp_instance
                global acp_instance
                acp_instance.requestFunds(
                    jobId=job.id,
                    amount=float(tr.amount),
                    reason=f"Funds needed for {tr.fromToken}->{tr.toToken} swap",
                    nextPhase=ACPJobPhase.TRANSACTION
                )
                
            except Exception as e:
                print(f"[SELLER] Error in funds request: {e}")
    '''

    def on_new_task(job: ACPJob, memo_to_sign=None):
    print(f"[SELLER] on_new_task: phase={job.phase} job_id={getattr(job, 'id', None)} memos={len(job.memos)}")
    
    global job_designated_wallets, job_trade_details
    
    if job.phase == ACPJobPhase.REQUEST:
        print("[SELLER] REQUEST received. Checking memos for NEGOTIATION transition...")
        for memo in job.memos:
            if memo.next_phase == ACPJobPhase.NEGOTIATION:
                designated_wallet = YOUR_TEST_WALLET
                print(f"[SELLER] Generated designated wallet: {designated_wallet['address']}")
                
                job_designated_wallets[job.id] = designated_wallet
                
                # Create proper NegotiationPayload with wallet address
                payload = NegotiationPayload(
                    service_requirement={"walletAddress": designated_wallet['address']}
                )
                
                job.respond(
                    accept=True,
                    payload=payload,
                    reason="Ready to process trade"
                )
                break
    
    elif job.phase == ACPJobPhase.TRANSACTION:
        print("[SELLER] TRANSACTION received. Preparing funds request...")
        
        original_trade_memo = None
        for memo in job.memos:
            if memo.content and memo.content.strip().startswith('{') and 'side' in memo.content:
                original_trade_memo = memo
                break
        
        if not original_trade_memo:
            print("[SELLER] ERROR: Could not find original trade request memo")
            return
        
        try:
            requirements = _parse_service_requirement(original_trade_memo.content)
            tr = TradeRequest.from_dict(requirements)

            designated_wallet = job_designated_wallets.get(job.id)
            if not designated_wallet:
                print(f"[SELLER] ERROR: No designated wallet found for job {job.id}")
                return

            trade_details = {
                'fromToken': tr.fromToken,
                'toToken': tr.toToken, 
                'amount': tr.amount,
                'sell_decimals': 6
            }
            
            job_trade_details[job.id] = trade_details
            
            save_job_data(job.id, designated_wallet, trade_details)
            print(f"[SELLER] Registered trade details for job {job.id}")
            
            global acp_instance
            acp_instance.requestFunds(
                jobId=job.id,
                amount=float(tr.amount),
                reason=f"Funds needed for {tr.fromToken}->{tr.toToken} swap",
                nextPhase=ACPJobPhase.TRANSACTION
            )
            
        except Exception as e:
            print(f"[SELLER] Error in funds request: {e}")


    # Create VirtualsACP instance ONCE
    global acp_instance
    acp_instance = VirtualsACP(
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