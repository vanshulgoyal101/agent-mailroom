import time
import pytest
from eth_account import Account
from agent_mailroom.auth import (
    sign_quote,
    verify_quote,
    AgentQuoteEnvelope,
    build_quote_eip712_struct
)
from agent_mailroom.registry import build_agent_did

# Test configurations
CLIENT_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
PROVIDER_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"

client_address = Account.from_key(CLIENT_KEY).address
provider_address = Account.from_key(PROVIDER_KEY).address

CLIENT_DID = build_agent_did(client_address)
PROVIDER_DID = build_agent_did(provider_address)


def test_quote_signing_and_verification():
    """Verifies that a provider can sign a quote and a client can verify it."""
    price = 10 ** 15  # 0.001 ETH
    expiry = int(time.time()) + 60
    quote_id = "test-quote-1"

    # Sign quote
    quote_env = sign_quote(
        sender_did=CLIENT_DID,
        price_wei=price,
        expiry=expiry,
        quote_id=quote_id,
        provider_private_key=PROVIDER_KEY
    )

    assert isinstance(quote_env, AgentQuoteEnvelope)
    assert quote_env.price_wei == price
    assert quote_env.quote_id == quote_id

    # Verify quote
    success = verify_quote(
        envelope=quote_env,
        expected_sender_did=CLIENT_DID,
        expected_recipient_did=PROVIDER_DID
    )
    assert success is True


def test_quote_verification_failures():
    """Verifies quote verification failures for invalid DIDs and expiration."""
    price = 2 * 10 ** 15
    expiry = int(time.time()) + 60
    quote_id = "test-quote-2"

    quote_env = sign_quote(
        sender_did=CLIENT_DID,
        price_wei=price,
        expiry=expiry,
        quote_id=quote_id,
        provider_private_key=PROVIDER_KEY
    )

    # 1. Invalid Client DID expectation
    with pytest.raises(ValueError, match="Quote client DID mismatch"):
        verify_quote(
            envelope=quote_env,
            expected_sender_did="did:agent:eth:0x0000000000000000000000000000000000000000",
            expected_recipient_did=PROVIDER_DID
        )

    # 2. Invalid Provider DID expectation
    with pytest.raises(ValueError, match="Quote provider DID mismatch"):
        verify_quote(
            envelope=quote_env,
            expected_sender_did=CLIENT_DID,
            expected_recipient_did="did:agent:eth:0x0000000000000000000000000000000000000000"
        )

    # 3. Expired Quote
    quote_env_expired = sign_quote(
        sender_did=CLIENT_DID,
        price_wei=price,
        expiry=int(time.time()) - 10,  # 10s in the past
        quote_id=quote_id,
        provider_private_key=PROVIDER_KEY
    )
    with pytest.raises(ValueError, match="expired"):
        verify_quote(
            envelope=quote_env_expired,
            expected_sender_did=CLIENT_DID,
            expected_recipient_did=PROVIDER_DID
        )
