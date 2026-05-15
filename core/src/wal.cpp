#include "controldb/wal.hpp"

#include <atomic>
#include <cstring>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <zlib.h>   // for crc32

namespace controldb {

namespace {

// Big-endian 8-byte encode/decode
inline void write_be64(uint8_t* buf, uint64_t v) {
    for (int i = 7; i >= 0; --i) { buf[i] = v & 0xFF; v >>= 8; }
}
inline uint64_t read_be64(const uint8_t* buf) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) { v = (v << 8) | buf[i]; }
    return v;
}
inline void write_be32(uint8_t* buf, uint32_t v) {
    for (int i = 3; i >= 0; --i) { buf[i] = v & 0xFF; v >>= 8; }
}
inline uint32_t read_be32(const uint8_t* buf) {
    uint32_t v = 0;
    for (int i = 0; i < 4; ++i) { v = (v << 8) | buf[i]; }
    return v;
}

uint32_t crc32_of(const uint8_t* data, size_t len) {
    return static_cast<uint32_t>(::crc32(0L, data, static_cast<uInt>(len)));
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// WAL on-disk format per entry:
//   [8-byte BE wal_seq]
//   [1-byte op_type]
//   [8-byte BE timestamp_ms]
//   [4-byte BE payload_len]
//   [payload bytes]
//   [4-byte BE CRC32 of the above 21+payload bytes]
// ---------------------------------------------------------------------------

struct Wal::Impl {
    std::string         wal_dir;
    std::string         wal_path;
    std::string         applied_path;  // stores applied_seq as text
    int                 fd = -1;       // append-only file descriptor
    std::mutex          mu;
    std::atomic<uint64_t> last_seq_{0};
    std::atomic<uint64_t> applied_seq_{0};

    void open() {
        namespace fs = std::filesystem;
        fs::create_directories(wal_dir);
        wal_path    = wal_dir + "/wal.log";
        applied_path = wal_dir + "/applied.seq";

        // Load applied seq
        {
            std::ifstream f(applied_path);
            if (f) { f >> applied_seq_; }
        }

        // Scan existing WAL to find last_seq
        if (fs::exists(wal_path)) {
            std::ifstream fin(wal_path, std::ios::binary);
            if (fin) {
                uint8_t hdr[21]; // 8+1+8+4
                while (fin.read(reinterpret_cast<char*>(hdr), 21)) {
                    uint64_t seq  = read_be64(hdr);
                    uint32_t plen = read_be32(hdr + 17);
                    // skip payload + crc
                    fin.seekg(plen + 4, std::ios::cur);
                    if (!fin) break;
                    if (seq > last_seq_) last_seq_.store(seq);
                }
            }
        }

        fd = ::open(wal_path.c_str(),
                    O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0644);
        if (fd < 0) {
            throw std::runtime_error("WAL: cannot open " + wal_path + ": " +
                                     strerror(errno));
        }
    }

    void close() {
        if (fd >= 0) { ::close(fd); fd = -1; }
    }
};

Wal::Wal(const std::string& wal_dir) : impl_(std::make_unique<Impl>()) {
    impl_->wal_dir = wal_dir;
    impl_->open();
}

Wal::~Wal() { impl_->close(); }

uint64_t Wal::append(WalOpType op, const std::string& payload_json) {
    std::lock_guard<std::mutex> lock(impl_->mu);

    uint64_t seq     = impl_->last_seq_.fetch_add(1) + 1;
    uint64_t ts_ms   = static_cast<uint64_t>(
                           std::chrono::duration_cast<std::chrono::milliseconds>(
                               std::chrono::system_clock::now().time_since_epoch())
                           .count());
    uint32_t plen    = static_cast<uint32_t>(payload_json.size());

    // Build the record buffer for CRC computation
    std::vector<uint8_t> buf(21 + plen);
    write_be64(buf.data(),     seq);
    buf[8] = static_cast<uint8_t>(op);
    write_be64(buf.data() + 9, ts_ms);
    write_be32(buf.data() + 17, plen);
    std::memcpy(buf.data() + 21, payload_json.data(), plen);

    uint32_t crc = crc32_of(buf.data(), buf.size());
    uint8_t crc_bytes[4];
    write_be32(crc_bytes, crc);

    // Write record + CRC to file
    ::write(impl_->fd, buf.data(), buf.size());
    ::write(impl_->fd, crc_bytes, 4);
    ::fsync(impl_->fd);  // durability guarantee

    return seq;
}

void Wal::mark_applied(uint64_t wal_seq) {
    std::lock_guard<std::mutex> lock(impl_->mu);
    if (wal_seq > impl_->applied_seq_) {
        impl_->applied_seq_.store(wal_seq);
        // Persist so crash recovery knows what's applied
        std::ofstream f(impl_->applied_path, std::ios::trunc);
        f << wal_seq;
    }
}

std::vector<WalEntry> Wal::read_unapplied(uint64_t after_seq) const {
    std::vector<WalEntry> result;
    std::ifstream fin(impl_->wal_path, std::ios::binary);
    if (!fin) return result;

    uint8_t hdr[21];
    while (fin.read(reinterpret_cast<char*>(hdr), 21)) {
        uint64_t seq  = read_be64(hdr);
        auto op       = static_cast<WalOpType>(hdr[8]);
        uint64_t ts   = read_be64(hdr + 9);
        uint32_t plen = read_be32(hdr + 17);

        std::string payload(plen, '\0');
        if (!fin.read(payload.data(), plen)) break;

        uint8_t crc_bytes[4];
        if (!fin.read(reinterpret_cast<char*>(crc_bytes), 4)) break;

        if (seq > after_seq) {
            WalEntry e;
            e.wal_seq        = seq;
            e.op_type        = op;
            e.timestamp_ms   = ts;
            e.payload_json   = std::move(payload);
            e.crc32          = read_be32(crc_bytes);
            result.push_back(std::move(e));
        }
    }
    return result;
}

void Wal::truncate_applied() {
    // Re-write WAL keeping only entries > applied_seq.
    uint64_t applied = impl_->applied_seq_.load();
    auto unapplied   = read_unapplied(applied);

    std::string tmp = impl_->wal_path + ".tmp";
    {
        std::ofstream fout(tmp, std::ios::binary | std::ios::trunc);
        uint8_t hdr[21];
        for (auto& e : unapplied) {
            write_be64(hdr, e.wal_seq);
            hdr[8] = static_cast<uint8_t>(e.op_type);
            write_be64(hdr + 9, e.timestamp_ms);
            uint32_t plen = static_cast<uint32_t>(e.payload_json.size());
            write_be32(hdr + 17, plen);
            fout.write(reinterpret_cast<char*>(hdr), 21);
            fout.write(e.payload_json.data(), plen);
            uint8_t crc_bytes[4];
            write_be32(crc_bytes, e.crc32);
            fout.write(reinterpret_cast<char*>(crc_bytes), 4);
        }
    }
    std::filesystem::rename(tmp, impl_->wal_path);

    // Re-open fd
    impl_->close();
    impl_->fd = ::open(impl_->wal_path.c_str(),
                       O_CREAT | O_WRONLY | O_APPEND | O_CLOEXEC, 0644);
}

uint64_t Wal::last_seq()    const { return impl_->last_seq_.load(); }
uint64_t Wal::applied_seq() const { return impl_->applied_seq_.load(); }

} // namespace controldb
