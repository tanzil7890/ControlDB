#include "controldb/raft/node.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <filesystem>
#include <fstream>
#include <future>
#include <mutex>
#include <random>
#include <stdexcept>
#include <thread>
#include <unordered_map>

namespace controldb::raft {

using Clock    = std::chrono::steady_clock;
using TimePoint = Clock::time_point;
using ms        = std::chrono::milliseconds;

// ---------------------------------------------------------------------------
// Persistent state on disk: current_term, voted_for
// ---------------------------------------------------------------------------

struct PersistentState {
    uint64_t    current_term = 0;
    std::string voted_for;   // "" = not voted this term

    void load(const std::string& path) {
        std::ifstream f(path);
        if (!f) return;
        f >> current_term;
        if (!(f >> voted_for)) voted_for = "";
    }
    void save(const std::string& path) const {
        std::ofstream f(path, std::ios::trunc);
        f << current_term << "\n" << voted_for << "\n";
        f.flush();
    }
};

// ---------------------------------------------------------------------------
// Match/next indices per peer (leader state)
// ---------------------------------------------------------------------------

struct PeerState {
    uint64_t next_index  = 1;   // Index of next entry to send
    uint64_t match_index = 0;   // Highest index known to be replicated
};

// ---------------------------------------------------------------------------
// RaftNode::State
// ---------------------------------------------------------------------------

struct RaftNode::State {
    RaftConfig   cfg;
    ApplyFn      apply_fn;
    std::unique_ptr<Transport> transport;
    std::unique_ptr<RaftLog>   log;
    PersistentState             ps;
    std::string                 ps_path;

    mutable std::mutex mu;
    std::condition_variable cv_commit;    // Notified when committed_index advances
    std::condition_variable cv_apply;

    std::atomic<Role>        role_        {Role::Follower};
    std::atomic<bool>        running_     {false};
    std::atomic<uint64_t>    committed_index_  {0};
    std::atomic<uint64_t>    last_applied_     {0};

    std::string              leader_id_;
    TimePoint                election_deadline_;
    std::unordered_map<std::string, PeerState> peers_;  // leader-only

    std::thread election_thread_;
    std::thread heartbeat_thread_;
    std::thread apply_thread_;

    std::random_device rd;
    std::mt19937       rng{rd()};

    int64_t random_election_timeout() {
        std::uniform_int_distribution<int64_t> dist(
            cfg.election_timeout_min_ms, cfg.election_timeout_max_ms);
        return dist(rng);
    }

    void reset_election_deadline() {
        election_deadline_ = Clock::now() + ms(random_election_timeout());
    }

    void persist() { ps.save(ps_path); }

    void become_follower(uint64_t term) {
        ps.current_term = term;
        ps.voted_for    = "";
        persist();
        role_.store(Role::Follower);
        leader_id_ = "";
    }

    bool try_grant_vote(const RequestVoteArgs& args) {
        // §5.2: grant if (no vote yet or voted for this candidate) AND candidate log up-to-date
        if (args.term < ps.current_term) return false;
        if (!ps.voted_for.empty() && ps.voted_for != args.candidate_id) return false;
        bool up_to_date = log->is_up_to_date(args.last_log_term, args.last_log_index);
        if (!up_to_date) return false;
        ps.voted_for = args.candidate_id;
        persist();
        reset_election_deadline();
        return true;
    }

