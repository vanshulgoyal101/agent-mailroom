import json
import sys
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
from typing import Dict, Any, Tuple
import rlp
import eth_abi
from eth_account import Account
from web3 import Web3

# Ensure we can import modules from agent_mailroom
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_mailroom.registry import REGISTRY_ABI, DEFAULT_REGISTRY_ADDRESS
from agent_mailroom.channel import CHANNEL_ABI, DEFAULT_CHANNEL_ADDRESS


class SandboxState:
    """In-memory blockchain storage for Registry and Payment Channels."""
    # agent_address (lowercase str) -> dict
    registry: Dict[str, Dict[str, Any]] = {}
    
    # channel_id (hex str) -> dict
    channels: Dict[str, Dict[str, Any]] = {}

    # nonce tracking: address -> int
    nonces: Dict[str, int] = {}
    
    # transaction receipts: tx_hash -> receipt dict
    receipts: Dict[str, Dict[str, Any]] = {}

    # agent_address (lowercase str) -> stake amount (int)
    stakes: Dict[str, int] = {}

    # channel_id (hex str) -> dict with keys: taskHash (bytes), expiry (int), active (bool)
    disputes: Dict[str, Dict[str, Any]] = {}

    # Authorized payment channel address
    payment_channel: str = ""

    # Time offset in seconds (for evm_increaseTime / evm_mine)
    time_offset: int = 0

    # Execution logs for UI visualizer
    logs: list[str] = []
    is_running_swarm: bool = False


