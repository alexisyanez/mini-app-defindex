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
from stellar_sdk.operation import InvokeHostFunction
#from stellar_sdk.scval import from_xdr_object as scval_from_xdr
import asyncio
import time

app = Flask(__name__)
# Allow all origins (for development)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- Configuration ---
RPC_SERVER_URL = "https://soroban-testnet.stellar.org"
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE

# IMPORTANT: Replace with your actual DeFindex Contract ID (for existing vaults or general interaction)
# This is the Contract ID obtained after deploying your DeFindex contract to Soroban.
DEFINDEX_CONTRACT_ID = os.environ.get("DEFINDEX_CONTRACT_ID", "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2")

# IMPORTANT: Replace with the Contract ID of your DeFindex FACTORY Contract
# This contract is responsible for deploying new DeFindex vaults.
DEFINDEX_FACTORY_CONTRACT_ID = os.environ.get("DEFINDEX_FACTORY_CONTRACT_ID", "CAVELNFTH4GFMBD3FEMBGCSPVFR4R3YPWR4EATXMRKNZ43B3QKSO4CUJ") #"YOUR_DEFINDEX_FACTORY_CONTRACT_ID_HERE")

# IMPORTANT: Source Keypair for transactions that the backend initiates (e.g., if the contract requires admin actions)
# For user-initiated transactions (deposit/withdraw/create_vault), the user should sign client-side.
SOURCE_SECRET = os.environ.get("SOURCE_SECRET", "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV")

# --- Configuration Validation ---
if DEFINDEX_CONTRACT_ID == "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2":
    print("WARNING: DEFINDEX_CONTRACT_ID is still a placeholder. Please update it.")
if DEFINDEX_FACTORY_CONTRACT_ID == "YOUR_DEFINDEX_FACTORY_CONTRACT_ID_HERE":
    print("WARNING: DEFINDEX_FACTORY_CONTRACT_ID is still a placeholder. Please update it with your deployed factory contract ID.")
if SOURCE_SECRET == "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV":
    print("WARNING: SOURCE_SECRET is still a placeholder. Please update it with a funded Testnet secret key.")

try:
    source_keypair = Keypair.from_secret(SOURCE_SECRET)
except Exception as e:
    print(f"ERROR: Invalid SOURCE_SECRET provided. Error: {e}")
    source_keypair = None

soroban_server = Server(RPC_SERVER_URL)

# --- Helper Functions for Soroban Interaction ---

async def get_account_details(public_key: str):
    """Fetches the latest account details, including sequence number."""
    try:
        return await soroban_server.load_account(public_key)
    except exceptions.ResourceNotFoundError:
        return None
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
        source_account = await get_account_details(source_account_public_key)
        if not source_account:
            raise ValueError(f"Source account {source_account_public_key} not found on network or not funded. Please fund it via Friendbot (https://friendbot.stellar.org/).")

        tx_builder = TransactionBuilder(
            source_account=source_account,
            network_passphrase=network_passphrase,
            base_fee=100 # Adjust base fee as needed for Soroban transactions
        )
        
        for op in operations:
            tx_builder.add_operation(op)

        transaction = tx_builder.build()
        prepared_transaction = await soroban_server.prepare_transaction(transaction)
        return prepared_transaction.to_xdr()

    except exceptions.PrepareTransactionException as e:
        error_message = f"Failed to prepare transaction: {e}"
        if e.simulate_transaction_response and e.simulate_transaction_response.error:
            error_message = f"Soroban simulation error: {e.simulate_transaction_response.error}"
        raise ValueError(error_message)
    except Exception as e:
        print(f"Error in prepare_and_simulate_transaction: {e}")
        raise

async def submit_transaction_to_soroban(signed_xdr: str):
    """
    Submits a signed transaction XDR to the Soroban network and polls for its status.
    """
    try:
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

