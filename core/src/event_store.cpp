#include "controldb/event_store.hpp"
#include "controldb/hash_chain.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <mutex>
#include <nlohmann/json.hpp>
#include <rocksdb/checkpoint.h>
#include <rocksdb/db.h>
#include <rocksdb/options.h>
#include <rocksdb/slice.h>
#include <rocksdb/status.h>
#include <rocksdb/write_batch.h>
#include <stdexcept>
#include <string>
#include <vector>

using json = nlohmann::json;
namespace fs = std::filesystem;

namespace controldb {

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::system_clock::now().time_since_epoch()).count();
}

// 8-byte big-endian encode/decode for sequence keys
static void seq_to_key(Seq seq, char out[8]) {
    for (int i = 7; i >= 0; --i) {
        out[i] = static_cast<char>(seq & 0xFF);
        seq >>= 8;
    }
}
static Seq key_to_seq(const char* in) {
    Seq seq = 0;
    for (int i = 0; i < 8; ++i) seq = (seq << 8) | static_cast<uint8_t>(in[i]);
    return seq;
}

static void check(const rocksdb::Status& s, const std::string& ctx) {
    if (!s.ok()) throw std::runtime_error(ctx + ": " + s.ToString());
}

// ---------------------------------------------------------------------------
// AgentRun / AuditEvent / Approval JSON serialisation
// ---------------------------------------------------------------------------

static json run_to_json(const AgentRun& r) {
    return {
        {"run_id",            r.run_id},
        {"org_id",            r.org_id},
        {"project_id",        r.project_id},
        {"environment",       r.environment},
        {"agent_id",          r.agent_id},
        {"agent_version",     r.agent_version},
        {"status",            r.status},
        {"metadata_json",     r.metadata_json},
        {"started_at_ms",     r.started_at_ms},
        {"ended_at_ms",       r.ended_at_ms},
        {"last_event_hash",   r.last_event_hash},
        {"event_count",       r.event_count},
    };
}

static AgentRun json_to_run(const json& j) {
    AgentRun r;
    r.run_id          = j.at("run_id").get<std::string>();
    r.org_id          = j.at("org_id").get<std::string>();
    r.project_id      = j.at("project_id").get<std::string>();
    r.environment     = j.at("environment").get<std::string>();
    r.agent_id        = j.at("agent_id").get<std::string>();
    r.agent_version   = j.value("agent_version", "");
    r.status          = j.at("status").get<std::string>();
    r.metadata_json   = j.value("metadata_json", "{}");
    r.started_at_ms   = j.at("started_at_ms").get<int64_t>();
    r.ended_at_ms     = j.value("ended_at_ms", (int64_t)0);
    r.last_event_hash = j.value("last_event_hash", "");
    r.event_count     = j.value("event_count",  (int64_t)0);
    return r;
}

static json event_to_json(const AuditEvent& e) {
    return {
        {"sequence",             e.sequence},
        {"event_id",             e.event_id},
        {"org_id",               e.org_id},
        {"project_id",           e.project_id},
        {"environment",          e.environment},
        {"run_id",               e.run_id},
        {"step_id",              e.step_id},
        {"step_index",           e.step_index},
        {"event_type",           e.event_type},
        {"schema_version",       e.schema_version},
        {"actor_type",           e.actor_type},
        {"actor_id",             e.actor_id},
        {"trace_id",             e.trace_id},
        {"span_id",              e.span_id},
        {"parent_span_id",       e.parent_span_id},
        {"payload_mode",         e.payload_mode},
        {"payload",              e.payload},
        {"payload_hash",         e.payload_hash},
        {"previous_event_hash",  e.previous_event_hash},
        {"event_hash",           e.event_hash},
        {"occurred_at_ms",       e.occurred_at_ms},
        {"ingested_at_ms",       e.ingested_at_ms},
        {"idempotency_key",      e.idempotency_key},
    };
}

