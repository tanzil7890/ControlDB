#pragma once
#include <memory>
#include <string>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// Snapshotting — wraps RocksDB Checkpoint API.
//
// A checkpoint is a point-in-time, consistent, hard-linked copy of the DB
// files at snapshot_dir. It can be used for:
//   - Backup to remote object storage
//   - Bootstrapping new Raft replicas
//   - Point-in-time recovery
// ---------------------------------------------------------------------------

struct SnapshotMeta {
    std::string snapshot_id;
    std::string snapshot_dir;
    int64_t     created_at_ms  = 0;
    int64_t     sequence_number = 0;  // RocksDB sequence number at snapshot time
    uint64_t    size_bytes     = 0;
};

class SnapshotManager {
public:
    explicit SnapshotManager(const std::string& snapshots_base_dir);
    ~SnapshotManager();

    // Create a new checkpoint. Returns the snapshot metadata.
    SnapshotMeta create(const std::string& db_path);

    // List all available snapshots (sorted by creation time, newest first).
    std::vector<SnapshotMeta> list() const;

    // Delete a snapshot by ID.
    void remove(const std::string& snapshot_id);

    // Return path to the latest snapshot, or "" if none.
    std::string latest_dir() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace controldb
