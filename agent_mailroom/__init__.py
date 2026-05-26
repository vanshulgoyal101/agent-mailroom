"""
AgentMailroom: Cryptographic M2M Identity & Micro-payment Layer for AI Agents.

A protocol for decentralized agent communication, secure request signing, and
off-chain state channel payments.
"""

from .registry import (
    AgentProfile,
    AgentRegistryClient,
    parse_agent_did,
    build_agent_did,
    DEFAULT_REGISTRY_ADDRESS,
)
from .auth import (
    AgentRequestEnvelope,
    AgentAuth,
    build_request_eip712_struct,
    TaskSpec,
    AgentQuoteEnvelope,
    sign_quote,
    verify_quote,
)
from .channel import (
    PaymentVoucher,
    PaymentChannelManager,
    DEFAULT_CHANNEL_ADDRESS,
)
from .mailroom import AgentMailroom
from .server import create_agent_app
from .broker import BrokerAgent

__all__ = [
    "AgentProfile",
    "AgentRegistryClient",
    "parse_agent_did",
    "build_agent_did",
    "DEFAULT_REGISTRY_ADDRESS",
    "AgentRequestEnvelope",
    "AgentAuth",
    "build_request_eip712_struct",
    "TaskSpec",
    "AgentQuoteEnvelope",
    "sign_quote",
    "verify_quote",
    "PaymentVoucher",
    "PaymentChannelManager",
    "DEFAULT_CHANNEL_ADDRESS",
    "AgentMailroom",
    "create_agent_app",
    "BrokerAgent",
]