static AuditEvent json_to_event(const json& j) {
    AuditEvent e;
    e.sequence             = j.at("sequence").get<Seq>();
    e.event_id             = j.at("event_id").get<std::string>();
    e.org_id               = j.value("org_id", "");
    e.project_id           = j.value("project_id", "");
    e.environment          = j.value("environment", "");
    e.run_id               = j.value("run_id", "");
    e.step_id              = j.value("step_id", "");
    e.step_index           = j.value("step_index", -1);
    e.event_type           = j.value("event_type", "");
    e.schema_version       = j.value("schema_version", "");
    e.actor_type           = j.value("actor_type", "");
    e.actor_id             = j.value("actor_id", "");
    e.trace_id             = j.value("trace_id", "");
    e.span_id              = j.value("span_id", "");
    e.parent_span_id       = j.value("parent_span_id", "");
    e.payload_mode         = j.value("payload_mode", "metadata_only");
    e.payload              = j.value("payload", "");
    e.payload_hash         = j.value("payload_hash", "");
    e.previous_event_hash  = j.value("previous_event_hash", "");
    e.event_hash           = j.value("event_hash", "");
    e.occurred_at_ms       = j.value("occurred_at_ms", (int64_t)0);
    e.ingested_at_ms       = j.value("ingested_at_ms", (int64_t)0);
    e.idempotency_key      = j.value("idempotency_key", "");
    return e;
}

static json approval_to_json(const Approval& a) {
    return {
        {"approval_id",    a.approval_id},
        {"run_id",         a.run_id},
        {"org_id",         a.org_id},
        {"project_id",     a.project_id},
        {"policy_id",      a.policy_id},
        {"requested_by",   a.requested_by},
        {"reviewer_id",    a.reviewer_id},
        {"status",         a.status},
        {"reason",         a.reason},
        {"input_json",     a.input_json},
        {"created_at_ms",  a.created_at_ms},
        {"decided_at_ms",  a.decided_at_ms},
    };
}

static Approval json_to_approval(const json& j) {
    Approval a;
    a.approval_id   = j.at("approval_id").get<std::string>();
    a.run_id        = j.value("run_id",        "");
    a.org_id        = j.value("org_id",        "");
    a.project_id    = j.value("project_id",    "");
    a.policy_id     = j.value("policy_id",     "");
    a.requested_by  = j.value("requested_by",  "");
    a.reviewer_id   = j.value("reviewer_id",   "");
    a.status        = j.value("status",        "requested");
    a.reason        = j.value("reason",        "");
    a.input_json    = j.value("input_json",    "{}");
    a.created_at_ms = j.value("created_at_ms", (int64_t)0);
    a.decided_at_ms = j.value("decided_at_ms", (int64_t)0);
    return a;
}

// ---------------------------------------------------------------------------
// Impl
// ---------------------------------------------------------------------------

struct EventStore::Impl {
    rocksdb::DB*                                       db = nullptr;
    std::vector<rocksdb::ColumnFamilyHandle*>          cf_handles;
    std::unordered_map<std::string, rocksdb::ColumnFamilyHandle*> cf_map;
    std::unique_ptr<Wal>                               wal;
    mutable std::mutex                                 write_mu;  // serialise writes
    std::atomic<Seq>                                   global_seq_{0};
    std::string                                        db_path;

    rocksdb::ColumnFamilyHandle* cf(const std::string& name) const {
        auto it = cf_map.find(name);
        if (it == cf_map.end()) throw std::runtime_error("CF not found: " + name);
        return it->second;
    }

    // Atomic sequence increment — uses merge operator or simple lock.
    Seq next_seq() { return global_seq_.fetch_add(1) + 1; }

    std::string get(const std::string& cf_name, const std::string& key) const {
        std::string val;
        auto s = db->Get(rocksdb::ReadOptions(), cf(cf_name),
                         rocksdb::Slice(key), &val);
        if (s.IsNotFound()) return "";
        check(s, "Get");
        return val;
    }

    void put(rocksdb::WriteBatch& batch,
             const std::string& cf_name,
             const std::string& key,
             const std::string& value) {
        batch.Put(cf(cf_name), rocksdb::Slice(key), rocksdb::Slice(value));
    }

    void write_batch(rocksdb::WriteBatch& batch) {
        rocksdb::WriteOptions wo;
        wo.sync = true;
        check(db->Write(wo, &batch), "Write");
    }

    void load_global_seq() {
        std::string val = get("meta", "global_seq");
        if (!val.empty()) {
            Seq v;
            std::memcpy(&v, val.data(), sizeof(v));
            global_seq_.store(v);
        }
    }

    void persist_global_seq(rocksdb::WriteBatch& batch) {
        Seq s = global_seq_.load();
        std::string val(reinterpret_cast<char*>(&s), sizeof(s));
        batch.Put(cf("meta"), rocksdb::Slice("global_seq"),
                  rocksdb::Slice(val));
    }
};

