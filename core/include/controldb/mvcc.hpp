#pragma once
#include "types.hpp"
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// MVCC read view — wraps RocksDB Snapshot for consistent point-in-time reads.
//
// Callers obtain a ReadView, then issue reads against it. All reads in a
// given ReadView see the DB state at the moment the view was opened, even
// if concurrent writes commit after that point.
//
// Usage:
//   auto view = store.read_view();   // Opens snapshot
//   auto run  = view.get_run("r1");  // Consistent read
//   // view destructor releases snapshot
// ---------------------------------------------------------------------------

class EventStore;  // forward

class ReadView {
public:
    // Constructed by EventStore::read_view().
    explicit ReadView(std::shared_ptr<void> snapshot_handle,
                      const EventStore& store);
    ~ReadView();

    std::optional<AgentRun>    get_run(const std::string& run_id)       const;
    std::optional<AuditEvent>  get_event(Seq seq)                        const;
    std::vector<AuditEvent>    events_for_run(const std::string& run_id,
                                               int32_t limit = 1000,
                                               std::optional<Seq> cursor = std::nullopt) const;
    std::optional<Approval>    get_approval(const std::string& approval_id) const;
    std::vector<Approval>      pending_approvals(const std::string& org_id,
                                                 const std::string& project_id) const;

private:
    std::shared_ptr<void> snapshot_handle_;
    const EventStore& store_;
};

} // namespace controldb
