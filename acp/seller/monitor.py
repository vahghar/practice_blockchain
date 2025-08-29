import os
import time
import json
import glob
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

import sys
import os

# Make operari-server and operari-server/data importable to reuse modules
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OPERARI_ROOT = os.path.join(BASE_DIR, "operari-server")
OPERARI_DATA = os.path.join(OPERARI_ROOT, "data")
for p in (OPERARI_ROOT, OPERARI_DATA):
    if p not in sys.path:
        sys.path.append(p)

from data.crew.tools.tokenTools import TokenTransactionTool

JOBS_DIR = "/tmp/acp_jobs"

def load_pending_jobs():
    """Load all pending jobs from files"""
    jobs = {}
    if not os.path.exists(JOBS_DIR):
        return jobs
    
    for job_file in glob.glob(f"{JOBS_DIR}/*.json"):
        try:
            with open(job_file, 'r') as f:
                job_data = json.load(f)
            
            job_id = os.path.basename(job_file).replace('.json', '')
            if job_data.get('status') == 'waiting_for_funds':
                jobs[job_id] = job_data
                
        except Exception as e:
            print(f"[MONITOR] Error loading {job_file}: {e}")
    
    return jobs

def update_job_status(job_id, status, result_data=None):
    """Update job status in file"""
    job_file = f"{JOBS_DIR}/{job_id}.json"
    
    try:
        with open(job_file, 'r') as f:
            job_data = json.load(f)
        
        job_data['status'] = status
        job_data['completed_at'] = time.time()
        if result_data:
            job_data['result'] = result_data
        
        with open(job_file, 'w') as f:
            json.dump(job_data, f)
            
        print(f"[MONITOR] Updated job {job_id} status to {status}")
        
    except Exception as e:
        print(f"[MONITOR] Error updating job status: {e}")

def monitor_designated_wallets():
    w3 = Web3(Web3.HTTPProvider(os.getenv("BASE_MAINNET_RPC_URL")))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware(), layer=0)
    
    print("[MONITOR] Starting monitoring...")
    
    while True:
        try:
            # FIXED: Load from files instead of global variables
            pending_jobs = load_pending_jobs()
            
            for job_id, job_data in pending_jobs.items():
                wallet_info = job_data['wallet_info']
                trade_details = job_data['trade_details']
                
                balance = w3.eth.get_balance(wallet_info['address'])
                
                if balance > 0:
                    print(f"[MONITOR] Funds detected for job {job_id}: {balance} wei")
                    
                    # Execute swap and get result
                    swap_result = execute_swap_with_designated_wallet(
                        wallet_info, job_id, trade_details
                    )
                    
                    # Update job status with result
                    if "error" in swap_result:
                        update_job_status(job_id, "failed", swap_result)
                    else:
                        update_job_status(job_id, "completed", swap_result)
            
            time.sleep(15)
            
        except Exception as e:
            print(f"[MONITOR] Error: {e}")
            time.sleep(30)

def execute_swap_with_designated_wallet(wallet_info, job_id, trade_details):
    """Execute swap using designated wallet's private key"""
    try:
        print(f"[MONITOR] Executing swap for job {job_id}")
        
        # Use your existing TokenTransactionTool but with DESIGNATED wallet
        tool = TokenTransactionTool()
        
        # Execute swap using the DESIGNATED wallet's private key
        tool_resp_raw = tool._run(
            buy_token=trade_details['toToken'],
            sell_token=trade_details['fromToken'],
            sell_amount=str(trade_details['amount']),
            wallet_address=wallet_info['address'],  # USE DESIGNATED WALLET
            sell_token_decimals=trade_details.get('sell_decimals', 6),
            private_key=wallet_info['private_key']  # CRITICAL: Use designated wallet's key
        )
        
        # Parse and handle response
        tool_resp = json.loads(tool_resp_raw) if isinstance(tool_resp_raw, str) else tool_resp_raw
        
        if "error" in tool_resp:
            print(f"[MONITOR] Swap failed: {tool_resp.get('error')}")
            return tool_resp
        else:
            print(f"[MONITOR] Swap executed successfully!")
            print(f"Transaction: {tool_resp.get('transaction', {}).get('transactionHash', 'N/A')}")
            return tool_resp
            
    except Exception as e:
        print(f"[MONITOR] Swap execution failed: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e)}

if __name__ == "__main__":
    monitor_designated_wallets()