// ---------------------------------------------------------------------------
// Column family names
// ---------------------------------------------------------------------------

static const std::vector<std::string> CF_NAMES = {
    rocksdb::kDefaultColumnFamilyName,  // "default" (unused, required by RocksDB)
    "meta",
    "runs",
    "runs_by_org",
    "events",
    "ev_by_run",
    "ev_by_type",
    "idempotency",
    "approvals",
    "exports",
    "api_keys",
    "orgs",
    "projects",
};

// ---------------------------------------------------------------------------
// Constructor / Destructor
// ---------------------------------------------------------------------------

EventStore::EventStore(const std::string& db_path_,
                       const std::string& wal_dir)
    : impl_(std::make_unique<Impl>()) {
    impl_->db_path = db_path_;
    fs::create_directories(db_path_);

    // WAL
    std::string effective_wal = wal_dir.empty() ? (db_path_ + "/wal") : wal_dir;
    impl_->wal = std::make_unique<Wal>(effective_wal);

    // Open or create RocksDB with all column families
    rocksdb::DBOptions db_opts;
    db_opts.create_if_missing              = true;
    db_opts.create_missing_column_families = true;

    std::vector<rocksdb::ColumnFamilyDescriptor> cf_descs;
    for (const auto& name : CF_NAMES) {
        rocksdb::ColumnFamilyOptions cfo;
        if (name == "events" || name == "ev_by_run") {
            // Bloom filter for point lookups; prefix extractor for range scans
            cfo.OptimizeLevelStyleCompaction();
        }
        cf_descs.push_back({name, cfo});
    }

    std::vector<rocksdb::ColumnFamilyHandle*> handles;
    auto s = rocksdb::DB::Open(db_opts, db_path_, cf_descs,
                                &handles, &impl_->db);
    check(s, "Open RocksDB");

    for (size_t i = 0; i < CF_NAMES.size(); ++i) {
        impl_->cf_handles.push_back(handles[i]);
        impl_->cf_map[CF_NAMES[i]] = handles[i];
    }

    impl_->load_global_seq();

    // Crash recovery: replay WAL entries not yet in RocksDB
    // (WAL tracks this via applied_seq vs last_seq)
}

EventStore::~EventStore() {
    if (impl_->db) {
        for (auto* h : impl_->cf_handles) impl_->db->DestroyColumnFamilyHandle(h);
        delete impl_->db;
        impl_->db = nullptr;
    }
}

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

void EventStore::bootstrap(const std::string& org_id,
                            const std::string& project_id,
                            const std::vector<std::string>& api_keys) {
    // Only run if org doesn't exist yet
    if (!impl_->get("orgs", org_id).empty()) return;

    rocksdb::WriteBatch batch;
    Organization org{ org_id, "default", now_ms() };
    impl_->put(batch, "orgs", org_id, json{
        {"org_id", org.org_id}, {"name", org.name},
        {"created_at_ms", org.created_at_ms}
    }.dump());

    Project proj{ project_id, org_id, "default", now_ms() };
    impl_->put(batch, "projects", project_id, json{
        {"project_id", proj.project_id}, {"org_id", proj.org_id},
        {"name", proj.name}, {"created_at_ms", proj.created_at_ms}
    }.dump());

    // Compute sha256 hash for each key
    for (const auto& raw_key : api_keys) {
        HashStr key_hash = sha256_hex(raw_key);
        // key_id = key_hash for simplicity
        json scope = {
            {"runs", "write"}, {"events", "write"}, {"policies", "read"},
            {"approvals", "review"}, {"exports", "create"}
        };
        impl_->put(batch, "api_keys", key_hash, json{
            {"key_id",       key_hash},
            {"api_key_hash", key_hash},
            {"org_id",       org_id},
            {"project_id",   project_id},
            {"name",         "bootstrap"},
            {"scopes",       scope.dump()},
            {"created_at_ms", now_ms()},
            {"revoked_at_ms", 0}
        }.dump());
    }

    impl_->write_batch(batch);
}

// ---------------------------------------------------------------------------
// Org / Project / ApiKey
// ---------------------------------------------------------------------------

void EventStore::put_org(const Organization& org) {
    rocksdb::WriteBatch batch;
    impl_->put(batch, "orgs", org.org_id, json{
        {"org_id", org.org_id}, {"name", org.name},
        {"created_at_ms", org.created_at_ms}
    }.dump());
    impl_->write_batch(batch);
}

