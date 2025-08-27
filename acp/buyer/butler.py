import os
import sys
import threading
from datetime import datetime, timedelta

from dotenv import load_dotenv
from web3 import Web3

# Prefer local SDK at ../acp-python over installed site-packages
#_ACPROOT = os.path.dirname(os.path.dirname(__file__))  # .../acp
#_SDK_DIR = os.path.join(os.path.dirname(_ACPROOT), "acp-python")  # sibling to acp
# if _SDK_DIR not in sys.path:
#     sys.path.insert(0, _SDK_DIR)

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.models import ACPGraduationStatus, ACPOnlineStatus
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG

load_dotenv(override=True)

def execute_swap_transaction(tx_data, private_key, rpc_url):
    """
    Execute the actual swap transaction on-chain.
    
    Args:
        tx_data: Transaction data from seller's delivery
        private_key: Buyer's wallet private key
        rpc_url: RPC endpoint for the network
        
    Returns:
        bool: True if transaction succeeded, False otherwise
    """
    try:
        # Initialize Web3
        web3 = Web3(Web3.HTTPProvider(rpc_url))
        if not web3.is_connected():
            print("[BUYER] Failed to connect to RPC")
            return False
        
        # Get account from private key
        account = web3.eth.account.from_key(private_key)
        wallet_address = account.address
        
        # Get current nonce
        nonce = web3.eth.get_transaction_count(wallet_address)
        
        # Prepare transaction
        transaction = {
            'to': web3.to_checksum_address(tx_data['to']),
            'data': tx_data['data'],
            'value': int(tx_data.get('value', '0')),
            'gas': int(tx_data.get('totalGas', '200000')),  # fallback gas limit
            'gasPrice': web3.to_wei(float(tx_data.get('gasPriceGwei', '0.1')), 'gwei'),
            'nonce': nonce
        }
        
        print(f"[BUYER] Executing swap transaction: {transaction['to']}")
        print(f"[BUYER] Value: {transaction['value']} wei")
        print(f"[BUYER] Gas limit: {transaction['gas']}")
        
        # Sign transaction
        signed_txn = web3.eth.account.sign_transaction(transaction, private_key)
        
        # Send transaction
        tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        print(f"[BUYER] Transaction sent: {tx_hash.hex()}")
        
        # Wait for confirmation
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            print(f"[BUYER] Swap successful! Gas used: {receipt.gasUsed}")
            return True
        else:
            print(f"[BUYER] Swap failed! Transaction reverted")
            return False
            
    except Exception as e:
        print(f"[BUYER] Swap execution error: {e}")
        return False


def execute_approval_transaction(approval_data, private_key, rpc_url):
    """
    Execute token approval transaction if needed.
    
    Args:
        approval_data: Approval transaction data
        private_key: Buyer's wallet private key
        rpc_url: RPC endpoint
        
    Returns:
        bool: True if approval succeeded, False otherwise
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
        
        print("[BUYER] Executing approval transaction...")
        signed_txn = web3.eth.account.sign_transaction(approval_tx, private_key)
        tx_hash = web3.eth.send_raw_transaction(signed_txn.rawTransaction)
        
        receipt = web3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            print("[BUYER] Approval successful!")
            return True
        else:
            print("[BUYER] Approval failed!")
            return False
            
    except Exception as e:
        print(f"[BUYER] Approval execution error: {e}")
        return False


def buyer():
    env = EnvSettings()

    def on_new_task(job: ACPJob, memo_to_sign=None):
        if job.phase == ACPJobPhase.NEGOTIATION:
            for memo in job.memos:
                print("[DEBUG][BUYER] About to pay job", job.id)
                print(f"[DEBUG][BUYER] Payment price: {job.price}")
                print(f"[DEBUG][BUYER] Buyer wallet: {env.BUYER_AGENT_WALLET_ADDRESS}")
                print(f"[DEBUG][BUYER] Provider wallet: {job.provider_address if hasattr(job, 'provider_address') else 'unknown'}")
                if memo.next_phase == ACPJobPhase.TRANSACTION:
                    print("Paying job", job.id)
                    job.pay(job.price)
                    break
        elif job.phase == ACPJobPhase.COMPLETED:
            print("Job completed", job)
        elif job.phase == ACPJobPhase.REJECTED:
            print("Job rejected", job)

    def on_evaluate(job: ACPJob):
        print("Evaluation function called", job.memos)
        for memo in job.memos:
            if memo.next_phase == ACPJobPhase.COMPLETED:
                job.evaluate(True)
                break

    if env.WHITELISTED_WALLET_PRIVATE_KEY is None:
        raise ValueError("WHITELISTED_WALLET_PRIVATE_KEY is not set")
    if env.BUYER_AGENT_WALLET_ADDRESS is None:
        raise ValueError("BUYER_AGENT_WALLET_ADDRESS is not set")
    if env.BUYER_ENTITY_ID is None:
        raise ValueError("BUYER_ENTITY_ID is not set")

    # Allow overriding the RPC via .env without touching SDK
    rpc_override = os.getenv("BASE_MAINNET_RPC_URL")
    config = replace(BASE_MAINNET_CONFIG, rpc_url=rpc_override) if rpc_override else BASE_MAINNET_CONFIG

    print("[BUYER] Using config:", {
        "chain_env": config.chain_env,
        "rpc_url": config.rpc_url,
        "contract": config.contract_address,
    })

    acp = VirtualsACP(
        wallet_private_key=env.WHITELISTED_WALLET_PRIVATE_KEY,
        agent_wallet_address=env.BUYER_AGENT_WALLET_ADDRESS,
        on_new_task=on_new_task,
        on_evaluate=on_evaluate,
        entity_id=env.BUYER_ENTITY_ID,
        config=config,
    )

    # Force direct targeting of our seller to avoid offering/provider mismatches
    provider = env.SELLER_AGENT_WALLET_ADDRESS
    print(f"[BUYER] Initiating job directly to provider: {provider}")
    job_id = acp.initiate_job(
        provider_address=provider,
        service_requirement={
            "side": "buy",
            "fromToken": "USDC",  # or zero address
            "toToken": "0x50c5725949a6f0c72e6c4a641f24049a917db0cb", 
            "amount": "0.01",  # human units of fromToken
            "slippageBps": int(os.getenv("DEFAULT_SLIPPAGE_BPS", "100")),
            "recipient": env.BUYER_AGENT_WALLET_ADDRESS,
            "chain": os.getenv("CHAIN", "base"),
            "notes": "demo request from buyer",
        },
        # Use a non-dust budget on mainnet USDC (6 decimals). Adjust if your policy expects a minimum.
        amount=0.01,
        evaluator_address=env.BUYER_AGENT_WALLET_ADDRESS,
        expired_at=datetime.now() + timedelta(days=1),
    )

    print(f"[BUYER] Job {job_id} initiated and listening for next steps...")
    threading.Event().wait()


if __name__ == "__main__":
    buyer()