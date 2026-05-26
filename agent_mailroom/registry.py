import re
from typing import List, Tuple, Dict, Any
from pydantic import BaseModel, Field, field_validator
from web3 import Web3
from web3.contract import Contract

# ABI for the AgentRegistry Solidity Smart Contract
REGISTRY_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "agent", "type": "address"},
            {"internalType": "string", "name": "endpoint", "type": "string"},
            {"internalType": "string", "name": "modelCapabilities", "type": "string"},
            {"internalType": "uint256", "name": "ratePerTaskWei", "type": "uint256"}
        ],
        "name": "registerAgent",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "agent", "type": "address"},
            {"internalType": "string", "name": "endpoint", "type": "string"},
            {"internalType": "string", "name": "modelCapabilities", "type": "string"},
            {"internalType": "uint256", "name": "ratePerTaskWei", "type": "uint256"}
        ],
        "name": "updateAgentProfile",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "agent", "type": "address"}
        ],
        "name": "deregisterAgent",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "agent", "type": "address"}
        ],
        "name": "getAgent",
        "outputs": [
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "string", "name": "endpoint", "type": "string"},
            {"internalType": "string", "name": "modelCapabilities", "type": "string"},
            {"internalType": "uint256", "name": "ratePerTaskWei", "type": "uint256"},
            {"internalType": "bool", "name": "active", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# Standard contract deployment address for mock sandbox registry
DEFAULT_REGISTRY_ADDRESS = "0x0000000000000000000000000000000000000100"


class AgentProfile(BaseModel):
    """Structured Agent Profile metadata loaded from the on-chain registry."""
    owner: str = Field(..., description="EVM address of the human/DAO controller.")
    endpoint: str = Field(..., description="The HTTP URL endpoint where the agent processes requests.")
    model_capabilities: List[str] = Field(..., description="List of supported AI models/skills.")
    rate_per_task_wei: int = Field(..., description="Service pricing per task execution in Wei.")
    active: bool = Field(..., description="Whether the agent is currently active.")

    @field_validator("owner")
    @classmethod
    def validate_owner(cls, v: str) -> str:
        if not Web3.is_address(v):
            raise ValueError(f"Invalid Ethereum address: {v}")
        return Web3.to_checksum_address(v)


def parse_agent_did(did: str) -> str:
    """
    Parses a Decentralized Identifier (DID) and extracts the Ethereum address.
    
    Args:
        did: The DID string e.g., 'did:agent:eth:0x742d35Cc6634C0532925a3b844Bc454e4438f44e'
        
    Returns:
        str: The checksummed Ethereum address.
        
    Raises:
        ValueError: If the DID format or address is invalid.
    """
    match = re.match(r"^did:agent:eth:(0x[a-fA-F0-9]{40})$", did)
    if not match:
        raise ValueError(f"Invalid Agent DID format: '{did}'. Must be 'did:agent:eth:<ethereum_address>'.")
    address = match.group(1)
    return Web3.to_checksum_address(address)


def build_agent_did(address: str) -> str:
    """Formats an Ethereum address into a standard Agent DID string."""
    if not Web3.is_address(address):
        raise ValueError(f"Invalid address for DID generation: {address}")
    return f"did:agent:eth:{Web3.to_checksum_address(address)}"


class AgentRegistryClient:
    """Client SDK for interacting with the AgentRegistry smart contract."""

    def __init__(self, w3: Web3, contract_address: str = DEFAULT_REGISTRY_ADDRESS):
        self.w3 = w3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.contract = w3.eth.contract(address=self.contract_address, abi=REGISTRY_ABI)

    def get_agent_profile(self, agent_did: str) -> AgentProfile:
        """
        Retrieves the registered metadata profile for an agent DID.
        """
        agent_address = parse_agent_did(agent_did)
        try:
            owner, endpoint, capabilities, rate, active = self.contract.functions.getAgent(agent_address).call()
            
            # Capability strings are stored comma-separated on-chain
            caps_list = [c.strip() for c in capabilities.split(",") if c.strip()] if capabilities else []
            
            return AgentProfile(
                owner=owner,
                endpoint=endpoint,
                model_capabilities=caps_list,
                rate_per_task_wei=rate,
                active=active
            )
        except Exception as e:
            raise RuntimeError(f"Failed to lookup agent profile for {agent_did}: {str(e)}") from e

    def register_agent(
        self, 
        owner_private_key: str, 
        agent_address: str, 
        endpoint: str, 
        capabilities: List[str], 
        rate_wei: int
    ) -> str:
        """
        Registers a new agent DID on the contract.
        
        Args:
            owner_private_key: The private key of the human owner.
            agent_address: The signing/identity public key address of the agent.
            endpoint: URL for HTTP message execution.
            capabilities: List of models supported.
            rate_wei: Price in Wei.
            
        Returns:
            str: Transaction hash of the registration transaction.
        """
        account = self.w3.eth.account.from_key(owner_private_key)
        caps_str = ",".join(capabilities)
        agent_address_checksummed = Web3.to_checksum_address(agent_address)

        # Build transaction
        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.registerAgent(
            agent_address_checksummed,
            endpoint,
            caps_str,
            rate_wei
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        # Sign and send
        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=owner_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def update_agent_profile(
        self,
        owner_private_key: str,
        agent_address: str,
        endpoint: str,
        capabilities: List[str],
        rate_wei: int
    ) -> str:
        """
        Updates the profile of a registered agent. Can only be executed by the owner.
        """
        account = self.w3.eth.account.from_key(owner_private_key)
        caps_str = ",".join(capabilities)
        agent_address_checksummed = Web3.to_checksum_address(agent_address)

        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.updateAgentProfile(
            agent_address_checksummed,
            endpoint,
            caps_str,
            rate_wei
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=owner_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def deregister_agent(self, owner_private_key: str, agent_address: str) -> str:
        """
        Deactivates a registered agent. Can only be executed by the owner.
        """
        account = self.w3.eth.account.from_key(owner_private_key)
        agent_address_checksummed = Web3.to_checksum_address(agent_address)

        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.deregisterAgent(
            agent_address_checksummed
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 150000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=owner_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()