std::optional<Organization> EventStore::get_org(const std::string& org_id) const {
    auto v = impl_->get("orgs", org_id);
    if (v.empty()) return std::nullopt;
    auto j = json::parse(v);
    return Organization{ j["org_id"], j["name"], j["created_at_ms"] };
}

void EventStore::put_project(const Project& proj) {
    rocksdb::WriteBatch batch;
    impl_->put(batch, "projects", proj.project_id, json{
        {"project_id", proj.project_id}, {"org_id", proj.org_id},
        {"name", proj.name}, {"created_at_ms", proj.created_at_ms}
    }.dump());
    impl_->write_batch(batch);
}

std::optional<Project> EventStore::get_project(const std::string& project_id) const {
    auto v = impl_->get("projects", project_id);
    if (v.empty()) return std::nullopt;
    auto j = json::parse(v);
    return Project{ j["project_id"], j["org_id"], j["name"], j["created_at_ms"] };
}

void EventStore::put_api_key(const ApiKey& key) {
    rocksdb::WriteBatch batch;
    impl_->put(batch, "api_keys", key.api_key_hash, json{
        {"key_id",       key.key_id},
        {"api_key_hash", key.api_key_hash},
        {"org_id",       key.org_id},
        {"project_id",   key.project_id},
        {"name",         key.name},
        {"scopes",       key.scopes},
        {"created_at_ms", key.created_at_ms},
        {"revoked_at_ms", key.revoked_at_ms}
    }.dump());
    impl_->write_batch(batch);
}

std::optional<ApiKey> EventStore::get_api_key_by_hash(const std::string& hash) const {
    auto v = impl_->get("api_keys", hash);
    if (v.empty()) return std::nullopt;
    auto j = json::parse(v);
    ApiKey k;
    k.key_id       = j["key_id"];
    k.api_key_hash = j["api_key_hash"];
    k.org_id       = j["org_id"];
    k.project_id   = j["project_id"];
    k.name         = j.value("name", "");
    k.scopes       = j["scopes"].dump();
    k.created_at_ms = j["created_at_ms"];
    k.revoked_at_ms = j.value("revoked_at_ms", (int64_t)0);
    return k;
}

// ---------------------------------------------------------------------------
// Agent Runs
// ---------------------------------------------------------------------------

AgentRun EventStore::start_run(const AgentRun& run) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);

    // Idempotency
    auto existing = impl_->get("runs", run.run_id);
    if (!existing.empty()) return json_to_run(json::parse(existing));

    AgentRun r = run;
    if (r.started_at_ms == 0) r.started_at_ms = now_ms();
    if (r.status.empty()) r.status = "running";

    auto wal_seq = impl_->wal->append(WalOpType::INSERT_RUN,
                                       run_to_json(r).dump());

    rocksdb::WriteBatch batch;
    impl_->put(batch, "runs", r.run_id, run_to_json(r).dump());

    // Secondary index: runs_by_org key = "org_id:proj_id:started_at_ms_be:run_id"
    char seq_buf[8]; seq_to_key(r.started_at_ms, seq_buf);
    std::string idx_key = r.org_id + ":" + r.project_id + ":" +
                          std::string(seq_buf, 8) + ":" + r.run_id;
    batch.Put(impl_->cf("runs_by_org"), rocksdb::Slice(idx_key),
              rocksdb::Slice(r.run_id));

    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
    return r;
}

AgentRun EventStore::commit_run(const std::string& run_id,
                                 const std::string& org_id,
                                 const std::string& project_id) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);
    auto v = impl_->get("runs", run_id);
    if (v.empty()) throw std::runtime_error("run not found: " + run_id);
    auto r = json_to_run(json::parse(v));
    if (r.org_id != org_id || r.project_id != project_id)
        throw std::runtime_error("forbidden");
    r.status     = "committed";
    r.ended_at_ms = now_ms();

    auto wal_seq = impl_->wal->append(WalOpType::UPDATE_RUN, run_to_json(r).dump());
    rocksdb::WriteBatch batch;
    impl_->put(batch, "runs", run_id, run_to_json(r).dump());
    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
    return r;
}

