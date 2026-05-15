#include <gtest/gtest.h>
#include "controldb/hash_chain.hpp"

using namespace controldb;

// ---------------------------------------------------------------------------
// sha256_hex
// ---------------------------------------------------------------------------

TEST(HashChain, Sha256EmptyString) {
    // Known: sha256("") = e3b0c442...
    auto h = sha256_hex("");
    EXPECT_EQ(h, "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
}

TEST(HashChain, Sha256Prefix) {
    EXPECT_TRUE(sha256_hex("hello").substr(0, 7) == "sha256:");
}

TEST(HashChain, Sha256Deterministic) {
    EXPECT_EQ(sha256_hex("foo"), sha256_hex("foo"));
    EXPECT_NE(sha256_hex("foo"), sha256_hex("bar"));
}

// ---------------------------------------------------------------------------
// compute_event_hash
// ---------------------------------------------------------------------------

static AuditEvent make_event() {
    AuditEvent ev;
    ev.event_id           = "evt_test_001";
    ev.event_type         = "tool_call.completed";
    ev.run_id             = "run_abc123";
    ev.org_id             = "org_default";
    ev.project_id         = "proj_default";
    ev.environment        = "dev";
    ev.schema_version     = "2026-05-01";
    ev.payload_mode       = "metadata_only";
    ev.occurred_at_ms     = 1714521600000LL;
    ev.ingested_at_ms     = 1714521600000LL;
    return ev;
}

TEST(HashChain, ComputeEventHashProducesPrefix) {
    auto ev   = make_event();
    auto hash = compute_event_hash(ev);
    EXPECT_EQ(hash.substr(0, 7), "sha256:");
    EXPECT_EQ(hash.size(), 7 + 64);  // "sha256:" + 64 hex chars
}

TEST(HashChain, ComputeEventHashDeterministic) {
    auto ev = make_event();
    EXPECT_EQ(compute_event_hash(ev), compute_event_hash(ev));
}

TEST(HashChain, ComputeEventHashExcludesEventHashField) {
    auto ev1 = make_event();
    auto ev2 = make_event();
    ev2.event_hash = "sha256:deadbeef";  // should not affect result
    EXPECT_EQ(compute_event_hash(ev1), compute_event_hash(ev2));
}

TEST(HashChain, DifferentRunIdsDifferentHashes) {
    auto ev1 = make_event(); ev1.run_id = "run_aaa";
    auto ev2 = make_event(); ev2.run_id = "run_bbb";
    EXPECT_NE(compute_event_hash(ev1), compute_event_hash(ev2));
}

TEST(HashChain, PreviousHashLinksChain) {
    auto ev1 = make_event();
    HashStr h1 = compute_event_hash(ev1);

    auto ev2 = make_event();
    ev2.event_id           = "evt_test_002";
    ev2.previous_event_hash = h1;
    HashStr h2 = compute_event_hash(ev2);

    EXPECT_NE(h1, h2);
}

// ---------------------------------------------------------------------------
// verify_chain
// ---------------------------------------------------------------------------

TEST(HashChain, VerifyEmptyChain) {
    auto r = verify_chain({}, "run_x");
    EXPECT_TRUE(r.valid);
    EXPECT_EQ(r.event_count, 0);
}

TEST(HashChain, VerifySingleEvent) {
    auto ev = make_event();
    ev.previous_event_hash = "";
    ev.event_hash          = compute_event_hash(ev);
    ev.sequence            = 1;

    auto r = verify_chain({ev}, ev.run_id);
    EXPECT_TRUE(r.valid);
    EXPECT_EQ(r.event_count, 1);
}

TEST(HashChain, VerifyTwoEventChain) {
    auto ev1 = make_event(); ev1.sequence = 1;
    ev1.previous_event_hash = "";
    ev1.event_hash          = compute_event_hash(ev1);

    auto ev2 = make_event(); ev2.sequence = 2;
    ev2.event_id            = "evt_002";
    ev2.previous_event_hash = ev1.event_hash;
    ev2.event_hash          = compute_event_hash(ev2);

    auto r = verify_chain({ev1, ev2}, "run_abc123");
    EXPECT_TRUE(r.valid);
    EXPECT_EQ(r.event_count, 2);
}

TEST(HashChain, TamperDetected) {
    auto ev1 = make_event(); ev1.sequence = 1;
    ev1.previous_event_hash = "";
    ev1.event_hash          = compute_event_hash(ev1);

    auto ev2 = make_event(); ev2.sequence = 2;
    ev2.event_id            = "evt_002";
    ev2.previous_event_hash = ev1.event_hash;
    ev2.event_hash          = compute_event_hash(ev2);

    // Tamper: change event_type but keep stored event_hash
    ev2.event_type = "TAMPERED";

    auto r = verify_chain({ev1, ev2}, "run_abc123");
    EXPECT_FALSE(r.valid);
    EXPECT_EQ(r.broken_at_event_id, "evt_002");
}

TEST(HashChain, BrokenPreviousHashDetected) {
    auto ev1 = make_event(); ev1.sequence = 1;
    ev1.previous_event_hash = "";
    ev1.event_hash          = compute_event_hash(ev1);

    auto ev2 = make_event(); ev2.sequence = 2;
    ev2.event_id            = "evt_002";
    ev2.previous_event_hash = "sha256:wronghash";  // broken link
    ev2.event_hash          = compute_event_hash(ev2);

    auto r = verify_chain({ev1, ev2}, "run_abc123");
    EXPECT_FALSE(r.valid);
    EXPECT_EQ(r.broken_at_event_id, "evt_002");
}
