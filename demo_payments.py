import os
import sys
import time
from web3 import Web3
from eth_account import Account

# Ensure we can import agent_mailroom
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from agent_mailroom.channel import PaymentChannelManager

# Sandbox config
RPC_URL = "http://127.0.0.1:8545"

# Sandbox accounts (Alice Agent & Bob Agent keys)
ALICE_AGENT_KEY = "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690c"
BOB_AGENT_KEY = "0xabf82f5110266c165e6488bc1103c80ff2570891d4e0e5a8e64e10b42f61a1a7"

def time_travel_sandbox(w3: Web3, seconds: int):
    """Fast-forwards time in the mock sandbox node and mines a block."""
    w3.provider.make_request("evm_increaseTime", [seconds])
    w3.provider.make_request("evm_mine", [])
    print(f"  [Time-Travel] Fast-forwarded EVM time by {seconds} seconds.")

def run_payment_demo():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print("[ERROR] Sandbox node not running on http://127.0.0.1:8545. Please start it first.")
        sys.exit(1)

    alice = Account.from_key(ALICE_AGENT_KEY)
    bob = Account.from_key(BOB_AGENT_KEY)

    print("====================================================")
    # Print initial balances
    alice_bal_initial = w3.eth.get_balance(alice.address)
    bob_bal_initial = w3.eth.get_balance(bob.address)
    print(f"Alice Agent Address: {alice.address}")
    print(f"Alice Initial Balance: {w3.from_wei(alice_bal_initial, 'ether'):.4f} ETH")
    print(f"Bob Agent Address:   {bob.address}")
    print(f"Bob Initial Balance:   {w3.from_wei(bob_bal_initial, 'ether'):.4f} ETH")
    print("====================================================\n")

    # Initialize the Channel Manager
    channel_manager = PaymentChannelManager(w3)

    # 1. Open State Channel
    deposit_amount = w3.to_wei(0.05, "ether")
    print(f"[1/5] Opening Payment Channel Alice -> Bob with {w3.from_wei(deposit_amount, 'ether')} ETH deposit...")
    tx_hash = channel_manager.open_channel(
        sender_private_key=ALICE_AGENT_KEY,
        recipient_address=bob.address,
        amount_wei=deposit_amount
    )
    print(f"  [SUCCESS] Channel opened on-chain. Tx: {tx_hash}")
    
    deposit, expiry, challenged = channel_manager.get_channel_info(alice.address, bob.address)
    print(f"  Locked Channel Deposit: {w3.from_wei(deposit, 'ether')} ETH")

    # 2. Generate Off-Chain Vouchers (Micro-payments)
    print("\n[2/5] Simulating Off-chain Micro-payments (Gas-free signatures)...")
    
    # Voucher 1: 0.01 ETH
    v1_amount = w3.to_wei(0.01, "ether")
    voucher1 = channel_manager.create_voucher(ALICE_AGENT_KEY, bob.address, v1_amount)
    print(f"  - Alice signs Voucher 1: {w3.from_wei(v1_amount, 'ether')} ETH (Sig: {voucher1.signature[:16]}...)")
    assert channel_manager.verify_voucher(voucher1), "Voucher 1 verification failed!"
    print("    [Bob Verified] Voucher 1 signature is authentic.")

    # Voucher 2: 0.025 ETH (Cumulative)
    v2_amount = w3.to_wei(0.025, "ether")
    voucher2 = channel_manager.create_voucher(ALICE_AGENT_KEY, bob.address, v2_amount)
    print(f"  - Alice signs Voucher 2: {w3.from_wei(v2_amount, 'ether')} ETH (Sig: {voucher2.signature[:16]}...)")
    assert channel_manager.verify_voucher(voucher2), "Voucher 2 verification failed!"
    print("    [Bob Verified] Voucher 2 signature is authentic.")

    # Voucher 3: 0.035 ETH (Cumulative - Final)
    v3_amount = w3.to_wei(0.035, "ether")
    voucher3 = channel_manager.create_voucher(ALICE_AGENT_KEY, bob.address, v3_amount)
    print(f"  - Alice signs Voucher 3: {w3.from_wei(v3_amount, 'ether')} ETH (Sig: {voucher3.signature[:16]}...)")
    assert channel_manager.verify_voucher(voucher3), "Voucher 3 verification failed!"
    print("    [Bob Verified] Voucher 3 signature is authentic.")

    # 3. Bob Redeems Final Voucher On-Chain
    print(f"\n[3/5] Bob redeeming Voucher 3 on-chain for {w3.from_wei(v3_amount, 'ether')} ETH...")
    tx_redeem = channel_manager.redeem_voucher_on_chain(
        recipient_private_key=BOB_AGENT_KEY,
        sender_address=alice.address,
        voucher=voucher3
    )
    print(f"  [SUCCESS] Voucher redeemed on-chain. Tx: {tx_redeem}")

    # Check updated channel info
    deposit, expiry, challenged = channel_manager.get_channel_info(alice.address, bob.address)
    print(f"  Remaining Locked Deposit: {w3.from_wei(deposit, 'ether')} ETH")

    # 4. Initiate Challenge Period for Remaining Deposit Refund
    print("\n[4/5] Alice initiating challenge to refund remaining channel balance...")
    tx_challenge = channel_manager.initiate_challenge(ALICE_AGENT_KEY, bob.address)
    print(f"  [SUCCESS] Challenge window opened. Tx: {tx_challenge}")
    
    deposit, expiry, challenged = channel_manager.get_channel_info(alice.address, bob.address)
    print(f"  Challenged: {challenged}, Challenge Expiry: Block Timestamp + 3600")

    # Fast forward time to expire challenge (1 hour)
    time_travel_sandbox(w3, 3601)

    # 5. Claim Refund
    print("\n[5/5] Alice claiming remaining refund after challenge expiration...")
    tx_refund = channel_manager.claim_refund(ALICE_AGENT_KEY, bob.address)
    print(f"  [SUCCESS] Channel closed & refunded. Tx: {tx_refund}")

    # Verify channel is closed
    deposit, expiry, challenged = channel_manager.get_channel_info(alice.address, bob.address)
    print(f"  Final Locked Channel Deposit: {w3.from_wei(deposit, 'ether')} ETH")

    print("\n====================================================")
    # Print final balances
    alice_bal_final = w3.eth.get_balance(alice.address)
    bob_bal_final = w3.eth.get_balance(bob.address)
    print(f"Alice Final Balance: {w3.from_wei(alice_bal_final, 'ether'):.4f} ETH (Net change: {w3.from_wei(alice_bal_final - alice_bal_initial, 'ether'):.4f} ETH)")
    print(f"Bob Final Balance:   {w3.from_wei(bob_bal_final, 'ether'):.4f} ETH (Net change: +{w3.from_wei(bob_bal_final - bob_bal_initial, 'ether'):.4f} ETH)")
    print("====================================================")

if __name__ == "__main__":
    print("====================================================")
    # Run the demo
    run_payment_demo()
