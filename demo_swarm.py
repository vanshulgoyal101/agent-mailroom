import os
import sys
import subprocess
import time
import json
import threading
import uvicorn
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv

# Ensure we can import modules from agent_mailroom
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_mailroom import (
    AgentMailroom,
    create_agent_app,
    TaskSpec,
    build_agent_did,
    BrokerAgent
)

load_dotenv()

RPC_URL = os.getenv("ETH_RPC_URL", "http://127.0.0.1:8545")

# Setup private keys for the demo
# Alice: Client/Buyer
ALICE_OWNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
ALICE_AGENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690c"

# Broker: Middleman Swarm Broker
BROKER_OWNER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
BROKER_AGENT_KEY = "0xabf82f5110266c165e6488bc1103c80ff2570891d4e0e5a8e64e10b42f61a789"

# Developer Sub-Agent
DEV_OWNER_KEY = "0x47e1754f7b1d9c2f82195000575d30a8a37c093a1cf552a4e2ef30f81d11a234"
DEV_AGENT_KEY = "0x70c72b1a8cd26b840134a6210f0322bf25852891d4e0e5a8e64e10b42f61a789"

# Auditor Sub-Agent
AUDITOR_OWNER_KEY = "0x8b3a74bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
AUDITOR_AGENT_KEY = "0x80c72b1a8cd26b840134a6210f0322bf25852891d4e0e5a8e64e10b42f61a456"


