import os
import sys
import threading
from datetime import datetime, timedelta

from dotenv import load_dotenv

# Prefer local SDK at ../acp-python over installed site-packages
_ACPROOT = os.path.dirname(os.path.dirname(__file__))  # .../acp
_SDK_DIR = os.path.join(os.path.dirname(_ACPROOT), "acp-python")  # sibling to acp
if _SDK_DIR not in sys.path:
    sys.path.insert(0, _SDK_DIR)

from dataclasses import replace
from virtuals_acp import VirtualsACP, ACPJob, ACPJobPhase
from virtuals_acp.models import ACPGraduationStatus, ACPOnlineStatus
from virtuals_acp.env import EnvSettings
from virtuals_acp.configs import BASE_MAINNET_CONFIG

load_dotenv(override=True)


def buyer():
    env = EnvSettings()

    def on_new_task(job: ACPJob, memo_to_sign=None):
        if job.phase == ACPJobPhase.NEGOTIATION:
            for memo in job.memos:
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
            "walletAddress": "0x6B00F08F81d81FeC5154B6E807AcD4613cD16795",
            "prompt": "swap 0.01 VIRTUAL for ETH",
        },
        # Use a non-dust budget on mainnet USDC (6 decimals). Adjust if your policy expects a minimum.
        amount=0.1,
        evaluator_address=env.BUYER_AGENT_WALLET_ADDRESS,
        expired_at=datetime.now() + timedelta(days=1),
    )

    print(f"[BUYER] Job {job_id} initiated and listening for next steps...")
    threading.Event().wait()


if __name__ == "__main__":
    buyer()