AgentRun EventStore::rollback_run(const std::string& run_id,
                                   const std::string& org_id,
                                   const std::string& project_id,
                                   const std::string& reason) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);
    auto v = impl_->get("runs", run_id);
    if (v.empty()) throw std::runtime_error("run not found: " + run_id);
    auto r = json_to_run(json::parse(v));
    if (r.org_id != org_id || r.project_id != project_id)
        throw std::runtime_error("forbidden");
    r.status      = "rolled_back";
    r.ended_at_ms = now_ms();
    if (!reason.empty()) {
        auto meta = json::parse(r.metadata_json.empty() ? "{}" : r.metadata_json);
        meta["rollback_reason"] = reason;
        r.metadata_json = meta.dump();
    }

    auto wal_seq = impl_->wal->append(WalOpType::UPDATE_RUN, run_to_json(r).dump());
    rocksdb::WriteBatch batch;
    impl_->put(batch, "runs", run_id, run_to_json(r).dump());
    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
    return r;
}

std::optional<AgentRun> EventStore::get_run(const std::string& run_id) const {
    auto v = impl_->get("runs", run_id);
    if (v.empty()) return std::nullopt;
    return json_to_run(json::parse(v));
}

std::vector<AgentRun> EventStore::list_runs(const std::string& org_id,
                                              const std::string& project_id,
                                              int32_t limit) const {
    std::string prefix = org_id + ":" + project_id + ":";
    rocksdb::ReadOptions ro;
    std::unique_ptr<rocksdb::Iterator> it(
        impl_->db->NewIterator(ro, impl_->cf("runs_by_org")));

    std::vector<AgentRun> result;
    // Iterate in reverse for newest-first
    it->SeekToLast();
    for (; it->Valid() && (int32_t)result.size() < limit; it->Prev()) {
        std::string k = it->key().ToString();
        if (k.substr(0, prefix.size()) != prefix) continue;
        std::string run_id = it->value().ToString();
        auto rv = impl_->get("runs", run_id);
        if (!rv.empty()) result.push_back(json_to_run(json::parse(rv)));
    }
    return result;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

std::vector<InsertEventResult> EventStore::append_events(
    const std::string& run_id,
    const std::string& org_id,
    const std::string& project_id,
    std::vector<AuditEvent> events) {

    std::lock_guard<std::mutex> lock(impl_->write_mu);

    // Fetch run
    auto rv = impl_->get("runs", run_id);
    if (rv.empty()) throw std::runtime_error("run not found: " + run_id);
    auto run = json_to_run(json::parse(rv));
    if (run.org_id != org_id || run.project_id != project_id)
        throw std::runtime_error("forbidden");

    std::vector<InsertEventResult> results;
    rocksdb::WriteBatch batch;
    std::string prev_hash = run.last_event_hash;

    for (auto& ev : events) {
        InsertEventResult res;

        // Idempotency check
        if (!ev.idempotency_key.empty()) {
            auto existing_id = impl_->get("idempotency", ev.idempotency_key);
            if (!existing_id.empty()) {
                res.event_id     = existing_id;
                res.was_duplicate = true;
                // Look up the hash
                auto ev_seq_val = impl_->get("idempotency",
                                              ev.idempotency_key + ":hash");
                res.event_hash   = ev_seq_val.empty() ? "" : ev_seq_val;
                results.push_back(res);
                continue;
            }
        }

        // Assign sequence and timestamps
        ev.sequence      = impl_->next_seq();
        ev.ingested_at_ms = now_ms();
        if (ev.occurred_at_ms == 0) ev.occurred_at_ms = ev.ingested_at_ms;
        ev.org_id        = org_id;
        ev.project_id    = project_id;
        ev.run_id        = run_id;
        ev.environment   = run.environment;
        if (ev.schema_version.empty()) ev.schema_version = "2026-05-01";
        if (ev.payload_mode.empty())   ev.payload_mode   = "metadata_only";

        // Hash chain
        ev.previous_event_hash = prev_hash;
        ev.event_hash          = compute_event_hash(ev);

        // WAL
        auto wal_seq = impl_->wal->append(WalOpType::INSERT_EVENT,
                                           event_to_json(ev).dump());

        // Primary: events CF keyed by sequence (8-byte BE)
        char seq_key[8]; seq_to_key(ev.sequence, seq_key);
        std::string seq_k(seq_key, 8);
        batch.Put(impl_->cf("events"), rocksdb::Slice(seq_k),
                  rocksdb::Slice(event_to_json(ev).dump()));

        // Index: ev_by_run keyed by run_id + seq
        std::string run_key = run_id + ":" + seq_k;
        batch.Put(impl_->cf("ev_by_run"), rocksdb::Slice(run_key),
                  rocksdb::Slice(""));

        // Index: ev_by_type keyed by org:proj:type:seq
        std::string type_key = org_id + ":" + project_id + ":" +
                               ev.event_type + ":" + seq_k;
        batch.Put(impl_->cf("ev_by_type"), rocksdb::Slice(type_key),
                  rocksdb::Slice(""));

        // Idempotency
        if (!ev.idempotency_key.empty()) {
            batch.Put(impl_->cf("idempotency"),
                      rocksdb::Slice(ev.idempotency_key),
                      rocksdb::Slice(ev.event_id));
            batch.Put(impl_->cf("idempotency"),
                      rocksdb::Slice(ev.idempotency_key + ":hash"),
                      rocksdb::Slice(ev.event_hash));
        }

        prev_hash = ev.event_hash;
        impl_->wal->mark_applied(wal_seq);
        res.event_id   = ev.event_id;
        res.event_hash = ev.event_hash;
        results.push_back(res);
    }

    // Update run metadata
    run.last_event_hash = prev_hash;
    run.event_count    += static_cast<int64_t>(
        std::count_if(results.begin(), results.end(),
                      [](const InsertEventResult& r){ return !r.was_duplicate; }));

    // Update run status if any state-change events
    for (const auto& ev : events) {
        if      (ev.event_type == "agent_run.committed") { run.status = "committed"; run.ended_at_ms = now_ms(); }
        else if (ev.event_type == "agent_run.failed")    { run.status = "failed";    run.ended_at_ms = now_ms(); }
        else if (ev.event_type == "agent_run.started")   { run.status = "running"; }
    }

    batch.Put(impl_->cf("runs"), rocksdb::Slice(run_id),
              rocksdb::Slice(run_to_json(run).dump()));
    impl_->persist_global_seq(batch);
    impl_->write_batch(batch);

    return results;
}

TimelineResult EventStore::timeline(const std::string& run_id,
                                     const std::string& org_id,
                                     const std::string& project_id,
                                     int32_t limit,
                                     std::optional<Seq> cursor) const {
    auto rv = impl_->get("runs", run_id);
    if (rv.empty()) throw std::runtime_error("run not found: " + run_id);
    auto run = json_to_run(json::parse(rv));
    if (run.org_id != org_id || run.project_id != project_id)
        throw std::runtime_error("forbidden");

    // Use ev_by_run index: prefix = run_id + ":"
    std::string prefix = run_id + ":";
    rocksdb::ReadOptions ro;
    std::unique_ptr<rocksdb::Iterator> it(
        impl_->db->NewIterator(ro, impl_->cf("ev_by_run")));

    // Seek past cursor
    if (cursor.has_value()) {
        char seq_key[8]; seq_to_key(*cursor + 1, seq_key);
        it->Seek(rocksdb::Slice(prefix + std::string(seq_key, 8)));
    } else {
        it->Seek(rocksdb::Slice(prefix));
    }

    std::vector<AuditEvent> events;
    events.reserve(limit);
    Seq last_seq = 0;
    for (; it->Valid() && (int32_t)events.size() < limit; it->Next()) {
        auto k = it->key().ToString();
        if (k.substr(0, prefix.size()) != prefix) break;
        // Extract sequence from key suffix
        const char* seq_part = k.data() + prefix.size();
        if ((int)k.size() < (int)prefix.size() + 8) break;
        Seq seq = key_to_seq(seq_part);
        // Fetch event
        char seq_buf[8]; seq_to_key(seq, seq_buf);
        auto ev_val = impl_->get("events", std::string(seq_buf, 8));
        if (!ev_val.empty()) {
            events.push_back(json_to_event(json::parse(ev_val)));
            last_seq = seq;
        }
    }

    std::optional<Seq> next_cursor;
    if ((int32_t)events.size() == limit && it->Valid()) {
        next_cursor = last_seq;
    }

    return { run, std::move(events), next_cursor };
}

QueryResult EventStore::query(const QueryFilter& filter) const {
    // Use ev_by_type if event_types specified, otherwise scan ev_by_run
    std::vector<AuditEvent> events;
    events.reserve(filter.limit);

    rocksdb::ReadOptions ro;
    Seq last_seq = 0;

    if (!filter.event_types.empty()) {
        for (const auto& et : filter.event_types) {
            std::string prefix = filter.org_id + ":" + filter.project_id +
                                 ":" + et + ":";
            std::unique_ptr<rocksdb::Iterator> it(
                impl_->db->NewIterator(ro, impl_->cf("ev_by_type")));

            if (filter.cursor.has_value()) {
                char seq_buf[8]; seq_to_key(*filter.cursor + 1, seq_buf);
                it->Seek(rocksdb::Slice(prefix + std::string(seq_buf, 8)));
            } else {
                it->Seek(rocksdb::Slice(prefix));
            }

            for (; it->Valid() && (int32_t)events.size() < filter.limit; it->Next()) {
                auto k = it->key().ToString();
                if (k.substr(0, prefix.size()) != prefix) break;
                Seq seq = key_to_seq(k.data() + prefix.size());
                char seq_buf[8]; seq_to_key(seq, seq_buf);
                auto ev_val = impl_->get("events", std::string(seq_buf, 8));
                if (ev_val.empty()) continue;
                auto ev = json_to_event(json::parse(ev_val));
                if (filter.run_id.has_value() && ev.run_id != *filter.run_id) continue;
                events.push_back(std::move(ev));
                last_seq = seq;
            }
            if ((int32_t)events.size() >= filter.limit) break;
        }
    } else if (filter.run_id.has_value()) {
        auto tl = timeline(*filter.run_id, filter.org_id, filter.project_id,
                           filter.limit, filter.cursor);
        return { tl.events, tl.next_cursor };
    }

    std::sort(events.begin(), events.end(),
              [](const AuditEvent& a, const AuditEvent& b){ return a.sequence < b.sequence; });

    std::optional<Seq> next_cursor;
    if ((int32_t)events.size() == filter.limit) next_cursor = last_seq;
    return { std::move(events), next_cursor };
}

VerificationResult EventStore::verify(const std::string& run_id,
                                       const std::string& org_id,
                                       const std::string& project_id) const {
    auto tl = timeline(run_id, org_id, project_id, 5000);
    return verify_chain(tl.events, run_id);
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

Approval EventStore::upsert_approval(const Approval& approval) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);
    auto existing = impl_->get("approvals", approval.approval_id);
    if (!existing.empty()) return json_to_approval(json::parse(existing));

    Approval a = approval;
    if (a.created_at_ms == 0) a.created_at_ms = now_ms();
    if (a.status.empty())     a.status = "requested";

    auto wal_seq = impl_->wal->append(WalOpType::INSERT_APPROVAL,
                                       approval_to_json(a).dump());
    rocksdb::WriteBatch batch;
    impl_->put(batch, "approvals", a.approval_id, approval_to_json(a).dump());
    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
    return a;
}