def run_swarm_demo():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    server_proc = None
    
    servers = {}
    threads = {}

    try:
        # Step 1: Start Sandbox Node
        print("[DEMO SWARM] Starting local Sandbox RPC Node on port 8545...")
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
        print("[DEMO SWARM] Sandbox Node is running.")

        # Step 2: Initialize Mailrooms
        alice_mailroom = AgentMailroom(ALICE_AGENT_KEY, w3)
        broker_mailroom = AgentMailroom(BROKER_AGENT_KEY, w3)
        dev_mailroom = AgentMailroom(DEV_AGENT_KEY, w3)
        auditor_mailroom = AgentMailroom(AUDITOR_AGENT_KEY, w3)

        # Set payment channel address in registry
        alice_mailroom.registry.set_payment_channel(ALICE_OWNER_KEY, alice_mailroom.channel_manager.contract_address)

        # Step 3: Register Agents on-chain (automatic staking is handled by register_on_chain!)
        print("\n[DEMO SWARM] Registering Swarm Agents in the Registry...")
        alice_mailroom.register_on_chain(ALICE_OWNER_KEY, "http://127.0.0.1:8001", ["buyer"], 0)
        broker_mailroom.register_on_chain(BROKER_OWNER_KEY, "http://127.0.0.1:8004", ["orchestration"], 0)
        dev_mailroom.register_on_chain(DEV_OWNER_KEY, "http://127.0.0.1:8003", ["refactor"], w3.to_wei(0.01, "ether"))
        auditor_mailroom.register_on_chain(AUDITOR_OWNER_KEY, "http://127.0.0.1:8002", ["audit"], w3.to_wei(0.015, "ether"))
        print("[DEMO SWARM] Registration completed successfully.")

        # Step 4: Configure and start Developer Sub-Agent server (8003)
        def dev_task_handler(task_type: str, params: dict) -> dict:
            code = params.get("code", "")
            print(f"  [Dev Server] Refactoring requested code...")
            refactored = f"// Developer Refactored Code\nfunction optimized() {{\n  // Done\n}}\n{code}"
            return {"code": refactored}

        dev_app = create_agent_app(dev_mailroom, dev_task_handler)
        print("[DEMO SWARM] Starting Developer Server on port 8003...")
        dev_config = uvicorn.Config(dev_app, host="127.0.0.1", port=8003, log_level="warning")
        servers["dev"] = uvicorn.Server(dev_config)
        threads["dev"] = threading.Thread(target=servers["dev"].run, daemon=True)
        threads["dev"].start()

        # Step 5: Configure and start Auditor Sub-Agent server (8002)
        def auditor_task_handler(task_type: str, params: dict) -> dict:
            code = params.get("code", "")
            print(f"  [Auditor Server] Scanning refactored code for security vulnerabilities...")
            report = f"Security Scan Report:\n- Buffer Overflows: None\n- Re-entrancy check: Safe\n- Lines Scanned: {len(code.splitlines())}"
            return {"report": report}

        auditor_app = create_agent_app(auditor_mailroom, auditor_task_handler)
        print("[DEMO SWARM] Starting Auditor Server on port 8002...")
        auditor_config = uvicorn.Config(auditor_app, host="127.0.0.1", port=8002, log_level="warning")
        servers["auditor"] = uvicorn.Server(auditor_config)
        threads["auditor"] = threading.Thread(target=servers["auditor"].run, daemon=True)
        threads["auditor"].start()

        # Wait for dev and auditor servers to bind
        time.sleep(0.5)

        # Step 6: Configure and start Broker Agent server (8004)
        broker_agent = BrokerAgent(
            w3=w3,
            private_key=BROKER_AGENT_KEY,
            developer_did=dev_mailroom.did,
            auditor_did=auditor_mailroom.did,
            registry_address=broker_mailroom.registry.contract_address,
            channel_address=broker_mailroom.channel_manager.contract_address,
            brokerage_fee_wei=w3.to_wei(0.005, "ether")  # Broker charges 0.005 ETH middleman fee
        )
        broker_app = broker_agent.create_app()
        print("[DEMO SWARM] Starting Broker Server on port 8004...")
        broker_config = uvicorn.Config(broker_app, host="127.0.0.1", port=8004, log_level="warning")
        servers["broker"] = uvicorn.Server(broker_config)
        threads["broker"] = threading.Thread(target=servers["broker"].run, daemon=True)
        threads["broker"].start()

        # Wait for all servers to bind
        time.sleep(0.5)

        # Step 7: Alice triggers Swarm execute over HTTP
        print("\n[DEMO SWARM] Alice resolving Broker's DID and sending swarm task request...")
        broker_profile = alice_mailroom.registry.get_agent_profile(broker_mailroom.did)
        
        task_payload = {
            "task": "refactor-and-audit",
            "params": {
                "code": "function start() { return 1; }",
                "rules": ["gas-optimization"]
            }
        }

        # Send request through the SDK. This will:
        # - request quote from Broker
        # - Broker will request quotes from Developer and Auditor, calculate composite total, sign and return
        # - Alice will open channel and fund it for the Broker
        # - Alice will sign execution payload and call execute on Broker
        # - Broker will open channels to Developer and Auditor and execute sub-tasks
        # - Broker will consolidate results and return to Alice
        result = alice_mailroom.send_request_http(
            recipient_did=broker_mailroom.did,
            recipient_endpoint=broker_profile.endpoint,
            task_payload=task_payload
        )

        print("\n[DEMO SWARM RESULT] Alice received dynamic Swarm result:")
        print(json.dumps(result, indent=2))

        # Step 8: Settle payments on-chain
        print("\n[DEMO SWARM] Initiating on-chain settlements...")
        
        # Developer settles Broker's voucher
        dev_voucher = dev_app.state.verified_vouchers.get(broker_mailroom.agent_address.lower())
        if dev_voucher:
            tx_dev = dev_mailroom.channel_manager.redeem_voucher_on_chain(
                recipient_private_key=DEV_AGENT_KEY,
                sender_address=broker_mailroom.agent_address,
                voucher=dev_voucher
            )
            print(f"  Developer settled payment from Broker. Tx Hash: {tx_dev}")

        # Auditor settles Broker's voucher
        auditor_voucher = auditor_app.state.verified_vouchers.get(broker_mailroom.agent_address.lower())
        if auditor_voucher:
            tx_aud = auditor_mailroom.channel_manager.redeem_voucher_on_chain(
                recipient_private_key=AUDITOR_AGENT_KEY,
                sender_address=broker_mailroom.agent_address,
                voucher=auditor_voucher
            )
            print(f"  Auditor settled payment from Broker. Tx Hash: {tx_aud}")

        # Broker settles Alice's voucher
        broker_voucher = broker_app.state.verified_vouchers.get(alice_mailroom.agent_address.lower())
        if broker_voucher:
            tx_bro = broker_mailroom.channel_manager.redeem_voucher_on_chain(
                recipient_private_key=BROKER_AGENT_KEY,
                sender_address=alice_mailroom.agent_address,
                voucher=broker_voucher
            )
            print(f"  Broker settled payment from Alice. Tx Hash: {tx_bro}")

        print("\n====================================================")
        print("    MULTI-AGENT SWARM ORCHESTRATION COMPLETED       ")
        print("====================================================")

    except Exception as e:
        print(f"\n[DEMO SWARM ERROR] Execution failed: {str(e)}")
        import traceback
        traceback.print_exc()

    finally:
        # Teardown servers
        for name, server in servers.items():
            print(f"[DEMO SWARM] Shutting down {name} server...")
            server.should_exit = True
            if name in threads:
                threads[name].join(timeout=2)
                
        if server_proc:
            print("[DEMO SWARM] Shutting down Sandbox RPC Server...")
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server_proc.kill()
                server_proc.wait()


if __name__ == "__main__":
    print("====================================================")
    print("      MULTI-AGENT SWARM COORDINATION DEMO           ")
    print("====================================================")
    run_swarm_demo()
