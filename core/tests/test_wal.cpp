#include <gtest/gtest.h>
#include "controldb/wal.hpp"
#include <filesystem>
#include <string>

using namespace controldb;
namespace fs = std::filesystem;

class WalTest : public ::testing::Test {
protected:
    std::string wal_dir;

    void SetUp() override {
        wal_dir = "/tmp/controldb_test_wal_" +
                  std::to_string(std::chrono::system_clock::now()
                                     .time_since_epoch().count());
    }

    void TearDown() override {
        fs::remove_all(wal_dir);
    }
};

TEST_F(WalTest, AppendAndRead) {
    Wal wal(wal_dir);
    auto seq1 = wal.append(WalOpType::INSERT_RUN, R"({"run_id":"r1"})");
    auto seq2 = wal.append(WalOpType::INSERT_EVENT, R"({"event_id":"e1"})");

    EXPECT_EQ(seq1, 1);
    EXPECT_EQ(seq2, 2);
    EXPECT_EQ(wal.last_seq(), 2);

    auto entries = wal.read_unapplied(0);
    EXPECT_EQ(entries.size(), 2);
    EXPECT_EQ(entries[0].wal_seq, 1);
    EXPECT_EQ(entries[0].op_type, WalOpType::INSERT_RUN);
    EXPECT_EQ(entries[0].payload_json, R"({"run_id":"r1"})");
}

TEST_F(WalTest, MarkApplied) {
    Wal wal(wal_dir);
    wal.append(WalOpType::INSERT_RUN,   R"({"run_id":"r1"})");
    wal.append(WalOpType::INSERT_EVENT, R"({"event_id":"e1"})");
    wal.append(WalOpType::INSERT_EVENT, R"({"event_id":"e2"})");

    wal.mark_applied(2);
    EXPECT_EQ(wal.applied_seq(), 2);

    auto unapplied = wal.read_unapplied(2);
    EXPECT_EQ(unapplied.size(), 1);
    EXPECT_EQ(unapplied[0].wal_seq, 3);
}

TEST_F(WalTest, Persistence) {
    {
        Wal wal(wal_dir);
        wal.append(WalOpType::INSERT_RUN, R"({"run_id":"r1"})");
        wal.append(WalOpType::INSERT_RUN, R"({"run_id":"r2"})");
        wal.mark_applied(1);
    }
    // Re-open — should recover state
    Wal wal2(wal_dir);
    EXPECT_EQ(wal2.last_seq(),    2);
    EXPECT_EQ(wal2.applied_seq(), 1);
    auto unapplied = wal2.read_unapplied(wal2.applied_seq());
    EXPECT_EQ(unapplied.size(), 1);
    EXPECT_EQ(unapplied[0].payload_json, R"({"run_id":"r2"})");
}

TEST_F(WalTest, TruncateApplied) {
    Wal wal(wal_dir);
    for (int i = 0; i < 5; ++i)
        wal.append(WalOpType::INSERT_EVENT, "{\"i\":" + std::to_string(i) + "}");
    wal.mark_applied(3);
    wal.truncate_applied();

    auto remaining = wal.read_unapplied(0);
    EXPECT_EQ(remaining.size(), 2);
    EXPECT_EQ(remaining[0].wal_seq, 4);
    EXPECT_EQ(remaining[1].wal_seq, 5);
}
