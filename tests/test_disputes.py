import pytest
import time
from web3 import Web3
from eth_account import Account
from agent_mailroom.registry import AgentRegistryClient, build_agent_did
from agent_mailroom.channel import PaymentChannelManager

ALICE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
BOB_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

ALICE_ADDR = Account.from_key(ALICE_KEY).address
BOB_ADDR = Account.from_key(BOB_KEY).address

def test_reputation_staking_limit(w3):
    registry = AgentRegistryClient(w3)
    agent_addr = BOB_ADDR
    agent_did = build_agent_did(agent_addr)

    # 1. Assert initial stake
    initial_stake = registry.get_stake(agent_addr)

    # 2. Registering without stake should fail (receipt status == 0)
    tx_fail = registry.register_agent(
        owner_private_key=BOB_KEY,
        agent_address=agent_addr,
        endpoint="http://127.0.0.1:8002",
        capabilities=["testing"],
        rate_wei=1000
    )
    receipt_fail = w3.eth.wait_for_transaction_receipt(tx_fail)
    assert receipt_fail.status == 0

    # 3. Stake 0.1 ETH (minimum required)
    registry.stake_reputation(
        owner_private_key=BOB_KEY,
        agent_address=agent_addr,
        amount_wei=100000000000000000 # 0.1 ETH
    )
    assert registry.get_stake(agent_addr) == initial_stake + 100000000000000000

    # 4. Now registration succeeds
    tx_ok = registry.register_agent(
        owner_private_key=BOB_KEY,
        agent_address=agent_addr,
        endpoint="http://127.0.0.1:8002",
        capabilities=["testing"],
        rate_wei=1000
    )
    receipt_ok = w3.eth.wait_for_transaction_receipt(tx_ok)
    assert receipt_ok.status == 1
    assert registry.get_agent_profile(agent_did).active is True


def test_dispute_initiate_and_resolve(w3):
    registry = AgentRegistryClient(w3)
    channel = PaymentChannelManager(w3)

    # Ensure BOB is registered
    if not registry.get_agent_profile(build_agent_did(BOB_ADDR)).active:
        registry.stake_reputation(BOB_KEY, BOB_ADDR, 100000000000000000)
        registry.register_agent(BOB_KEY, BOB_ADDR, "http://bob:8002", ["test"], 1000)

    # 1. Open payment channel
    channel.open_channel(
        sender_private_key=ALICE_KEY,
        recipient_address=BOB_ADDR,
        amount_wei=100000000000000000 # 0.1 ETH
    )

    # 2. Define task hash
    task_hash = w3.solidity_keccak(["string"], ["some-task-details"])

    # 3. Alice initiates dispute
    tx_dispute = channel.initiate_dispute(
        sender_private_key=ALICE_KEY,
        recipient_address=BOB_ADDR,
        task_hash=task_hash
    )
    assert tx_dispute.startswith("0x")
    receipt_dispute = w3.eth.wait_for_transaction_receipt(tx_dispute)
    assert receipt_dispute.status == 1

    # Verify dispute is active
    hash_res, expiry, active = channel.get_dispute_info(ALICE_ADDR, BOB_ADDR)
    assert active is True
    assert hash_res == task_hash

    # Ensure redeeming voucher is locked while dispute is active (receipt status == 0)
    voucher = channel.create_voucher(ALICE_KEY, BOB_ADDR, 5000)
    tx_redeem_fail = channel.redeem_voucher_on_chain(BOB_KEY, ALICE_ADDR, voucher)
    receipt_redeem_fail = w3.eth.wait_for_transaction_receipt(tx_redeem_fail)
    assert receipt_redeem_fail.status == 0

    # 4. Alice signs a dispute resolution message
    msg_hash = w3.solidity_keccak(
        ["address", "address", "address", "bytes32", "string"],
        [channel.contract_address, ALICE_ADDR, BOB_ADDR, task_hash, "RESOLVED"]
    )
    from eth_account.messages import encode_defunct
    signable_msg = encode_defunct(hexstr=msg_hash.hex())
    signed_res = Account.sign_message(signable_msg, private_key=ALICE_KEY)
    
    # Bob resolves dispute
    tx_resolve = channel.resolve_dispute(
        recipient_private_key=BOB_KEY,
        sender_address=ALICE_ADDR,
        task_hash=task_hash,
        signature=signed_res.signature.hex()
    )
    assert tx_resolve.startswith("0x")
    receipt_resolve = w3.eth.wait_for_transaction_receipt(tx_resolve)
    assert receipt_resolve.status == 1

    # Verify dispute is inactive
    _, _, active = channel.get_dispute_info(ALICE_ADDR, BOB_ADDR)
    assert active is False


def test_dispute_slash_reputation(w3):
    registry = AgentRegistryClient(w3)
    channel = PaymentChannelManager(w3)

    # Reset BOB state in mock registry if registered
    try:
        registry.deregister_agent(BOB_KEY, BOB_ADDR)
        registry.unstake_reputation(BOB_KEY, BOB_ADDR)
    except Exception:
        pass

    registry.stake_reputation(BOB_KEY, BOB_ADDR, 200000000000000000) # 0.2 ETH
    registry.register_agent(BOB_KEY, BOB_ADDR, "http://bob:8002", ["test"], 1000)

    # Register payment channel contract address in registry
    registry.set_payment_channel(ALICE_KEY, channel.contract_address) # ALICE is admin in sandbox

    # 1. Open payment channel
    channel.open_channel(
        sender_private_key=ALICE_KEY,
        recipient_address=BOB_ADDR,
        amount_wei=100000000000000000 # 0.1 ETH
    )

    # 2. Initiate dispute
    task_hash = w3.solidity_keccak(["string"], ["malicious-work"])
    channel.initiate_dispute(ALICE_KEY, BOB_ADDR, task_hash)

    # 3. Try to slash before expiry (receipt status == 0)
    tx_slash_fail = channel.claim_dispute_slash(ALICE_KEY, BOB_ADDR)
    receipt_slash_fail = w3.eth.wait_for_transaction_receipt(tx_slash_fail)
    assert receipt_slash_fail.status == 0

    # 4. Fast forward time using our custom RPC method
    w3.provider.make_request("evm_increaseTime", [3600]) # 1 hour
    w3.provider.make_request("evm_mine", [])

    # 5. Slash!
    initial_bob_stake = registry.get_stake(BOB_ADDR)
    tx_slash = channel.claim_dispute_slash(ALICE_KEY, BOB_ADDR)
    assert tx_slash.startswith("0x")
    receipt_slash = w3.eth.wait_for_transaction_receipt(tx_slash)
    assert receipt_slash.status == 1

    # Assert Bob's stake is slashed by 0.05 ETH (50000000000000000 Wei)
    final_bob_stake = registry.get_stake(BOB_ADDR)
    assert final_bob_stake == initial_bob_stake - 50000000000000000
