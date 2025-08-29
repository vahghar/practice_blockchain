import os
import sys
import threading
from datetime import datetime, timedelta

from dotenv import load_dotenv
from web3 import Web3

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.models import ACPGraduationStatus, ACPOnlineStatus
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG
import sys
import os

# Add operari-server to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPERARI_ROOT = os.path.join(BASE_DIR, "operari-server")
if OPERARI_ROOT not in sys.path:
    sys.path.append(OPERARI_ROOT)

from data.utils import check_token_approval, approve_unlimited

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


def check_balance_and_allowance(env, token_address, spender_address, required_amount_wei, rpc_url):
    """
    Check if the buyer wallet has enough balance and allowance.

    Args:
        env: Environment settings object
        token_address: ERC20 token contract address
        spender_address: Contract that needs allowance
        required_amount_wei: Amount in token's smallest units
        rpc_url: RPC endpoint to use

    Returns:
        (bool, bool): (has_balance, has_allowance)
    """
    try:
        # Initialize Web3 with the provided RPC URL
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            print("[ERROR] Failed to connect to RPC endpoint")
            return False, False

        # Standard ERC20 ABI for balanceOf and allowance functions
        token_abi = [
            {
                "constant": True,
                "inputs": [{"name": "_owner", "type": "address"}],
                "name": "balanceOf",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            },
            {
                "constant": True,
                "inputs": [
                    {"name": "_owner", "type": "address"},
                    {"name": "_spender", "type": "address"}
                ],
                "name": "allowance",
                "outputs": [{"name": "", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function"
            }
        ]

        # Ensure addresses are checksummed
        token_address = Web3.to_checksum_address(token_address)
        spender_address = Web3.to_checksum_address(spender_address)
        wallet_address = Web3.to_checksum_address(env.BUYER_AGENT_WALLET_ADDRESS)

        # Create contract instance
        token = w3.eth.contract(address=token_address, abi=token_abi)

        # Get balance and allowance
        balance = token.functions.balanceOf(wallet_address).call()
        allowance = token.functions.allowance(wallet_address, spender_address).call()

        # Log the values for debugging
        print(f"[BALANCE] Wallet: {wallet_address}")
        print(f"[BALANCE] Token: {token_address}")
        print(f"[BALANCE] Current: {balance} wei")
        print(f"[BALANCE] Required: {required_amount_wei} wei")
        print(f"[ALLOWANCE] Spender: {spender_address}")
        print(f"[ALLOWANCE] Current: {allowance} wei")
        print(f"[ALLOWANCE] Required: {required_amount_wei} wei")

        # Check if we have enough balance and allowance
        has_balance = balance >= required_amount_wei
        has_allowance = allowance >= required_amount_wei

        if not has_balance:
            print("[WARNING] Insufficient token balance")
        if not has_allowance:
            print("[WARNING] Insufficient token allowance")

        return has_balance, has_allowance

    except Exception as e:
        print(f"[ERROR] Error in check_balance_and_allowance: {str(e)}")
        return False, False


def buyer():
    env = EnvSettings()
    '''
    def on_new_task(job: ACPJob, memo_to_sign=None):
        if job.phase == ACPJobPhase.NEGOTIATION:
            for memo in job.memos:
                print("\n[BUYER] Processing job:", job.id)
                print(f"[DETAILS] Phase: {job.phase}")
                print(f"[DETAILS] Price: {job.price} USDC")
                print(f"[DETAILS] Buyer wallet: {env.BUYER_AGENT_WALLET_ADDRESS}")
                print(f"[DETAILS] Provider wallet: {job.provider_address if hasattr(job, 'provider_address') else 'unknown'}")
            
                if memo.next_phase == ACPJobPhase.TRANSACTION:
                    print("\n[PAYMENT] Initiating payment for job:", job.id)
                    
                    try:
                        # Get the required price from the job
                        price = float(job.price)
                        if price <= 0:
                            print("[ERROR] Invalid price in job")
                            return
                            
                        print(f"[PAYMENT] Amount to pay: {price} USDC")
                        required_amount_wei = int(price * 10**6)
                        has_balance, has_allowance = check_balance_and_allowance(
                            env,
                            token_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 
                            spender_address="0x6a1FE26D54ab0d3E1e3168f2e0c0cDa5cC0A0A4A", 
                            required_amount_wei=required_amount_wei,
                            rpc_url=config.rpc_url
                        )
                        if not has_balance:
                            print("[ERROR] Cannot proceed: insufficient USDC balance")
                            return
                        if not has_allowance:
                            print("[ERROR] Cannot proceed: insufficient allowance for KyberSwap router")
                            return
                        # Before paying in your buyer() function

                        #usdc_address="0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
                        #kyber_router="0x6a1FE26D54ab0d3E1e3168f2e0c0cDa5cC0A0A4A"
                        #buyer_wallet="0x0651e71bfB734Ecf2E294d79FF28e17a17cc9877"
                        #buyer_private_key=env.WHITELISTED_WALLET_PRIVATE_KEY
                        
                        #if not check_token_approval(usdc_address, buyer_wallet, kyber_router):
                        #    print("[INFO] No allowance detected. Sending approval transaction...")
                        #    tx_hash = approve_unlimited(usdc_address, buyer_wallet, kyber_router, buyer_private_key)
                        #    print(f"[INFO] Approval tx sent: {tx_hash.hex()}")
                        #    w3.eth.wait_for_transaction_receipt(tx_hash)
                        #    print("[INFO] Approval confirmed, proceeding with payment...")
                        #else:
                        #    print("[INFO] Token already approved, proceeding with payment...")

                        # Proceed with payment using the ACP SDK
                        # The SDK will handle approvals and payments using the whitelisted wallet
                        print("[PAYMENT] Sending payment transaction...")
                        
                        # Call job.pay() which will be handled by the ACP SDK
                        # The SDK will use the whitelisted wallet's private key for signing
                        # but will move funds from the buyer's wallet (env.BUYER_AGENT_WALLET_ADDRESS)
                        tx_hash = job.pay(price)
                        
                        if tx_hash:
                            print(f"[SUCCESS] Payment transaction sent successfully: {tx_hash}")
                        else:
                            print("[ERROR] Failed to send payment transaction")
                            
                    except Exception as e:
                        print(f"[ERROR] Payment failed: {str(e)}")
                        import traceback
                        traceback.print_exc()
                        return
                        
                    break

        elif job.phase == ACPJobPhase.COMPLETED:
            print("Job completed", job)
        elif job.phase == ACPJobPhase.REJECTED:
            print("Job rejected", job)
    '''

    def on_new_task(job: ACPJob, memo_to_sign=None):
        if job.phase == ACPJobPhase.NEGOTIATION:
            # Your existing payment logic here...
            pass
            
        elif job.phase == ACPJobPhase.TRANSACTION:
            print("\n[BUYER] TRANSACTION phase - checking for funds requests")
            
            # Look for funds request memos
            for memo in job.memos:
                if hasattr(memo, 'type') and getattr(memo.type, 'value', None) == 3:  # FUNDS_REQUEST type
                    print(f"[BUYER] Funds request found: {memo.amount} {memo.reason}")
                    
                    # Get the designated wallet from job response
                    designated_wallet = None
                    for prev_memo in job.memos:
                        if hasattr(prev_memo, 'walletAddress') and prev_memo.walletAddress:
                            designated_wallet = prev_memo.walletAddress
                            break
                    
                    if not designated_wallet:
                        print("[BUYER] ERROR: No designated wallet found in job memos")
                        return
                    
                    # Transfer funds to designated wallet
                    print(f"[BUYER] Transferring {memo.amount} to designated wallet: {designated_wallet}")
                    
                    try:
                        tx_hash = acp.transferFunds(
                            jobId=job.id,
                            amount=float(memo.amount),
                            recipient=designated_wallet,
                            reason=memo.reason,
                            nextPhase=ACPJobPhase.TRANSACTION
                        )
                        
                        if tx_hash:
                            print(f"[SUCCESS] Funds transferred: {tx_hash}")
                        else:
                            print("[ERROR] Failed to transfer funds")
                            
                    except Exception as e:
                        print(f"[ERROR] Transfer failed: {str(e)}")
                        import traceback
                        traceback.print_exc()
                    break

    '''def on_evaluate(job: ACPJob):
        print("Evaluation function called", job.memos)
        for memo in job.memos:
            if memo.next_phase == ACPJobPhase.COMPLETED:
                job.evaluate(True)
                break'''

    def on_evaluate(job: ACPJob):
        """Handle the evaluation phase - execute approval and swap transactions"""
        print("Evaluation function called", job.memos)
    
        # Find the delivery memo with the swap data
        delivery_memo = None
        for memo in job.memos:
            if hasattr(memo, 'type') and memo.type.value == 4:  # OBJECT_URL type
                delivery_memo = memo
                break
    
        if not delivery_memo:
            print("[BUYER] No delivery memo found")
            job.evaluate(False)
            return
    
        try:
            # Parse the delivery content
            import json
            delivery_data = json.loads(delivery_memo.content)
            
            # Check if there's an error in the delivery
            if "error" in delivery_data.get("value", {}):
                error_msg = delivery_data["value"].get("message", "Unknown error")
                print(f"[BUYER] Seller delivery contains error: {error_msg}")
                job.evaluate(False)
                return
        
            # Get the transaction bundle
            bundle = delivery_data.get("value", {}).get("non_custodial_bundle", {})
            if not bundle:
                print("[BUYER] No transaction bundle found in delivery")
                job.evaluate(False)
                return
        
            # Execute approval if needed
            if bundle.get("approvalData"):
                print("[BUYER] Approval required - executing approval transaction...")
                approval_success = execute_approval_transaction(
                    bundle["approvalData"], 
                    env.WHITELISTED_WALLET_PRIVATE_KEY, 
                    config.rpc_url
                )
                if not approval_success:
                    print("[BUYER] Approval transaction failed")
                    job.evaluate(False)
                    return
            
                # Wait a moment for approval to be mined
                import time
                time.sleep(5)
        
            # Execute the swap transaction
            print("[BUYER] Executing swap transaction...")
            tx_data = bundle.get("transactionData", {})
            swap_success = execute_swap_transaction(
                tx_data, 
                env.WHITELISTED_WALLET_PRIVATE_KEY, 
                config.rpc_url
            )
            
            if swap_success:
                print("[BUYER] Swap executed successfully!")
                job.evaluate(True)
            else:
                print("[BUYER] Swap execution failed")
                job.evaluate(False)
                
        except Exception as e:
            print(f"[BUYER] Error during evaluation: {e}")
            import traceback
            traceback.print_exc()
            job.evaluate(False)
        
        # Original logic as fallback
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
            "fromToken": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  #usdc
            "toToken": "0x50c5725949a6f0c72e6c4a641f24049a917db0cb", #dai
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