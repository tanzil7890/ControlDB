#include "controldb/raft/log.hpp"
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <unistd.h>
#include <zlib.h>

namespace controldb::raft {

// ---------------------------------------------------------------------------
// On-disk format per entry:
//   [8-byte BE index][8-byte BE term][4-byte BE cmd_len][command][4-byte CRC32]
// ---------------------------------------------------------------------------

namespace {

void write_be64(uint8_t* b, uint64_t v) {
    for (int i = 7; i >= 0; --i) { b[i] = v & 0xFF; v >>= 8; }
}
uint64_t read_be64(const uint8_t* b) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v = (v << 8) | b[i];
    return v;
}
void write_be32(uint8_t* b, uint32_t v) {
    for (int i = 3; i >= 0; --i) { b[i] = v & 0xFF; v >>= 8; }
}
uint32_t read_be32(const uint8_t* b) {
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) v = (v << 8) | b[i];
    return v;
}
uint32_t crc_of(const uint8_t* data, size_t len) {
    return static_cast<uint32_t>(::crc32(0L, data, static_cast<uInt>(len)));
}

} // anonymous namespace

struct RaftLog::Impl {
    std::string              log_path;
    int                      fd = -1;
    mutable std::mutex       mu;
    std::vector<LogEntry>    entries;  // in-memory index, 0-based (index 1 = entries[0])

    void open() {
        std::filesystem::create_directories(
            std::filesystem::path(log_path).parent_path());
        fd = ::open(log_path.c_str(),
                    O_CREAT | O_RDWR | O_APPEND | O_CLOEXEC, 0644);
        if (fd < 0)
            throw std::runtime_error("RaftLog: cannot open " + log_path);
        load();
    }

    void load() {
        std::ifstream fin(log_path, std::ios::binary);
        if (!fin) return;
        uint8_t hdr[20]; // 8+8+4
        while (fin.read(reinterpret_cast<char*>(hdr), 20)) {
            uint64_t idx  = read_be64(hdr);
            uint64_t term = read_be64(hdr + 8);
            uint32_t clen = read_be32(hdr + 16);
            std::string cmd(clen, '\0');
            if (!fin.read(cmd.data(), clen)) break;
            uint8_t crc_b[4];
            if (!fin.read(reinterpret_cast<char*>(crc_b), 4)) break;
            LogEntry e;
            e.index   = idx;
            e.term    = term;
            e.command = std::move(cmd);
            e.entry_hash = "";  // loaded from file, trust CRC
            entries.push_back(std::move(e));
        }
    }

    void write_entry(const LogEntry& e) {
        uint32_t clen = static_cast<uint32_t>(e.command.size());
        std::vector<uint8_t> buf(20 + clen);
        write_be64(buf.data(),     e.index);
        write_be64(buf.data() + 8, e.term);
        write_be32(buf.data() + 16, clen);
        std::memcpy(buf.data() + 20, e.command.data(), clen);
        uint32_t crc = crc_of(buf.data(), buf.size());
        uint8_t crc_b[4]; write_be32(crc_b, crc);
        ::write(fd, buf.data(), buf.size());
        ::write(fd, crc_b, 4);
        ::fsync(fd);
    }
};

RaftLog::RaftLog(const std::string& log_path)
    : impl_(std::make_unique<Impl>()) {
    impl_->log_path = log_path;
    impl_->open();
}

RaftLog::~RaftLog() {
    if (impl_->fd >= 0) ::close(impl_->fd);
}

uint64_t RaftLog::append(uint64_t term, const std::string& command) {
    std::lock_guard<std::mutex> lock(impl_->mu);
    LogEntry e;
    e.index   = impl_->entries.empty() ? 1 : impl_->entries.back().index + 1;
    e.term    = term;
    e.command = command;
    impl_->write_entry(e);
    impl_->entries.push_back(e);
    return e.index;
}

void RaftLog::append_batch(const std::vector<LogEntry>& entries) {
    std::lock_guard<std::mutex> lock(impl_->mu);
    for (const auto& e : entries) {
        impl_->write_entry(e);
        impl_->entries.push_back(e);
    }
}

void RaftLog::truncate_suffix(uint64_t keep_index) {
    std::lock_guard<std::mutex> lock(impl_->mu);
    while (!impl_->entries.empty() &&
           impl_->entries.back().index > keep_index) {
        impl_->entries.pop_back();
    }
    // Rewrite file
    ::close(impl_->fd);
    ::truncate(impl_->log_path.c_str(), 0);
    impl_->fd = ::open(impl_->log_path.c_str(),
                       O_CREAT | O_RDWR | O_APPEND | O_CLOEXEC, 0644);
    for (const auto& e : impl_->entries) impl_->write_entry(e);
}

std::optional<LogEntry> RaftLog::get(uint64_t index) const {
    std::lock_guard<std::mutex> lock(impl_->mu);
    if (impl_->entries.empty() || index < impl_->entries.front().index ||
        index > impl_->entries.back().index) return std::nullopt;
    size_t off = index - impl_->entries.front().index;
    if (off >= impl_->entries.size()) return std::nullopt;
    return impl_->entries[off];
}

std::vector<LogEntry> RaftLog::get_range(uint64_t from_index,
                                          uint64_t to_index) const {
    std::lock_guard<std::mutex> lock(impl_->mu);
    std::vector<LogEntry> result;
    if (impl_->entries.empty()) return result;
    uint64_t base = impl_->entries.front().index;
    for (uint64_t idx = from_index; idx <= to_index; ++idx) {
        if (idx < base || (idx - base) >= impl_->entries.size()) continue;
        result.push_back(impl_->entries[idx - base]);
    }
    return result;
}

uint64_t RaftLog::last_index() const {
    std::lock_guard<std::mutex> lock(impl_->mu);
    return impl_->entries.empty() ? 0 : impl_->entries.back().index;
}

uint64_t RaftLog::last_term() const {
    std::lock_guard<std::mutex> lock(impl_->mu);
    return impl_->entries.empty() ? 0 : impl_->entries.back().term;
}

uint64_t RaftLog::term_at(uint64_t index) const {
    auto e = get(index);
    return e ? e->term : 0;
}

bool RaftLog::is_up_to_date(uint64_t last_log_term_other,
                              uint64_t last_log_index_other) const {
    uint64_t my_term  = last_term();
    uint64_t my_index = last_index();
    if (last_log_term_other != my_term)
        return last_log_term_other > my_term;
    return last_log_index_other >= my_index;
}

uint64_t RaftLog::size() const {
    std::lock_guard<std::mutex> lock(impl_->mu);
    return impl_->entries.size();
}

} // namespace controldb::raft
