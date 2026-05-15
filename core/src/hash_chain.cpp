#include "controldb/hash_chain.hpp"

#include <algorithm>
#include <iomanip>
#include <iterator>
#include <openssl/sha.h>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace controldb {

// ---------------------------------------------------------------------------
// Minimal canonical JSON encoder
// Rules mirror Python json.dumps(sort_keys=True, separators=(",",":")):
//  - Objects: keys sorted lexicographically, no spaces
//  - Arrays: no spaces
//  - Strings: minimal escaping (only required by JSON spec)
//  - null/true/false/numbers: bare keywords/digits
// ---------------------------------------------------------------------------

namespace {

// Forward declaration
std::string encode_value(const std::string& json_fragment);

// Escape a raw C++ string for use as a JSON string value.
// Produces the minimal set of escapes required by RFC 8259 §7.
std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 2);
    out += '"';
    for (unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20) {
                    // Control characters: \uXXXX
                    char buf[8];
                    snprintf(buf, sizeof(buf), "\\u%04x", (unsigned)c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    out += '"';
    return out;
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// canonical_json_event — builds the canonical JSON for an AuditEvent.
//
// The field order matches alphabetical key sort that Python's sort_keys=True
// produces. We hand-code this for the AuditEvent struct to avoid pulling in
// a full JSON library at hash time (keeps the hot path dependency-free).
//
// IMPORTANT: every field that participates in hashing must appear here, in
// exactly the same format as the Python encoder in hashing.py.
// ---------------------------------------------------------------------------

std::string canonical_json_event(const AuditEvent& ev) {
    // Helper: append a key:value pair to builder
    auto kv_str = [](const std::string& k, const std::string& v) -> std::string {
        return json_escape(k) + ":" + json_escape(v);
    };
    auto kv_int = [](const std::string& k, int64_t v) -> std::string {
        return json_escape(k) + ":" + std::to_string(v);
    };
    auto kv_null = [](const std::string& k) -> std::string {
        return json_escape(k) + ":null";
    };
    auto kv_raw = [](const std::string& k, const std::string& raw) -> std::string {
        // raw must already be valid JSON
        return json_escape(k) + ":" + raw;
    };

    // We include ONLY the fields that the Python envelope dict includes
    // (see ingest.py _persist_event — the `envelope` dict).
    // Fields are ordered alphabetically (sort_keys=True).
    //
    // Python envelope keys (alphabetical):
    //   actor, environment, event_id, event_type, idempotency_key,
    //   ingested_at, occurred_at, org_id, parent_span_id, payload,
    //   payload_hash, payload_mode, previous_event_hash, project_id,
    //   run_id, schema_version, span_id, step_id, step_index, trace_id

    std::vector<std::string> pairs;
    pairs.reserve(20);

    // actor: null or {"type":"...","id":"..."}
    if (ev.actor_type.empty() && ev.actor_id.empty()) {
        pairs.push_back(kv_null("actor"));
    } else {
        std::string actor_json = "{" +
            json_escape("id") + ":" + json_escape(ev.actor_id) + "," +
            json_escape("type") + ":" + json_escape(ev.actor_type) +
            "}";
        pairs.push_back(kv_raw("actor", actor_json));
    }

    pairs.push_back(kv_str("environment", ev.environment));
    pairs.push_back(kv_str("event_id",    ev.event_id));
    pairs.push_back(kv_str("event_type",  ev.event_type));

    if (ev.idempotency_key.empty())
        pairs.push_back(kv_null("idempotency_key"));
    else
        pairs.push_back(kv_str("idempotency_key", ev.idempotency_key));

    // ingested_at / occurred_at: ISO-8601 UTC strings (stored as ms since epoch)
    auto ms_to_iso = [](int64_t ms) -> std::string {
        time_t sec = static_cast<time_t>(ms / 1000);
        int millis = static_cast<int>(ms % 1000);
        struct tm t;
        gmtime_r(&sec, &t);
        char buf[32];
        snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d.%03dZ",
                 t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
                 t.tm_hour, t.tm_min, t.tm_sec, millis);
        return std::string(buf);
    };

    pairs.push_back(kv_str("ingested_at", ms_to_iso(ev.ingested_at_ms)));
    pairs.push_back(kv_str("occurred_at", ms_to_iso(ev.occurred_at_ms)));
    pairs.push_back(kv_str("org_id",      ev.org_id));

    if (ev.parent_span_id.empty())
        pairs.push_back(kv_null("parent_span_id"));
    else
        pairs.push_back(kv_str("parent_span_id", ev.parent_span_id));

    // payload: null or raw JSON blob
    if (ev.payload.empty() || ev.payload == "null")
        pairs.push_back(kv_null("payload"));
    else
        pairs.push_back(kv_raw("payload", ev.payload));

    if (ev.payload_hash.empty())
        pairs.push_back(kv_null("payload_hash"));
    else
        pairs.push_back(kv_str("payload_hash", ev.payload_hash));

    pairs.push_back(kv_str("payload_mode", ev.payload_mode));

    if (ev.previous_event_hash.empty())
        pairs.push_back(kv_null("previous_event_hash"));
    else
        pairs.push_back(kv_str("previous_event_hash", ev.previous_event_hash));

    pairs.push_back(kv_str("project_id",      ev.project_id));
    pairs.push_back(kv_str("run_id",          ev.run_id));
    pairs.push_back(kv_str("schema_version",  ev.schema_version));

    if (ev.span_id.empty())
        pairs.push_back(kv_null("span_id"));
    else
        pairs.push_back(kv_str("span_id", ev.span_id));

    if (ev.step_id.empty())
        pairs.push_back(kv_null("step_id"));
    else
        pairs.push_back(kv_str("step_id", ev.step_id));

    if (ev.step_index < 0)
        pairs.push_back(kv_null("step_index"));
    else
        pairs.push_back(kv_int("step_index", ev.step_index));

    if (ev.trace_id.empty())
        pairs.push_back(kv_null("trace_id"));
    else
        pairs.push_back(kv_str("trace_id", ev.trace_id));

    // Build "{key1:val1,key2:val2,...}"
    std::string result = "{";
    for (size_t i = 0; i < pairs.size(); ++i) {
        if (i > 0) result += ',';
        result += pairs[i];
    }
    result += '}';
    return result;
}

// ---------------------------------------------------------------------------
// SHA-256 via OpenSSL
// ---------------------------------------------------------------------------

HashStr sha256_hex(const std::string& data) {
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256(reinterpret_cast<const unsigned char*>(data.data()),
           data.size(), digest);

    std::ostringstream oss;
    oss << "sha256:";
    for (int i = 0; i < SHA256_DIGEST_LENGTH; ++i)
        oss << std::hex << std::setw(2) << std::setfill('0')
            << static_cast<int>(digest[i]);
    return oss.str();
}

HashStr compute_event_hash(const AuditEvent& ev) {
    // Exclude ev.event_hash from the canonical JSON — compute on everything else.
    // canonical_json_event() already excludes the event_hash field.
    return sha256_hex(canonical_json_event(ev));
}

HashStr compute_payload_hash(const std::string& payload_json) {
    // If payload is absent/null use "{}"  (matches Python compute_payload_hash)
    const std::string& effective = (payload_json.empty() || payload_json == "null")
                                   ? std::string("{}") : payload_json;
    return sha256_hex(effective);
}

// ---------------------------------------------------------------------------
// Chain verification
// ---------------------------------------------------------------------------

VerificationResult verify_chain(const std::vector<AuditEvent>& events,
                                const std::string& run_id) {
    VerificationResult result;
    result.run_id      = run_id;
    result.event_count = static_cast<int64_t>(events.size());
    result.valid       = true;

    std::string expected_prev;  // starts as empty (first event has null prev)

    for (const auto& ev : events) {
        // 1. Check previous_event_hash chain continuity
        if (ev.previous_event_hash != expected_prev) {
            result.valid               = false;
            result.broken_at_event_id  = ev.event_id;
            return result;
        }

        // 2. Recompute event_hash and compare to stored value
        const HashStr recomputed = compute_event_hash(ev);
        if (recomputed != ev.event_hash) {
            result.valid               = false;
            result.broken_at_event_id  = ev.event_id;
            return result;
        }

        expected_prev = ev.event_hash;
    }
    return result;
}

} // namespace controldb
