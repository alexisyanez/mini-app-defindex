import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from stellar_sdk import (
    Keypair,
    Network,
    TransactionBuilder,
    xdr,
    Server,
    scval,
    exceptions
)
# Corrected import: 'soroban' module is within 'stellar_sdk.soroban'
#from stellar_sdk.soroban import SorobanServer, soroban 
import asyncio
import time

app = Flask(__name__)
CORS(app) # Enable CORS for your frontend (Telegram Mini App)

# --- Configuration ---
# IMPORTANT: Replace with your actual Soroban Testnet RPC URL
# For production, consider running your own node or using a dedicated RPC provider.
RPC_SERVER_URL = "https://soroban-testnet.stellar.org:443"
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE # "Test SDF Network ; September 2015"

# IMPORTANT: Replace with your actual DeFindex Contract ID
# This is the Contract ID obtained after deploying your DeFindex contract to Soroban.
# Example: "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN"
DEFINDEX_CONTRACT_ID = os.environ.get("DEFINDEX_CONTRACT_ID", "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2") #YOUR_DEFINDEX_CONTRACT_ID_HERE")

# IMPORTANT: Source Keypair for transactions that the backend initiates (e.g., if the contract requires admin actions)
# For user-initiated transactions (deposit/withdraw), the user should sign client-side.
# This keypair is for demonstration purposes only. In production, use secure secret management.
# You can generate one using `stellar keys generate --global my_backend_user --network testnet --fund`
SOURCE_SECRET = os.environ.get("SOURCE_SECRET", "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV") # Replace with your actual secret key

# --- Configuration Validation ---
if DEFINDEX_CONTRACT_ID == "YOUR_DEFINDEX_CONTRACT_ID_HERE":
    print("WARNING: DEFINDEX_CONTRACT_ID is still a placeholder. Please update it with your deployed contract ID.")
if SOURCE_SECRET == "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV":
    print("WARNING: SOURCE_SECRET is still a placeholder. Please update it with a funded Testnet secret key.")

try:
    source_keypair = Keypair.from_secret(SOURCE_SECRET)
except Exception as e:
    print(f"ERROR: Invalid SOURCE_SECRET provided. Please check your SOURCE_SECRET environment variable or hardcoded value. Error: {e}")
    # Exit or handle gracefully if the secret is critical for startup
    # For now, we'll let it proceed, but operations requiring it will fail.
    source_keypair = None # Set to None if invalid

# Initialize Soroban Server
soroban_server = Server(RPC_SERVER_URL)

# --- Helper Functions for Soroban Interaction ---

async def get_account_details(public_key: str):
    """Fetches the latest account details, including sequence number."""
    try:
        return await soroban_server.load_account(public_key)
    except exceptions.ResourceNotFoundError:
        return None # Account not found, needs to be created/funded
    except Exception as e:
        print(f"Error loading account details for {public_key}: {e}")
        raise

async def prepare_and_simulate_transaction(source_account_public_key: str, operations: list, network_passphrase: str):
    """
    Prepares a transaction for Soroban, simulates it, and returns the unsigned XDR.
    This function handles the necessary Soroban-specific preparation.
    It takes the user's public key as the source for the transaction.
    """
    try:
        # Load the account details to get the current sequence number
        source_account = await get_account_details(source_account_public_key)
        if not source_account:
            raise ValueError(f"Source account {source_account_public_key} not found on network or not funded. Please fund it via Friendbot (https://friendbot.stellar.org/).")

        # Create a transaction builder
        tx_builder = (
            TransactionBuilder(
                source_account=source_account,
                network_passphrase=network_passphrase,
                base_fee=100 # Adjust base fee as needed for Soroban transactions
            )
        )

        # Add all operations
        for op in operations:
            tx_builder.add_operation(op)

        # Build the transaction (without signing yet)
        transaction = tx_builder.build()

        # Prepare the transaction for Soroban execution
        # This step simulates the transaction and adds the necessary footprint and resource requirements.
        prepared_transaction = await soroban_server.prepare_transaction(transaction)

        # Return the unsigned XDR for client-side signing
        return prepared_transaction.to_xdr()

    except exceptions.PrepareTransactionException as e:
        print(f"Prepare transaction failed: {e}")
        # Detailed error parsing for PrepareTransactionException
        if e.simulate_transaction_response and e.simulate_transaction_response.error:
            # Attempt to extract more specific error from simulation result
            error_message = e.simulate_transaction_response.error
            if "error" in error_message and isinstance(error_message["error"], dict) and "message" in error_message["error"]:
                raise ValueError(f"Soroban simulation error: {error_message['error']['message']}")
            raise ValueError(f"Soroban simulation error: {error_message}")
        raise ValueError(f"Failed to prepare transaction: {e}")
    except Exception as e:
        print(f"Error in prepare_and_simulate_transaction: {e}")
        raise

