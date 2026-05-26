// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AgentPaymentChannel
 * @dev Enforces state channel micro-payments off-chain and settlement on-chain.
 *      Allows Agent A (sender) to lock funds for Agent B (recipient). Agent B can
 *      redeem signed off-chain vouchers, which resolves the channel. Enforces a 
 *      challenge window to protect both parties if one goes offline.
 */
contract AgentPaymentChannel {
    
    struct Channel {
        uint256 deposit;
        uint256 challengeExpiry;
        bool challenged;
    }

    // Maps keccak256(abi.encodePacked(sender, recipient)) => Channel
    mapping(bytes32 => Channel) public channels;

    // Events
    event ChannelOpened(address indexed sender, address indexed recipient, uint256 deposit);
    event ChannelClosed(address indexed sender, address indexed recipient, uint256 settledAmount);
    event ChallengeInitiated(address indexed sender, address indexed recipient, uint256 expiry);
    event ChannelRefunded(address indexed sender, address indexed recipient, uint256 amount);

    /**
     * @notice Creates or adds funds to a payment channel for a specific recipient.
     * @param recipient The address of the agent who will receive payments.
     */
    function createChannel(address recipient) external payable {
        require(recipient != address(0), "Invalid recipient address");
        require(msg.value > 0, "Must deposit positive value");

        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];

        channel.deposit += msg.value;
        // Reset challenge status on new deposits
        channel.challenged = false;
        channel.challengeExpiry = 0;

        emit ChannelOpened(msg.sender, recipient, channel.deposit);
    }

    /**
     * @notice Redeems a signed cumulative payment voucher from the sender.
     *         Payouts the voucher amount to the caller (recipient) and refunds 
     *         the remainder to the sender, closing the channel.
     * @param sender The address of the channel sender who signed the voucher.
     * @param amount The cumulative amount of Wei authorized in the voucher.
     * @param signature The EIP-191 signature of the sender.
     */
    function redeemVoucher(
        address sender,
        uint256 amount,
        bytes calldata signature
    ) external {
        bytes32 channelId = getChannelId(sender, msg.sender);
        Channel storage channel = channels[channelId];

        require(channel.deposit > 0, "No active channel deposit found");
        require(amount <= channel.deposit, "Voucher amount exceeds channel deposit");

        // Verify the signature
        bytes32 msgHash = keccak256(abi.encodePacked(address(this), sender, msg.sender, amount));
        bytes32 ethSignedMsgHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", msgHash));

        address recovered = recoverSigner(ethSignedMsgHash, signature);
        require(recovered == sender, "Invalid voucher signature");

        uint256 totalDeposit = channel.deposit;
        
        // Delete channel to prevent re-entrancy / double redemption
        delete channels[channelId];

        // Payout to recipient
        payable(msg.sender).transfer(amount);

        // Refund remainder to sender
        uint256 remainder = totalDeposit - amount;
        if (remainder > 0) {
            payable(sender).transfer(remainder);
        }

        emit ChannelClosed(sender, msg.sender, amount);
    }

    /**
     * @notice Initiates a challenge window to claim unused deposits back.
     *         Allows the recipient to still redeem any valid vouchers before expiry.
     * @param recipient The recipient of the channel.
     */
    function initiateChallenge(address recipient) external {
        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];

        require(channel.deposit > 0, "No active channel to challenge");
        require(!channel.challenged, "Channel already in challenge state");

        channel.challenged = true;
        // Challenge period of 1 hour for local demo/fast settlement, standard is 1 day.
        channel.challengeExpiry = block.timestamp + 1 hours;

        emit ChallengeInitiated(msg.sender, recipient, channel.challengeExpiry);
    }

    /**
     * @notice Claims a refund of all locked deposits once the challenge window expires.
     * @param recipient The recipient of the channel.
     */
    function claimChallengeRefund(address recipient) external {
        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];

        require(channel.deposit > 0, "No active channel found");
        require(channel.challenged, "Channel has not been challenged");
        require(block.timestamp >= channel.challengeExpiry, "Challenge window is still active");

        uint256 refundAmount = channel.deposit;
        delete channels[channelId];

        payable(msg.sender).transfer(refundAmount);

        emit ChannelRefunded(msg.sender, recipient, refundAmount);
    }

    /**
     * @notice Helper to calculate the unique key identifying a channel.
     */
    function getChannelId(address sender, address recipient) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(sender, recipient));
    }

    /**
     * @dev Helper function to verify signatures on-chain.
     */
    function recoverSigner(bytes32 ethSignedMsgHash, bytes memory signature) internal pure returns (address) {
        (bytes32 r, bytes32 s, uint8 v) = splitSignature(signature);
        return ecrecover(ethSignedMsgHash, v, r, s);
    }

    function splitSignature(bytes memory sig) internal pure returns (bytes32 r, bytes32 s, uint8 v) {
        require(sig.length == 65, "Invalid signature length");
        assembly {
            r := mload(add(sig, 32))
            s := mload(add(sig, 64))
            v := byte(0, mload(add(sig, 96)))
        }
    }
}
