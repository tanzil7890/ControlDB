#pragma once
#include "types.hpp"
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// Application-level Write-Ahead Log (WAL).
//
// Every mutation is appended here BEFORE it is applied to RocksDB.
// On crash, replay() re-applies all committed entries whose RocksDB write
// did not complete.
//
// On-disk format (each entry):
//   [8-byte BE seq][4-byte BE payload_len][payload JSON][4-byte BE CRC32]
//
// RocksDB already has its own internal WAL; this layer provides:
//   - Explicit sequence numbers for Raft log entries
//   - Crash-recovery replay without reading RocksDB
//   - Audit of every mutation for debugging
// ---------------------------------------------------------------------------

enum class WalOpType : uint8_t {
    INSERT_RUN      = 1,
    UPDATE_RUN      = 2,
    INSERT_EVENT    = 3,
    INSERT_APPROVAL = 4,
    UPDATE_APPROVAL = 5,
    INSERT_EXPORT   = 6,
    CHECKPOINT      = 7,  // Marks a safe snapshot point
};

struct WalEntry {
    uint64_t   wal_seq   = 0;
    WalOpType  op_type;
    uint64_t   timestamp_ms = 0;
    std::string payload_json;  // Serialised domain object
    uint32_t   crc32     = 0;
};

class Wal {
public:
    explicit Wal(const std::string& wal_dir);
    ~Wal();

    // Append an entry and fsync. Returns the assigned WAL sequence number.
    uint64_t append(WalOpType op, const std::string& payload_json);

    // Mark wal_seq as applied (allows truncation up to this point).
    void mark_applied(uint64_t wal_seq);

    // Read all entries with wal_seq > after_seq for crash recovery.
    std::vector<WalEntry> read_unapplied(uint64_t after_seq = 0) const;

    // Truncate entries that have been applied and snapshotted.
    void truncate_applied();

    // Last WAL sequence number successfully appended.
    uint64_t last_seq() const;

    // Last WAL sequence number marked applied.
    uint64_t applied_seq() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace controldb