async def submit_transaction_to_soroban(signed_xdr: str):
    """
    Submits a signed transaction XDR to the Soroban network and polls for its status.
    """
    try:
        # Send the signed transaction
        send_response = await soroban_server.send_transaction(signed_xdr)
        tx_hash = send_response.transaction_hash
        print(f"Transaction sent. Hash: {tx_hash}. Status: {send_response.status}")

        if send_response.status == "ERROR":
            raise ValueError(f"Transaction submission error: {send_response.error}")

        # Poll for transaction status with exponential backoff
        for i in range(5): # Retry up to 5 times
            time.sleep(2 ** i) # Exponential backoff: 1s, 2s, 4s, 8s, 16s
            get_response = await soroban_server.get_transaction(tx_hash)
            print(f"Polling status for {tx_hash}: {get_response.status}")
            if get_response.status != "NOT_FOUND" and get_response.status != "PENDING":
                if get_response.status == "SUCCESS":
                    return {"status": "SUCCESS", "hash": tx_hash, "result": get_response.result_xdr}
                else:
                    # Attempt to parse detailed error from failed transaction
                    error_detail = get_response.result_xdr if get_response.result_xdr else get_response.error
                    raise ValueError(f"Transaction failed: {get_response.status}. Detail: {error_detail}")
        raise TimeoutError("Transaction polling timed out.")

    except Exception as e:
        print(f"Error submitting transaction: {e}")
        raise

# --- API Endpoints ---

@app.route('/api/deposit', methods=['POST'])
async def deposit():
    """
    Prepares an unsigned deposit transaction for client-side signing.
    """
    data = request.json
    amount_str = data.get('amount')
    user_address = data.get('user_address')

    if not amount_str or not user_address:
        return jsonify({"error": "Amount and user address are required"}), 400

    if DEFINDEX_CONTRACT_ID == "YOUR_DEFINDEX_CONTRACT_ID_HERE":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        # Convert XLM to stroops (1 XLM = 10^7 stroops)
        # Assuming the contract expects a 64-bit integer (i64 or u64) or 128-bit (i128/u128)
        # Adjust precision as per your contract's `deposit` function signature.
        amount = int(float(amount_str) * 10**7)
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        # Construct the InvokeHostFunctionOp for the deposit
        # IMPORTANT: The function name and parameter types must EXACTLY match your DeFindex contract's Rust code.
        # If your contract uses `Address` for the user, use `scval.to_address(user_address)`.
        # If it uses `i128` for amount, use `scval.to_i128(amount)`.
        # You might also need to pass an asset ID if your vault is multi-asset.
        # Example for a simple `deposit(user: Address, amount: i128)` function:
        op = soroban.invoke_contract_function(
            contract_id=DEFINDEX_CONTRACT_ID,
            function_name="deposit", # Replace with actual deposit function name from your contract
            parameters=[
                scval.to_address(user_address),
                scval.to_i128(amount)
            ]
        )

        # Prepare the transaction (simulate and get footprint) using the user's public key as source
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for deposit", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Deposit endpoint error: {e}")
        return jsonify({"error": "Internal server error during deposit preparation"}), 500

@app.route('/api/withdraw', methods=['POST'])
async def withdraw():
    """
    Prepares an unsigned withdraw transaction for client-side signing.
    """
    data = request.json
    amount_str = data.get('amount')
    user_address = data.get('user_address')

    if not amount_str or not user_address:
        return jsonify({"error": "Amount and user address are required"}), 400

    if DEFINDEX_CONTRACT_ID == "YOUR_DEFINDEX_CONTRACT_ID_HERE":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        amount = int(float(amount_str) * 10**7) # Convert XLM to stroops
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        # Construct the InvokeHostFunctionOp for the withdraw
        # IMPORTANT: The function name and parameter types must EXACTLY match your DeFindex contract's Rust code.
        # Example for a simple `withdraw(user: Address, amount: i128)` function:
        op = soroban.invoke_contract_function(
            contract_id=DEFINDEX_CONTRACT_ID,
            function_name="withdraw", # Replace with actual withdraw function name from your contract
            parameters=[
                scval.to_address(user_address),
                scval.to_i128(amount)
            ]
        )

        # Prepare the transaction (simulate and get footprint) using the user's public key as source
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for withdrawal", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Withdraw endpoint error: {e}")
        return jsonify({"error": "Internal server error during withdrawal preparation"}), 500

