import os
import sys
import threading
from datetime import datetime, timedelta
import logging

from dotenv import load_dotenv
from web3 import Web3

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.models import ACPGraduationStatus, ACPOnlineStatus, GenericPayload, PayloadType, FeeType, NegotiationPayload
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG
import sys
import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Add operari-server to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPERARI_ROOT = os.path.join(BASE_DIR, "operari-server")
if OPERARI_ROOT not in sys.path:
    sys.path.append(OPERARI_ROOT)

from data.utils import check_token_approval, approve_unlimited

load_dotenv(override=True)


def buyer():
    env = EnvSettings()

    def on_new_task(job: ACPJob, memo_to_sign=None):
        if job.phase == ACPJobPhase.NEGOTIATION:
            funds_request_memo = None
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.TRANSACTION:
                    funds_request_memo = memo
                    break

            if not funds_request_memo:
                # This part is commented out, but if it were active, it would need to be here.
                # print("[BUYER] ERROR: Could not find funds request memo")
                return

            print("\n[BUYER] Provider is requesting funds transfer")
            designated_wallet_address = None
            if funds_request_memo.content:
                import json
                try:
                    content_data = json.loads(funds_request_memo.content)
                    designated_wallet_address = content_data.get('data', {}).get('walletAddress')
                except json.JSONDecodeError:
                    print(f"[BUYER] Error: Could not decode memo content as JSON: {funds_request_memo.content}")

            try:
                # Pay the service fee first
                service_fee = float(job.price)
                if service_fee > 0:
                    print(f"[PAYMENT] Paying service fee: {service_fee} USDC")
                    tx_hash = job.pay(service_fee)

                # Parse original trade request to get trading amount
                original_memo = None
                for memo in job.memos:
                    if memo.content and memo.content.strip().startswith('{') and 'side' in memo.content:
                        original_memo = memo
                        break

                trading_amount = 0
                from_token = ""
                if original_memo:
                    import json
                    trade_data = json.loads(original_memo.content)
                    trading_amount = float(trade_data.get("amount", 0))
                    from_token = trade_data.get("fromToken", "")

                if trading_amount > 0:
                    print(f"[FUNDS] Transferring trading funds: {trading_amount} tokens")
                    # Use the ACP SDK's transfer_funds method via the acp_client
                    #reason_payload = GenericPayload(
                        #type=PayloadType.MESSAGE,
                    #    data=f"Trading funds for swap: {trading_amount} {from_token}"
                    #)
                    reason_payload = NegotiationPayload(
                        service_requirement=f"Trading funds for swap: {trading_amount} {from_token}"
                    )
                    fund_transfer_result = job.acp_client.transfer_funds(
                        job_id=job.id,
                        amount=trading_amount,
                        #receiver_address=env.SELLER_AGENT_WALLET_ADDRESS,
                        receiver_address=designated_wallet_address,
                        fee_amount=0,
                        fee_type=FeeType.NO_FEE,
                        #reason=f"Trading funds for swap: {trading_amount} {from_token}",
                        reason = reason_payload,
                        #reason=GenericPayload(data=f"Trading funds for swap: {trading_amount} {from_token}"),
                        next_phase=ACPJobPhase.TRANSACTION,
                        expired_at=datetime.now() + timedelta(minutes=10)
                    )

                print("\n[BUYER] Processing job:", job.id)
                print(f"[DETAILS] Phase: {job.phase}")
                print(f"[DETAILS] Price: {job.price} USDC")
                print(f"[DETAILS] Buyer wallet: {env.BUYER_AGENT_WALLET_ADDRESS}")
                print(f"[DETAILS] Provider wallet: {job.provider_address if hasattr(job, 'provider_address') else 'unknown'}")

                if funds_request_memo.next_phase == ACPJobPhase.TRANSACTION:
                    print("\n[PAYMENT] Initiating payment for job:", job.id)
                    price = float(job.price)
                    if price <= 0:
                        print("[ERROR] Invalid price in job")
                        return

                    print(f"[PAYMENT] Amount to pay: {price} USDC")
                    print("[PAYMENT] Sending payment transaction...")
                    tx_hash = job.pay(price)

                    if tx_hash:
                        print(f"[SUCCESS] Payment transaction sent successfully: {tx_hash}")
                    else:
                        print("[ERROR] Failed to send payment transaction")

            except Exception as e:
                print(f"[ERROR] An error occurred during negotiation: {str(e)}")
                import traceback
                traceback.print_exc()
                return

        elif job.phase == ACPJobPhase.COMPLETED:
            print("Job completed", job)
        elif job.phase == ACPJobPhase.REJECTED:
            print("Job rejected", job)


    def on_evaluate(job: ACPJob):
        """Handle the evaluation phase - check for seller's confirmation."""
        print("[BUYER] Evaluation function called")

        delivery_memo = None
        for memo in job.memos:
            # Look for the OBJECT_URL type memo which contains the delivery
            if hasattr(memo, 'type') and memo.type.value == 4:
                delivery_memo = memo
                break
        
        if not delivery_memo:
            print("[BUYER] No delivery memo found from seller.")
            job.evaluate(False)
            return

        try:
            import json
            delivery_data = json.loads(delivery_memo.content)
            delivery_value = delivery_data.get("value", {})

            # Check for success status and a transaction hash
            if delivery_value.get("status") == "SUCCESS" and "transaction_hash" in delivery_value:
                tx_hash = delivery_value["transaction_hash"]
                print(f"[BUYER] Seller confirmed swap success. Tx Hash: {tx_hash}")
                job.evaluate(True)
            else:
                error_msg = delivery_value.get("message", "No success message from seller.")
                print(f"[BUYER] Seller delivery did not indicate success: {error_msg}")
                job.evaluate(False)

        except Exception as e:
            print(f"[BUYER] Error parsing seller's delivery: {e}")
            job.evaluate(False)

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
            "slippageBps": int(os.getenv("DEFAULT_SLIPPAGE_BPS", "300")),
            "recipient": env.BUYER_AGENT_WALLET_ADDRESS,
            "chain": os.getenv("CHAIN", "base"),
            "notes": "demo request from buyer",
        },
        amount=0.01,
        evaluator_address=env.BUYER_AGENT_WALLET_ADDRESS,
        expired_at=datetime.now() + timedelta(days=1),
    )

    print(f"[BUYER] Job {job_id} initiated and listening for next steps...")
    threading.Event().wait()


if __name__ == "__main__":
    buyer()
