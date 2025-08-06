import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from stellar_sdk import ServerAsync, TransactionBuilder, Network, Keypair, SorobanServer, xdr, scval, exceptions
# from stellar_sdk import (
#     Keypair,
#     Network,
#     TransactionBuilder,
#     xdr,
#     ServerAsync,
#     scval,
#     exceptions
# )
from stellar_sdk.operation import InvokeHostFunction
import asyncio
import time
from base64 import b64decode

app = Flask(__name__)
# Allow all origins (for development)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- Configuration ---
#RPC_SERVER_URL = "https://stellar.liquify.com/api=41EEWAH79Y5OCGI7/testnet"
HORIZON_SERVER_URL = "https://soroban-testnet.stellar.org" #"https://horizon-testnet.stellar.org"
NETWORK_PASSPHRASE = Network.TESTNET_NETWORK_PASSPHRASE

# The Contract ID of your DeFindex FACTORY Contract
DEFINDEX_FACTORY_CONTRACT_ID = os.environ.get("DEFINDEX_FACTORY_CONTRACT_ID", "CAVELNFTH4GFMBD3FEMBGCSPVFR4R3YPWR4EATXMRKNZ43B3QKSO4CUJ")

# This will hold the ID of the newly created vault, making it dynamic
DEFINDEX_CONTRACT_ID = None

# IMPORTANT: Source Keypair for transactions that the backend initiates (e.g., if the contract requires admin actions)
SOURCE_SECRET = os.environ.get("SOURCE_SECRET", "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV")

# --- Configuration Validation ---
if DEFINDEX_FACTORY_CONTRACT_ID == "YOUR_DEFINDEX_FACTORY_CONTRACT_ID_HERE":
    print("WARNING: DEFINDEX_FACTORY_CONTRACT_ID is still a placeholder. Please update it with your deployed factory contract ID.")
if SOURCE_SECRET == "SAAPYAPTTRZMCUZFPG3G66V4ZMHTK4TWA6NS7U4F7Z3IMUD52EK4DDEV":
    print("WARNING: SOURCE_SECRET is still a placeholder. Please update it with a funded Testnet secret key.")

try:
    source_keypair = Keypair.from_secret(SOURCE_SECRET)
except Exception as e:
    print(f"ERROR: Invalid SOURCE_SECRET provided. Error: {e}")
    source_keypair = None

soroban_server = SorobanServer(HORIZON_SERVER_URL)#Server(RPC_SERVER_URL)

# --- Helper Functions for Soroban Interaction ---

async def get_account_details(public_key: str):
    """Fetches the latest account details, including sequence number."""
    try:
        return soroban_server.load_account(public_key)
    except exceptions.NotFoundError: 
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
            base_fee=100
        )

        for op in operations:
            tx_builder.append_operation(op)

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
                    return {"status": "SUCCESS", "hash": tx_hash, "result_xdr": get_response.result_xdr}
                else:
                    error_detail = get_response.result_xdr if get_response.result_xdr else get_response.error
                    raise ValueError(f"Transaction failed: {get_response.status}. Detail: {error_detail}")
        raise TimeoutError("Transaction polling timed out.")

    except Exception as e:
        print(f"Error submitting transaction: {e}")
        raise

# --- API Endpoints ---

