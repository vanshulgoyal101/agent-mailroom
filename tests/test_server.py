import pytest
from fastapi.testclient import TestClient
from eth_account import Account
from agent_mailroom import (
    AgentMailroom,
    create_agent_app,
    TaskSpec,
    build_agent_did
)

# Test key configurations
CLIENT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PROVIDER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"


def test_server_status_quote_and_execute(w3):
    """
    Tests the status, dynamic RFQ quote generation, and execution endpoints of
    the FastAPI agent app server framework using the TestClient.
    """
    # 1. Initialize mailroom handlers
    alice_mailroom = AgentMailroom(CLIENT_KEY, w3)
    bob_mailroom = AgentMailroom(PROVIDER_KEY, w3)

    # 2. Register both agents on-chain (in the sandbox simulator)
    alice_mailroom.register_on_chain(
        owner_private_key=CLIENT_KEY,
        agent_address=alice_mailroom.agent_address,
        endpoint="http://127.0.0.1:8001",
        capabilities=["requester"],
        rate_wei=0
    )

    bob_mailroom.register_on_chain(
        owner_private_key=PROVIDER_KEY,
        agent_address=bob_mailroom.agent_address,
        endpoint="http://127.0.0.1:8002",
        capabilities=["contract-audit", "transcription"],
        rate_wei=2000  # Default base price
    )

    # Define mock task processing handler
    def mock_task_handler(task_type: str, params: dict) -> dict:
        return {"output": f"Mock processed task '{task_type}'", "received_params": params}

    # Define dynamic price calculator: audit costs 5000, others cost base 2000
    def dynamic_price_calculator(spec: TaskSpec) -> int:
        if spec.task_type == "contract-audit":
            return 5000
        return 2000

    # 3. Create FastAPI app
    app = create_agent_app(
        mailroom=bob_mailroom, 
        task_handler=mock_task_handler,
        price_calculator=dynamic_price_calculator
    )
    client = TestClient(app)

    # 4. Verify status endpoint
    res_status = client.get("/")
    assert res_status.status_code == 200
    assert res_status.json()["did"] == bob_mailroom.did

    # 5. Verify /quote endpoint
    # Alice requests a quote for a "contract-audit"
    res_quote = client.post("/quote", json={
        "spec": {
            "task_type": "contract-audit",
            "params": {"depth": "deep"}
        },
        "client_did": alice_mailroom.did
    })
    assert res_quote.status_code == 200
    quote_data = res_quote.json()
    assert quote_data["price_wei"] == 5000
    assert quote_data["sender_did"] == alice_mailroom.did
    assert quote_data["recipient_did"] == bob_mailroom.did

    # 6. Verify /execute endpoint
    # Open payment channel to ensure Bob accepts the voucher
    alice_mailroom.channel_manager.open_channel(
        sender_private_key=CLIENT_KEY,
        recipient_address=bob_mailroom.agent_address,
        amount_wei=10000
    )

    # Alice creates a payment voucher for 5000 Wei
    voucher = alice_mailroom.channel_manager.create_voucher(
        sender_private_key=CLIENT_KEY,
        recipient_address=bob_mailroom.agent_address,
        amount_wei=5000
    )

    # Alice signs the request envelope containing the audit task details
    envelope, _ = alice_mailroom.prepare_request(
        recipient_did=bob_mailroom.did,
        payload={"task": "contract-audit", "params": {"contract": "0x123"}}
    )

    # Post to execution route
    res_exec = client.post("/execute", json={
        "envelope": envelope,
        "quote": quote_data,
        "voucher": voucher.model_dump()
    })
    
    assert res_exec.status_code == 200
    exec_data = res_exec.json()
    assert exec_data["status"] == "success"
    assert exec_data["result"]["output"] == "Mock processed task 'contract-audit'"
