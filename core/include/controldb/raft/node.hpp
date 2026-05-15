#pragma once
#include "log.hpp"
#include "transport.hpp"
#include <atomic>
#include <condition_variable>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace controldb::raft {

// ---------------------------------------------------------------------------
// RaftNode — complete Raft consensus implementation (Raft paper §5–§6).
//
// Roles:   Follower → Candidate → Leader
// Timers:  Election timeout (150–300ms random), Heartbeat interval (50ms)
// Quorum:  majority(cluster_size) acknowledgements required to commit
//
// State machine integration:
//   When an entry is committed (committed_index advances), apply_fn is called
//   with the command string. The caller (EventStore) applies the mutation
//   to RocksDB inside apply_fn.
// ---------------------------------------------------------------------------

enum class Role { Follower, Candidate, Leader };

struct PeerInfo {
    std::string node_id;
    std::string address;  // "host:port" for TCP transport
};

struct RaftConfig {
    std::string              node_id;
    std::vector<PeerInfo>    peers;
    std::string              log_dir;
    int64_t                  election_timeout_min_ms  = 150;
    int64_t                  election_timeout_max_ms  = 300;
    int64_t                  heartbeat_interval_ms    = 50;
    bool                     single_node_mode         = false; // skip elections
};

class RaftNode {
public:
    using ApplyFn = std::function<void(uint64_t index, const std::string& command)>;

    explicit RaftNode(RaftConfig cfg,
                      std::unique_ptr<Transport> transport,
                      ApplyFn apply_fn);
    ~RaftNode();

    // Start background election + heartbeat threads.
    void start();

    // Gracefully stop all background threads.
    void stop();

    // Propose a command (must be called on leader; throws if not leader).
    // Blocks until the entry is committed on a quorum.
    void propose(const std::string& command);

    // Non-blocking propose; returns false if not leader.
    bool try_propose(const std::string& command);

    // Current role.
    Role role() const;

    // Returns the current leader node_id, or "" if unknown.
    std::string leader_id() const;

    bool is_leader() const { return role() == Role::Leader; }

    uint64_t current_term()    const;
    uint64_t committed_index() const;
    uint64_t last_log_index()  const;

    // ---- RPC handlers (called by Transport) --------------------------------

    RequestVoteReply   handle_request_vote(const RequestVoteArgs& args);
    AppendEntriesReply handle_append_entries(const AppendEntriesArgs& args);

private:
    struct State;
    std::unique_ptr<State> s_;

    void election_loop();
    void heartbeat_loop();
    void run_election();
    void send_heartbeats();
    void replicate_to_peer(const PeerInfo& peer);
    void advance_commit_index();
    void apply_committed();
    void reset_election_timer();

    bool has_quorum(int vote_count) const;
};

} // namespace controldb::raft
