import time
import pytest
from eth_account import Account
from agent_mailroom.auth import AgentAuth, AgentRequestEnvelope
from agent_mailroom.registry import build_agent_did

# Test key configurations
SENDER_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
RECIPIENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

sender_addr = Account.from_key(SENDER_KEY).address
recipient_addr = Account.from_key(RECIPIENT_KEY).address

SENDER_DID = build_agent_did(sender_addr)
RECIPIENT_DID = build_agent_did(recipient_addr)


def test_request_signature_and_verification():
    auth = AgentAuth()
    payload = {"query": "Analyze Pool", "params": [1, 2, 3]}

    # Sign request
    envelope = auth.sign_request(
        sender_private_key=SENDER_KEY,
        recipient_did=RECIPIENT_DID,
        payload=payload,
        nonce=100
    )

    assert isinstance(envelope, AgentRequestEnvelope)
    assert envelope.sender_did == SENDER_DID
    assert envelope.recipient_did == RECIPIENT_DID

    # Verify request
    recovered_sender = auth.verify_request(
        envelope=envelope,
        expected_recipient_did=RECIPIENT_DID
    )
    assert recovered_sender == sender_addr


def test_verification_failures():
    auth = AgentAuth()
    payload = {"hello": "world"}

    # Case 1: Recipient DID mismatch
    envelope = auth.sign_request(
        sender_private_key=SENDER_KEY,
        recipient_did=RECIPIENT_DID,
        payload=payload,
        nonce=200
    )
    
    with pytest.raises(ValueError, match="Recipient DID mismatch"):
        auth.verify_request(envelope, expected_recipient_did="did:agent:eth:0x0000000000000000000000000000000000000000")

    # Case 2: Replay attack (reusing the same nonce)
    # The first time must pass:
    auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID)
    
    # The second time must trigger a replay protection error:
    with pytest.raises(ValueError, match="Replay attack detected"):
        auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID)


def test_timestamp_drift():
    # Enforce a strict 5-second drift limit for testing
    auth = AgentAuth(max_timestamp_drift_sec=5)
    payload = {"test": "drift"}

    envelope = auth.sign_request(
        sender_private_key=SENDER_KEY,
        recipient_did=RECIPIENT_DID,
        payload=payload,
        nonce=300
    )

    # Artificially modify the envelope timestamp to simulate drift
    envelope.timestamp = int(time.time()) - 10  # 10 seconds in the past

    with pytest.raises(ValueError, match="Request timestamp drift too high"):
        auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID)


def test_tampered_payload_fails_verification():
    """A payload mutated after signing must fail signature verification."""
    auth = AgentAuth()
    envelope = auth.sign_request(
        sender_private_key=SENDER_KEY,
        recipient_did=RECIPIENT_DID,
        payload={"amount": 1},
        nonce=400,
    )

    # Tamper with the payload without re-signing.
    envelope.payload = {"amount": 1_000_000}

    with pytest.raises(ValueError, match="Signature verification failed"):
        auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID)


def test_tampered_signature_fails_recovery():
    """A corrupted signature must fail signer recovery."""
    auth = AgentAuth()
    envelope = auth.sign_request(
        sender_private_key=SENDER_KEY,
        recipient_did=RECIPIENT_DID,
        payload={"x": 1},
        nonce=401,
    )

    # Flip a byte in the signature hex.
    sig = envelope.signature
    prefix = sig[:2] if sig.startswith("0x") else ""
    body = sig[len(prefix):]
    flipped = ("f" if body[0] != "f" else "0") + body[1:]
    envelope.signature = prefix + flipped

    with pytest.raises(ValueError):
        auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID)


def test_distinct_nonces_do_not_collide():
    """Different nonces from the same sender should both verify successfully."""
    auth = AgentAuth()
    for nonce in (500, 501, 502):
        envelope = auth.sign_request(
            sender_private_key=SENDER_KEY,
            recipient_did=RECIPIENT_DID,
            payload={"n": nonce},
            nonce=nonce,
        )
        assert auth.verify_request(envelope, expected_recipient_did=RECIPIENT_DID) == sender_addr