class SandboxJSONRPCHandler(BaseHTTPRequestHandler):

    def log_message(self, format: str, *args: Any) -> None:
        # Override to suppress standard HTTP logging and keep console output clean
        pass

    def do_POST(self) -> None:
        """Handles POST requests carrying JSON-RPC commands from Web3.py."""
        if self.path == "/api/run-swarm":
            import threading
            threading.Thread(target=execute_swarm_in_background, daemon=True).start()
            
            response_bytes = json.dumps({"status": "started"}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
            return

        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        try:
            request = json.loads(post_data.decode('utf-8'))
        except Exception as e:
            self.send_error_response(-32700, f"Parse error: {str(e)}", None)
            return

        if isinstance(request, list):
            response = [self._handle_single_request(req) for req in request]
        else:
            response = self._handle_single_request(request)

        response_bytes = json.dumps(response).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_GET(self) -> None:
        """Handles GET requests, specifically /api/state for the React UI visualizer."""
        if self.path == "/api/state":
            reg_data = {}
            for addr, val in SandboxState.registry.items():
                reg_data[addr] = {
                    "owner": val["owner"],
                    "endpoint": val["endpoint"],
                    "modelCapabilities": val["modelCapabilities"],
                    "ratePerTaskWei": val["ratePerTaskWei"],
                    "active": val["active"],
                    "stake": SandboxState.stakes.get(addr, 0)
                }
            
            chan_data = {}
            for cid, val in SandboxState.channels.items():
                disp = SandboxState.disputes.get(cid, {"taskHash": b"", "expiry": 0, "active": False})
                th_hex = "0x" + disp["taskHash"].hex() if isinstance(disp["taskHash"], bytes) else ""
                
                chan_data[cid] = {
                    "sender": val.get("sender", "unknown"),
                    "recipient": val.get("recipient", "unknown"),
                    "deposit": val["deposit"],
                    "challengeExpiry": val["challengeExpiry"],
                    "challenged": val["challenged"],
                    "dispute": {
                        "taskHash": th_hex,
                        "expiry": disp["expiry"],
                        "active": disp["active"]
                    }
                }
            
            state_payload = {
                "registry": reg_data,
                "channels": chan_data,
                "stakes": SandboxState.stakes,
                "time_offset": SandboxState.time_offset,
                "logs": SandboxState.logs,
                "is_running_swarm": SandboxState.is_running_swarm
            }
            
            response_bytes = json.dumps(state_payload).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Content-Length', str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)
        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self) -> None:
        """Handles CORS preflight requests."""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()

    def send_error_response(self, code: int, message: str, req_id: Any) -> None:
        error_response = {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message
            }
        }
        response_bytes = json.dumps(error_response).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Content-Length', str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _handle_single_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", [])
        
        result = None

        if method == "eth_chainId":
            result = "0x7a69"  # 31337
            
        elif method == "net_version":
            result = "31337"
            
        elif method == "net_listening":
            result = True
            
        elif method == "eth_blockNumber":
            result = "0x" + hex(int(time.time()) + SandboxState.time_offset)[2:]  # Pseudo incrementing blocks based on time
            
        elif method == "eth_gasPrice":
            result = "0x4a817c800"  # 20 Gwei
            
        elif method == "eth_estimateGas":
            result = "0x5208"  # 21000 standard transfer limit
            
        elif method == "eth_getTransactionCount":
            address = params[0].lower() if params else "0x"
            nonce = SandboxState.nonces.get(address, 0)
            result = hex(nonce)
            
        elif method == "eth_sendRawTransaction":
            raw_tx_hex = params[0]
            tx_hash = "0x" + hashlib.sha256(bytes.fromhex(raw_tx_hex[2:])).hexdigest()
            
            # Decode parameters and perform state changes
            success = self._process_raw_tx(raw_tx_hex, tx_hash)
            
            result = tx_hash
            SandboxState.receipts[tx_hash] = {
                "transactionHash": tx_hash,
                "blockHash": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
                "blockNumber": "0x42",
                "status": "0x1" if success else "0x0",
                "gasUsed": "0x5208",
                "effectiveGasPrice": "0x4a817c800",
                "logs": []
            }
            
        elif method == "eth_getTransactionReceipt":
            tx_hash = params[0]
            result = SandboxState.receipts.get(tx_hash)
            
        elif method == "eth_call":
            call_dict = params[0]
            result = self._process_eth_call(call_dict)
            
        elif method == "web3_clientVersion":
            result = "SandboxNode/v1.0.0"

        elif method == "evm_increaseTime":
            seconds = params[0]
            SandboxState.time_offset += seconds
            result = SandboxState.time_offset

        elif method == "evm_mine":
            result = "0x0"
            
        else:
            result = "0x0"

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result
        }

    def _decode_tx_data(self, raw_tx_hex: str) -> Tuple[str, int, bytes, int]:
        """Parses destination address, value, data bytes, and nonce from a raw transaction hex."""
        raw_bytes = bytes.fromhex(raw_tx_hex[2:])
        tx_type = raw_bytes[0]
        
        # EIP-2718 Envelope (Type 1 or Type 2)
        if tx_type in (1, 2):
            payload = rlp.decode(raw_bytes[1:])
            # For EIP-1559 Type 2: [chain_id, nonce, max_priority, max_fee, gas_limit, to, value, data, access_list, ...]
            to_bytes = payload[5]
            value_bytes = payload[6]
            data_bytes = payload[7]
            nonce_bytes = payload[1]
        else:
            # Legacy transaction
            payload = rlp.decode(raw_bytes)
            # Legacy format: [nonce, gas_price, gas_limit, to, value, data, v, r, s]
            to_bytes = payload[3]
            value_bytes = payload[4]
            data_bytes = payload[5]
            nonce_bytes = payload[0]

        to_addr = "0x" + to_bytes.hex() if to_bytes else "0x0000000000000000000000000000000000000000"
        value_wei = int.from_bytes(value_bytes, byteorder='big') if value_bytes else 0
        nonce = int.from_bytes(nonce_bytes, byteorder='big') if nonce_bytes else 0
        
        return Web3.to_checksum_address(to_addr), value_wei, data_bytes, nonce

    def _process_raw_tx(self, raw_tx_hex: str, tx_hash: str) -> bool:
        """Executes state modifications based on raw transactions sent by the SDK client."""
        try:
            # Recover sender address using eth_account
            sender = Account.recover_transaction(raw_tx_hex)
            sender_lower = sender.lower()

            to, value, data, nonce = self._decode_tx_data(raw_tx_hex)
            
            # Increment nonce
            current_nonce = SandboxState.nonces.get(sender_lower, 0)
            SandboxState.nonces[sender_lower] = current_nonce + 1

            w3 = Web3()
            
            # Registry Contract interaction
            if to.lower() == DEFAULT_REGISTRY_ADDRESS.lower():
                contract = w3.eth.contract(address=DEFAULT_REGISTRY_ADDRESS, abi=REGISTRY_ABI)
                func_obj, func_args = contract.decode_function_input(data)
                
                agent_addr = func_args.get("agent")
                if agent_addr:
                    agent_addr = agent_addr.lower()

                if func_obj.fn_name == "registerAgent":
                    print(f"\n[SANDBOX NODE] Tx Mined: registerAgent")
                    print(f"  Owner:      {sender}")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    print(f"  Endpoint:   {func_args['endpoint']}")
                    print(f"  Rate:       {func_args['ratePerTaskWei']} Wei")
                    
                    min_stake = 100000000000000000 # 0.1 ETH
                    if SandboxState.stakes.get(agent_addr, 0) < min_stake:
                        print("  [ERROR] Insufficient reputation stake! Minimum 0.1 ETH required.")
                        return False
                    
                    SandboxState.registry[agent_addr] = {
                        "owner": sender,
                        "endpoint": func_args["endpoint"],
                        "modelCapabilities": func_args["modelCapabilities"],
                        "ratePerTaskWei": func_args["ratePerTaskWei"],
                        "active": True
                    }
                    return True

                elif func_obj.fn_name == "updateAgentProfile":
                    print(f"\n[SANDBOX NODE] Tx Mined: updateAgentProfile")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    print(f"  Endpoint:   {func_args['endpoint']}")
                    
                    if agent_addr in SandboxState.registry:
                        profile = SandboxState.registry[agent_addr]
                        if profile["owner"].lower() != sender.lower():
                            print("  [ERROR] Unauthorized owner update!")
                            return False
                        
                        profile["endpoint"] = func_args["endpoint"]
                        profile["modelCapabilities"] = func_args["modelCapabilities"]
                        profile["ratePerTaskWei"] = func_args["ratePerTaskWei"]
                        return True
                    return False

                elif func_obj.fn_name == "deregisterAgent":
                    print(f"\n[SANDBOX NODE] Tx Mined: deregisterAgent")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    
                    if agent_addr in SandboxState.registry:
                        profile = SandboxState.registry[agent_addr]
                        if profile["owner"].lower() != sender.lower():
                            print("  [ERROR] Unauthorized owner deregister!")
                            return False
                        profile["active"] = False
                        return True
                    return False

                elif func_obj.fn_name == "stakeReputation":
                    print(f"\n[SANDBOX NODE] Tx Mined: stakeReputation")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    print(f"  Staker:     {sender}")
                    print(f"  Value:      {value} Wei ({value / 1e18} ETH)")
                    SandboxState.stakes[agent_addr] = SandboxState.stakes.get(agent_addr, 0) + value
                    return True

                elif func_obj.fn_name == "unstakeReputation":
                    print(f"\n[SANDBOX NODE] Tx Mined: unstakeReputation")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    
                    staked_amount = SandboxState.stakes.get(agent_addr, 0)
                    if staked_amount == 0:
                        print("  [ERROR] No stake found to unstake!")
                        return False
                    
                    if agent_addr in SandboxState.registry and SandboxState.registry[agent_addr]["active"]:
                        print("  [ERROR] Must deregister agent profile before unstaking!")
                        return False
                    
                    SandboxState.stakes[agent_addr] = 0
                    print(f"  [SUCCESS] Unstaked {staked_amount} Wei ({staked_amount / 1e18} ETH) to owner/caller.")
                    return True

                elif func_obj.fn_name == "setPaymentChannel":
                    payment_channel = func_args["_paymentChannel"].lower()
                    SandboxState.payment_channel = payment_channel
                    print(f"\n[SANDBOX NODE] Tx Mined: setPaymentChannel")
                    print(f"  Channel Contract: {payment_channel}")
                    return True

                elif func_obj.fn_name == "slashAgent":
                    print(f"\n[SANDBOX NODE] Tx Mined: slashAgent")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    print(f"  Recipient:  {func_args['recipient']}")
                    print(f"  Amount:     {func_args['amount']} Wei")
                    
                    # Verify caller is authorized payment channel (if set)
                    if sender_lower != SandboxState.payment_channel.lower() and SandboxState.payment_channel != "":
                        print(f"  [ERROR] Caller {sender} is not authorized payment channel {SandboxState.payment_channel}!")
                        return False
                        
                    agent_addr = func_args["agent"].lower()
                    amount = func_args["amount"]
                    if SandboxState.stakes.get(agent_addr, 0) < amount:
                        print("  [ERROR] Slash amount exceeds locked stake!")
                        return False
                        
                    SandboxState.stakes[agent_addr] -= amount
                    print(f"  [SUCCESS] Slashed {amount} Wei from agent {agent_addr}.")
                    return True

            # Payment Channel Contract interaction
            elif to.lower() == DEFAULT_CHANNEL_ADDRESS.lower():
                contract = w3.eth.contract(address=DEFAULT_CHANNEL_ADDRESS, abi=CHANNEL_ABI)
                func_obj, func_args = contract.decode_function_input(data)

                if func_obj.fn_name == "createChannel":
                    recipient = func_args["recipient"]
                    
                    # Compute channel_id = keccak256(sender, recipient)
                    channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient]).hex()
                    
                    print(f"\n[SANDBOX NODE] Tx Mined: createChannel")
                    print(f"  Sender:    {sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  Deposit:   {value} Wei ({value / 1e18} ETH)")
                    print(f"  ChannelID: {channel_id}")

                    channel = SandboxState.channels.get(channel_id, {
                        "sender": sender,
                        "recipient": recipient,
                        "deposit": 0,
                        "challengeExpiry": 0,
                        "challenged": False
                    })
                    channel["deposit"] += value
                    channel["challenged"] = False
                    channel["challengeExpiry"] = 0
                    SandboxState.channels[channel_id] = channel
                    return True

                elif func_obj.fn_name == "redeemVoucher":
                    chan_sender = func_args["sender"]
                    amount = func_args["amount"]
                    signature = func_args["signature"].hex()
                    
                    # Recipient is the caller (sender of this tx)
                    recipient = sender
                    channel_id = Web3.solidity_keccak(["address", "address"], [chan_sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: redeemVoucher")
                    print(f"  Sender:    {chan_sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  Amount:    {amount} Wei ({amount / 1e18} ETH)")
                    print(f"  ChannelID: {channel_id}")

                    channel = SandboxState.channels.get(channel_id)
                    if not channel:
                        print("  [ERROR] Channel not found!")
                        return False
                    
                    if amount > channel["deposit"]:
                        print("  [ERROR] Voucher amount exceeds locked deposit!")
                        return False

                    # Check for active dispute
                    dispute = SandboxState.disputes.get(channel_id)
                    if dispute and dispute["active"]:
                        print("  [ERROR] Cannot settle channel: Active dispute exists!")
                        return False

                    # Delete channel (Standard closed state)
                    del SandboxState.channels[channel_id]
                    print(f"  [SUCCESS] Channel settled! Payout {amount} Wei, Refunded remainder.")
                    return True

                elif func_obj.fn_name == "initiateChallenge":
                    recipient = func_args["recipient"]
                    channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: initiateChallenge")
                    print(f"  Sender:    {sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  ChannelID: {channel_id}")

                    channel = SandboxState.channels.get(channel_id)
                    if channel:
                        channel["challenged"] = True
                        channel["challengeExpiry"] = int(time.time()) + SandboxState.time_offset + 3600  # 1 hour expiry
                        return True
                    return False

                elif func_obj.fn_name == "claimChallengeRefund":
                    recipient = func_args["recipient"]
                    channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: claimChallengeRefund")
                    print(f"  Sender:    {sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  ChannelID: {channel_id}")

                    channel = SandboxState.channels.get(channel_id)
                    current_time = int(time.time()) + SandboxState.time_offset
                    if channel and channel["challenged"] and current_time >= channel["challengeExpiry"]:
                        del SandboxState.channels[channel_id]
                        print("  [SUCCESS] Refund claimed! Funds returned to sender.")
                        return True
                    print(f"  [ERROR] Challenge not expired (expiry {channel.get('challengeExpiry') if channel else 0}, current {current_time}) or not challenged!")
                    return False

                elif func_obj.fn_name == "initiateDispute":
                    recipient = func_args["recipient"]
                    task_hash = func_args["taskHash"]
                    channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: initiateDispute")
                    print(f"  Sender:    {sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  Task Hash: 0x{task_hash.hex()}")
                    print(f"  ChannelID: {channel_id}")

                    channel = SandboxState.channels.get(channel_id)
                    if not channel:
                        print("  [ERROR] Channel does not exist to dispute!")
                        return False

                    dispute = SandboxState.disputes.get(channel_id)
                    if dispute and dispute["active"]:
                        print("  [ERROR] Dispute already active!")
                        return False

                    SandboxState.disputes[channel_id] = {
                        "taskHash": task_hash,
                        "expiry": int(time.time()) + SandboxState.time_offset + 3600,  # 1 hour
                        "active": True
                    }
                    return True

                elif func_obj.fn_name == "resolveDispute":
                    chan_sender = func_args["sender"]
                    task_hash = func_args["taskHash"]
                    signature = func_args["signature"].hex()
                    
                    # msg.sender is the recipient (sender of this resolveDispute tx)
                    recipient = sender
                    channel_id = Web3.solidity_keccak(["address", "address"], [chan_sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: resolveDispute")
                    print(f"  Sender:    {chan_sender}")
                    print(f"  Recipient: {recipient}")
                    print(f"  Task Hash: 0x{task_hash.hex()}")

                    dispute = SandboxState.disputes.get(channel_id)
                    if not dispute or not dispute["active"]:
                        print("  [ERROR] No active dispute found to resolve!")
                        return False

                    if dispute["taskHash"] != task_hash:
                        print("  [ERROR] Dispute task hash mismatch!")
                        return False

                    dispute["active"] = False
                    print(f"  [SUCCESS] Dispute resolved by recipient submitting signature.")
                    return True

                elif func_obj.fn_name == "claimDisputeSlash":
                    recipient = func_args["recipient"]
                    channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient]).hex()

                    print(f"\n[SANDBOX NODE] Tx Mined: claimDisputeSlash")
                    print(f"  Sender:    {sender}")
                    print(f"  Recipient: {recipient}")

                    channel = SandboxState.channels.get(channel_id)
                    dispute = SandboxState.disputes.get(channel_id)
                    if not channel or not dispute or not dispute["active"]:
                        print("  [ERROR] No active channel or dispute found!")
                        return False

                    current_time = int(time.time()) + SandboxState.time_offset
                    if current_time < dispute["expiry"]:
                        print(f"  [ERROR] Dispute challenge window still active (expiry {dispute['expiry']}, current {current_time})!")
                        return False

                    refund_amount = channel["deposit"]
                    del SandboxState.channels[channel_id]
                    del SandboxState.disputes[channel_id]

                    # Perform slash: 0.05 ETH (50000000000000000 Wei)
                    slash_amount = 50000000000000000
                    recipient_lower = recipient.lower()
                    if SandboxState.stakes.get(recipient_lower, 0) >= slash_amount:
                        SandboxState.stakes[recipient_lower] -= slash_amount
                        print(f"  [SUCCESS] Slashed 0.05 ETH from {recipient_lower} registry stake. Refunded {refund_amount} Wei to {sender}.")
                    else:
                        print(f"  [WARNING] Registry stake for {recipient_lower} is insufficient ({SandboxState.stakes.get(recipient_lower, 0)} Wei). Slashed remaining.")
                        SandboxState.stakes[recipient_lower] = 0

                    return True

            print(f"\n[SANDBOX NODE] Unknown transaction to target {to}")
            return False

        except Exception as e:
            print(f"\n[SANDBOX NODE] Error processing transaction: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    def _process_eth_call(self, call_dict: Dict[str, Any]) -> str:
        """Processes read-only calls (getAgent, channels, getChannelId) and returns ABI-encoded responses."""
        w3 = Web3()
        to = call_dict.get("to", "").lower()
        data_hex = call_dict.get("data", "")
        data = bytes.fromhex(data_hex[2:] if data_hex.startswith("0x") else data_hex)

        # 1. Registry Contract Reads
        if to == DEFAULT_REGISTRY_ADDRESS.lower():
            contract = w3.eth.contract(address=DEFAULT_REGISTRY_ADDRESS, abi=REGISTRY_ABI)
            func_obj, func_args = contract.decode_function_input(data)

            if func_obj.fn_name == "getAgent":
                agent_addr = func_args["agent"].lower()
                profile = SandboxState.registry.get(agent_addr, {
                    "owner": "0x0000000000000000000000000000000000000000",
                    "endpoint": "",
                    "modelCapabilities": "",
                    "ratePerTaskWei": 0,
                    "active": False
                })

                encoded = eth_abi.encode(
                    ["address", "string", "string", "uint256", "bool"],
                    [
                        Web3.to_checksum_address(profile["owner"]),
                        profile["endpoint"],
                        profile["modelCapabilities"],
                        profile["ratePerTaskWei"],
                        profile["active"]
                    ]
                )
                return "0x" + encoded.hex()

            elif func_obj.fn_name == "stakes":
                agent_addr = func_args["agent"].lower() if "agent" in func_args else list(func_args.values())[0].lower()
                staked = SandboxState.stakes.get(agent_addr, 0)
                encoded = eth_abi.encode(["uint256"], [staked])
                return "0x" + encoded.hex()

        # 2. Payment Channel Contract Reads
        elif to == DEFAULT_CHANNEL_ADDRESS.lower():
            contract = w3.eth.contract(address=DEFAULT_CHANNEL_ADDRESS, abi=CHANNEL_ABI)
            func_obj, func_args = contract.decode_function_input(data)

            if func_obj.fn_name == "getChannelId":
                sender = func_args["sender"]
                recipient = func_args["recipient"]
                channel_id = Web3.solidity_keccak(["address", "address"], [sender, recipient])
                
                encoded = eth_abi.encode(["bytes32"], [channel_id])
                return "0x" + encoded.hex()

            elif func_obj.fn_name == "channels":
                channel_id = func_args["channelId"].hex()
                channel = SandboxState.channels.get(channel_id, {
                    "deposit": 0,
                    "challengeExpiry": 0,
                    "challenged": False
                })

                encoded = eth_abi.encode(
                    ["uint256", "uint256", "bool"],
                    [channel["deposit"], channel["challengeExpiry"], channel["challenged"]]
                )
                return "0x" + encoded.hex()

            elif func_obj.fn_name == "disputes":
                channel_id = func_args["channelId"].hex()
                dispute = SandboxState.disputes.get(channel_id, {
                    "taskHash": b"\x00" * 32,
                    "expiry": 0,
                    "active": False
                })

                encoded = eth_abi.encode(
                    ["bytes32", "uint256", "bool"],
                    [dispute["taskHash"], dispute["expiry"], dispute["active"]]
                )
                return "0x" + encoded.hex()

        # Fallback return empty bytes
        return "0x"


# Globally track if servers are running
agent_servers_started = False
servers_dict = {}
apps_dict = {}

def execute_swarm_in_background():
    global agent_servers_started, apps_dict
    if SandboxState.is_running_swarm:
        return
    
    SandboxState.is_running_swarm = True
    SandboxState.logs = []
    
    def log(msg: str):
        print(f"[SWARM LOG] {msg}")
        SandboxState.logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        
    try:
        w3 = Web3(Web3.HTTPProvider("http://127.0.0.1:8545"))
        
        # Keys
        ALICE_OWNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        ALICE_AGENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690c"
        BROKER_OWNER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
        BROKER_AGENT_KEY = "0xabf82f5110266c165e6488bc1103c80ff2570891d4e0e5a8e64e10b42f61a789"
        DEV_OWNER_KEY = "0x47e1754f7b1d9c2f82195000575d30a8a37c093a1cf552a4e2ef30f81d11a234"
        DEV_AGENT_KEY = "0x70c72b1a8cd26b840134a6210f0322bf25852891d4e0e5a8e64e10b42f61a789"
        AUDITOR_OWNER_KEY = "0x8b3a74bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
        AUDITOR_AGENT_KEY = "0x80c72b1a8cd26b840134a6210f0322bf25852891d4e0e5a8e64e10b42f61a456"

        from agent_mailroom.mailroom import AgentMailroom
        from agent_mailroom.server import create_agent_app
        from agent_mailroom.broker import BrokerAgent
        import uvicorn
        import threading
        
        log("Initializing Agent Mailrooms...")
        alice_mailroom = AgentMailroom(ALICE_AGENT_KEY, w3)
        broker_mailroom = AgentMailroom(BROKER_AGENT_KEY, w3)
        dev_mailroom = AgentMailroom(DEV_AGENT_KEY, w3)
        auditor_mailroom = AgentMailroom(AUDITOR_AGENT_KEY, w3)

        # Set payment channel address
        alice_mailroom.registry.set_payment_channel(ALICE_OWNER_KEY, alice_mailroom.channel_manager.contract_address)

        log("Registering Alice (Buyer) DID profile...")
        alice_mailroom.register_on_chain(ALICE_OWNER_KEY, "http://127.0.0.1:8001", ["buyer"], 0)
        
        log("Registering Broker DID profile...")
        broker_mailroom.register_on_chain(BROKER_OWNER_KEY, "http://127.0.0.1:8004", ["orchestration"], 0)
        
        log("Registering Developer DID profile (Stake: 0.1 ETH, Cost: 0.01 ETH)...")
        dev_mailroom.register_on_chain(DEV_OWNER_KEY, "http://127.0.0.1:8003", ["refactor"], w3.to_wei(0.01, "ether"))
        
        log("Registering Auditor DID profile (Stake: 0.1 ETH, Cost: 0.015 ETH)...")
        auditor_mailroom.register_on_chain(AUDITOR_OWNER_KEY, "http://127.0.0.1:8002", ["audit"], w3.to_wei(0.015, "ether"))

        if not agent_servers_started:
            log("Starting sub-agent FastAPI HTTP servers...")
            
            # Dev Server (8003)
            def dev_task_handler(task_type: str, params: dict) -> dict:
                code = params.get("code", "")
                return {"code": f"// Developer Refactored Code\nfunction optimized() {{\n  // Done\n}}\n{code}"}
            dev_app = create_agent_app(dev_mailroom, dev_task_handler)
            apps_dict["dev"] = dev_app
            dev_config = uvicorn.Config(dev_app, host="127.0.0.1", port=8003, log_level="warning")
            servers_dict["dev"] = uvicorn.Server(dev_config)
            threading.Thread(target=servers_dict["dev"].run, daemon=True).start()

            # Auditor Server (8002)
            def auditor_task_handler(task_type: str, params: dict) -> dict:
                code = params.get("code", "")
                return {"report": f"Security Scan Report:\n- Buffer Overflows: None\n- Re-entrancy check: Safe\n- Lines Scanned: {len(code.splitlines())}"}
            auditor_app = create_agent_app(auditor_mailroom, auditor_task_handler)
            apps_dict["auditor"] = auditor_app
            auditor_config = uvicorn.Config(auditor_app, host="127.0.0.1", port=8002, log_level="warning")
            servers_dict["auditor"] = uvicorn.Server(auditor_config)
            threading.Thread(target=servers_dict["auditor"].run, daemon=True).start()

            # Broker Server (8004)
            broker_agent = BrokerAgent(
                w3=w3,
                private_key=BROKER_AGENT_KEY,
                developer_did=dev_mailroom.did,
                auditor_did=auditor_mailroom.did,
                registry_address=broker_mailroom.registry.contract_address,
                channel_address=broker_mailroom.channel_manager.contract_address,
                brokerage_fee_wei=w3.to_wei(0.005, "ether")
            )
            broker_app = broker_agent.create_app()
            apps_dict["broker"] = broker_app
            broker_config = uvicorn.Config(broker_app, host="127.0.0.1", port=8004, log_level="warning")
            servers_dict["broker"] = uvicorn.Server(broker_config)
            threading.Thread(target=servers_dict["broker"].run, daemon=True).start()

            agent_servers_started = True
            time.sleep(0.5)
            log("Sub-agent servers started successfully.")

        log("Alice requesting quote for 'refactor-and-audit' from Broker...")
        broker_profile = alice_mailroom.registry.get_agent_profile(broker_mailroom.did)
        
        task_payload = {
            "task": "refactor-and-audit",
            "params": {
                "code": "function start() { return 1; }",
                "rules": ["gas-optimization"]
            }
        }
        
        # Outgoing HTTP request handles the RFQ negotiation, locks deposit, signs envelope, and posts
        log("Executing secure dynamic multi-agent execution...")
        result = alice_mailroom.send_request_http(
            recipient_did=broker_mailroom.did,
            recipient_endpoint=broker_profile.endpoint,
            task_payload=task_payload
        )
        
        log("Alice received final consolidated output!")
        log("Developer code refactored successfully.")
        log("Auditor scan report generated successfully.")
        
        log("Executing dynamic on-chain settlements...")
        dev_app = apps_dict["dev"]
        auditor_app = apps_dict["auditor"]
        broker_app = apps_dict["broker"]
        # Settle Dev
        dev_voucher = dev_app.state.verified_vouchers.get(broker_mailroom.agent_address.lower())
        if dev_voucher:
            dev_mailroom.channel_manager.redeem_voucher_on_chain(DEV_AGENT_KEY, broker_mailroom.agent_address, dev_voucher)
            log("Developer settled payment from Broker (0.01 ETH).")
            
        # Settle Auditor
        auditor_voucher = auditor_app.state.verified_vouchers.get(broker_mailroom.agent_address.lower())
        if auditor_voucher:
            auditor_mailroom.channel_manager.redeem_voucher_on_chain(AUDITOR_AGENT_KEY, broker_mailroom.agent_address, auditor_voucher)
            log("Auditor settled payment from Broker (0.015 ETH).")
            
        # Settle Broker
        broker_voucher = broker_app.state.verified_vouchers.get(alice_mailroom.agent_address.lower())
        if broker_voucher:
            broker_mailroom.channel_manager.redeem_voucher_on_chain(BROKER_AGENT_KEY, alice_mailroom.agent_address, broker_voucher)
            log("Broker settled payment from Alice (0.03 ETH).")
            
        log("Swarm economy coordination settled successfully.")
        
    except Exception as e:
        log(f"Swarm error: {str(e)}")
    finally:
        SandboxState.is_running_swarm = False


def run_server(port: int = 8545) -> None:
    server_address = ('127.0.0.1', port)
    httpd = HTTPServer(server_address, SandboxJSONRPCHandler)
    print(f"[SANDBOX NODE] Running HTTP JSON-RPC EVM node simulator on http://127.0.0.1:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[SANDBOX NODE] Shutting down server...")
        sys.exit(0)


if __name__ == "__main__":
    port = 8545
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    run_server(port)
