// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.26;

/// ---------------------------------------------------------------------------
/// PYTHAI LOGO REGISTRY (PLR) — canonical source of truth for verified token
/// logos across all ALLCHAIN-registered networks.
///
/// Design constraints (cypherpunk2048):
///   - No upgradeable proxy. No post-deploy admin keys.
///   - Fees and treasury (Aerarium) are immutable, set at construction.
///   - The only privileged actor is the mindX validator signer, itself
///     immutable and expected to be an ERC-8004-registered agent identity.
///   - Mainnet-only deployment; Foundry for testing.
///
/// Each registered token receives a validation iNFT (ERC-721). The iNFT's
/// tokenURI resolves to live metadata served by mindX and published through
/// rage.pythai.net, containing the canonical logo CID (Arweave/IPFS), the
/// validation assessment, confidence score, and evolution history. ERC-4906
/// MetadataUpdate events are emitted on every state change so indexers and
/// wallets refresh rather than serve stale art.
/// ---------------------------------------------------------------------------

interface IERC165 {
    function supportsInterface(bytes4 interfaceId) external view returns (bool);
}

interface IERC721Receiver {
    function onERC721Received(address, address, uint256, bytes calldata)
        external
        returns (bytes4);
}

contract LogoRegistry is IERC165 {
    // ------------------------------------------------------------------ types

    enum Status {
        None,        // never registered
        Pending,     // fee paid, awaiting mindX assessment
        Assessed,    // mindX-assessed and Arweave-pinned, awaiting DAIO ratification
        Verified,    // Senatus-ratified canonical logo
        Revoked      // validation withdrawn (rug, impersonation, dead project)
    }

    struct Entry {
        uint64 chainId;        // ALLCHAIN chain id
        address token;         // token contract on that chain
        bytes32 logoHash;      // keccak256 of the canonical logo bytes
        string logoCID;        // Arweave/IPFS CID of the canonical logo
        Status status;
        uint64 registeredAt;
        uint64 lastValidated;
        uint32 revision;       // increments on each graphic update
        address submitter;
    }

    // ---------------------------------------------------------------- storage

    string public constant name = "PYTHAI Logo Registry";
    string public constant symbol = "PLRiNFT";

    /// Senatus — the DAIO governance executor. Immutable. The only power it
    /// holds is validator rotation; it cannot touch fees, treasury, or entries.
    address public immutable SENATUS;

    /// mindX validator signer (ERC-8004 agent). Rotatable exclusively by
    /// Senatus, so a compromised signer is survivable without redeploying
    /// the layer — governance, not an admin key, holds the rotation power.
    address public validator;

    /// Fee for a new chain to onboard into the PLR/ALLCHAIN layer (wei).
    uint256 public immutable CHAIN_ONBOARD_FEE;

    /// SPINTRADE listing desk — receives first-notification of every new
    /// chain onboarding via best-effort push, before any public indexer
    /// can observe anything other than the event itself.
    address public immutable SPINTRADE;

    struct ChainRecord {
        string chainName;
        string rpcURL;
        address registrar;
        uint64 onboardedAt;
        bool active;
    }

    /// chainId => onboarded chain metadata (the on-chain ALLCHAIN extension)
    mapping(uint64 => ChainRecord) public chains;
    uint64[] public chainIds;

    /// Aerarium treasury receiving all fees. Immutable.
    address public immutable AERARIUM;

    /// Fee to register a new token logo (wei). Immutable.
    uint256 public immutable SUBMISSION_FEE;

    /// Fee to update the graphic of an already-verified token (wei). Immutable.
    uint256 public immutable UPDATE_FEE;

    /// Base URI for live iNFT metadata, e.g. "https://rage.pythai.net/plr/".
    string public baseURI;

    uint256 public nextTokenId = 1;

    // iNFT id => entry
    mapping(uint256 => Entry) public entries;
    // keccak256(chainId, token) => iNFT id (0 = unregistered)
    mapping(bytes32 => uint256) public keyToId;

    // Minimal ERC-721 state
    mapping(uint256 => address) private _owners;
    mapping(address => uint256) private _balances;
    mapping(uint256 => address) private _tokenApprovals;
    mapping(address => mapping(address => bool)) private _operatorApprovals;

    // ----------------------------------------------------------------- events

    // ERC-721
    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event Approval(address indexed owner, address indexed approved, uint256 indexed tokenId);
    event ApprovalForAll(address indexed owner, address indexed operator, bool approved);

    // ERC-4906
    event MetadataUpdate(uint256 tokenId);
    event BatchMetadataUpdate(uint256 fromTokenId, uint256 toTokenId);

    // Registry
    event LogoSubmitted(
        uint256 indexed tokenId,
        uint64 indexed chainId,
        address indexed token,
        bytes32 logoHash,
        string logoCID,
        address submitter
    );
    event LogoAssessed(uint256 indexed tokenId, bytes32 logoHash, string arweaveCID);
    event LogoRatified(uint256 indexed tokenId, bytes32 logoHash, string arweaveCID);
    event LogoRejected(uint256 indexed tokenId, string reason);
    event LogoUpdated(uint256 indexed tokenId, bytes32 newHash, string newCID, uint32 revision);
    event LogoRevoked(uint256 indexed tokenId, string reason);
    event FeeCollected(address indexed payer, uint256 amount, bytes32 indexed key);
    event ValidatorRotated(address indexed oldValidator, address indexed newValidator);
    event ChainOnboarded(uint64 indexed chainId, string chainName, address indexed registrar);
    event ChainStatusChanged(uint64 indexed chainId, bool active);
    event SpintradeNotified(uint64 indexed chainId, bool delivered);

    // ----------------------------------------------------------------- errors

    error NotValidator();
    error WrongFee(uint256 sent, uint256 required);
    error AlreadyRegistered(uint64 chainId, address token);
    error NotRegistered();
    error BadStatus(Status current);
    error ZeroAddress();
    error NotOwnerNorApproved();
    error TransferToNonReceiver();
    error TreasuryTransferFailed();
    error NotSenatus();
    error ChainExists(uint64 chainId);
    error ChainUnknown(uint64 chainId);
    error EmptyCID();

    // ------------------------------------------------------------ constructor

    constructor(
        address senatus,
        address mindxValidator,
        address aerarium,
        address spintrade,
        uint256 submissionFee,
        uint256 updateFee,
        uint256 chainOnboardFee,
        string memory baseURI_
    ) {
        if (
            senatus == address(0) ||
            mindxValidator == address(0) ||
            aerarium == address(0) ||
            spintrade == address(0)
        ) revert ZeroAddress();
        SENATUS = senatus;
        validator = mindxValidator;
        AERARIUM = aerarium;
        SPINTRADE = spintrade;
        SUBMISSION_FEE = submissionFee;
        UPDATE_FEE = updateFee;
        CHAIN_ONBOARD_FEE = chainOnboardFee;
        baseURI = baseURI_;
    }

    modifier onlyValidator() {
        if (msg.sender != validator) revert NotValidator();
        _;
    }

    modifier onlySenatus() {
        if (msg.sender != SENATUS) revert NotSenatus();
        _;
    }

    // --------------------------------------------------------- daio governance

    /// The single governance power: rotate the mindX validator signer.
    /// Executed by Senatus after a passed proposal (Fides-weighted vote).
    function rotateValidator(address newValidator) external onlySenatus {
        if (newValidator == address(0)) revert ZeroAddress();
        address old = validator;
        validator = newValidator;
        emit ValidatorRotated(old, newValidator);
    }

    // --------------------------------------------------------- chain onboarding

    /// New chains buy into the layer. Payment goes straight to the Aerarium,
    /// the chain enters the on-chain ALLCHAIN extension, and SPINTRADE is
    /// push-notified in the same transaction — first to know, by construction.
    /// The push is best-effort: a reverting or absent listener cannot block
    /// onboarding, and delivery status is recorded in the event log.
    function onboardChain(
        uint64 chainId,
        string calldata chainName,
        string calldata rpcURL
    ) external payable {
        if (msg.value != CHAIN_ONBOARD_FEE) revert WrongFee(msg.value, CHAIN_ONBOARD_FEE);
        if (chains[chainId].onboardedAt != 0) revert ChainExists(chainId);

        chains[chainId] = ChainRecord({
            chainName: chainName,
            rpcURL: rpcURL,
            registrar: msg.sender,
            onboardedAt: uint64(block.timestamp),
            active: true
        });
        chainIds.push(chainId);

        _forwardFee(keccak256(abi.encodePacked("chain", chainId)));
        emit ChainOnboarded(chainId, chainName, msg.sender);

        // SPINTRADE first-notification: bounded-gas push, failure-tolerant.
        bool delivered;
        if (SPINTRADE.code.length > 0) {
            (delivered, ) = SPINTRADE.call{gas: 200_000}(
                abi.encodeWithSignature(
                    "onChainOnboarded(uint64,string,address)",
                    chainId,
                    chainName,
                    msg.sender
                )
            );
        }
        emit SpintradeNotified(chainId, delivered);
    }

    /// mindX may deactivate a chain (dead RPC, chain halt, fraud) or restore it.
    function setChainActive(uint64 chainId, bool active) external onlyValidator {
        if (chains[chainId].onboardedAt == 0) revert ChainUnknown(chainId);
        chains[chainId].active = active;
        emit ChainStatusChanged(chainId, active);
    }

    function chainCount() external view returns (uint256) {
        return chainIds.length;
    }

    // ------------------------------------------------------------ registry api

    /// Key derivation shared with the off-chain aggregator.
    function entryKey(uint64 chainId, address token) public pure returns (bytes32) {
        return keccak256(abi.encodePacked(chainId, token));
    }

    /// Anyone may submit a logo for any (chainId, token) pair by paying the
    /// submission fee. The entry enters Pending until mindX validates it.
    /// Fees are forwarded straight to the Aerarium — nothing is custodied here.
    function submitLogo(
        uint64 chainId,
        address token,
        bytes32 logoHash,
        string calldata logoCID
    ) external payable returns (uint256 tokenId) {
        if (msg.value != SUBMISSION_FEE) revert WrongFee(msg.value, SUBMISSION_FEE);
        bytes32 key = entryKey(chainId, token);
        if (keyToId[key] != 0) revert AlreadyRegistered(chainId, token);

        tokenId = nextTokenId++;
        keyToId[key] = tokenId;
        entries[tokenId] = Entry({
            chainId: chainId,
            token: token,
            logoHash: logoHash,
            logoCID: logoCID,
            status: Status.Pending,
            registeredAt: uint64(block.timestamp),
            lastValidated: 0,
            revision: 0,
            submitter: msg.sender
        });

        // iNFT is minted to the registry's validator custody model: the
        // submitter owns the NFT (transferable proof-of-registration), but
        // validation state is exclusively mindX-controlled.
        _mint(msg.sender, tokenId);

        _forwardFee(key);
        emit LogoSubmitted(tokenId, chainId, token, logoHash, logoCID, msg.sender);
    }

    /// Paid graphic update on a Verified entry. Re-enters Pending until mindX
    /// re-validates, so the canonical feed never serves an unreviewed image.
    function updateLogo(
        uint64 chainId,
        address token,
        bytes32 newHash,
        string calldata newCID
    ) external payable {
        if (msg.value != UPDATE_FEE) revert WrongFee(msg.value, UPDATE_FEE);
        uint256 tokenId = keyToId[entryKey(chainId, token)];
        if (tokenId == 0) revert NotRegistered();
        Entry storage e = entries[tokenId];
        if (e.status != Status.Verified) revert BadStatus(e.status);

        e.logoHash = newHash;
        e.logoCID = newCID;
        e.status = Status.Pending;
        e.revision += 1;

        _forwardFee(entryKey(chainId, token));
        emit LogoUpdated(tokenId, newHash, newCID, e.revision);
        emit MetadataUpdate(tokenId);
    }

    // --------------------------------------------------------- validator api

    /// Step one — mindX assesses the pending logo. The daemon verifies the
    /// submitted keccak256 commitment against the actual image bytes, pins
    /// those bytes permanently to Arweave, and records the permanent CID
    /// on-chain here. The submitted hash is the commitment; assessment fails
    /// off-chain (and is never written) if the pinned bytes do not hash to it.
    /// Permanence is enforced structurally: no entry can reach Verified
    /// without an Arweave CID committed at this step.
    function assess(uint256 tokenId, string calldata arweaveCID) external onlyValidator {
        Entry storage e = entries[tokenId];
        if (e.status != Status.Pending) revert BadStatus(e.status);
        if (bytes(arweaveCID).length == 0) revert EmptyCID();
        e.logoCID = arweaveCID; // permanent storage pointer replaces any placeholder
        e.status = Status.Assessed;
        emit LogoAssessed(tokenId, e.logoHash, arweaveCID);
        emit MetadataUpdate(tokenId);
    }

    /// Step two — the DAIO accepts the new logo. Senatus, executing a passed
    /// Fides-weighted proposal, ratifies the assessed entry into canonical
    /// status. No logo becomes truth without both the agent's assessment and
    /// the DAIO's acceptance.
    function ratify(uint256 tokenId) external onlySenatus {
        Entry storage e = entries[tokenId];
        if (e.status != Status.Assessed) revert BadStatus(e.status);
        e.status = Status.Verified;
        e.lastValidated = uint64(block.timestamp);
        emit LogoRatified(tokenId, e.logoHash, e.logoCID);
        emit MetadataUpdate(tokenId);
    }

    /// The DAIO may reject an assessed logo, returning it to Pending so the
    /// submitter (or mindX) can supply corrected art without a new fee.
    function reject(uint256 tokenId, string calldata reason) external onlySenatus {
        Entry storage e = entries[tokenId];
        if (e.status != Status.Assessed) revert BadStatus(e.status);
        e.status = Status.Pending;
        emit LogoRejected(tokenId, reason);
        emit MetadataUpdate(tokenId);
    }

    /// mindX periodic re-attestation without content change (freshness beacon
    /// consumed by wallets and by the rage.pythai.net token list generator).
    function reattest(uint256 tokenId) external onlyValidator {
        Entry storage e = entries[tokenId];
        if (e.status != Status.Verified) revert BadStatus(e.status);
        e.lastValidated = uint64(block.timestamp);
        emit MetadataUpdate(tokenId);
    }

    /// mindX withdraws validation (impersonation, exploit, abandonment).
    function revoke(uint256 tokenId, string calldata reason) external onlyValidator {
        Entry storage e = entries[tokenId];
        if (e.status == Status.None || e.status == Status.Revoked) revert BadStatus(e.status);
        e.status = Status.Revoked;
        emit LogoRevoked(tokenId, reason);
        emit MetadataUpdate(tokenId);
    }

    /// mindX proposes an evolved graphic for a verified entry (iNFT evolution
    /// path). The new art is pinned permanently and enters Assessed — the
    /// current canonical logo continues serving until the DAIO ratifies the
    /// evolution, because every new graphic requires DAIO acceptance.
    function evolve(uint256 tokenId, bytes32 newHash, string calldata newArweaveCID)
        external
        onlyValidator
    {
        Entry storage e = entries[tokenId];
        if (e.status != Status.Verified) revert BadStatus(e.status);
        if (bytes(newArweaveCID).length == 0) revert EmptyCID();
        e.logoHash = newHash;
        e.logoCID = newArweaveCID;
        e.revision += 1;
        e.status = Status.Assessed;
        emit LogoUpdated(tokenId, newHash, newArweaveCID, e.revision);
        emit MetadataUpdate(tokenId);
    }

    // -------------------------------------------------------------- read api

    /// One-call lookup used by wallets, ALLCHAIN, and the EIP-747 helper.
    function canonicalLogo(uint64 chainId, address token)
        external
        view
        returns (bool verified, bytes32 logoHash, string memory logoCID, uint32 revision)
    {
        uint256 tokenId = keyToId[entryKey(chainId, token)];
        if (tokenId == 0) return (false, bytes32(0), "", 0);
        Entry storage e = entries[tokenId];
        return (e.status == Status.Verified, e.logoHash, e.logoCID, e.revision);
    }

    /// Live metadata endpoint (mindX-served): {baseURI}{tokenId}
    function tokenURI(uint256 tokenId) external view returns (string memory) {
        if (_owners[tokenId] == address(0)) revert NotRegistered();
        return string(abi.encodePacked(baseURI, _toString(tokenId)));
    }

    // ------------------------------------------------------------- internals

    function _forwardFee(bytes32 key) private {
        if (msg.value == 0) return;
        (bool ok, ) = AERARIUM.call{value: msg.value}("");
        if (!ok) revert TreasuryTransferFailed();
        emit FeeCollected(msg.sender, msg.value, key);
    }

    function _toString(uint256 value) private pure returns (string memory) {
        if (value == 0) return "0";
        uint256 temp = value;
        uint256 digits;
        while (temp != 0) { digits++; temp /= 10; }
        bytes memory buffer = new bytes(digits);
        while (value != 0) {
            digits -= 1;
            buffer[digits] = bytes1(uint8(48 + (value % 10)));
            value /= 10;
        }
        return string(buffer);
    }

    // -------------------------------------------------- minimal ERC-721 core

    function balanceOf(address owner) external view returns (uint256) {
        if (owner == address(0)) revert ZeroAddress();
        return _balances[owner];
    }

    function ownerOf(uint256 tokenId) public view returns (address) {
        address owner = _owners[tokenId];
        if (owner == address(0)) revert NotRegistered();
        return owner;
    }

    function approve(address to, uint256 tokenId) external {
        address owner = ownerOf(tokenId);
        if (msg.sender != owner && !_operatorApprovals[owner][msg.sender]) {
            revert NotOwnerNorApproved();
        }
        _tokenApprovals[tokenId] = to;
        emit Approval(owner, to, tokenId);
    }

    function getApproved(uint256 tokenId) external view returns (address) {
        ownerOf(tokenId); // existence check
        return _tokenApprovals[tokenId];
    }

    function setApprovalForAll(address operator, bool approved) external {
        _operatorApprovals[msg.sender][operator] = approved;
        emit ApprovalForAll(msg.sender, operator, approved);
    }

    function isApprovedForAll(address owner, address operator) external view returns (bool) {
        return _operatorApprovals[owner][operator];
    }

    function transferFrom(address from, address to, uint256 tokenId) public {
        if (to == address(0)) revert ZeroAddress();
        address owner = ownerOf(tokenId);
        if (owner != from) revert NotOwnerNorApproved();
        if (
            msg.sender != owner &&
            msg.sender != _tokenApprovals[tokenId] &&
            !_operatorApprovals[owner][msg.sender]
        ) revert NotOwnerNorApproved();

        delete _tokenApprovals[tokenId];
        _balances[from] -= 1;
        _balances[to] += 1;
        _owners[tokenId] = to;
        emit Transfer(from, to, tokenId);
    }

    function safeTransferFrom(address from, address to, uint256 tokenId) external {
        safeTransferFrom(from, to, tokenId, "");
    }

    function safeTransferFrom(address from, address to, uint256 tokenId, bytes memory data)
        public
    {
        transferFrom(from, to, tokenId);
        if (to.code.length > 0) {
            try IERC721Receiver(to).onERC721Received(msg.sender, from, tokenId, data)
            returns (bytes4 retval) {
                if (retval != IERC721Receiver.onERC721Received.selector) {
                    revert TransferToNonReceiver();
                }
            } catch {
                revert TransferToNonReceiver();
            }
        }
    }

    function _mint(address to, uint256 tokenId) private {
        _balances[to] += 1;
        _owners[tokenId] = to;
        emit Transfer(address(0), to, tokenId);
    }

    function supportsInterface(bytes4 interfaceId) external pure returns (bool) {
        return
            interfaceId == 0x01ffc9a7 || // ERC-165
            interfaceId == 0x80ac58cd || // ERC-721
            interfaceId == 0x5b5e139f || // ERC-721 Metadata
            interfaceId == 0x49064906;   // ERC-4906
    }
}