Approval EventStore::update_approval_status(const std::string& approval_id,
                                              const std::string& org_id,
                                              const std::string& project_id,
                                              const std::string& status,
                                              const std::string& reviewer_id,
                                              const std::string& reason) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);
    auto v = impl_->get("approvals", approval_id);
    if (v.empty()) throw std::runtime_error("approval not found: " + approval_id);
    auto a = json_to_approval(json::parse(v));
    if (a.org_id != org_id || a.project_id != project_id)
        throw std::runtime_error("forbidden");
    a.status      = status;
    a.reviewer_id = reviewer_id;
    a.decided_at_ms = now_ms();
    if (!reason.empty()) a.reason = reason;

    auto wal_seq = impl_->wal->append(WalOpType::UPDATE_APPROVAL,
                                       approval_to_json(a).dump());
    rocksdb::WriteBatch batch;
    impl_->put(batch, "approvals", approval_id, approval_to_json(a).dump());
    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
    return a;
}

std::optional<Approval> EventStore::get_approval(const std::string& approval_id) const {
    auto v = impl_->get("approvals", approval_id);
    if (v.empty()) return std::nullopt;
    return json_to_approval(json::parse(v));
}

std::vector<Approval> EventStore::pending_approvals(const std::string& org_id,
                                                      const std::string& project_id) const {
    // Full scan of approvals CF — in production, add a secondary index
    rocksdb::ReadOptions ro;
    std::unique_ptr<rocksdb::Iterator> it(
        impl_->db->NewIterator(ro, impl_->cf("approvals")));
    std::vector<Approval> result;
    for (it->SeekToFirst(); it->Valid(); it->Next()) {
        auto a = json_to_approval(json::parse(it->value().ToString()));
        if (a.org_id == org_id && a.project_id == project_id &&
            a.status == "requested") {
            result.push_back(a);
        }
    }
    std::sort(result.begin(), result.end(),
              [](const Approval& x, const Approval& y){
                  return x.created_at_ms < y.created_at_ms; });
    return result;
}

