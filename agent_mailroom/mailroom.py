import json
from typing import Dict, Any, Tuple, Optional
import httpx
from web3 import Web3
from eth_account import Account
from .registry import AgentRegistryClient, AgentProfile, build_agent_did
from .auth import AgentAuth, AgentRequestEnvelope, TaskSpec, AgentQuoteEnvelope
from .channel import PaymentChannelManager, PaymentVoucher


class AgentMailroom:
    """
    Unified manager representing an Agent's mailroom endpoint node.
    Coorders DID identity, EIP-712 request verification, and payment vouchers.
    """

    def __init__(
        self,
        agent_private_key: str,
        w3: Web3,
        registry_address: Optional[str] = None,
        channel_address: Optional[str] = None
    ):
        self.w3 = w3
        self.agent_account = Account.from_key(agent_private_key)
        self.agent_address = self.agent_account.address
        self.did = build_agent_did(self.agent_address)
        self._private_key = agent_private_key

        # Initialize clients
        kwargs_reg = {"contract_address": registry_address} if registry_address else {}
        kwargs_chan = {"contract_address": channel_address} if channel_address else {}
        self.registry = AgentRegistryClient(w3, **kwargs_reg)
        self.channel_manager = PaymentChannelManager(w3, **kwargs_chan)
        self.auth = AgentAuth()

        # Counter for outgoing request nonces
        self._next_nonce = 0

    def register_on_chain(
        self, 
        owner_private_key: str, 
        endpoint: str, 
        capabilities: list[str], 
        rate_wei: int
    ) -> str:
        """
        Helper method to register this agent's DID on-chain using the owner's private key.
        Automatically stakes reputation if current stake is below 0.1 ETH.
        """
        current_stake = self.registry.get_stake(self.agent_address)
        min_stake = 100000000000000000 # 0.1 ETH
        if current_stake < min_stake:
            stake_needed = min_stake - current_stake
            self.registry.stake_reputation(
                owner_private_key=owner_private_key,
                agent_address=self.agent_address,
                amount_wei=stake_needed
            )
        return self.registry.register_agent(
            owner_private_key=owner_private_key,
            agent_address=self.agent_address,
            endpoint=endpoint,
            capabilities=capabilities,
            rate_wei=rate_wei
        )

    def prepare_request(
        self, 
        recipient_did: str, 
        payload: Dict[str, Any], 
        attach_voucher_amount_wei: int = 0
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Prepares a cryptographically signed request envelope and an optional payment voucher.
        
        Args:
            recipient_did: The DID of the target agent.
            payload: The request inputs (JSON serializable).
            attach_voucher_amount_wei: Cumulative payment to authorize (0 if free).
            
        Returns:
            Tuple[Dict[str, Any], Optional[Dict[str, Any]]]: 
                (request_envelope_dict, payment_voucher_dict)
        """
        # Sign the envelope request
        envelope = self.auth.sign_request(
            sender_private_key=self._private_key,
            recipient_did=recipient_did,
            payload=payload,
            nonce=self._next_nonce,
            chain_id=self.w3.eth.chain_id
        )
        self._next_nonce += 1

        voucher_dict = None
        if attach_voucher_amount_wei > 0:
            recipient_address = recipient_did.split(":")[-1]
            voucher = self.channel_manager.create_voucher(
                sender_private_key=self._private_key,
                recipient_address=recipient_address,
                amount_wei=attach_voucher_amount_wei
            )
            voucher_dict = voucher.model_dump()

        return envelope.model_dump(), voucher_dict

    def process_incoming_request(
        self, 
        envelope_data: Dict[str, Any], 
        voucher_data: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, Dict[str, Any], Optional[PaymentVoucher]]:
        """
        Validates an incoming request and optional payment voucher.
        
        Steps:
        1. Decrypts and parses the request envelope.
        2. Validates sender DID and signature (anti-replay + time drift).
        3. Queries the on-chain registry to ensure the sender is registered and active.
        4. Validates the payment voucher signature and contract binding if attached.

        Args:
            envelope_data: Serialized AgentRequestEnvelope dictionary.
            voucher_data: Serialized PaymentVoucher dictionary.

        Returns:
            Tuple[str, Dict[str, Any], Optional[PaymentVoucher]]:
                (verified_sender_did, payload, verified_voucher)
        """
        # Parse Pydantic envelope
        envelope = AgentRequestEnvelope.model_validate(envelope_data)

        # 1. Cryptographic authentication (signature & replay checks)
        sender_address = self.auth.verify_request(
            envelope=envelope,
            expected_recipient_did=self.did,
            chain_id=self.w3.eth.chain_id
        )

        # 2. On-chain validation: Verify sender is registered and active in the DID registry
        sender_profile = self.registry.get_agent_profile(envelope.sender_did)
        if not sender_profile.active:
            raise ValueError(f"Sender DID {envelope.sender_did} is registered but marked INACTIVE.")

        # 3. Optional Voucher validation
        voucher = None
        if voucher_data:
            voucher = PaymentVoucher.model_validate(voucher_data)
            # Ensure the voucher sender matches the request sender
            if voucher.sender_address.lower() != sender_address.lower():
                raise ValueError(
                    f"Payment voucher sender {voucher.sender_address} "
                    f"does not match request sender {sender_address}."
                )
            
            # Ensure voucher recipient matches self
            if voucher.recipient_address.lower() != self.agent_address.lower():
                raise ValueError(
                    f"Payment voucher recipient {voucher.recipient_address} "
                    f"does not match self {self.agent_address}."
                )

            # Cryptographically verify the voucher signature
            self.channel_manager.verify_voucher(voucher)

        return envelope.sender_did, envelope.payload, voucher

    def request_quote_http(self, recipient_endpoint: str, task_spec: TaskSpec) -> AgentQuoteEnvelope:
        """
        Sends an HTTP POST request to another agent's endpoint to request a price quote.
        
        Args:
            recipient_endpoint: The base URL of the service agent (e.g. 'http://127.0.0.1:8002').
            task_spec: The specification of the task to be quoted.
            
        Returns:
            AgentQuoteEnvelope: The signed price quote returned by the provider.
        """
        url = f"{recipient_endpoint.rstrip('/')}/quote"
        payload = {
            "spec": task_spec.model_dump(),
            "client_did": self.did
        }
        
        response = httpx.post(url, json=payload, timeout=10.0)
        if response.status_code != 200:
            raise RuntimeError(f"Quote request failed with status {response.status_code}: {response.text}")
            
        return AgentQuoteEnvelope.model_validate(response.json())

    def send_request_http(
        self,
        recipient_did: str,
        recipient_endpoint: str,
        task_payload: Dict[str, Any],
        payment_required: bool = True
    ) -> Dict[str, Any]:
        """
        Orchestrates the dynamic RFQ and execution handshake over HTTP.
        
        Steps:
        1. If payment is required, sends a request for quote to recipient's server.
        2. Inspects local channel deposits. If insufficient to cover the quote, funds channel.
        3. Generates a signed off-chain voucher matching the quote.
        4. Prepares the signed task envelope.
        5. Posts the request, quote, and voucher to the execute endpoint.
        
        Args:
            recipient_did: The DID of the service agent.
            recipient_endpoint: The HTTP address of the service agent.
            task_payload: Details of the task (e.g. {"task": "audit", "params": {...}}).
            payment_required: Whether to perform the RFQ payment loop.
            
        Returns:
            Dict[str, Any]: The execution result returned by the service agent.
        """
        recipient_address = recipient_did.split(":")[-1]
        
        quote_envelope = None
        voucher_envelope = None

        if payment_required:
            # 1. RFQ Stage: Request a dynamic price quote
            task_spec = TaskSpec(
                task_type=task_payload["task"],
                params=task_payload.get("params", {})
            )
            quote_envelope = self.request_quote_http(recipient_endpoint, task_spec)
            
            # 2. Deposit check: Ensure channel is funded for the quote
            if quote_envelope.price_wei > 0:
                deposit, _, _ = self.channel_manager.get_channel_info(
                    sender=self.agent_address,
                    recipient=recipient_address
                )
                
                if deposit < quote_envelope.price_wei:
                    # Fund the channel with a safety buffer (max of 0.05 ETH and the required fee)
                    fund_amount = max(self.w3.to_wei(0.05, "ether"), quote_envelope.price_wei)
                    print(f"[SDK] Insufficient channel deposit ({Web3.from_wei(deposit, 'ether')} ETH). "
                          f"Funding channel with {Web3.from_wei(fund_amount, 'ether')} ETH...")
                    self.channel_manager.open_channel(
                        sender_private_key=self._private_key,
                        recipient_address=recipient_address,
                        amount_wei=fund_amount
                    )

                # 3. Create the payment voucher for Bob
                voucher = self.channel_manager.create_voucher(
                    sender_private_key=self._private_key,
                    recipient_address=recipient_address,
                    amount_wei=quote_envelope.price_wei
                )
                voucher_envelope = voucher.model_dump()

        # 4. Sign the execution envelope (without attaching voucher directly, as it's sent in HTTP payload)
        envelope_data, _ = self.prepare_request(
            recipient_did=recipient_did,
            payload=task_payload,
            attach_voucher_amount_wei=0
        )

        # 5. Send POST request to Bob's execute endpoint
        url = f"{recipient_endpoint.rstrip('/')}/execute"
        execute_payload = {
            "envelope": envelope_data,
            "quote": quote_envelope.model_dump() if quote_envelope else None,
            "voucher": voucher_envelope
        }

        response = httpx.post(url, json=execute_payload, timeout=10.0)
        if response.status_code != 200:
            raise RuntimeError(f"Task execution failed with status {response.status_code}: {response.text}")

        return response.json()
