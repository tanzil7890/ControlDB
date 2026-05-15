#pragma once
#include "types.hpp"
#include <string>

namespace controldb {

// ---------------------------------------------------------------------------
// Canonical JSON + SHA-256 hash chain.
//
// Rules must be byte-identical to the Python implementation:
//   canonical_json(v) == json.dumps(v, sort_keys=True, separators=(",",":"))
//   sha256_hex(s)     == "sha256:" + hashlib.sha256(s.encode()).hexdigest()
//
// The event hash covers the full envelope EXCLUDING the event_hash field:
//   event_hash = sha256_hex(canonical_json({...envelope..., event_hash: omitted}))
// ---------------------------------------------------------------------------

// Produce deterministic JSON from an AuditEvent envelope fields.
// Keys are sorted; no spaces; ASCII-safe string escaping.
std::string canonical_json_event(const AuditEvent& ev);

// SHA-256 hex string with "sha256:" prefix.
HashStr sha256_hex(const std::string& data);

// Compute the event_hash for ev (excludes ev.event_hash from input).
HashStr compute_event_hash(const AuditEvent& ev);

// Compute sha256 of canonical JSON of a raw JSON blob.
HashStr compute_payload_hash(const std::string& payload_json);

// ---------------------------------------------------------------------------
// Verify the full hash chain for a sequence of ordered events.
// Returns VerificationResult with valid=true when all hashes match.
// ---------------------------------------------------------------------------
VerificationResult verify_chain(const std::vector<AuditEvent>& events,
                                const std::string& run_id);

} // namespace controldb
