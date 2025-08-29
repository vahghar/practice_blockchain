import os
import sys
import logging
from dotenv import load_dotenv
from web3 import Web3

# --- Configuration ---
# Load environment variables from a .env file
load_dotenv(override=True)

# Set up basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Mainnet Configuration ---
# The token you want to approve (USDC on Base)
TOKEN_TO_APPROVE_ADDRESS = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"

# The smart contract that will spend the token (KyberSwap Router on Base)
SPENDER_ADDRESS = "0x6131B5fae19EA4f9D964eAc0408E4408b66337b5"

# Your wallet credentials from the .env file
WALLET_ADDRESS = os.getenv("TEST_WALLET_ADDRESS")
PRIVATE_KEY = os.getenv("TEST_WALLET_PRIVATE_KEY")
RPC_URL = os.getenv("BASE_MAINNET_RPC_URL", "https://mainnet.base.org")

# A more complete ERC20 ABI that includes both 'approve' and 'allowance'
ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}, {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False, "stateMutability": "view", "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"}, {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "payable": False, "stateMutability": "nonpayable", "type": "function"
    }
]

def check_token_approval(w3, token_address, owner_address, spender_address):
    """Checks if the spender has a sufficient allowance."""
    try:
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        allowance = token_contract.functions.allowance(
            Web3.to_checksum_address(owner_address),
            Web3.to_checksum_address(spender_address)
        ).call()
        logging.info(f"Current allowance is: {allowance}")
        # A small non-zero allowance is considered sufficient for this check
        return allowance > 0
    except Exception as e:
        logging.error(f"Failed to check token approval: {e}")
        return False

def approve_unlimited(w3, token_address, owner_address, spender_address, private_key):
    """Grants the maximum possible token approval to the spender."""
    try:
        token_contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_ABI)
        
        # Max uint256 value for "unlimited" approval
        unlimited_amount = 2**256 - 1
        
        # Build the transaction
        nonce = w3.eth.get_transaction_count(Web3.to_checksum_address(owner_address))
        
        approve_txn = token_contract.functions.approve(
            Web3.to_checksum_address(spender_address),
            unlimited_amount
        ).build_transaction({
            'from': Web3.to_checksum_address(owner_address),
            'gas': 100000,  # A reasonable gas limit for an approval
            'gasPrice': w3.eth.gas_price,
            'nonce': nonce,
            'chainId': 8453  # Base Mainnet Chain ID
        })
        
        # Sign and send the transaction
        signed_txn = w3.eth.account.sign_transaction(approve_txn, private_key=private_key)
        
        # --- THIS IS THE FIX ---
        # Use .raw_transaction (snake_case) instead of .rawTransaction (camelCase)
        tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
        # -----------------------
        
        logging.info(f"Approval transaction sent with hash: {tx_hash.hex()}")
        logging.info(f"View on BaseScan: https://basescan.org/tx/{tx_hash.hex()}")
        
        # Wait for the transaction to be confirmed
        logging.info("Waiting for transaction confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        
        if receipt.status == 1:
            logging.info("Transaction confirmed successfully!")
            return True
        else:
            logging.error("Transaction failed (reverted on-chain).")
            return False
            
    except Exception as e:
        logging.error(f"An error occurred during the approval process: {e}")
        return False

def main():
    """Main function to run the approval process."""
    logging.info("--- Token Approval Script ---")
    
    # --- Validations ---
    if not all([WALLET_ADDRESS, PRIVATE_KEY, RPC_URL]):
        logging.error("Error: TEST_WALLET_ADDRESS, TEST_WALLET_PRIVATE_KEY, and BASE_MAINNET_RPC_URL must be set in your .env file.")
        sys.exit(1)
        
    logging.info(f"Wallet: {WALLET_ADDRESS}")
    logging.info(f"Token: {TOKEN_TO_APPROVE_ADDRESS}")
    logging.info(f"Spender: {SPENDER_ADDRESS}")
    logging.info(f"RPC Node: {RPC_URL}")
    print("-" * 30)

    try:
        w3 = Web3(Web3.HTTPProvider(RPC_URL))
        if not w3.is_connected():
            logging.error(f"Failed to connect to the RPC provider at {RPC_URL}")
            sys.exit(1)
    except Exception as e:
        logging.error(f"Error initializing Web3 connection: {e}")
        sys.exit(1)

    # 1. Check if approval is already sufficient
    logging.info("Checking current token approval status...")
    if check_token_approval(w3, TOKEN_TO_APPROVE_ADDRESS, WALLET_ADDRESS, SPENDER_ADDRESS):
        logging.info("✅ Token is already approved for the spender. No action needed.")
        sys.exit(0)
        
    # 2. If not approved, grant approval
    logging.warning("Token not approved. Proceeding to grant unlimited approval.")
    
    if approve_unlimited(w3, TOKEN_TO_APPROVE_ADDRESS, WALLET_ADDRESS, SPENDER_ADDRESS, PRIVATE_KEY):
        logging.info("✅ Approval process completed successfully!")
    else:
        logging.error("❌ Approval process failed. Please check the logs for errors.")
        sys.exit(1)

if __name__ == "__main__":
    main()

