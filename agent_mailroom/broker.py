import time
import httpx
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from web3 import Web3

from .mailroom import AgentMailroom
from .auth import TaskSpec, AgentRequestEnvelope, AgentQuoteEnvelope
from .channel import PaymentVoucher
from .server import create_agent_app, ExecuteRequestPayload

class BrokerAgent:
    """
    BrokerAgent coordinates composite tasks across multiple specialized sub-agents.
    For example, it acts as a middleman that resolves Developer and Auditor endpoints,
    requests sub-task quotes, handles payments, opens state channels, signs payment vouchers,
    and consolidates outputs back to the client.
    
    This implements Phase 3.3 (Swarm Brokerage) of the AgentMailroom specification.
    """
    def __init__(
        self,
        w3: Web3,
        private_key: str,
        developer_did: str,
        auditor_did: str,
        registry_address: str,
        channel_address: str,
        brokerage_fee_wei: int = 10_000_000_000_000_000  # 0.01 ETH default fee
    ):
        self.w3 = w3
        self.mailroom = AgentMailroom(
            agent_private_key=private_key,
            w3=w3,
            registry_address=registry_address,
            channel_address=channel_address
        )
        self.developer_did = developer_did
        self.auditor_did = auditor_did
        self.brokerage_fee_wei = brokerage_fee_wei

        # Store sub-agent quotes and vouchers during the request flow
        # In a production environment, this would be persisted or session-scoped
        self.active_quotes: Dict[str, Dict[str, Any]] = {}

    def get_sub_agent_quote(self, agent_did: str, task_type: str, params: Dict[str, Any]) -> AgentQuoteEnvelope:
        """Resolves the sub-agent's endpoint and requests a quote for a sub-task."""
        profile = self.mailroom.registry.get_agent_profile(agent_did)
        if not profile.active:
            raise RuntimeError(f"Sub-agent {agent_did} is not active in the registry.")
        
        endpoint = profile.endpoint.rstrip("/")
        quote_url = f"{endpoint}/quote"

        print(f"[BROKER] Resolving quote for {agent_did} at endpoint: {quote_url}")
        
        payload = {
            "spec": {
                "task_type": task_type,
                "params": params
            },
            "client_did": self.mailroom.did
        }

        # We can use httpx to make a synchronous POST call to get the quote
        response = httpx.post(quote_url, json=payload, timeout=10.0)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to get quote from {agent_did}: {response.text}")
        
        return AgentQuoteEnvelope.model_validate(response.json())

    def calculate_composite_price(self, spec: TaskSpec) -> int:
        """
        Runs during the quote phase. Broker queries sub-agents for their pricing,
        calculates the total cost (Sub-Agents + Brokerage fee), and caches the quotes.
        """
        task_type = spec.task_type
        params = spec.params
        print(f"\n[BROKER] Calculating composite price for task '{task_type}'")

        if task_type == "refactor-and-audit":
            # 1. Query Developer quote for 'refactor' sub-task
            dev_quote = self.get_sub_agent_quote(self.developer_did, "refactor", params)
            # 2. Query Auditor quote for 'audit' sub-task
            auditor_quote = self.get_sub_agent_quote(self.auditor_did, "audit", params)

            total_price = dev_quote.price_wei + auditor_quote.price_wei + self.brokerage_fee_wei
            
            # Cache the quotes under a temp key or task description so we can reuse them during execution
            # In a real environment, we'd generate a composite quote ID and map it
            # For simplicity, we cache it in memory
            cache_key = f"{spec.task_type}-{int(time.time() // 30)}" # 30 second window cache
            self.active_quotes[cache_key] = {
                "dev_quote": dev_quote,
                "auditor_quote": auditor_quote
            }

            print(f"[BROKER] Composite Price Breakdown:")
            print(f"  Developer: {dev_quote.price_wei} Wei")
            print(f"  Auditor:   {auditor_quote.price_wei} Wei")
            print(f"  Fee:       {self.brokerage_fee_wei} Wei")
            print(f"  Total:     {total_price} Wei")
            
            return total_price
        else:
            raise ValueError(f"Unsupported composite task type: {task_type}")

    def execute_sub_task(self, agent_did: str, task_type: str, params: Dict[str, Any], quote: AgentQuoteEnvelope) -> Dict[str, Any]:
        """
        Executes a task on a sub-agent by opening/funding a payment channel (if necessary),
        creating a payment voucher, signing the EIP-712 envelope, and calling their endpoint.
        """
        profile = self.mailroom.registry.get_agent_profile(agent_did)
        recipient_address = Web3.to_checksum_address(agent_did.split(":")[-1])
        endpoint = profile.endpoint.rstrip("/")
        execute_url = f"{endpoint}/execute"

        # Check existing channel deposit on-chain
        deposit, expiry, challenged = self.mailroom.channel_manager.get_channel_info(
            sender=self.mailroom.agent_address,
            recipient=recipient_address
        )

        # Open/fund the payment channel if deposit is insufficient
        required_wei = quote.price_wei
        if deposit < required_wei:
            fund_amount = max(required_wei * 2, 50000000000000000) # fund with 2x requirement or 0.05 ETH minimum
            print(f"[BROKER] Funding channel to {agent_did} with {fund_amount} Wei...")
            tx_hash = self.mailroom.channel_manager.open_channel(
                sender_private_key=self.mailroom._private_key,
                recipient_address=recipient_address,
                amount_wei=fund_amount
            )
            print(f"[BROKER] Channel funded. Tx Hash: {tx_hash}")
            # wait a brief second for the local mock chain to register it (instant in sandbox)
            time.sleep(0.1)

        # Create off-chain payment voucher for the sub-agent
        voucher = self.mailroom.channel_manager.create_voucher(
            sender_private_key=self.mailroom._private_key,
            recipient_address=recipient_address,
            amount_wei=required_wei
        )

        # Build EIP-712 Request Envelope
        payload = {
            "task": task_type,
            "params": params
        }
        # In a real system, the broker tracks nonces for each sub-agent
        # For mock simplicity, we use timestamp-based nonce
        nonce = int(time.time() * 1000)
        envelope = self.mailroom.auth.sign_request(
            sender_private_key=self.mailroom._private_key,
            recipient_did=agent_did,
            payload=payload,
            nonce=nonce,
            chain_id=self.w3.eth.chain_id
        )

        # POST call to execute
        exec_payload = {
            "envelope": envelope.model_dump(),
            "quote": quote.model_dump(),
            "voucher": voucher.model_dump()
        }

        print(f"[BROKER] Executing sub-task '{task_type}' on {agent_did} at url: {execute_url}")
        response = httpx.post(execute_url, json=exec_payload, timeout=20.0)
        if response.status_code != 200:
            raise RuntimeError(f"Sub-task execution failed on {agent_did}: {response.text}")

        res_json = response.json()
        if res_json.get("status") != "success":
            raise RuntimeError(f"Sub-task returned unsuccessful status: {res_json}")

        return res_json.get("result", {})

    def execute_composite_task(self, task_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes the composite workflow step-by-step.
        """
        if task_type == "refactor-and-audit":
            # Retrieve cached quotes
            cache_key = f"{task_type}-{int(time.time() // 30)}"
            quotes = self.active_quotes.get(cache_key)
            if not quotes:
                # If cache missed, fetch fresh quotes on-the-fly
                print("[BROKER] Quote cache miss. Fetching fresh quotes...")
                dev_quote = self.get_sub_agent_quote(self.developer_did, "refactor", params)
                auditor_quote = self.get_sub_agent_quote(self.auditor_did, "audit", params)
            else:
                dev_quote = quotes["dev_quote"]
                auditor_quote = quotes["auditor_quote"]

            # Step 1: Execute Developer task
            print("\n[BROKER] Executing Developer sub-task...")
            dev_result = self.execute_sub_task(
                agent_did=self.developer_did,
                task_type="refactor",
                params=params,
                quote=dev_quote
            )
            refactored_code = dev_result.get("code", "")
            print(f"[BROKER] Developer output code: '{refactored_code}'")

            # Step 2: Execute Auditor task, passing the Developer output code in the parameters
            print("\n[BROKER] Executing Auditor sub-task...")
            audit_params = {
                "code": refactored_code,
                "rules": params.get("rules", [])
            }
            auditor_result = self.execute_sub_task(
                agent_did=self.auditor_did,
                task_type="audit",
                params=audit_params,
                quote=auditor_quote
            )
            print(f"[BROKER] Auditor output report: {auditor_result}")

            # Step 3: Combine outputs
            return {
                "refactored_code": refactored_code,
                "audit_report": auditor_result.get("report", "No audit report generated"),
                "status": "Verified & Audited"
            }
        else:
            raise ValueError(f"Unsupported composite task: {task_type}")

    def create_app(self) -> FastAPI:
        """Wraps the BrokerAgent instance in a FastAPI app."""
        return create_agent_app(
            mailroom=self.mailroom,
            task_handler=self.execute_composite_task,
            price_calculator=self.calculate_composite_price
        )