    // Advance committed_index if a quorum has replicated up to an index.
    void try_advance_commit(uint64_t leader_commit) {
        // Leader uses peer match_index to determine quorum
        if (role_.load() != Role::Leader) {
            if (leader_commit > committed_index_.load()) {
                uint64_t last = std::min(leader_commit, log->last_index());
                committed_index_.store(last);
                cv_commit.notify_all();
            }
            return;
        }
        // Leader: find highest N such that majority match_index >= N and log[N].term == current_term
        std::vector<uint64_t> match_vals;
        match_vals.push_back(log->last_index());  // leader itself
        for (auto& [id, ps_] : peers_) match_vals.push_back(ps_.match_index);
        std::sort(match_vals.rbegin(), match_vals.rend());
        size_t majority = (cfg.peers.size() + 1) / 2 + 1;  // including self
        if (majority > match_vals.size()) majority = match_vals.size();
        uint64_t new_commit = match_vals[majority - 1];
        if (new_commit > committed_index_.load() &&
            log->term_at(new_commit) == ps.current_term) {
            committed_index_.store(new_commit);
            cv_commit.notify_all();
        }
    }
};

// ---------------------------------------------------------------------------
// Constructor / Destructor
// ---------------------------------------------------------------------------

RaftNode::RaftNode(RaftConfig cfg,
                   std::unique_ptr<Transport> transport,
                   ApplyFn apply_fn)
    : s_(std::make_unique<State>()) {
    s_->cfg       = std::move(cfg);
    s_->transport = std::move(transport);
    s_->apply_fn  = std::move(apply_fn);

    std::filesystem::create_directories(s_->cfg.log_dir);
    s_->ps_path = s_->cfg.log_dir + "/raft_state.txt";
    s_->ps.load(s_->ps_path);

    s_->log = std::make_unique<RaftLog>(s_->cfg.log_dir + "/raft.log");
    s_->reset_election_deadline();

    for (const auto& peer : s_->cfg.peers)
        s_->peers_[peer.node_id] = PeerState{s_->log->last_index() + 1, 0};
}

RaftNode::~RaftNode() { stop(); }

// ---------------------------------------------------------------------------
// Start / Stop
// ---------------------------------------------------------------------------

void RaftNode::start() {
    if (s_->running_.exchange(true)) return;

    // Register RPC handlers
    s_->transport->register_vote_handler(
        [this](const RequestVoteArgs& a){ return handle_request_vote(a); });
    s_->transport->register_append_handler(
        [this](const AppendEntriesArgs& a){ return handle_append_entries(a); });

    if (!s_->cfg.cfg.peers.empty())
        s_->transport->start("0.0.0.0:0");  // Actual port in RaftConfig.peers[self]

    if (s_->cfg.single_node_mode) {
        // Skip elections — immediately become leader
        std::lock_guard<std::mutex> lock(s_->mu);
        s_->ps.current_term++;
        s_->persist();
        s_->role_.store(Role::Leader);
        s_->leader_id_ = s_->cfg.node_id;
    } else {
        s_->election_thread_  = std::thread([this]{ election_loop(); });
        s_->heartbeat_thread_ = std::thread([this]{ heartbeat_loop(); });
    }
    s_->apply_thread_ = std::thread([this]{ apply_committed(); });
}

void RaftNode::stop() {
    if (!s_->running_.exchange(false)) return;
    s_->cv_commit.notify_all();
    s_->cv_apply.notify_all();
    if (!s_->cfg.single_node_mode) {
        if (s_->election_thread_.joinable())  s_->election_thread_.join();
        if (s_->heartbeat_thread_.joinable()) s_->heartbeat_thread_.join();
    }
    if (s_->apply_thread_.joinable()) s_->apply_thread_.join();
    s_->transport->stop();
}

// ---------------------------------------------------------------------------
// Propose (leader only)
// ---------------------------------------------------------------------------

void RaftNode::propose(const std::string& command) {
    if (!s_->running_.load()) throw std::runtime_error("Raft not running");
    if (s_->cfg.single_node_mode) {
        std::lock_guard<std::mutex> lock(s_->mu);
        uint64_t idx = s_->log->append(s_->ps.current_term, command);
        s_->committed_index_.store(idx);
        s_->cv_commit.notify_all();
        return;
    }
    if (s_->role_.load() != Role::Leader)
        throw std::runtime_error("not leader; current leader: " + s_->leader_id_);

    uint64_t log_index;
    {
        std::lock_guard<std::mutex> lock(s_->mu);
        log_index = s_->log->append(s_->ps.current_term, command);
    }

    // Wait until committed
    std::unique_lock<std::mutex> lock(s_->mu);
    s_->cv_commit.wait_for(lock, ms(5000), [&]{
        return s_->committed_index_.load() >= log_index || !s_->running_.load();
    });
    if (s_->committed_index_.load() < log_index)
        throw std::runtime_error("Raft propose timeout");
}

bool RaftNode::try_propose(const std::string& command) {
    if (s_->role_.load() != Role::Leader &&
        !s_->cfg.single_node_mode) return false;
    try { propose(command); return true; }
    catch (...) { return false; }
}

// ---------------------------------------------------------------------------
// Election loop (follower/candidate)
// ---------------------------------------------------------------------------

void RaftNode::election_loop() {
    while (s_->running_.load()) {
        std::unique_lock<std::mutex> lock(s_->mu);
        auto deadline = s_->election_deadline_;
        lock.unlock();

        auto now = Clock::now();
        if (now >= deadline && s_->role_.load() != Role::Leader) {
            run_election();
        }
        std::this_thread::sleep_for(ms(10));
    }
}

void RaftNode::run_election() {
    std::lock_guard<std::mutex> lock(s_->mu);
    if (s_->role_.load() == Role::Leader) return;

    s_->ps.current_term++;
    s_->ps.voted_for = s_->cfg.node_id;
    s_->persist();
    s_->role_.store(Role::Candidate);
    s_->reset_election_deadline();

    uint64_t term      = s_->ps.current_term;
    uint64_t last_idx  = s_->log->last_index();
    uint64_t last_term = s_->log->last_term();

    std::atomic<int> votes{1};  // vote for self
    std::vector<std::future<void>> futures;

    for (const auto& peer : s_->cfg.peers) {
        futures.push_back(std::async(std::launch::async,
        [&, peer_id = peer.node_id]() {
            RequestVoteArgs args{term, s_->cfg.node_id, last_idx, last_term};
            try {
                auto reply = s_->transport->send_request_vote(peer_id, args);
                std::lock_guard<std::mutex> l(s_->mu);
                if (reply.term > s_->ps.current_term) {
                    s_->become_follower(reply.term);
                    return;
                }
                if (reply.vote_granted) votes.fetch_add(1);
            } catch (...) {}
        }));
    }

    for (auto& f : futures) f.wait();

    std::lock_guard<std::mutex> lock2(s_->mu);
    if (s_->role_.load() != Role::Candidate) return;
    if (has_quorum(votes.load())) {
        s_->role_.store(Role::Leader);
        s_->leader_id_ = s_->cfg.node_id;
        // Reinitialise peer indices
        for (auto& [id, ps_] : s_->peers_) {
            ps_.next_index  = s_->log->last_index() + 1;
            ps_.match_index = 0;
        }
    } else {
        s_->become_follower(s_->ps.current_term);
    }
}

// ---------------------------------------------------------------------------
// Heartbeat loop (leader)
// ---------------------------------------------------------------------------

void RaftNode::heartbeat_loop() {
    while (s_->running_.load()) {
        if (s_->role_.load() == Role::Leader) {
            send_heartbeats();
        }
        std::this_thread::sleep_for(ms(s_->cfg.heartbeat_interval_ms));
    }
}

void RaftNode::send_heartbeats() {
    for (const auto& peer : s_->cfg.peers) {
        std::thread([this, peer]{ replicate_to_peer(peer); }).detach();
    }
}

void RaftNode::replicate_to_peer(const PeerInfo& peer) {
    if (s_->role_.load() != Role::Leader) return;

    uint64_t next_idx, prev_idx, prev_term, leader_commit, term;
    std::vector<std::string> entries_json;
    {
        std::lock_guard<std::mutex> lock(s_->mu);
        if (s_->peers_.find(peer.node_id) == s_->peers_.end()) return;
        next_idx     = s_->peers_[peer.node_id].next_index;
        prev_idx     = next_idx - 1;
        prev_term    = s_->log->term_at(prev_idx);
        leader_commit = s_->committed_index_.load();
        term         = s_->ps.current_term;
        auto entries = s_->log->get_range(next_idx, s_->log->last_index());
        for (const auto& e : entries)
            entries_json.push_back(e.command);
    }

    AppendEntriesArgs args{term, s_->cfg.node_id, prev_idx,
                           prev_term, entries_json, leader_commit};
    try {
        auto reply = s_->transport->send_append_entries(peer.node_id, args);
        std::lock_guard<std::mutex> lock(s_->mu);
        if (reply.term > s_->ps.current_term) {
            s_->become_follower(reply.term);
            return;
        }
        if (reply.success) {
            uint64_t new_match = prev_idx + entries_json.size();
            s_->peers_[peer.node_id].match_index = new_match;
            s_->peers_[peer.node_id].next_index  = new_match + 1;
            s_->try_advance_commit(leader_commit);
        } else {
            // Back off next_index for fast catch-up
            uint64_t ni = s_->peers_[peer.node_id].next_index;
            if (ni > 1) s_->peers_[peer.node_id].next_index = ni - 1;
        }
    } catch (...) {}
}

// ---------------------------------------------------------------------------
// Apply committed entries to the state machine
// ---------------------------------------------------------------------------

void RaftNode::apply_committed() {
    while (s_->running_.load()) {
        std::unique_lock<std::mutex> lock(s_->mu);
        s_->cv_commit.wait_for(lock, ms(100), [&]{
            return s_->committed_index_.load() > s_->last_applied_.load() ||
                   !s_->running_.load();
        });
        uint64_t committed = s_->committed_index_.load();
        uint64_t applied   = s_->last_applied_.load();
        lock.unlock();

        for (uint64_t idx = applied + 1; idx <= committed; ++idx) {
            auto entry = s_->log->get(idx);
            if (!entry) continue;
            s_->apply_fn(idx, entry->command);
            s_->last_applied_.store(idx);
        }
    }
}

// ---------------------------------------------------------------------------
// RPC handlers
// ---------------------------------------------------------------------------

RequestVoteReply RaftNode::handle_request_vote(const RequestVoteArgs& args) {
    std::lock_guard<std::mutex> lock(s_->mu);
    if (args.term > s_->ps.current_term) s_->become_follower(args.term);

    RequestVoteReply reply{s_->ps.current_term, false};
    if (args.term < s_->ps.current_term) return reply;

    reply.vote_granted = s_->try_grant_vote(args);
    return reply;
}

AppendEntriesReply RaftNode::handle_append_entries(const AppendEntriesArgs& args) {
    std::lock_guard<std::mutex> lock(s_->mu);
    AppendEntriesReply reply{s_->ps.current_term, false, 0, 0};

    if (args.term < s_->ps.current_term) return reply;
    if (args.term > s_->ps.current_term) s_->become_follower(args.term);

    // Recognise valid leader
    s_->leader_id_ = args.leader_id;
    s_->role_.store(Role::Follower);
    s_->reset_election_deadline();

    // Consistency check: prev_log_index / prev_log_term
    if (args.prev_log_index > 0) {
        if (s_->log->last_index() < args.prev_log_index) {
            reply.conflict_index = s_->log->last_index() + 1;
            return reply;
        }
        if (s_->log->term_at(args.prev_log_index) != args.prev_log_term) {
            reply.conflict_term  = s_->log->term_at(args.prev_log_index);
            reply.conflict_index = args.prev_log_index;
            s_->log->truncate_suffix(args.prev_log_index - 1);
            return reply;
        }
    }

    // Append new entries
    uint64_t insert_index = args.prev_log_index + 1;
    std::vector<LogEntry> new_entries;
    for (size_t i = 0; i < args.entries.size(); ++i) {
        uint64_t idx = insert_index + i;
        if (idx <= s_->log->last_index()) {
            if (s_->log->term_at(idx) != args.term) {
                s_->log->truncate_suffix(idx - 1);
            } else {
                continue;  // already have this entry
            }
        }
        LogEntry e;
        e.index   = idx;
        e.term    = args.term;
        e.command = args.entries[i];
        new_entries.push_back(std::move(e));
    }
    if (!new_entries.empty()) s_->log->append_batch(new_entries);

    s_->try_advance_commit(args.leader_commit);
    reply.success = true;
    return reply;
}

// ---------------------------------------------------------------------------
// Accessors
// ---------------------------------------------------------------------------

Role     RaftNode::role()            const { return s_->role_.load(); }
std::string RaftNode::leader_id()   const {
    std::lock_guard<std::mutex> l(s_->mu); return s_->leader_id_; }
uint64_t RaftNode::current_term()   const { return s_->ps.current_term; }
uint64_t RaftNode::committed_index() const { return s_->committed_index_.load(); }
uint64_t RaftNode::last_log_index() const { return s_->log->last_index(); }

bool RaftNode::has_quorum(int vote_count) const {
    size_t cluster = s_->cfg.peers.size() + 1;  // +1 for self
    return vote_count > (int)(cluster / 2);
}

void RaftNode::reset_election_timer() { s_->reset_election_deadline(); }
void RaftNode::advance_commit_index()  {}  // called internally

} // namespace controldb::raft
