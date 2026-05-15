#include <gtest/gtest.h>
#include "controldb/event_store.hpp"
#include "controldb/hash_chain.hpp"
#include <filesystem>
#include <chrono>

using namespace controldb;
namespace fs = std::filesystem;

class EventStoreTest : public ::testing::Test {
protected:
    std::string db_path;
    std::unique_ptr<EventStore> store;

    void SetUp() override {
        db_path = "/tmp/controldb_test_store_" +
                  std::to_string(std::chrono::system_clock::now()
                                     .time_since_epoch().count());
        store = std::make_unique<EventStore>(db_path);
        store->bootstrap("org_test", "proj_test", {"test-api-key"});
    }

    void TearDown() override {
        store.reset();
        fs::remove_all(db_path);
    }

    AgentRun make_run(const std::string& run_id = "run_001") {
        AgentRun r;
        r.run_id      = run_id;
        r.org_id      = "org_test";
        r.project_id  = "proj_test";
        r.environment = "test";
        r.agent_id    = "test-agent";
        r.status      = "running";
        return r;
    }

    AuditEvent make_event(const std::string& run_id = "run_001",
                           const std::string& event_id = "evt_001") {
        AuditEvent ev;
        ev.event_id       = event_id;
        ev.run_id         = run_id;
        ev.org_id         = "org_test";
        ev.project_id     = "proj_test";
        ev.environment    = "test";
        ev.event_type     = "tool_call.completed";
        ev.schema_version = "2026-05-01";
        ev.payload_mode   = "metadata_only";
        return ev;
    }
};

// ---------------------------------------------------------------------------
// Run lifecycle
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, StartRunAndGet) {
    auto run = store->start_run(make_run());
    EXPECT_EQ(run.run_id, "run_001");
    EXPECT_EQ(run.status, "running");

    auto got = store->get_run("run_001");
    ASSERT_TRUE(got.has_value());
    EXPECT_EQ(got->run_id, "run_001");
    EXPECT_EQ(got->agent_id, "test-agent");
}

TEST_F(EventStoreTest, StartRunIdempotent) {
    auto r1 = store->start_run(make_run());
    auto r2 = store->start_run(make_run());  // same run_id
    EXPECT_EQ(r1.run_id, r2.run_id);
    EXPECT_EQ(r1.started_at_ms, r2.started_at_ms);
}

TEST_F(EventStoreTest, CommitRun) {
    store->start_run(make_run());
    auto committed = store->commit_run("run_001", "org_test", "proj_test");
    EXPECT_EQ(committed.status, "committed");
    EXPECT_GT(committed.ended_at_ms, 0);
}

TEST_F(EventStoreTest, RollbackRun) {
    store->start_run(make_run());
    auto rb = store->rollback_run("run_001", "org_test", "proj_test", "test rollback");
    EXPECT_EQ(rb.status, "rolled_back");
    EXPECT_NE(rb.metadata_json.find("test rollback"), std::string::npos);
}

TEST_F(EventStoreTest, GetRunNotFound) {
    auto r = store->get_run("nonexistent");
    EXPECT_FALSE(r.has_value());
}

TEST_F(EventStoreTest, RunForbidden) {
    store->start_run(make_run());
    EXPECT_THROW(
        store->commit_run("run_001", "org_other", "proj_test"),
        std::runtime_error);
}

TEST_F(EventStoreTest, ListRuns) {
    store->start_run(make_run("run_a"));
    store->start_run(make_run("run_b"));
    auto runs = store->list_runs("org_test", "proj_test", 10);
    EXPECT_GE(runs.size(), 2);
}

// ---------------------------------------------------------------------------
// Event ingestion
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, AppendSingleEvent) {
    store->start_run(make_run());
    auto results = store->append_events("run_001", "org_test", "proj_test",
                                         {make_event()});
    ASSERT_EQ(results.size(), 1);
    EXPECT_FALSE(results[0].event_id.empty());
    EXPECT_FALSE(results[0].event_hash.empty());
    EXPECT_FALSE(results[0].was_duplicate);
    EXPECT_EQ(results[0].event_hash.substr(0, 7), "sha256:");
}

