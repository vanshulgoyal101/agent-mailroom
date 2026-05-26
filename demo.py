import os
import sys
import subprocess
import time
import json
from web3 import Web3
from dotenv import load_dotenv

# Ensure we can import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_mailroom import (
    AgentMailroom,
    build_agent_did,
)

# Load environment variables
load_dotenv()

# Test keys and configuration
RPC_URL = os.getenv("ETH_RPC_URL", "http://127.0.0.1:8545")

# Human Owner Keys
ALICE_OWNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
BOB_OWNER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

# Agent Keypairs (Generated internally or loaded here)
ALICE_AGENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690c" # Alice Agent Key (Account 2)
BOB_AGENT_KEY = "0xabf82f5110266c165e6488bc1103c80ff2570891d4e0e5a8e64e10b42f61a1a7" # Bob Agent Key (Account 3)"


def run_demo():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    server_proc = None

    try:
        # Step 1: Spin up Sandbox RPC Server in the background
        print("[DEMO] Starting local Sandbox RPC Node on port 8545...")
        node_script = os.path.join(os.path.dirname(__file__), "sandbox_node.py")
        server_proc = subprocess.Popen(
            [sys.executable, "-u", node_script, "8545"],
            stdout=subprocess.stdout if hasattr(subprocess, "stdout") else None,
            stderr=subprocess.stderr if hasattr(subprocess, "stderr") else None
        )
        
        # Give the server a moment to launch
        time.sleep(1.5)
        
        if server_proc.poll() is not None:
            raise RuntimeError(f"Failed to start Sandbox Node. Exit code: {server_proc.returncode}.")
            
        print("[DEMO] Sandbox Node is running. Initializing Mailrooms...")

        # Step 2: Initialize Agent Mailrooms
        alice_mailroom = AgentMailroom(ALICE_AGENT_KEY, w3)
        bob_mailroom = AgentMailroom(BOB_AGENT_KEY, w3)

        print(f"Alice Agent DID: {alice_mailroom.did}")
        print(f"Bob Agent DID:   {bob_mailroom.did}")

        # Step 3: Register Agents in the On-Chain Registry
        print("\n[DEMO] Registering Agents on-chain...")
        
        # Alice Owner registers Alice Agent
        tx1 = alice_mailroom.register_on_chain(
            owner_private_key=ALICE_OWNER_KEY,
            endpoint="http://127.0.0.1:8001/alice-mailroom",
            capabilities=["defi-arbitrage", "rebalancing"],
            rate_wei=0  # Alice does not charge for requests
        )
        print(f"  Alice Agent Registered: tx_hash={tx1}")

        # Bob Owner registers Bob Agent
        bob_rate = w3.to_wei(0.01, "ether")  # Bob charges 0.01 ETH per analysis task
        tx2 = bob_mailroom.register_on_chain(
            owner_private_key=BOB_OWNER_KEY,
            endpoint="http://127.0.0.1:8002/bob-mailroom",
            capabilities=["contract-audit", "summarization"],
            rate_wei=bob_rate
        )
        print(f"  Bob Agent Registered (Charges 0.01 ETH/task): tx_hash={tx2}")

        # Step 4: Alice resolves Bob's DID profile from the registry
        print("\n[DEMO] Alice discovering Bob's profile on-chain...")
        bob_profile = alice_mailroom.registry.get_agent_profile(bob_mailroom.did)
        print(f"  Bob Endpoint:     {bob_profile.endpoint}")
        print(f"  Bob Capabilities: {bob_profile.model_capabilities}")
        print(f"  Bob Rate:         {w3.from_wei(bob_profile.rate_per_task_wei, 'ether')} ETH")

        # Step 5: Alice opens a payment channel to Bob
        print("\n[DEMO] Alice opening state channel to Bob...")
        deposit_amount = w3.to_wei(0.05, "ether")
        tx3 = alice_mailroom.channel_manager.open_channel(
            sender_private_key=ALICE_AGENT_KEY,
            recipient_address=bob_mailroom.agent_address,
            amount_wei=deposit_amount
        )
        print(f"  Payment Channel Opened: tx_hash={tx3}")

        # Verify balance locked in channel
        dep, exp, chg = alice_mailroom.channel_manager.get_channel_info(
            sender=alice_mailroom.agent_address,
            recipient=bob_mailroom.agent_address
        )
        print(f"  Verified Channel Balance: {w3.from_wei(dep, 'ether')} ETH")

        # Step 6: Alice prepares secure task request for Bob with voucher payment
        print("\n[DEMO] Alice preparing secure payload & signed payment voucher...")
        task_payload = {
            "task": "contract-audit",
            "contract_address": "0xdeadbeef10101010101010101010101010101010",
            "depth": "deep"
        }
        
        # Alice signs the request envelope and attaches a 0.01 ETH cumulative voucher
        envelope, voucher = alice_mailroom.prepare_request(
            recipient_did=bob_mailroom.did,
            payload=task_payload,
            attach_voucher_amount_wei=bob_profile.rate_per_task_wei
        )

        print(f"  Request Signed! Envelope: {json.dumps(envelope, indent=2)[:300]}...")
        print(f"  Voucher Issued: {json.dumps(voucher, indent=2)}")

        # Step 7: Bob receives the secure request and processes it
        print("\n[DEMO] Bob verifying Alice's request envelope and voucher...")
        
        # Bob executes validation protocol
        verified_sender_did, parsed_payload, verified_voucher = bob_mailroom.process_incoming_request(
            envelope_data=envelope,
            voucher_data=voucher
        )

        print("  [SUCCESS] All checks passed!")
        print(f"    Authenticated Sender DID: {verified_sender_did}")
        print(f"    Payload Content matches:  {parsed_payload}")
        print(f"    Valid Voucher value:      {w3.from_wei(verified_voucher.amount_wei, 'ether')} ETH")

        # Bob processes the mock analysis task
        print("\n[DEMO] Bob executing task...")
        print("  [Bob Node] Analyzing contract 0xdeadbeef... running AST audits...")
        time.sleep(1.0)
        task_response = {
            "status": "success",
            "result": "No critical re-entrancy or overflow vulnerabilities found. Verify compiler version ^0.8.20."
        }
        print(f"  Task result: {task_response}")

        # Step 8: Bob redeems the voucher on-chain to withdraw his earnings
        print("\n[DEMO] Bob settling the payment channel on-chain...")
        tx4 = bob_mailroom.channel_manager.redeem_voucher_on_chain(
            recipient_private_key=BOB_AGENT_KEY,
            sender_address=alice_mailroom.agent_address,
            voucher=verified_voucher
        )
        print(f"  Redeem Voucher transaction mined: tx_hash={tx4}")

        # Step 9: Verify final channel balance (the remaining 0.04 ETH is returned to Alice, channel closed)
        print("\n[DEMO] Verifying post-settlement channel status...")
        post_dep, _, _ = bob_mailroom.channel_manager.get_channel_info(
            sender=alice_mailroom.agent_address,
            recipient=bob_mailroom.agent_address
        )
        print(f"  Remaining Channel Balance (should be 0 because settled/closed): {post_dep} Wei")
        print("\n====================================================")
        print("    M2M IDENTITY & PAYMENT PROTOCOL RUN COMPLETED   ")
        print("====================================================")

    except Exception as e:
        print(f"\n[DEMO ERROR] Execution failed: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        # Step 10: Clean up Sandbox Node
        if server_proc:
            print("\n[DEMO] Cleaning up. Shutting down Sandbox RPC Server...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
                print("[DEMO] Sandbox RPC Server shutdown successfully.")
            except subprocess.TimeoutExpired:
                print("[DEMO] Server did not exit in time. Killing process...")
                server_proc.kill()
                server_proc.wait()


if __name__ == "__main__":
    print("====================================================")
    print("      AGENTMAILROOM SECURE HANDSHAKE & PAYMENT      ")
    print("====================================================")
    run_demo()
