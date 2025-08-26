import os
import sys
import threading
import json
from ast import literal_eval

from dotenv import load_dotenv

# Prefer local SDK at ../acp-python over installed site-packages
_ACPROOT = os.path.dirname(os.path.dirname(__file__))  # .../acp
_SDK_DIR = os.path.join(os.path.dirname(_ACPROOT), "acp-python")  # sibling to acp
if _SDK_DIR not in sys.path:
    sys.path.insert(0, _SDK_DIR)

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
from crew.crew import CryptoAnalysisCrew


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
            print("[SELLER] TRANSACTION received. Preparing delivery and moving to EVALUATION...")
            for memo in job.memos:
                if memo.next_phase == ACPJobPhase.EVALUATION:
                    print("Delivering job", job)
                    requirements = _parse_service_requirement(job.service_requirement)
                    message = requirements.get("prompt", "")
                    wallet_address = requirements.get("walletAddress")
                    result = CryptoAnalysisCrew().crew().kickoff(inputs={
                        "wallet_address": wallet_address,
                        "message": message,
                    })
                    data = result.pydantic.to_dict() if getattr(result, "pydantic", None) is not None else result.raw
                    delivery_data = {
                        "type": "object",
                        "value": data,
                    }
                    print(f"[SELLER] Delivering data (truncated preview): {str(delivery_data)[:200]}...")
                    job.deliver(json.dumps(delivery_data))
                    break

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