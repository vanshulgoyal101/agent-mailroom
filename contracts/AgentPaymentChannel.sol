// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IAgentRegistry {
    function slashAgent(address agent, address recipient, uint256 amount) external;
}

/**
 * @title AgentPaymentChannel
 * @dev Enforces state channel micro-payments off-chain and settlement on-chain.
 *      Upgraded with Dispute Escrows and On-chain Reputation Slashing.
 */
contract AgentPaymentChannel {
    
    struct Channel {
        uint256 deposit;
        uint256 challengeExpiry;
        bool challenged;
    }

    struct Dispute {
        bytes32 taskHash;
        uint256 expiry;
        bool active;
    }

    address public registryContract;

    // Maps keccak256(abi.encodePacked(sender, recipient)) => Channel
    mapping(bytes32 => Channel) public channels;

    // Maps keccak256(abi.encodePacked(sender, recipient)) => Dispute
    mapping(bytes32 => Dispute) public disputes;

    // Events
    event ChannelOpened(address indexed sender, address indexed recipient, uint256 deposit);
    event ChannelClosed(address indexed sender, address indexed recipient, uint256 settledAmount);
    event ChallengeInitiated(address indexed sender, address indexed recipient, uint256 expiry);
    event ChannelRefunded(address indexed sender, address indexed recipient, uint256 amount);
    event DisputeOpened(address indexed sender, address indexed recipient, bytes32 indexed taskHash, uint256 expiry);
    event DisputeResolved(address indexed sender, address indexed recipient, bytes32 indexed taskHash);
    event DisputeSlashed(address indexed sender, address indexed recipient, uint256 slashAmount);
    event SlashFailed(address indexed sender, address indexed recipient);

    constructor(address _registryContract) {
        require(_registryContract != address(0), "Invalid registry address");
        registryContract = _registryContract;
    }

    /**
     * @notice Creates or adds funds to a payment channel for a specific recipient.
     */
    function createChannel(address recipient) external payable {
        require(recipient != address(0), "Invalid recipient address");
        require(msg.value > 0, "Must deposit positive value");

        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];

        channel.deposit += msg.value;
        channel.challenged = false;
        channel.challengeExpiry = 0;

        emit ChannelOpened(msg.sender, recipient, channel.deposit);
    }

    /**
     * @notice Redeems a signed cumulative payment voucher from the sender.
     *         Cannot be settled if there is an active dispute.
     */
    function redeemVoucher(
        address sender,
        uint256 amount,
        bytes calldata signature
    ) external {
        bytes32 channelId = getChannelId(sender, msg.sender);
        Channel storage channel = channels[channelId];
        Dispute storage dispute = disputes[channelId];

        require(channel.deposit > 0, "No active channel deposit found");
        require(amount <= channel.deposit, "Voucher amount exceeds channel deposit");
        require(!dispute.active, "Cannot settle channel: Active dispute exists");

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
     * @notice Initiates a standard challenge window to claim deposits back if recipient goes offline.
     */
    function initiateChallenge(address recipient) external {
        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];

        require(channel.deposit > 0, "No active channel to challenge");
        require(!channel.challenged, "Channel already in challenge state");

        channel.challenged = true;
        channel.challengeExpiry = block.timestamp + 1 hours;

        emit ChallengeInitiated(msg.sender, recipient, channel.challengeExpiry);
    }

    /**
     * @notice Claims a refund of all locked deposits once the standard challenge window expires.
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
     * @notice Alice (sender) opens a dispute for a specific task if Bob fails to deliver valid work.
     */
    function initiateDispute(address recipient, bytes32 taskHash) external {
        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];
        Dispute storage dispute = disputes[channelId];

        require(channel.deposit > 0, "No active channel to dispute");
        require(!dispute.active, "Dispute is already active");

        dispute.taskHash = taskHash;
        dispute.active = true;
        // Dispute challenge window of 1 hour for testing
        dispute.expiry = block.timestamp + 1 hours;

        emit DisputeOpened(msg.sender, recipient, taskHash, dispute.expiry);
    }

    /**
     * @notice Bob (recipient) resolves the dispute by submitting Alice's signed resolution message.
     */
    function resolveDispute(
        address sender,
        bytes32 taskHash,
        bytes calldata signature
    ) external {
        bytes32 channelId = getChannelId(sender, msg.sender);
        Dispute storage dispute = disputes[channelId];

        require(dispute.active, "No active dispute to resolve");
        require(dispute.taskHash == taskHash, "Dispute task hash mismatch");

        // Verify Alice's signature approving resolution
        bytes32 msgHash = keccak256(abi.encodePacked(address(this), sender, msg.sender, taskHash, "RESOLVED"));
        bytes32 ethSignedMsgHash = keccak256(abi.encodePacked("\x19Ethereum Signed Message:\n32", msgHash));

        address recovered = recoverSigner(ethSignedMsgHash, signature);
        require(recovered == sender, "Invalid dispute resolution signature");

        dispute.active = false;

        emit DisputeResolved(sender, msg.sender, taskHash);
    }

    /**
     * @notice Alice claims a refund on-chain and triggers a slash on Bob's registry stake
     *         if Bob fails to resolve the dispute in time.
     */
    function claimDisputeSlash(address recipient) external {
        bytes32 channelId = getChannelId(msg.sender, recipient);
        Channel storage channel = channels[channelId];
        Dispute storage dispute = disputes[channelId];

        require(channel.deposit > 0, "No active channel found");
        require(dispute.active, "No active dispute found");
        require(block.timestamp >= dispute.expiry, "Dispute challenge window is still active");

        uint256 refundAmount = channel.deposit;
        
        // Clear states to prevent re-entrancy
        delete channels[channelId];
        delete disputes[channelId];

        // Refund channel deposit to Alice
        payable(msg.sender).transfer(refundAmount);

        // Slash Bob's locked registry stake (0.05 ETH) and transfer it to Alice
        uint256 slashAmount = 0.05 ether;
        try IAgentRegistry(registryContract).slashAgent(recipient, msg.sender, slashAmount) {
            emit DisputeSlashed(msg.sender, recipient, slashAmount);
        } catch {
            emit SlashFailed(msg.sender, recipient);
        }

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
