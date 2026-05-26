import time
import uuid
from typing import Dict, Any, Callable, Optional
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel, Field
from web3 import Web3
from .mailroom import AgentMailroom
from .auth import (
    TaskSpec,
    AgentQuoteEnvelope,
    AgentRequestEnvelope,
    sign_quote,
    verify_quote
)
from .channel import PaymentVoucher

class ExecuteRequestPayload(BaseModel):
    """Payload representing a request to execute a paid/secure agent task."""
    envelope: Dict[str, Any] = Field(..., description="Serialized AgentRequestEnvelope containing signed task.")
    quote: Optional[Dict[str, Any]] = Field(None, description="Optional serialized AgentQuoteEnvelope if task is paid.")
    voucher: Optional[Dict[str, Any]] = Field(None, description="Optional serialized PaymentVoucher matching quote price.")


def create_agent_app(
    mailroom: AgentMailroom, 
    task_handler: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    price_calculator: Optional[Callable[[TaskSpec], int]] = None
) -> FastAPI:
    """
    Creates a FastAPI web server application representing the Agent's endpoint.
    
    Args:
        mailroom: The AgentMailroom instance coordinating Web3 keys and validation.
        task_handler: A function mapping (task_type, params) -> response_dict.
        price_calculator: Optional function mapping TaskSpec -> price in Wei.
                          If None, defaults to reading the rate card from the on-chain registry profile.
                          
    Returns:
        FastAPI: The configured web application.
    """
    app = FastAPI(
        title=f"Agent Mailroom Node ({mailroom.did})",
        description="FastAPI machine-to-machine endpoint implementing cryptographically validated handshakes and payment channels."
    )

    # In-memory dictionary to hold verified payment vouchers for later on-chain redemption
    # sender_address -> latest_verified_voucher
    app.state.verified_vouchers = {}

    @app.get("/")
    def status():
        """Returns the status and identity DID details of the running agent."""
        return {
            "status": "active",
            "did": mailroom.did,
            "address": mailroom.agent_address,
            "chain_id": mailroom.w3.eth.chain_id
        }

    @app.post("/quote", response_model=AgentQuoteEnvelope)
    def request_quote(spec: TaskSpec, client_did: str = Body(..., embed=True)):
        """
        Receives task details from a client agent and returns a cryptographically signed price quote.
        
        Newbies Note: 
            This represents the Request-for-Quote (RFQ) stage. The service provider evaluates 
            the computational parameters and signs a quote. Alice can't modify this quote because 
            it is cryptographically bound to Bob's signature.
        """
        print(f"\n[SERVER] Received /quote request from {client_did} for task type '{spec.task_type}'")
        
        # Calculate dynamic price
        if price_calculator:
            price_wei = price_calculator(spec)
        else:
            # Fallback: Query the agent's rate from the on-chain registry profile
            try:
                profile = mailroom.registry.get_agent_profile(mailroom.did)
                price_wei = profile.rate_per_task_wei
            except Exception:
                price_wei = 0

        # Generate quote parameters
        quote_id = str(uuid.uuid4())
        expiry_seconds = 300  # Quote valid for 5 minutes
        expiry_timestamp = int(time.time()) + expiry_seconds

        print(f"[SERVER] Quoting price: {price_wei} Wei ({Web3.from_wei(price_wei, 'ether')} ETH). Expiry in 5m.")

        # Sign the quote envelope using the service provider's private key
        try:
            quote_envelope = sign_quote(
                sender_did=client_did,
                price_wei=price_wei,
                expiry=expiry_timestamp,
                quote_id=quote_id,
                provider_private_key=mailroom._private_key,
                chain_id=mailroom.w3.eth.chain_id
            )
            return quote_envelope
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to sign quote: {str(e)}")

    @app.post("/execute")
    def execute_task(payload: ExecuteRequestPayload):
        """
        Validates the request envelope, the dynamic quote, and the state-channel payment voucher.
        If all cryptographic validation checks pass, runs the task and records the payment.
        """
        print(f"\n[SERVER] Received /execute request.")
        
        # Step 1: Parse and cryptographically verify the incoming EIP-712 Request Envelope.
        # This checks the client's signature, resolves the client's DID, and checks for replays.
        try:
            sender_did, task_payload, _ = mailroom.process_incoming_request(
                envelope_data=payload.envelope,
                voucher_data=None  # We will validate the voucher manually below to cross-check quotes
            )
            sender_address = Web3.to_checksum_address(sender_did.split(":")[-1])
        except Exception as e:
            print(f"  [ERROR] Cryptographic handshake failed: {str(e)}")
            raise HTTPException(status_code=401, detail=f"Handshake failed: {str(e)}")

        print(f"  [SUCCESS] Client authenticated! DID: {sender_did}")

        # Step 2: Handle Quote & Payment Verification
        # If a quote was supplied, or if the registry requires payment, enforce validations.
        if payload.quote:
            try:
                quote_env = AgentQuoteEnvelope.model_validate(payload.quote)
                
                # Verify that the quote was signed by this server (recipient of the task)
                verify_quote(
                    envelope=quote_env,
                    expected_sender_did=sender_did,
                    expected_recipient_did=mailroom.did,
                    chain_id=mailroom.w3.eth.chain_id
                )
                print(f"  [SUCCESS] Valid price quote verified (Price: {quote_env.price_wei} Wei)")
                
                # Verify the payment voucher is present
                if not payload.voucher:
                    raise ValueError("Payment voucher is missing for the quoted price.")
                
                voucher = PaymentVoucher.model_validate(payload.voucher)
                
                # Ensure the voucher is signed by the client, is addressed to this agent, and matches the contract
                mailroom.channel_manager.verify_voucher(voucher)
                
                # Check that the voucher amount covers the quoted task fee
                if voucher.amount_wei < quote_env.price_wei:
                    raise ValueError(
                        f"Insufficient payment voucher. Quote price is {quote_env.price_wei} Wei, "
                        f"but voucher only covers {voucher.amount_wei} Wei."
                    )
                
                # Store the verified voucher for settlement (overwriting any previous lower-value vouchers)
                app.state.verified_vouchers[sender_address.lower()] = voucher
                print(f"  [SUCCESS] State channel payment voucher verified and logged!")

            except Exception as e:
                print(f"  [ERROR] Payment verification failed: {str(e)}")
                raise HTTPException(status_code=402, detail=f"Payment validation failed: {str(e)}")

        # Step 3: Run the task handler
        task_type = task_payload.get("task")
        task_params = task_payload.get("params", {})
        
        if not task_type:
            raise HTTPException(status_code=400, detail="Missing 'task' identifier in payload.")

        try:
            print(f"[SERVER] Executing task '{task_type}'...")
            result = task_handler(task_type, task_params)
            print(f"[SERVER] Task completed successfully.")
            return {
                "status": "success",
                "result": result
            }
        except Exception as e:
            print(f"  [ERROR] Task execution failed: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Task execution error: {str(e)}")

    return app
