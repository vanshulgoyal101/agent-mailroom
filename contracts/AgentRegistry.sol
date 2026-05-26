// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title AgentRegistry
 * @dev On-chain Decentralized Identifier (DID) Registry for AI Agents.
 *      Maps agent addresses (their public keys) to metadata detailing their
 *      controllers, endpoints, capability models, and service pricing.
 *      Upgraded with Reputation Staking and Slashing guarantees.
 */
contract AgentRegistry {
    
    struct AgentProfile {
        address owner;
        string endpoint;
        string modelCapabilities;
        uint256 ratePerTaskWei;
        bool active;
    }

    address public admin;
    address public paymentChannel;
    uint256 public constant MIN_STAKE = 0.1 ether;

    // Maps agent address to its Profile
    mapping(address => AgentProfile) private _registry;

    // Maps agent address to locked reputation stake (in Wei)
    mapping(address => uint256) public stakes;

    // Events
    event AgentRegistered(
        address indexed agent, 
        address indexed owner, 
        string endpoint, 
        uint256 ratePerTaskWei
    );
    event AgentProfileUpdated(
        address indexed agent, 
        string endpoint, 
        string modelCapabilities, 
        uint256 ratePerTaskWei
    );
    event AgentDeregistered(address indexed agent);
    event ReputationStaked(address indexed agent, address indexed staker, uint256 amount);
    event ReputationUnstaked(address indexed agent, uint256 amount);
    event AgentSlashed(address indexed agent, address indexed recipient, uint256 amount);

    modifier onlyAgentOwner(address agent) {
        require(_registry[agent].active, "Agent is not registered");
        require(_registry[agent].owner == msg.sender, "Caller is not the agent owner");
        _;
    }

    modifier onlyRegistryAdmin() {
        require(msg.sender == admin, "Only registry admin can call this");
        _;
    }

    constructor() {
        admin = msg.sender;
    }

    /**
     * @notice Configures the authorized payment channel contract address that can trigger slashes.
     */
    function setPaymentChannel(address _paymentChannel) external onlyRegistryAdmin {
        paymentChannel = _paymentChannel;
    }

    /**
     * @notice Locks reputation stake for a specific agent.
     */
    function stakeReputation(address agent) external payable {
        require(agent != address(0), "Invalid agent address");
        require(msg.value > 0, "Must stake positive value");
        
        stakes[agent] += msg.value;
        emit ReputationStaked(agent, msg.sender, msg.value);
    }

    /**
     * @notice Reclaims locked reputation stake once the agent is deactivated.
     */
    function unstakeReputation(address agent) external {
        uint256 stakedAmount = stakes[agent];
        require(stakedAmount > 0, "No stake found");
        require(!_registry[agent].active, "Must deregister agent profile before unstaking");

        address owner = _registry[agent].owner;
        if (owner == address(0)) {
            owner = msg.sender;
        }

        stakes[agent] = 0;
        payable(owner).transfer(stakedAmount);
        emit ReputationUnstaked(agent, stakedAmount);
    }

    /**
     * @notice Slashes an agent's stake. Restricted to the authorized payment channel.
     */
    function slashAgent(address agent, address recipient, uint256 amount) external {
        require(msg.sender == paymentChannel, "Only authorized payment channel can trigger slashes");
        require(stakes[agent] >= amount, "Slash amount exceeds locked stake");

        stakes[agent] -= amount;
        payable(recipient).transfer(amount);
        emit AgentSlashed(agent, recipient, amount);
    }

    /**
     * @notice Registers a new agent profile under the caller's control.
     *         Requires a minimum locked reputation stake of 0.1 ETH.
     */
    function registerAgent(
        address agent,
        string calldata endpoint,
        string calldata modelCapabilities,
        uint256 ratePerTaskWei
    ) external {
        require(agent != address(0), "Invalid agent address");
        require(!_registry[agent].active, "Agent already registered");
        require(stakes[agent] >= MIN_STAKE, "Insufficient reputation stake. Minimum 0.1 ETH required.");
        
        _registry[agent] = AgentProfile({
            owner: msg.sender,
            endpoint: endpoint,
            modelCapabilities: modelCapabilities,
            ratePerTaskWei: ratePerTaskWei,
            active: true
        });

        emit AgentRegistered(agent, msg.sender, endpoint, ratePerTaskWei);
    }

    /**
     * @notice Updates an existing agent's profile details. Can only be called by the agent's owner.
     */
    function updateAgentProfile(
        address agent,
        string calldata endpoint,
        string calldata modelCapabilities,
        uint256 ratePerTaskWei
    ) external onlyAgentOwner(agent) {
        AgentProfile storage profile = _registry[agent];
        profile.endpoint = endpoint;
        profile.modelCapabilities = modelCapabilities;
        profile.ratePerTaskWei = ratePerTaskWei;

        emit AgentProfileUpdated(agent, endpoint, modelCapabilities, ratePerTaskWei);
    }

    /**
     * @notice Deregisters an agent profile, removing it from active status. Can only be called by owner.
     */
    function deregisterAgent(address agent) external onlyAgentOwner(agent) {
        _registry[agent].active = false;
        emit AgentDeregistered(agent);
    }

    /**
     * @notice Retrieves details of an agent by address.
     */
    function getAgent(address agent) 
        external 
        view 
        returns (
            address owner,
            string memory endpoint,
            string memory modelCapabilities,
            uint256 ratePerTaskWei,
            bool active
        ) 
    {
        AgentProfile memory profile = _registry[agent];
        return (
            profile.owner,
            profile.endpoint,
            profile.modelCapabilities,
            profile.ratePerTaskWei,
            profile.active
        );
    }
}
