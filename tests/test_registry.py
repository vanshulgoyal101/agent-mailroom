import pytest
from web3 import Web3
from eth_account import Account
from agent_mailroom.registry import (
    AgentRegistryClient,
    parse_agent_did,
    build_agent_did,
    AgentProfile
)

# Test key definitions
OWNER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
AGENT_ADDR = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"  # Checksummed address of test agent


def test_did_parsing():
    valid_did = f"did:agent:eth:{AGENT_ADDR}"
    assert parse_agent_did(valid_did) == AGENT_ADDR

    with pytest.raises(ValueError):
        parse_agent_did("did:agent:eth:invalid_address")

    with pytest.raises(ValueError):
        parse_agent_did(f"did:other:eth:{AGENT_ADDR}")


def test_did_building():
    did = build_agent_did(AGENT_ADDR)
    assert did == f"did:agent:eth:{AGENT_ADDR}"

    with pytest.raises(ValueError):
        build_agent_did("invalid_address")


def test_agent_registration_flow(w3):
    client = AgentRegistryClient(w3)
    # Generate a fresh account to prevent stake collisions from other tests
    test_account = Account.create()
    agent_addr = test_account.address
    agent_did = build_agent_did(agent_addr)

    # 0. Stake reputation first (0.1 ETH = 100000000000000000 Wei)
    stake_tx = client.stake_reputation(
        owner_private_key=OWNER_KEY,
        agent_address=agent_addr,
        amount_wei=100000000000000000
    )
    assert stake_tx.startswith("0x")
    assert client.get_stake(agent_addr) == 100000000000000000

    # 1. Register agent
    tx_hash = client.register_agent(
        owner_private_key=OWNER_KEY,
        agent_address=agent_addr,
        endpoint="http://127.0.0.1:8000",
        capabilities=["text-to-speech", "nlp"],
        rate_wei=1000
    )
    assert tx_hash.startswith("0x")

    # 2. Get profile and verify
    profile = client.get_agent_profile(agent_did)
    assert isinstance(profile, AgentProfile)
    assert profile.active is True
    assert profile.endpoint == "http://127.0.0.1:8000"
    assert "nlp" in profile.model_capabilities
    assert profile.rate_per_task_wei == 1000

    # 3. Update profile
    tx_hash_update = client.update_agent_profile(
        owner_private_key=OWNER_KEY,
        agent_address=agent_addr,
        endpoint="http://127.0.0.1:9000",
        capabilities=["image-gen"],
        rate_wei=5000
    )
    assert tx_hash_update.startswith("0x")

    # Verify update
    updated_profile = client.get_agent_profile(agent_did)
    assert updated_profile.endpoint == "http://127.0.0.1:9000"
    assert updated_profile.model_capabilities == ["image-gen"]
    assert updated_profile.rate_per_task_wei == 5000

    # 4. Deregister agent
    tx_hash_dereg = client.deregister_agent(
        owner_private_key=OWNER_KEY,
        agent_address=agent_addr
    )
    assert tx_hash_dereg.startswith("0x")

    # Verify deactivated status
    inactive_profile = client.get_agent_profile(agent_did)
    assert inactive_profile.active is False

    # 5. Unstake reputation
    tx_hash_unstake = client.unstake_reputation(
        owner_private_key=OWNER_KEY,
        agent_address=agent_addr
    )
    assert tx_hash_unstake.startswith("0x")
    assert client.get_stake(agent_addr) == 0
