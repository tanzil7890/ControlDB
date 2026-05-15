#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>

#include "controldb/event_store.hpp"
#include "controldb/hash_chain.hpp"
#include "controldb/raft/node.hpp"
#include "controldb/snapshot.hpp"
#include "controldb/types.hpp"

namespace py = pybind11;
using namespace controldb;
using namespace controldb::raft;

PYBIND11_MODULE(_controldb_engine, m) {
    m.doc() = "ControlDB C++/RocksDB/Raft storage engine — Python bindings";

    // -----------------------------------------------------------------------
    // Core types
    // -----------------------------------------------------------------------

    py::class_<AgentRun>(m, "AgentRun")
        .def(py::init<>())
        .def_readwrite("run_id",          &AgentRun::run_id)
        .def_readwrite("org_id",          &AgentRun::org_id)
        .def_readwrite("project_id",      &AgentRun::project_id)
        .def_readwrite("environment",     &AgentRun::environment)
        .def_readwrite("agent_id",        &AgentRun::agent_id)
        .def_readwrite("agent_version",   &AgentRun::agent_version)
        .def_readwrite("status",          &AgentRun::status)
        .def_readwrite("metadata_json",   &AgentRun::metadata_json)
        .def_readwrite("started_at_ms",   &AgentRun::started_at_ms)
        .def_readwrite("ended_at_ms",     &AgentRun::ended_at_ms)
        .def_readwrite("last_event_hash", &AgentRun::last_event_hash)
        .def_readwrite("event_count",     &AgentRun::event_count);

    py::class_<AuditEvent>(m, "AuditEvent")
        .def(py::init<>())
        .def_readwrite("sequence",             &AuditEvent::sequence)
        .def_readwrite("event_id",             &AuditEvent::event_id)
        .def_readwrite("org_id",               &AuditEvent::org_id)
        .def_readwrite("project_id",           &AuditEvent::project_id)
        .def_readwrite("environment",          &AuditEvent::environment)
        .def_readwrite("run_id",               &AuditEvent::run_id)
        .def_readwrite("step_id",              &AuditEvent::step_id)
        .def_readwrite("step_index",           &AuditEvent::step_index)
        .def_readwrite("event_type",           &AuditEvent::event_type)
        .def_readwrite("schema_version",       &AuditEvent::schema_version)
        .def_readwrite("actor_type",           &AuditEvent::actor_type)
        .def_readwrite("actor_id",             &AuditEvent::actor_id)
        .def_readwrite("trace_id",             &AuditEvent::trace_id)
        .def_readwrite("span_id",              &AuditEvent::span_id)
        .def_readwrite("parent_span_id",       &AuditEvent::parent_span_id)
        .def_readwrite("payload_mode",         &AuditEvent::payload_mode)
        .def_readwrite("payload",              &AuditEvent::payload)
        .def_readwrite("payload_hash",         &AuditEvent::payload_hash)
        .def_readwrite("previous_event_hash",  &AuditEvent::previous_event_hash)
        .def_readwrite("event_hash",           &AuditEvent::event_hash)
        .def_readwrite("occurred_at_ms",       &AuditEvent::occurred_at_ms)
        .def_readwrite("ingested_at_ms",       &AuditEvent::ingested_at_ms)
        .def_readwrite("idempotency_key",      &AuditEvent::idempotency_key);

    py::class_<Approval>(m, "Approval")
        .def(py::init<>())
        .def_readwrite("approval_id",    &Approval::approval_id)
        .def_readwrite("run_id",         &Approval::run_id)
        .def_readwrite("org_id",         &Approval::org_id)
        .def_readwrite("project_id",     &Approval::project_id)
        .def_readwrite("policy_id",      &Approval::policy_id)
        .def_readwrite("requested_by",   &Approval::requested_by)
        .def_readwrite("reviewer_id",    &Approval::reviewer_id)
        .def_readwrite("status",         &Approval::status)
        .def_readwrite("reason",         &Approval::reason)
        .def_readwrite("input_json",     &Approval::input_json)
        .def_readwrite("created_at_ms",  &Approval::created_at_ms)
        .def_readwrite("decided_at_ms",  &Approval::decided_at_ms);

    py::class_<VerificationResult>(m, "VerificationResult")
        .def_readwrite("run_id",              &VerificationResult::run_id)
        .def_readwrite("valid",               &VerificationResult::valid)
        .def_readwrite("event_count",         &VerificationResult::event_count)
        .def_readwrite("broken_at_event_id",  &VerificationResult::broken_at_event_id);

    py::class_<InsertEventResult>(m, "InsertEventResult")
        .def_readwrite("event_id",      &InsertEventResult::event_id)
        .def_readwrite("event_hash",    &InsertEventResult::event_hash)
        .def_readwrite("was_duplicate", &InsertEventResult::was_duplicate);

    py::class_<QueryFilter>(m, "QueryFilter")
        .def(py::init<>())
        .def_readwrite("org_id",      &QueryFilter::org_id)
        .def_readwrite("project_id",  &QueryFilter::project_id)
        .def_readwrite("run_id",      &QueryFilter::run_id)
        .def_readwrite("event_types", &QueryFilter::event_types)
        .def_readwrite("limit",       &QueryFilter::limit)
        .def_readwrite("cursor",      &QueryFilter::cursor);

    py::class_<TimelineResult>(m, "TimelineResult")
        .def_readwrite("run",         &TimelineResult::run)
        .def_readwrite("events",      &TimelineResult::events)
        .def_readwrite("next_cursor", &TimelineResult::next_cursor);

    py::class_<QueryResult>(m, "QueryResult")
        .def_readwrite("events",      &QueryResult::events)
        .def_readwrite("next_cursor", &QueryResult::next_cursor);

    // -----------------------------------------------------------------------
    // EventStore
    // -----------------------------------------------------------------------

    py::class_<EventStore>(m, "EventStore")
        .def(py::init<const std::string&, const std::string&>(),
             py::arg("db_path"), py::arg("wal_dir") = "")
        .def("bootstrap",    &EventStore::bootstrap,
             py::arg("org_id"), py::arg("project_id"), py::arg("api_keys"))
        .def("start_run",    &EventStore::start_run)
        .def("commit_run",   &EventStore::commit_run)
        .def("rollback_run", &EventStore::rollback_run,
             py::arg("run_id"), py::arg("org_id"), py::arg("project_id"),
             py::arg("reason") = "")
        .def("get_run",      &EventStore::get_run)
        .def("list_runs",    &EventStore::list_runs,
             py::arg("org_id"), py::arg("project_id"), py::arg("limit") = 50)
        .def("append_events", &EventStore::append_events)
        .def("timeline",     &EventStore::timeline,
             py::arg("run_id"), py::arg("org_id"), py::arg("project_id"),
             py::arg("limit") = 1000, py::arg("cursor") = py::none())
        .def("query",        &EventStore::query)
        .def("verify",       &EventStore::verify)
        .def("upsert_approval",        &EventStore::upsert_approval)
        .def("update_approval_status", &EventStore::update_approval_status,
             py::arg("approval_id"), py::arg("org_id"), py::arg("project_id"),
             py::arg("status"), py::arg("reviewer_id"), py::arg("reason") = "")
        .def("get_approval",      &EventStore::get_approval)
        .def("pending_approvals", &EventStore::pending_approvals)
        .def("put_export",   &EventStore::put_export)
        .def("get_export",   &EventStore::get_export)
        .def("list_exports", &EventStore::list_exports,
             py::arg("org_id"), py::arg("project_id"), py::arg("limit") = 50)
        .def("create_snapshot", &EventStore::create_snapshot)
        .def("compact_all",     &EventStore::compact_all)
        .def("stats",           &EventStore::stats)
        .def("current_seq",     &EventStore::current_seq);

    // -----------------------------------------------------------------------
    // Hash utilities (exposed for cross-language verification)
    // -----------------------------------------------------------------------

    m.def("sha256_hex",          &controldb::sha256_hex);
    m.def("compute_event_hash",  &controldb::compute_event_hash);
    m.def("compute_payload_hash",&controldb::compute_payload_hash);
    m.def("verify_chain",        &controldb::verify_chain);

    // -----------------------------------------------------------------------
    // Raft (single-node mode for testing)
    // -----------------------------------------------------------------------

    py::enum_<Role>(m, "RaftRole")
        .value("Follower",  Role::Follower)
        .value("Candidate", Role::Candidate)
        .value("Leader",    Role::Leader);

    py::class_<RaftConfig>(m, "RaftConfig")
        .def(py::init<>())
        .def_readwrite("node_id",                &RaftConfig::node_id)
        .def_readwrite("log_dir",                &RaftConfig::log_dir)
        .def_readwrite("election_timeout_min_ms",&RaftConfig::election_timeout_min_ms)
        .def_readwrite("election_timeout_max_ms",&RaftConfig::election_timeout_max_ms)
        .def_readwrite("heartbeat_interval_ms",  &RaftConfig::heartbeat_interval_ms)
        .def_readwrite("single_node_mode",       &RaftConfig::single_node_mode);
}
