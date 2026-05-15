#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace controldb::raft {

// ---------------------------------------------------------------------------
// Raft log entry.
// ---------------------------------------------------------------------------

struct LogEntry {
    uint64_t    index   = 0;    // 1-based log index (Raft paper §5.3)
    uint64_t    term    = 0;    // Leader term when entry was created
    std::string command;        // Serialised WAL operation (JSON)
    std::string entry_hash;     // sha256 of (index|term|command) for integrity
};

// ---------------------------------------------------------------------------
// Raft Log — persistent, append-only sequence of log entries.
//
// Backed by a single append-only file at log_path:
//   [8-byte BE index][8-byte BE term][4-byte BE cmd_len][command][4-byte CRC32]
//
// The in-memory index vector provides O(1) random access by log index.
// ---------------------------------------------------------------------------

class RaftLog {
public:
    explicit RaftLog(const std::string& log_path);
    ~RaftLog();

    // Append a new entry (returns its log index).
    uint64_t append(uint64_t term, const std::string& command);

    // Append multiple entries atomically (used when follower receives
    // AppendEntries with multiple entries).
    void append_batch(const std::vector<LogEntry>& entries);

    // Truncate log to keep only entries with index <= keep_index.
    // Used when follower receives conflicting entries from a new leader.
    void truncate_suffix(uint64_t keep_index);

    // Read a specific entry by 1-based index.
    std::optional<LogEntry> get(uint64_t index) const;

    // Range read [from_index, to_index] inclusive.
    std::vector<LogEntry> get_range(uint64_t from_index,
                                    uint64_t to_index) const;

    // Last log entry index (0 if empty).
    uint64_t last_index() const;

    // Term of the last entry (0 if empty).
    uint64_t last_term() const;

    // Term for a given index (0 if out of range).
    uint64_t term_at(uint64_t index) const;

    // True if (last_log_term, last_log_index) is at least as up-to-date
    // as this log — used in RequestVote RPC check (§5.4.1).
    bool is_up_to_date(uint64_t last_log_term,
                        uint64_t last_log_index) const;

    // Number of entries.
    uint64_t size() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace controldb::raft