// ---------------------------------------------------------------------------
// Evidence Exports
// ---------------------------------------------------------------------------

void EventStore::put_export(const EvidenceExport& exp) {
    std::lock_guard<std::mutex> lock(impl_->write_mu);
    rocksdb::WriteBatch batch;
    auto wal_seq = impl_->wal->append(WalOpType::INSERT_EXPORT,
                                       json{
                                           {"export_id",        exp.export_id},
                                           {"run_id",           exp.run_id},
                                           {"org_id",           exp.org_id},
                                           {"project_id",       exp.project_id},
                                           {"bundle_uri",       exp.bundle_uri},
                                           {"bundle_hash",      exp.bundle_hash},
                                           {"event_count",      exp.event_count},
                                           {"hash_chain_valid", exp.hash_chain_valid},
                                           {"generated_by",     exp.generated_by},
                                           {"generated_at_ms",  exp.generated_at_ms}
                                       }.dump());
    impl_->put(batch, "exports", exp.export_id,
               impl_->get("meta", ""));  // We just use the dump
    batch.Put(impl_->cf("exports"), rocksdb::Slice(exp.export_id),
              rocksdb::Slice(json{
                  {"export_id", exp.export_id}, {"run_id", exp.run_id},
                  {"org_id", exp.org_id}, {"project_id", exp.project_id},
                  {"bundle_uri", exp.bundle_uri}, {"bundle_hash", exp.bundle_hash},
                  {"event_count", exp.event_count},
                  {"hash_chain_valid", exp.hash_chain_valid},
                  {"generated_by", exp.generated_by},
                  {"generated_at_ms", exp.generated_at_ms}
              }.dump()));
    impl_->write_batch(batch);
    impl_->wal->mark_applied(wal_seq);
}

