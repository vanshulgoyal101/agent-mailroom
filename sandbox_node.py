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


class SandboxJSONRPCHandler(BaseHTTPRequestHandler):

    def log_message(self, format: str, *args: Any) -> None:
        # Override to suppress standard HTTP logging and keep console output clean
        pass

    def do_POST(self) -> None:
        """Handles POST requests carrying JSON-RPC commands from Web3.py."""
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
        self.send_header('Content-Length', str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

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
            result = "0x" + hex(int(time.time()))[2:]  # Pseudo incrementing blocks based on time
            
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
                
                agent_addr = func_args["agent"].lower()

                if func_obj.fn_name == "registerAgent":
                    print(f"\n[SANDBOX NODE] Tx Mined: registerAgent")
                    print(f"  Owner:      {sender}")
                    print(f"  Agent DID:  did:agent:eth:{func_args['agent']}")
                    print(f"  Endpoint:   {func_args['endpoint']}")
                    print(f"  Rate:       {func_args['ratePerTaskWei']} Wei")
                    
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
                        channel["challengeExpiry"] = int(time.time()) + 3600  # 1 hour expiry
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
                    if channel and channel["challenged"] and int(time.time()) >= channel["challengeExpiry"]:
                        del SandboxState.channels[channel_id]
                        print("  [SUCCESS] Refund claimed! Funds returned to sender.")
                        return True
                    print("  [ERROR] Challenge not expired or not challenged!")
                    return False

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

        # Fallback return empty bytes
        return "0x"


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
