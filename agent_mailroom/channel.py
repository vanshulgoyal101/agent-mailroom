from typing import Tuple, Dict, Any
from pydantic import BaseModel, Field, field_validator
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

# ABI for the AgentPaymentChannel Solidity Smart Contract
CHANNEL_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"}
        ],
        "name": "createChannel",
        "outputs": [],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "sender", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"}
        ],
        "name": "redeemVoucher",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"}
        ],
        "name": "initiateChallenge",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"}
        ],
        "name": "claimChallengeRefund",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "sender", "type": "address"},
            {"internalType": "address", "name": "recipient", "type": "address"}
        ],
        "name": "getChannelId",
        "outputs": [
            {"internalType": "bytes32", "name": "", "type": "bytes32"}
        ],
        "stateMutability": "pure",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "channelId", "type": "bytes32"}
        ],
        "name": "channels",
        "outputs": [
            {"internalType": "uint256", "name": "deposit", "type": "uint256"},
            {"internalType": "uint256", "name": "challengeExpiry", "type": "uint256"},
            {"internalType": "bool", "name": "challenged", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"},
            {"internalType": "bytes32", "name": "taskHash", "type": "bytes32"}
        ],
        "name": "initiateDispute",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "sender", "type": "address"},
            {"internalType": "bytes32", "name": "taskHash", "type": "bytes32"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"}
        ],
        "name": "resolveDispute",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "address", "name": "recipient", "type": "address"}
        ],
        "name": "claimDisputeSlash",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "channelId", "type": "bytes32"}
        ],
        "name": "disputes",
        "outputs": [
            {"internalType": "bytes32", "name": "taskHash", "type": "bytes32"},
            {"internalType": "uint256", "name": "expiry", "type": "uint256"},
            {"internalType": "bool", "name": "active", "type": "bool"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# Standard contract deployment address for mock sandbox payment channels
DEFAULT_CHANNEL_ADDRESS = "0x0000000000000000000000000000000000000200"


class PaymentVoucher(BaseModel):
    """Off-chain signed payment voucher representing micro-payment credit."""
    channel_address: str = Field(..., description="EVM address of the Payment Channel contract.")
    sender_address: str = Field(..., description="EVM address of the sender.")
    recipient_address: str = Field(..., description="EVM address of the recipient.")
    amount_wei: int = Field(..., description="Cumulative authorized spend limit in Wei.")
    signature: str = Field(..., description="Signature of the sender authorizing the voucher.")

    @field_validator("channel_address", "sender_address", "recipient_address")
    @classmethod
    def validate_addresses(cls, v: str) -> str:
        if not Web3.is_address(v):
            raise ValueError(f"Invalid Ethereum address format: {v}")
        return Web3.to_checksum_address(v)


class PaymentChannelManager:
    """Client SDK for managing and redeeming state-channel transactions."""

    def __init__(self, w3: Web3, contract_address: str = DEFAULT_CHANNEL_ADDRESS):
        self.w3 = w3
        self.contract_address = Web3.to_checksum_address(contract_address)
        self.contract = w3.eth.contract(address=self.contract_address, abi=CHANNEL_ABI)

    def get_channel_info(self, sender: str, recipient: str) -> Tuple[int, int, bool]:
        """
        Queries the blockchain for details of a payment channel.
        
        Returns:
            Tuple[int, int, bool]: (deposit_wei, challenge_expiry_timestamp, is_challenged)
        """
        sender_checksum = Web3.to_checksum_address(sender)
        recipient_checksum = Web3.to_checksum_address(recipient)
        
        channel_id = self.contract.functions.getChannelId(sender_checksum, recipient_checksum).call()
        deposit, expiry, challenged = self.contract.functions.channels(channel_id).call()
        
        return deposit, expiry, challenged

    def open_channel(self, sender_private_key: str, recipient_address: str, amount_wei: int) -> str:
        """
        Deploys/Deposits funds into a payment channel on-chain.
        """
        account = self.w3.eth.account.from_key(sender_private_key)
        recipient_checksum = Web3.to_checksum_address(recipient_address)
        
        nonce = self.w3.eth.get_transaction_count(account.address)
        
        tx = self.contract.functions.createChannel(
            recipient_checksum
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "value": amount_wei,
            "gas": 150000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=sender_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def create_voucher(self, sender_private_key: str, recipient_address: str, amount_wei: int) -> PaymentVoucher:
        """
        Generates and signs a cumulative off-chain payment voucher for the recipient.
        """
        w3 = Web3()
        sender_account = Account.from_key(sender_private_key)
        sender_address = sender_account.address

        sender_checksum = w3.to_checksum_address(sender_address)
        recipient_checksum = w3.to_checksum_address(recipient_address)

        # Replicate Solidity hashing: keccak256(abi.encodePacked(contract, sender, recipient, amount))
        message_hash = w3.solidity_keccak(
            ["address", "address", "address", "uint256"],
            [self.contract_address, sender_checksum, recipient_checksum, amount_wei]
        )

        # Sign the message hash using the Ethereum Signed Message standard
        signable_msg = encode_defunct(hexstr=message_hash.hex())
        signed_msg = Account.sign_message(signable_msg, private_key=sender_private_key)

        return PaymentVoucher(
            channel_address=self.contract_address,
            sender_address=sender_checksum,
            recipient_address=recipient_checksum,
            amount_wei=amount_wei,
            signature=signed_msg.signature.hex()
        )

    def verify_voucher(self, voucher: PaymentVoucher) -> bool:
        """
        Verifies that the signature on a payment voucher is valid and matches the declared sender.
        """
        w3 = Web3()
        if w3.to_checksum_address(voucher.channel_address) != self.contract_address:
            raise ValueError("Voucher is bound to a different payment channel contract address.")

        message_hash = w3.solidity_keccak(
            ["address", "address", "address", "uint256"],
            [
                self.contract_address,
                w3.to_checksum_address(voucher.sender_address),
                w3.to_checksum_address(voucher.recipient_address),
                voucher.amount_wei
            ]
        )

        signable_msg = encode_defunct(hexstr=message_hash.hex())
        
        try:
            recovered_signer = Account.recover_message(signable_msg, signature=voucher.signature)
        except Exception as e:
            raise ValueError(f"Failed to recover signer: {str(e)}") from e

        if w3.to_checksum_address(recovered_signer) != w3.to_checksum_address(voucher.sender_address):
            raise ValueError(
                f"Voucher signature verification failed. Recovered: {recovered_signer}, "
                f"Expected sender: {voucher.sender_address}"
            )

        return True

    def redeem_voucher_on_chain(self, recipient_private_key: str, sender_address: str, voucher: PaymentVoucher) -> str:
        """
        Submits the voucher to the blockchain to withdraw funds and settle/close the channel.
        """
        account = self.w3.eth.account.from_key(recipient_private_key)
        sender_checksum = Web3.to_checksum_address(sender_address)

        # Run verification check locally first
        self.verify_voucher(voucher)

        nonce = self.w3.eth.get_transaction_count(account.address)

        tx = self.contract.functions.redeemVoucher(
            sender_checksum,
            voucher.amount_wei,
            bytes.fromhex(voucher.signature[2:] if voucher.signature.startswith("0x") else voucher.signature)
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=recipient_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def initiate_challenge(self, sender_private_key: str, recipient_address: str) -> str:
        """Initiates the challenge window for the sender to reclaim locked deposits."""
        account = self.w3.eth.account.from_key(sender_private_key)
        recipient_checksum = Web3.to_checksum_address(recipient_address)

        nonce = self.w3.eth.get_transaction_count(account.address)

        tx = self.contract.functions.initiateChallenge(
            recipient_checksum
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 100000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=sender_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def claim_refund(self, sender_private_key: str, recipient_address: str) -> str:
        """Claims refund of deposits after the challenge period expires."""
        account = self.w3.eth.account.from_key(sender_private_key)
        recipient_checksum = Web3.to_checksum_address(recipient_address)

        nonce = self.w3.eth.get_transaction_count(account.address)

        tx = self.contract.functions.claimChallengeRefund(
            recipient_checksum
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 100000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=sender_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def initiate_dispute(self, sender_private_key: str, recipient_address: str, task_hash: bytes) -> str:
        """
        Alice (sender) opens a dispute for a task to freeze Bob's claim.
        """
        account = self.w3.eth.account.from_key(sender_private_key)
        recipient_checksum = Web3.to_checksum_address(recipient_address)

        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.initiateDispute(
            recipient_checksum,
            task_hash
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 150000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=sender_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def resolve_dispute(self, recipient_private_key: str, sender_address: str, task_hash: bytes, signature: str) -> str:
        """
        Bob (recipient) resolves the dispute by submitting Alice's signed resolution.
        """
        account = self.w3.eth.account.from_key(recipient_private_key)
        sender_checksum = Web3.to_checksum_address(sender_address)

        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.resolveDispute(
            sender_checksum,
            task_hash,
            bytes.fromhex(signature[2:] if signature.startswith("0x") else signature)
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 200000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=recipient_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def claim_dispute_slash(self, sender_private_key: str, recipient_address: str) -> str:
        """
        Alice claims a channel refund and slashes Bob's registry stake after expiry.
        """
        account = self.w3.eth.account.from_key(sender_private_key)
        recipient_checksum = Web3.to_checksum_address(recipient_address)

        nonce = self.w3.eth.get_transaction_count(account.address)
        tx = self.contract.functions.claimDisputeSlash(
            recipient_checksum
        ).build_transaction({
            "from": account.address,
            "nonce": nonce,
            "gas": 250000,
            "gasPrice": self.w3.eth.gas_price,
            "chainId": self.w3.eth.chain_id
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=sender_private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        return "0x" + tx_hash.hex()

    def get_dispute_info(self, sender: str, recipient: str) -> Tuple[bytes, int, bool]:
        """
        Queries the blockchain for details of an active dispute on a channel.
        
        Returns:
            Tuple[bytes, int, bool]: (task_hash_bytes, expiry_timestamp, is_active)
        """
        sender_checksum = Web3.to_checksum_address(sender)
        recipient_checksum = Web3.to_checksum_address(recipient)
        
        channel_id = self.contract.functions.getChannelId(sender_checksum, recipient_checksum).call()
        task_hash, expiry, active = self.contract.functions.disputes(channel_id).call()
        
        return task_hash, expiry, active
