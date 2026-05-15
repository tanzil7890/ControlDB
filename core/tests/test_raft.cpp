#include <gtest/gtest.h>
#include "controldb/raft/log.hpp"
#include "controldb/raft/node.hpp"
#include "controldb/raft/transport.hpp"
#include <atomic>
#include <filesystem>
#include <thread>
#include <chrono>

using namespace controldb::raft;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// RaftLog tests
// ---------------------------------------------------------------------------

class RaftLogTest : public ::testing::Test {
protected:
    std::string log_path;
    std::unique_ptr<RaftLog> log;

    void SetUp() override {
        log_path = "/tmp/controldb_test_raftlog_" +
                   std::to_string(std::chrono::system_clock::now()
                                      .time_since_epoch().count()) + ".log";
        log = std::make_unique<RaftLog>(log_path);
    }
    void TearDown() override {
        log.reset();
        fs::remove(log_path);
    }
};

TEST_F(RaftLogTest, AppendAndGet) {
    uint64_t idx = log->append(1, "cmd_1");
    EXPECT_EQ(idx, 1);
    auto entry = log->get(1);
    ASSERT_TRUE(entry.has_value());
    EXPECT_EQ(entry->term,    1);
    EXPECT_EQ(entry->command, "cmd_1");
}

TEST_F(RaftLogTest, AppendMultiple) {
    for (int i = 1; i <= 5; ++i)
        log->append(1, "cmd_" + std::to_string(i));
    EXPECT_EQ(log->last_index(), 5);
    EXPECT_EQ(log->size(), 5);
}

TEST_F(RaftLogTest, GetRange) {
    for (int i = 1; i <= 5; ++i) log->append(1, "cmd_" + std::to_string(i));
    auto range = log->get_range(2, 4);
    ASSERT_EQ(range.size(), 3);
    EXPECT_EQ(range[0].index, 2);
    EXPECT_EQ(range[2].index, 4);
}

TEST_F(RaftLogTest, TruncateSuffix) {
    for (int i = 1; i <= 5; ++i) log->append(1, "cmd_" + std::to_string(i));
    log->truncate_suffix(3);
    EXPECT_EQ(log->last_index(), 3);
    EXPECT_FALSE(log->get(4).has_value());
}

TEST_F(RaftLogTest, Persistence) {
    for (int i = 1; i <= 3; ++i) log->append(1, "cmd_" + std::to_string(i));
    log.reset();
    // Re-open
    RaftLog log2(log_path);
    EXPECT_EQ(log2.last_index(), 3);
    auto e = log2.get(2);
    ASSERT_TRUE(e.has_value());
    EXPECT_EQ(e->command, "cmd_2");
}

TEST_F(RaftLogTest, IsUpToDate) {
    log->append(1, "cmd_1");
    log->append(2, "cmd_2");
    // Same term, higher index → up to date
    EXPECT_TRUE(log->is_up_to_date(2, 2));
    EXPECT_TRUE(log->is_up_to_date(2, 3));
    // Higher term → up to date
    EXPECT_TRUE(log->is_up_to_date(3, 0));
    // Lower index same term → not up to date
    EXPECT_FALSE(log->is_up_to_date(2, 1));
    // Lower term → not up to date
    EXPECT_FALSE(log->is_up_to_date(1, 100));
}

// ---------------------------------------------------------------------------
// RaftNode single-node mode (no network, immediate commit)
// ---------------------------------------------------------------------------

class RaftNodeTest : public ::testing::Test {
protected:
    std::string log_dir;
    std::unique_ptr<RaftNode> node;
    std::vector<std::pair<uint64_t, std::string>> applied;
    std::mutex applied_mu;

    void SetUp() override {
        log_dir = "/tmp/controldb_test_raftnode_" +
                  std::to_string(std::chrono::system_clock::now()
                                     .time_since_epoch().count());

        RaftConfig cfg;
        cfg.node_id          = "node_1";
        cfg.log_dir          = log_dir;
        cfg.single_node_mode = true;

        auto transport = make_inprocess_transport("node_1");
        node = std::make_unique<RaftNode>(
            std::move(cfg),
            std::move(transport),
            [this](uint64_t idx, const std::string& cmd) {
                std::lock_guard<std::mutex> l(applied_mu);
                applied.emplace_back(idx, cmd);
            });
        node->start();
    }

    void TearDown() override {
        node->stop();
        node.reset();
        fs::remove_all(log_dir);
    }
};

TEST_F(RaftNodeTest, SingleNodeIsLeader) {
    // Single-node mode immediately becomes leader
    EXPECT_EQ(node->role(), Role::Leader);
    EXPECT_TRUE(node->is_leader());
}

TEST_F(RaftNodeTest, ProposeAndApply) {
    node->propose(R"({"op":"insert_run","run_id":"r1"})");
    node->propose(R"({"op":"insert_event","event_id":"e1"})");

    // Apply is async — wait briefly
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    std::lock_guard<std::mutex> l(applied_mu);
    EXPECT_GE(applied.size(), 2);
    EXPECT_EQ(node->committed_index(), 2);
}

TEST_F(RaftNodeTest, ProposeMultiple) {
    for (int i = 0; i < 20; ++i)
        node->propose("{\"i\":" + std::to_string(i) + "}");

    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    EXPECT_EQ(node->committed_index(), 20);
    EXPECT_EQ(node->last_log_index(), 20);
}
