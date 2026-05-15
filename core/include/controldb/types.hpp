#pragma once
#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// Core domain types used throughout the C++ engine.
// Field names mirror the SQLAlchemy models exactly so the Python layer
// can serialise/deserialise without any transformation.
// ---------------------------------------------------------------------------

using Json = std::string;   // Serialised JSON blob
using Seq  = int64_t;       // Global monotonic sequence number

// SHA-256 hash string in "sha256:<hex>" format.
using HashStr = std::string;

struct Organization {
    std::string org_id;
    std::string name;
    int64_t     created_at_ms = 0;  // Unix epoch milliseconds
};

struct Project {
    std::string project_id;
    std::string org_id;
    std::string name;
    int64_t     created_at_ms = 0;
};

struct ApiKey {
    std::string key_id;
    std::string api_key_hash;
    std::string org_id;
    std::string project_id;
    std::string name;
    Json        scopes;         // JSON object
    int64_t     created_at_ms = 0;
    int64_t     revoked_at_ms = 0;  // 0 = active
};

struct AgentRun {
    std::string run_id;
    std::string org_id;
    std::string project_id;
    std::string environment;
    std::string agent_id;
    std::string agent_version;
    std::string status;         // running|committed|rolled_back|failed|requires_approval
    Json        metadata_json;
    int64_t     started_at_ms = 0;
    int64_t     ended_at_ms   = 0;  // 0 = still running
    HashStr     last_event_hash;
    int64_t     event_count   = 0;
};

struct AuditEvent {
    Seq         sequence       = 0;   // Global auto-increment (primary ordering key)
    std::string event_id;
    std::string org_id;
    std::string project_id;
    std::string environment;
    std::string run_id;
    std::string step_id;
    int32_t     step_index     = -1;  // -1 = absent
    std::string event_type;
    std::string schema_version;
    std::string actor_type;
    std::string actor_id;
    std::string trace_id;
    std::string span_id;
    std::string parent_span_id;
    std::string payload_mode;   // metadata_only|hash_only|redacted_payload|full_payload|self_hosted_payload
    Json        payload;        // "" = absent
    HashStr     payload_hash;
    HashStr     previous_event_hash;
    HashStr     event_hash;     // sha256(canonical_json(envelope \ {event_hash}))
    int64_t     occurred_at_ms = 0;
    int64_t     ingested_at_ms = 0;
    std::string idempotency_key;
};

struct Approval {
    std::string approval_id;
    std::string run_id;
    std::string org_id;
    std::string project_id;
    std::string policy_id;
    std::string requested_by;
    std::string reviewer_id;
    std::string status;         // requested|approved|rejected|expired|overridden
    std::string reason;
    Json        input_json;
    int64_t     created_at_ms  = 0;
    int64_t     decided_at_ms  = 0;  // 0 = not yet decided
};

struct EvidenceExport {
    std::string export_id;
    std::string run_id;
    std::string org_id;
    std::string project_id;
    std::string bundle_uri;
    HashStr     bundle_hash;
    int64_t     event_count        = 0;
    bool        hash_chain_valid   = true;
    std::string generated_by;
    int64_t     generated_at_ms    = 0;
};

// ---------------------------------------------------------------------------
// Verification result
// ---------------------------------------------------------------------------

struct VerificationResult {
    std::string run_id;
    bool        valid              = true;
    int64_t     event_count        = 0;
    std::string broken_at_event_id;  // "" = chain intact
};

// ---------------------------------------------------------------------------
// Query filter
// ---------------------------------------------------------------------------

struct QueryFilter {
    std::string              org_id;
    std::string              project_id;
    std::optional<std::string> run_id;
    std::vector<std::string>   event_types;  // empty = all
    int32_t                  limit    = 200;
    std::optional<Seq>       cursor;         // sequence > cursor
};

} // namespace controldb