TEST_F(EventStoreTest, AppendBatchEvents) {
    store->start_run(make_run());
    std::vector<AuditEvent> batch;
    for (int i = 0; i < 10; ++i)
        batch.push_back(make_event("run_001", "evt_" + std::to_string(i)));

    auto results = store->append_events("run_001", "org_test", "proj_test",
                                         std::move(batch));
    ASSERT_EQ(results.size(), 10);
    for (const auto& r : results) {
        EXPECT_FALSE(r.was_duplicate);
        EXPECT_EQ(r.event_hash.substr(0, 7), "sha256:");
    }
}

TEST_F(EventStoreTest, IdempotencyKey) {
    store->start_run(make_run());
    auto ev = make_event(); ev.idempotency_key = "idem_001";

    auto r1 = store->append_events("run_001", "org_test", "proj_test", {ev});
    auto r2 = store->append_events("run_001", "org_test", "proj_test", {ev});  // duplicate

    EXPECT_FALSE(r1[0].was_duplicate);
    EXPECT_TRUE(r2[0].was_duplicate);
    EXPECT_EQ(r1[0].event_id, r2[0].event_id);
}

// ---------------------------------------------------------------------------
// Timeline
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, Timeline) {
    store->start_run(make_run());
    for (int i = 0; i < 5; ++i) {
        auto ev = make_event("run_001", "evt_" + std::to_string(i));
        store->append_events("run_001", "org_test", "proj_test", {ev});
    }

    auto tl = store->timeline("run_001", "org_test", "proj_test", 10);
    ASSERT_EQ(tl.events.size(), 5);

    // Verify ordering: sequence ascending
    for (size_t i = 1; i < tl.events.size(); ++i)
        EXPECT_LT(tl.events[i-1].sequence, tl.events[i].sequence);
}

TEST_F(EventStoreTest, TimelinePagination) {
    store->start_run(make_run());
    for (int i = 0; i < 6; ++i) {
        auto ev = make_event("run_001", "evt_" + std::to_string(i));
        store->append_events("run_001", "org_test", "proj_test", {ev});
    }

    auto page1 = store->timeline("run_001", "org_test", "proj_test", 4);
    ASSERT_EQ(page1.events.size(), 4);
    ASSERT_TRUE(page1.next_cursor.has_value());

    auto page2 = store->timeline("run_001", "org_test", "proj_test", 10,
                                  page1.next_cursor);
    EXPECT_EQ(page2.events.size(), 2);
    EXPECT_FALSE(page2.next_cursor.has_value());
}

// ---------------------------------------------------------------------------
// Hash chain verification
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, VerifyValidChain) {
    store->start_run(make_run());
    for (int i = 0; i < 5; ++i) {
        auto ev = make_event("run_001", "evt_" + std::to_string(i));
        store->append_events("run_001", "org_test", "proj_test", {ev});
    }
    auto v = store->verify("run_001", "org_test", "proj_test");
    EXPECT_TRUE(v.valid);
    EXPECT_EQ(v.event_count, 5);
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, ApprovalsWorkflow) {
    store->start_run(make_run());
    Approval a;
    a.approval_id  = "appr_001";
    a.run_id       = "run_001";
    a.org_id       = "org_test";
    a.project_id   = "proj_test";
    a.status       = "requested";
    a.requested_by = "agent";

    auto saved = store->upsert_approval(a);
    EXPECT_EQ(saved.status, "requested");

    auto pending = store->pending_approvals("org_test", "proj_test");
    ASSERT_EQ(pending.size(), 1);
    EXPECT_EQ(pending[0].approval_id, "appr_001");

    auto decided = store->update_approval_status(
        "appr_001", "org_test", "proj_test", "approved", "manager_1", "looks good");
    EXPECT_EQ(decided.status, "approved");
    EXPECT_EQ(decided.reviewer_id, "manager_1");

    auto pending2 = store->pending_approvals("org_test", "proj_test");
    EXPECT_TRUE(pending2.empty());
}

// ---------------------------------------------------------------------------
// Snapshot
// ---------------------------------------------------------------------------

TEST_F(EventStoreTest, CreateSnapshot) {
    store->start_run(make_run());
    auto snap_dir = db_path + "_snapshot";
    EXPECT_NO_THROW(store->create_snapshot(snap_dir));
    EXPECT_TRUE(fs::exists(snap_dir));
    fs::remove_all(snap_dir);
}
