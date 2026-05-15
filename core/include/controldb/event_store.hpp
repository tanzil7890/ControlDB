#pragma once
#include "types.hpp"
#include "wal.hpp"
#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// RocksDB column-family layout
//
//  CF "meta"          : "global_seq"        → uint64 (8-byte BE)
//                       "schema_version"    → string
//                       "wal_applied_seq"   → uint64 (8-byte BE)
//
//  CF "runs"          : run_id              → AgentRun JSON
//  CF "runs_by_org"   : org_id:proj_id:seq  → run_id  (secondary index)
//
//  CF "events"        : seq_be(8)           → AuditEvent JSON  (append-only)
//  CF "ev_by_run"     : run_id:seq_be(8)    → ""               (index)
//  CF "ev_by_type"    : org:proj:type:seq   → ""               (index)
//  CF "idempotency"   : idempotency_key     → event_id         (dedup)
//
//  CF "approvals"     : approval_id         → Approval JSON
//  CF "exports"       : export_id           → EvidenceExport JSON
//  CF "api_keys"      : api_key_hash        → ApiKey JSON
//  CF "orgs"          : org_id             → Organization JSON
//  CF "projects"      : project_id         → Project JSON
// ---------------------------------------------------------------------------

struct InsertEventResult {
    std::string event_id;
    HashStr     event_hash;
    bool        was_duplicate = false;
};

struct TimelineResult {
    AgentRun                 run;
    std::vector<AuditEvent>  events;
    std::optional<Seq>       next_cursor;
};

struct QueryResult {
    std::vector<AuditEvent> events;
    std::optional<Seq>      next_cursor;
};

class EventStore {
public:
    // Opens (or creates) the RocksDB database at db_path.
    // wal_dir is the directory for the application WAL.
    explicit EventStore(const std::string& db_path,
                        const std::string& wal_dir = "");
    ~EventStore();

    // Non-copyable; moveable.
    EventStore(const EventStore&) = delete;
    EventStore& operator=(const EventStore&) = delete;

    // ---- Bootstrap ---------------------------------------------------------

    void bootstrap(const std::string& org_id,
                   const std::string& project_id,
                   const std::vector<std::string>& api_keys);

    // ---- Org / Project / ApiKey --------------------------------------------

    void put_org(const Organization& org);
    std::optional<Organization> get_org(const std::string& org_id) const;

    void put_project(const Project& proj);
    std::optional<Project> get_project(const std::string& project_id) const;

    void put_api_key(const ApiKey& key);
    std::optional<ApiKey> get_api_key_by_hash(const std::string& hash) const;

    // ---- Agent Runs --------------------------------------------------------

    // Idempotent: returns existing run when run_id already present.
    AgentRun start_run(const AgentRun& run);

    AgentRun commit_run(const std::string& run_id,
                        const std::string& org_id,
                        const std::string& project_id);

    AgentRun rollback_run(const std::string& run_id,
                          const std::string& org_id,
                          const std::string& project_id,
                          const std::string& reason = "");

    std::optional<AgentRun> get_run(const std::string& run_id) const;

    // List runs for org/project, newest first.
    std::vector<AgentRun> list_runs(const std::string& org_id,
                                    const std::string& project_id,
                                    int32_t limit = 50) const;

    // ---- Events (Append-Only) ----------------------------------------------

    // Append a batch of events to run_id.
    // Each call is atomic: all events succeed or none do.
    std::vector<InsertEventResult> append_events(
        const std::string& run_id,
        const std::string& org_id,
        const std::string& project_id,
        std::vector<AuditEvent> events);  // mutable: fills event_hash, seq

    // Paginated event timeline for a run (ordered by sequence).
    TimelineResult timeline(const std::string& run_id,
                            const std::string& org_id,
                            const std::string& project_id,
                            int32_t limit = 1000,
                            std::optional<Seq> cursor = std::nullopt) const;

    // Cross-run filtered query.
    QueryResult query(const QueryFilter& filter) const;

    // Hash-chain verification (CPU-bound, no extra DB reads needed).
    VerificationResult verify(const std::string& run_id,
                              const std::string& org_id,
                              const std::string& project_id) const;

    // ---- Approvals ---------------------------------------------------------

    Approval upsert_approval(const Approval& approval);
    Approval update_approval_status(const std::string& approval_id,
                                    const std::string& org_id,
                                    const std::string& project_id,
                                    const std::string& status,
                                    const std::string& reviewer_id,
                                    const std::string& reason = "");
    std::optional<Approval> get_approval(const std::string& approval_id) const;
    std::vector<Approval> pending_approvals(const std::string& org_id,
                                            const std::string& project_id) const;

    // ---- Evidence Exports --------------------------------------------------

    void put_export(const EvidenceExport& exp);
    std::optional<EvidenceExport> get_export(const std::string& export_id) const;
    std::vector<EvidenceExport> list_exports(const std::string& org_id,
                                             const std::string& project_id,
                                             int32_t limit = 50) const;

    // ---- Snapshots & Compaction --------------------------------------------

    // Create a RocksDB Checkpoint at snapshot_dir.
    void create_snapshot(const std::string& snapshot_dir) const;

    // Compact all column families (reduces space amplification).
    void compact_all();

    // Return human-readable stats (column-family sizes, write amplification).
    std::string stats() const;

    // Current global sequence number.
    Seq current_seq() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace controldb