@app.route('/api/submit_signed_tx', methods=['POST'])
async def submit_signed_tx():
    """
    Receives a signed transaction XDR and submits it to the Soroban network.
    """
    data = request.json
    signed_xdr = data.get('signed_xdr')

    if not signed_xdr:
        return jsonify({"error": "Signed transaction XDR is required"}), 400

    try:
        result = await submit_transaction_to_soroban(signed_xdr)
        return jsonify({"message": "Transaction submitted", "transaction_hash": result['hash'], "status": result['status']}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except TimeoutError:
        return jsonify({"error": "Transaction submission timed out. Please check the explorer."}), 504
    except Exception as e:
        print(f"Submit signed transaction endpoint error: {e}")
        return jsonify({"error": "Internal server error during transaction submission"}), 500


@app.route('/api/yields', methods=['GET'])
async def get_yields():
    """
    Retrieves yield information for a given user from the DeFindex contract.
    This is a read-only operation and does not require signing.
    """
    user_address = request.args.get('user_address')
    if not user_address:
        return jsonify({"error": "User address is required"}), 400

    if DEFINDEX_CONTRACT_ID == "YOUR_DEFINDEX_CONTRACT_ID_HERE":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        # --- REAL YIELD FETCHING (Requires knowing your DeFindex contract's view functions/storage) ---
        # To get real yield data, you need to call a read-only (view) function on your DeFindex contract.
        # Example: If your contract has a function `get_user_yield(user: Address) -> u128`
        # You would construct an InvokeHostFunctionOp for simulation:
        # op = soroban.invoke_contract_function(
        #     contract_id=DEFINDEX_CONTRACT_ID,
        #     function_name="get_user_yield", # Replace with your actual view function name
        #     parameters=[scval.to_address(user_address)]
        # )
        #
        # # Create a dummy transaction for simulation (no actual network submission)
        # # The source account for simulation can be any valid account on the network.
        # # We'll use the backend's source_keypair for this, or a dummy one if source_keypair is None.
        # dummy_source_kp = source_keypair if source_keypair else Keypair.random()
        # dummy_source_account = await get_account_details(dummy_source_kp.public_key)
        # if not dummy_source_account:
        #     # Fund dummy_source_kp if it's new and needed for simulation
        #     print(f"WARNING: Dummy source account {dummy_source_kp.public_key} not found. Simulation might fail if it needs to exist.")
        #     # In a real scenario, you might fund it via Friendbot or ensure a funded account is used.
        #     # For now, we proceed, but be aware of potential simulation errors.
        #     # If you use a fixed backend SOURCE_SECRET, ensure it's funded.
        #
        # tx_builder = TransactionBuilder(
        #     source_account=dummy_source_account if dummy_source_account else Keypair.from_public_key(user_address), # Fallback to user_address if dummy not found
        #     network_passphrase=NETWORK_PASSPHRASE,
        #     base_fee=100
        # ).add_operation(op).build()
        #
        # simulate_response = await soroban_server.simulate_transaction(tx_builder)
        #
        # if simulate_response.result and simulate_response.result.retval:
        #     # Convert the SCVal result back to a Python type
        #     # This conversion depends on the actual return type of your contract function.
        #     # Example: if it returns u128, it will be an int.
        #     current_yield_stroops = scval.from_xdr_object(simulate_response.result.retval)
        #     current_yield = current_yield_stroops / 10**7 # Convert back to XLM
        # else:
        #     print(f"Simulation for get_user_yield failed or returned no value: {simulate_response.error}")
        #     current_yield = 0.0
        #
        # # Similar logic for total_deposited if your contract has a view function for it.
        # total_deposited = 0.0

        # --- MOCK DATA FOR DEMONSTRATION ---
        # Replace this with the real contract interaction logic above
        mock_yield = 0.005 * (len(user_address) % 10) # Simple mock based on address length
        mock_deposited = 100 * (len(user_address) % 5 + 1) # Simple mock

        return jsonify({
            "current_yield": f"{mock_yield:.3f}",
            "total_deposited": f"{mock_deposited:.3f}"
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Get yields endpoint error: {e}")
        return jsonify({"error": "Internal server error fetching yields"}), 500

if __name__ == '__main__':
    # You can set environment variables for DEFINDEX_CONTRACT_ID and SOURCE_SECRET
    # Example for local testing (replace with your actual values):
    # export DEFINDEX_CONTRACT_ID="CAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQ"
    # export SOURCE_SECRET="SAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    # Then run: python app.py
    app.run(debug=True, port=5000)

# Note: In production, consider using a WSGI server like Gunicorn or uWSGI for better performance.  