import os
import sys
import subprocess
import time
import json
import threading
import uvicorn
from web3 import Web3
from dotenv import load_dotenv

# Ensure we can import modules from agent_mailroom
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_mailroom import (
    AgentMailroom,
    create_agent_app,
    TaskSpec,
    build_agent_did,
)

# Load environment variables
load_dotenv()

# Test configuration
RPC_URL = os.getenv("ETH_RPC_URL", "http://127.0.0.1:8545")

# Human Owner Keys
ALICE_OWNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
BOB_OWNER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

# Agent Keys (Account 2 and Account 3)
ALICE_AGENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690c"
BOB_AGENT_KEY = "0xabf82f5110266c165e6488bc1103c80ff2570891d4e0e5a8e64e10b42f61a1a7"


def run_demo():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    server_proc = None
    bob_web_server = None
    bob_thread = None

    try:
        # Step 1: Start Sandbox EVM Node on port 8545
        print("[DEMO] Starting local Sandbox RPC Node on port 8545...")
        node_script = os.path.join(os.path.dirname(__file__), "sandbox_node.py")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", node_script, "8545"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        # Wait for simulator startup
        import socket
        start_time = time.time()
        success = False
        while time.time() - start_time < 5.0:
            try:
                with socket.create_connection(("127.0.0.1", 8545), timeout=0.5):
                    success = True
                    break
            except OSError:
                time.sleep(0.1)
                
        if not success or server_proc.poll() is not None:
            raise RuntimeError("Failed to start Sandbox Node simulator.")
            
        print("[DEMO] Sandbox Node is running.")

        # Step 2: Initialize Agent Mailrooms
        alice_mailroom = AgentMailroom(ALICE_AGENT_KEY, w3)
        bob_mailroom = AgentMailroom(BOB_AGENT_KEY, w3)

        print(f"Alice Agent DID: {alice_mailroom.did}")
        print(f"Bob Agent DID:   {bob_mailroom.did}")

        # Step 3: Register Agents in the DID Registry on-chain
        print("\n[DEMO] Registering Agents on-chain...")
        alice_mailroom.register_on_chain(
            owner_private_key=ALICE_OWNER_KEY,
            endpoint="http://127.0.0.1:8001",
            capabilities=["arbitrage"],
            rate_wei=0
        )
        bob_mailroom.register_on_chain(
            owner_private_key=BOB_OWNER_KEY,
            endpoint="http://127.0.0.1:8002",
            capabilities=["contract-audit", "summarization"],
            rate_wei=w3.to_wei(0.01, "ether")  # Base registered rate is 0.01 ETH
        )
        print("  [SUCCESS] Agents registered successfully on-chain.")

        # Step 4: Configure and start Bob's Agent HTTP Server in a background thread
        print("\n[DEMO] Configuring Bob's dynamic price calculator and task handler...")
        
        # Bob charges dynamically: 0.03 ETH for deep audits, 0.01 ETH for others
        def bob_price_calculator(spec: TaskSpec) -> int:
            if spec.task_type == "contract-audit" and spec.params.get("depth") == "deep":
                return w3.to_wei(0.03, "ether")
            return w3.to_wei(0.01, "ether")

        def bob_task_handler(task_type: str, params: dict) -> dict:
            print(f"  [Bob Server] Running AST audit scanner on address {params.get('contract_address')}...")
            return {
                "audit_status": "completed",
                "vulnerabilities": [],
                "details": "0 critical overflows, compiler optimization verified."
            }

        # Initialize FastAPI app
        bob_app = create_agent_app(
            mailroom=bob_mailroom,
            task_handler=bob_task_handler,
            price_calculator=bob_price_calculator
        )

        print("[DEMO] Starting Bob's Agent HTTP Web Server on http://127.0.0.1:8002...")
        config = uvicorn.Config(bob_app, host="127.0.0.1", port=8002, log_level="warning")
        bob_web_server = uvicorn.Server(config)
        bob_thread = threading.Thread(target=bob_web_server.run, daemon=True)
        bob_thread.start()

        # Wait for Bob's FastAPI server to bind
        start_time = time.time()
        success = False
        while time.time() - start_time < 5.0:
            try:
                with socket.create_connection(("127.0.0.1", 8002), timeout=0.5):
                    success = True
                    break
            except OSError:
                time.sleep(0.1)
        if not success:
            raise RuntimeError("Failed to start Bob's FastAPI server.")
            
        print("[DEMO] Bob's Web Server is running.")

        # Step 5: Alice discovers Bob's profile on-chain
        print("\n[DEMO] Alice resolving Bob's DID registry details...")
        bob_profile = alice_mailroom.registry.get_agent_profile(bob_mailroom.did)
        print(f"  Bob Endpoint resolved: {bob_profile.endpoint}")
        print(f"  Bob Capabilities:       {bob_profile.model_capabilities}")

        # Step 6: Alice sends HTTP request to Bob (Triggers RFQ & state-channel flow)
        print("\n[DEMO] Alice requesting dynamic quote and executing task over HTTP...")
        task_payload = {
            "task": "contract-audit",
            "params": {
                "contract_address": "0xdeadbeef33333333333333333333333333333333",
                "depth": "deep"  # Triggers the 0.03 ETH quote
            }
        }

        # Outgoing HTTP request handles the RFQ negotiation, locks deposit, signs envelope, and posts
        response = alice_mailroom.send_request_http(
            recipient_did=bob_mailroom.did,
            recipient_endpoint=bob_profile.endpoint,
            task_payload=task_payload
        )

        print(f"\n[DEMO RESULT] Alice received execution response from Bob over HTTP:")
        print(json.dumps(response, indent=2))

        # Step 7: Bob settles the payment channel on-chain using the voucher stored in his server state
        print("\n[DEMO] Bob extracting client voucher from server memory and redeeming on-chain...")
        
        # Retrieve Bob's voucher for Alice's address
        alice_addr_lower = alice_mailroom.agent_address.lower()
        stored_voucher = bob_app.state.verified_vouchers.get(alice_addr_lower)
        if not stored_voucher:
            raise RuntimeError("Bob's server state did not save Alice's voucher.")

        print(f"  Voucher amount to redeem: {w3.from_wei(stored_voucher.amount_wei, 'ether')} ETH")
        
        tx_settle = bob_mailroom.channel_manager.redeem_voucher_on_chain(
            recipient_private_key=BOB_AGENT_KEY,
            sender_address=alice_mailroom.agent_address,
            voucher=stored_voucher
        )
        print(f"  Voucher settled on-chain: tx_hash={tx_settle}")

        # Step 8: Verify final channel deposit (remainder of 0.02 ETH returned to Alice)
        post_deposit, _, _ = bob_mailroom.channel_manager.get_channel_info(
            sender=alice_mailroom.agent_address,
            recipient=bob_mailroom.agent_address
        )
        print(f"  Remaining Channel Balance (settled and closed): {post_deposit} Wei")
        print("\n====================================================")
        print("     M2M HTTP RFQ NETWORK HANDSHAKE COMPLETED       ")
        print("====================================================")

    except Exception as e:
        print(f"\n[DEMO ERROR] Handshake execution failed: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        # Step 9: Clean up Bob's Web Server
        if bob_web_server:
            print("\n[DEMO] Shutting down Bob's HTTP Server...")
            bob_web_server.should_exit = True
            if bob_thread:
                bob_thread.join(timeout=5)
                print("[DEMO] Bob's server shutdown successfully.")

        # Step 10: Clean up Sandbox EVM Node
        if server_proc:
            print("[DEMO] Shutting down Sandbox RPC Server...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
                print("[DEMO] Sandbox RPC Server shutdown successfully.")
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()


if __name__ == "__main__":
    print("====================================================")
    print("      AGENTMAILROOM HTTP RFQ & EXECUTION DEMO       ")
    print("====================================================")
    run_demo()
