#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace controldb::raft {

// ---------------------------------------------------------------------------
// Raft RPC message types (Raft paper §5).
// ---------------------------------------------------------------------------

struct RequestVoteArgs {
    uint64_t term            = 0;
    std::string candidate_id;
    uint64_t last_log_index  = 0;
    uint64_t last_log_term   = 0;
};

struct RequestVoteReply {
    uint64_t term            = 0;
    bool     vote_granted    = false;
};

struct AppendEntriesArgs {
    uint64_t    term           = 0;
    std::string leader_id;
    uint64_t    prev_log_index = 0;
    uint64_t    prev_log_term  = 0;
    std::vector<std::string> entries;  // serialised LogEntry JSON array
    uint64_t    leader_commit  = 0;
};

struct AppendEntriesReply {
    uint64_t term            = 0;
    bool     success         = false;
    uint64_t conflict_index  = 0;  // Optimised conflict resolution (§5.3)
    uint64_t conflict_term   = 0;
};

// ---------------------------------------------------------------------------
// Transport interface — pluggable to allow in-process testing or TCP.
// ---------------------------------------------------------------------------

class Transport {
public:
    virtual ~Transport() = default;

    // Send RequestVote RPC to peer_id. Returns reply or throws on timeout.
    virtual RequestVoteReply send_request_vote(
        const std::string& peer_id,
        const RequestVoteArgs& args) = 0;

    // Send AppendEntries RPC (also serves as heartbeat when entries empty).
    virtual AppendEntriesReply send_append_entries(
        const std::string& peer_id,
        const AppendEntriesArgs& args) = 0;

    // Start listening for incoming RPCs on this node.
    // Handlers are set via register_*_handler().
    virtual void start(const std::string& listen_addr) = 0;
    virtual void stop() = 0;

    using VoteHandler   = std::function<RequestVoteReply(const RequestVoteArgs&)>;
    using AppendHandler = std::function<AppendEntriesReply(const AppendEntriesArgs&)>;

    virtual void register_vote_handler(VoteHandler h)     = 0;
    virtual void register_append_handler(AppendHandler h) = 0;
};

// TCP transport implementation.
std::unique_ptr<Transport> make_tcp_transport(const std::string& node_id);

// In-process transport for single-binary testing.
std::unique_ptr<Transport> make_inprocess_transport(const std::string& node_id);

} // namespace controldb::raft