@app.route('/api/create_vault', methods=['POST'])
async def create_vault():
    """
    Prepares an unsigned transaction for creating a new DeFindex vault.
    This operation calls the DeFindex Factory Contract.
    """
    data = request.json
    vault_name = data.get('vault_name')
    vault_symbol = data.get('vault_symbol')
    manager_address = data.get('manager_address')
    emergency_manager_address = data.get('emergency_manager_address')
    fee_receiver_address = data.get('fee_receiver_address')
    fee_percentage = data.get('fee_percentage') # e.g., 0.5 for 0.5%
    asset_id = data.get('asset_id') # Contract ID of the asset (e.g., USDC, XLM token contract)
    user_address = data.get('user_address') # The user initiating the transaction

    required_fields = [
        vault_name, vault_symbol, manager_address, emergency_manager_address,
        fee_receiver_address, fee_percentage, asset_id, user_address
    ]
    if not all(required_fields):
        return jsonify({"error": "All vault creation parameters and user address are required"}), 400

    if DEFINDEX_FACTORY_CONTRACT_ID == "YOUR_DEFINDEX_FACTORY_CONTRACT_ID_HERE":
        return jsonify({"error": "DeFindex Factory Contract ID not configured in backend."}), 500
    if asset_id == "YOUR_ASSET_CONTRACT_ID_HERE":
        return jsonify({"error": "Asset Contract ID for the vault is still a placeholder. Please update it."}), 500

    try:
        # Convert fee_percentage (e.g., 0.5) to basis points (e.g., 50)
        fees_bps = int(float(fee_percentage) * 100)
        if fees_bps < 0:
            return jsonify({"error": "Fee percentage must be non-negative"}), 400

        # Construct the InvokeHostFunctionOp for creating the vault
        # IMPORTANT: The function name and parameter types must EXACTLY match your DeFindex Factory contract's Rust code.
        # This assumes a 'create_vault' function on the factory contract.
        host_function = xdr.HostFunction.from_dict({
            "type": "HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT",
            "invoke_contract": xdr.InvokeContractArgs.from_dict({
                "contract_address": scval.to_address(DEFINDEX_FACTORY_CONTRACT_ID),
                "function_name": scval.to_string("create_vault"), # Replace with actual factory function name
                "parameters": [
                    scval.to_string(vault_name),
                    scval.to_string(vault_symbol),
                    scval.to_address(manager_address),
                    scval.to_address(emergency_manager_address),
                    scval.to_address(fee_receiver_address),
                    scval.to_u32(fees_bps), # Fees in basis points
                    scval.to_address(asset_id) # Contract ID of the asset (e.g., USDC)
                ],
            }),
        })
        op = InvokeHostFunction(host_function=host_function)

        # Prepare the transaction (simulate and get footprint) using the user's public key as source
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for vault creation", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Create vault endpoint error: {e}")
        return jsonify({"error": "Internal server error during vault creation preparation"}), 500

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

    if DEFINDEX_CONTRACT_ID == "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        # Convert XLM to stroops (1 XLM = 10^7 stroops)
        amount = int(float(amount_str) * 10**7)
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        # Construct the InvokeHostFunctionOp for the deposit
        host_function = xdr.HostFunction.from_dict({
            "type": "HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT",
            "invoke_contract": xdr.InvokeContractArgs.from_dict({
                "contract_address": scval.to_address(DEFINDEX_CONTRACT_ID),
                "function_name": scval.to_string("deposit"), # Replace with actual deposit function name from your contract
                "parameters": [
                    scval.to_address(user_address),
                    scval.to_i128(amount)
                ],
            }),
        })
        op = InvokeHostFunction(host_function=host_function)

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

    if DEFINDEX_CONTRACT_ID == "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        amount = int(float(amount_str) * 10**7) # Convert XLM to stroops
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        # Construct the InvokeHostFunctionOp for the withdraw
        host_function = xdr.HostFunction.from_dict({
            "type": "HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT",
            "invoke_contract": xdr.InvokeContractArgs.from_dict({
                "contract_address": scval.to_address(DEFINDEX_CONTRACT_ID),
                "function_name": scval.to_string("withdraw"), # Replace with actual withdraw function name from your contract
                "parameters": [
                    scval.to_address(user_address),
                    scval.to_i128(amount)
                ],
            }),
        })
        op = InvokeHostFunction(host_function=host_function)

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

    if DEFINDEX_CONTRACT_ID == "CACDYF3CYMJEJTIVFESQYZTN67GO2R5D5IUABTCUG3HXQSRXCSOROBAN2":
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        # Construct the InvokeHostFunctionOp for simulation (read-only)
        host_function = xdr.HostFunction.from_dict({
            "type": "HostFunctionType.HOST_FUNCTION_TYPE_INVOKE_CONTRACT",
            "invoke_contract": xdr.InvokeContractArgs.from_dict({
                "contract_address": scval.to_address(DEFINDEX_CONTRACT_ID),
                "function_name": scval.to_string("get_user_yield"), # Assuming a view function exists
                "parameters": [scval.to_address(user_address)],
            }),
        })
        op = InvokeHostFunction(host_function=host_function)

        # Create a dummy transaction for simulation
        source_account = await get_account_details(source_keypair.public_key)
        if not source_account:
            return jsonify({"error": "Backend source account not funded. Cannot simulate."}), 500

        tx_builder = TransactionBuilder(
            source_account=source_account,
            network_passphrase=NETWORK_PASSPHRASE,
            base_fee=100
        ).add_operation(op).build()
        
        simulate_response = await soroban_server.simulate_transaction(tx_builder)
        
        current_yield = 0.0
        total_deposited = 0.0

        if simulate_response.result and simulate_response.result.retval:
            result_scval = simulate_response.result.retval
            current_yield_stroops = scval_from_xdr(result_scval)
            current_yield = current_yield_stroops / 10**7 # Convert to XLM
        else:
            print(f"Simulation for get_user_yield failed or returned no value: {simulate_response.error}")

        return jsonify({
            "current_yield": f"{current_yield:.3f}",
            "total_deposited": f"{total_deposited:.3f}"
        }), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Get yields endpoint error: {e}")
        return jsonify({"error": "Internal server error fetching yields"}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)