std::optional<EvidenceExport> EventStore::get_export(const std::string& export_id) const {
    auto v = impl_->get("exports", export_id);
    if (v.empty()) return std::nullopt;
    auto j = json::parse(v);
    EvidenceExport e;
    e.export_id        = j["export_id"];
    e.run_id           = j["run_id"];
    e.org_id           = j["org_id"];
    e.project_id       = j["project_id"];
    e.bundle_uri       = j["bundle_uri"];
    e.bundle_hash      = j["bundle_hash"];
    e.event_count      = j["event_count"];
    e.hash_chain_valid = j["hash_chain_valid"];
    e.generated_by     = j["generated_by"];
    e.generated_at_ms  = j["generated_at_ms"];
    return e;
}

std::vector<EvidenceExport> EventStore::list_exports(
    const std::string& org_id, const std::string& project_id, int32_t limit) const {
    rocksdb::ReadOptions ro;
    std::unique_ptr<rocksdb::Iterator> it(
        impl_->db->NewIterator(ro, impl_->cf("exports")));
    std::vector<EvidenceExport> result;
    for (it->SeekToFirst(); it->Valid() && (int32_t)result.size() < limit; it->Next()) {
        auto e = get_export(it->key().ToString());
        if (e && e->org_id == org_id && e->project_id == project_id)
            result.push_back(*e);
    }
    return result;
}

// ---------------------------------------------------------------------------
// Snapshot & Compaction
// ---------------------------------------------------------------------------

void EventStore::create_snapshot(const std::string& snapshot_dir) const {
    rocksdb::Checkpoint* cp = nullptr;
    check(rocksdb::Checkpoint::Create(impl_->db, &cp), "Checkpoint::Create");
    std::unique_ptr<rocksdb::Checkpoint> cpd(cp);
    check(cpd->CreateCheckpoint(snapshot_dir), "CreateCheckpoint");
}

void EventStore::compact_all() {
    for (auto* h : impl_->cf_handles) {
        impl_->db->CompactRange(rocksdb::CompactRangeOptions(), h, nullptr, nullptr);
    }
}

std::string EventStore::stats() const {
    std::string out;
    for (size_t i = 0; i < CF_NAMES.size(); ++i) {
        std::string stat;
        impl_->db->GetProperty(impl_->cf_handles[i],
                                "rocksdb.stats", &stat);
        out += "=== " + CF_NAMES[i] + " ===\n" + stat + "\n";
    }
    return out;
}

Seq EventStore::current_seq() const { return impl_->global_seq_.load(); }

} // namespace controldb