@app.route('/api/create_vault', methods=['POST'])
async def create_vault():
    data = request.json
    vault_name = data.get('vault_name')
    vault_symbol = data.get('vault_symbol')
    manager_address = data.get('manager_address')
    emergency_manager_address = data.get('emergency_manager_address')
    fee_receiver_address = data.get('fee_receiver_address')
    fee_percentage = data.get('fee_percentage')
    asset_id = data.get('asset_id')
    user_address = data.get('user_address')

    required_fields = [
        vault_name, vault_symbol, manager_address, emergency_manager_address,
        fee_receiver_address, fee_percentage, asset_id, user_address
    ]
    if not all(required_fields):
        return jsonify({"error": "All vault creation parameters and user address are required"}), 400

    if DEFINDEX_FACTORY_CONTRACT_ID == "YOUR_DEFINDEX_FACTORY_CONTRACT_ID_HERE":
        return jsonify({"error": "DeFindex Factory Contract ID not configured in backend."}), 500

    try:
        fees_bps = int(float(fee_percentage) * 100)
        if fees_bps < 0:
            return jsonify({"error": "Fee percentage must be non-negative"}), 400

        contract_args = xdr.InvokeContractArgs(
            contract_address=scval.to_address(DEFINDEX_FACTORY_CONTRACT_ID),
            function_name=scval.to_string("create_defindex_vault"),
            args=[
                scval.to_map({
                    1: scval.to_address(manager_address),         # Example: manager role
                    2: scval.to_address(fee_receiver_address),    # Example: fee receiver role
                    # Add other roles as needed
                }),
                scval.to_uint32(fees_bps),                                          # vault_fee
                scval.to_vec([                                                   # assets (vector of AssetStrategySet)
                    scval.to_struct([
                        scval.to_address(asset_id),
                        scval.to_vec([])
                    ])
                    # Add more AssetStrategySet as needed
                ]),
                scval.to_address(manager_address),                               # soroswap_router
                scval.to_map({
                    "name": scval.to_string(vault_name),
                    "symbol": scval.to_string(vault_symbol)
                }),                                                              # name_symbol
                scval.to_bool(True)                                        # upgradable
            ]
            # args=[
            #     scval.to_string(vault_name),
            #     scval.to_string(vault_symbol),
            #     scval.to_address(manager_address),
            #     scval.to_address(emergency_manager_address),
            #     scval.to_address(fee_receiver_address),
            #     scval.to_uint32(fees_bps),
            #     scval.to_address(asset_id)
            # ],
        )

        #host_function = xdr.HostFunction.invoke_contract(contract_args)
        #op = InvokeHostFunction(host_function=host_function)
        op = InvokeHostFunction(host_function=contract_args)
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for vault creation", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Create vault endpoint error: {e}")
        return jsonify({"error": "Internal server error during vault creation preparation"}), 500

@app.route('/api/deposit', methods=['POST'])
async def deposit():
    data = request.json
    amount_str = data.get('amount')
    user_address = data.get('user_address')

    if not amount_str or not user_address:
        return jsonify({"error": "Amount and user address are required"}), 400

    if not DEFINDEX_CONTRACT_ID:
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        amount = int(float(amount_str) * 10**7)
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        contract_args = xdr.InvokeContractArgs(
            contract_address=scval.to_address(DEFINDEX_CONTRACT_ID),
            function_name=scval.to_string("deposit"),
            args=[
                scval.to_address(user_address),
                scval.to_i128(amount)
            ],
        )
        #host_function = xdr.HostFunction.invoke_contract(contract_args)
        #op = InvokeHostFunction(host_function=host_function)
        op = InvokeHostFunction(host_function=contract_args)
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for deposit", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Deposit endpoint error: {e}")
        return jsonify({"error": "Internal server error during deposit preparation"}), 500

@app.route('/api/withdraw', methods=['POST'])
async def withdraw():
    data = request.json
    amount_str = data.get('amount')
    user_address = data.get('user_address')

    if not amount_str or not user_address:
        return jsonify({"error": "Amount and user address are required"}), 400

    if not DEFINDEX_CONTRACT_ID:
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        amount = int(float(amount_str) * 10**7)
        if amount <= 0:
            return jsonify({"error": "Amount must be positive"}), 400

        contract_args = xdr.InvokeContractArgs(
            contract_address=scval.to_address(DEFINDEX_CONTRACT_ID),
            function_name=scval.to_string("withdraw"),
            args=[
                scval.to_address(user_address),
                scval.to_i128(amount)
            ],
        )
        #host_function = xdr.HostFunction.invoke_contract(contract_args)
        #op = InvokeHostFunction(host_function=host_function)
        op = InvokeHostFunction(host_function=contract_args)
        unsigned_xdr = await prepare_and_simulate_transaction(user_address, [op], NETWORK_PASSPHRASE)

        return jsonify({"message": "Transaction prepared for withdrawal", "unsigned_xdr": unsigned_xdr}), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        print(f"Withdraw endpoint error: {e}")
        return jsonify({"error": "Internal server error during withdrawal preparation"}), 500

@app.route('/api/submit_signed_tx', methods=['POST'])
async def submit_signed_tx():
    global DEFINDEX_CONTRACT_ID

    data = request.json
    signed_xdr = data.get('signed_xdr')

    if not signed_xdr:
        return jsonify({"error": "Signed transaction XDR is required"}), 400

    try:
        result = await submit_transaction_to_soroban(signed_xdr)
        
        contract_id = None
        if result['status'] == 'SUCCESS':
            result_xdr_bytes = b64decode(result['result_xdr'].encode('utf-8'))
            result_xdr_obj = xdr.Xdr.from_bytes(result_xdr_bytes)
            
            if result_xdr_obj.result.value.value:
                first_op_result = result_xdr_obj.result.value.value[0]
                if first_op_result.value.invoke_host_function_result.value:
                    host_function_result = first_op_result.value.invoke_host_function_result.value
                    if host_function_result.type == xdr.ScValType.SCV_ADDRESS:
                        contract_id = scval.from_xdr(host_function_result)
                        DEFINDEX_CONTRACT_ID = contract_id
                        print(f"Successfully deployed new DeFindex Vault with ID: {contract_id}")

        response_data = {
            "message": "Transaction submitted",
            "transaction_hash": result['hash'],
            "status": result['status']
        }
        if contract_id:
            response_data["contract_id"] = contract_id
            
        return jsonify(response_data), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except TimeoutError:
        return jsonify({"error": "Transaction submission timed out. Please check the explorer."}), 504
    except Exception as e:
        print(f"Submit signed transaction endpoint error: {e}")
        return jsonify({"error": "Internal server error during transaction submission"}), 500


@app.route('/api/yields', methods=['GET'])
async def get_yields():
    user_address = request.args.get('user_address')
    if not user_address:
        return jsonify({"error": "User address is required"}), 400

    if not DEFINDEX_CONTRACT_ID:
        return jsonify({"error": "DeFindex Contract ID not configured in backend."}), 500

    try:
        contract_args = xdr.InvokeContractArgs(
            contract_address=scval.to_address(DEFINDEX_CONTRACT_ID),
            function_name=scval.to_string("get_user_yield"),
            args=[scval.to_address(user_address)],
        )
        #host_function = xdr.HostFunction.invoke_contract(contract_args)
        op = InvokeHostFunction(host_function=contract_args)
        #op = InvokeHostFunction(host_function=host_function)

        source_account = await get_account_details(source_keypair.public_key)
        if not source_account:
            return jsonify({"error": "Backend source account not funded. Cannot simulate."}), 500

        tx_builder = TransactionBuilder(
            source_account=source_account,
            network_passphrase=NETWORK_PASSPHRASE,
            base_fee=100
        ).append_operation(op).build()
        
        simulate_response = await soroban_server.simulate_transaction(tx_builder)
        
        current_yield = 0.0
        total_deposited = 0.0

        if simulate_response.result and simulate_response.result.retval:
            result_scval = simulate_response.result.retval
            if result_scval.type == xdr.ScValType.SCV_VEC:
                vec_elements = result_scval.vec.value
                if len(vec_elements) == 2:
                    current_yield = scval.from_xdr(vec_elements[0]).int128.lo / 10**7
                    total_deposited = scval.from_xdr(vec_elements[1]).int128.lo / 10**7
            else:
                print("Unexpected return type from get_user_yield.")
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