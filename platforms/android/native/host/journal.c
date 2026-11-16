#define _GNU_SOURCE
#include "journal.h"
#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

/* Frozen P2a v1 wire, all integers unsigned BIG ENDIAN, exactly 320 bytes:
 * 0 magic[8]="SCHJNL01", 8 version u16=1, 10 kind u16 (1 anchor,2 ACTIVE),
 * 12 length u32=320, 16 sequence u64 (anchor=0), 24 previous record SHA256[32],
 * 56 payload SHA256[32], 88 payload[192], 280 trailer[8]="SJCOMMIT",
 * 288 SHA256(bytes 0..287)[32]. Chain digest = SHA256(all 320 bytes).
 * Payload offsets relative to 88: source[32] at0, contract[32] at32,
 * storage_lineage/install/state/run/attempt/boot [16] at64/80/96/112/128/144,
 * generation u64 at160, known u32 at168 (=255), storage u8 at172 (1/2),
 * boot_kind u8 at173 (1/2), planned PG major u16 at174 (=17), durability
 * u32 at176 (=1), planned slots u32 at180 (=3), reserved zero[8] at184.
 * No arbitrary text or schema extension. Digests detect accidental corruption,
 * NOT authenticity, antirollback, freshness, boot trust or historical custody.
 * Fixed names: anchor.pending (never deleted), anchor (hard-link publication,
 * no replacement), essential (append only). Anchor binds ONE initial run.
 */
struct journal {
  int dir;
  uint8_t payload[192];
  struct journal_native_prefix prefix;
  struct journal_result result;
  int writable, failed;
  dev_t essential_dev;
  ino_t essential_ino;
};
static uint32_t be32(const uint8_t *p) {
  return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 |
         p[3];
}
static uint64_t be64(const uint8_t *p) {
  return (uint64_t)be32(p) << 32 | be32(p + 4);
}
static void put32(uint8_t *p, uint32_t n) {
  p[0] = (uint8_t)(n >> 24);
  p[1] = (uint8_t)(n >> 16);
  p[2] = (uint8_t)(n >> 8);
  p[3] = (uint8_t)n;
}
static void put64(uint8_t *p, uint64_t n) {
  put32(p, (uint32_t)(n >> 32));
  put32(p + 4, (uint32_t)n);
}
/* Minimal FIPS 180-4 SHA-256 compression, bounded in-memory input only.
 * No library, key scheme, dynamic crypto dependency or exported checksum API.
 */
static uint32_t rotr(uint32_t x, unsigned n) { return x >> n | x << (32 - n); }
static void sha256(const uint8_t *data, size_t len, uint8_t out[32]) {
  static const uint32_t k[64] = {
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
  uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  size_t blocks = (len + 9 + 63) / 64;
  for (size_t b = 0; b < blocks; ++b) {
    uint8_t buf[64] = {0};
    uint32_t w[64];
    for (size_t i = 0; i < 64; ++i) {
      size_t pos = b * 64 + i;
      if (pos < len)
        buf[i] = data[pos];
      else if (pos == len)
        buf[i] = 0x80;
    }
    if (b + 1 == blocks)
      put64(buf + 56, (uint64_t)len * 8);
    for (unsigned i = 0; i < 16; ++i)
      w[i] = be32(buf + i * 4);
    for (unsigned i = 16; i < 64; ++i) {
      uint32_t x = w[i - 15], y = w[i - 2];
      w[i] = w[i - 16] + (rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3)) + w[i - 7] +
             (rotr(y, 17) ^ rotr(y, 19) ^ (y >> 10));
    }
    uint32_t a = h[0], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6],
             v = h[7], bb = h[1];
    for (unsigned i = 0; i < 64; ++i) {
      uint32_t t = v + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) +
                   ((e & f) ^ (~e & g)) + k[i] + w[i];
      uint32_t u = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) +
                   ((a & bb) ^ (a & c) ^ (bb & c));
      v = g;
      g = f;
      f = e;
      e = d + t;
      d = c;
      c = bb;
      bb = a;
      a = t + u;
    }
    h[0] += a;
    h[1] += bb;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += v;
  }
  for (unsigned i = 0; i < 8; ++i)
    put32(out + 4 * i, h[i]);
}
static struct journal_result blank(void) {
  struct journal_result r;
  memset(&r, 0, sizeof(r));
  return r;
}
static int fail(struct journal *j, int status, int stage, int error) {
  if (!j->failed) {
    if (j->result.effect == JOURNAL_PENDING)
      j->result.effect = JOURNAL_UNKNOWN;
    if (j->result.effect == JOURNAL_DURABLE)
      j->result.effect = JOURNAL_NOT_ATTEMPTED;
    j->result.status = status;
    j->result.stage = stage;
    j->result.error = error > 0 && error <= 4095 ? error : 0;
    j->failed = 1;
  }
  j->writable = 0;
  return 0;
}
static void closefd(struct journal *j, int fd) {
  if (close(fd) < 0) {
    int e = errno;
    if (j->failed) {
      if (!j->result.cleanup_error)
        j->result.cleanup_error = e > 0 && e <= 4095 ? e : 0;
    } else {
      j->result.effect = JOURNAL_UNKNOWN;
      (void)fail(j, JOURNAL_IO, JOURNAL_CLOSE, e);
    }
  }
}
static int metadata(struct journal *j, int fd, int directory) {
  struct stat s;
  if (fstat(fd, &s) < 0)
    return fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
  if ((directory ? !S_ISDIR(s.st_mode) : !S_ISREG(s.st_mode)) ||
      s.st_uid != geteuid() ||
      (s.st_mode & 07777) != (directory ? 0700 : 0600) ||
      (!directory && (s.st_nlink < 1 || s.st_nlink > 2)))
    return fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
  return 1;
}
static int bound(struct journal *j, int fd, const char *name) {
  struct stat a, b;
  if (fstat(fd, &a) < 0 || fstatat(j->dir, name, &b, AT_SYMLINK_NOFOLLOW) < 0)
    return fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
  if (a.st_dev != b.st_dev || a.st_ino != b.st_ino)
    return fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
  if (!metadata(j, fd, 0))
    return 0;
  if (strcmp(name, "anchor") && a.st_nlink != 1)
    return fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
  return 1;
}
static int getfile(struct journal *j, const char *name, int flags) {
  int fd =
      openat(j->dir, name, flags | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK, 0600);
  if (fd < 0) {
    (void)fail(j, errno == ENOENT ? JOURNAL_MISSING : JOURNAL_IO, JOURNAL_OPEN,
               errno);
    return -1;
  }
  if (!bound(j, fd, name)) {
    closefd(j, fd);
    return -1;
  }
  return fd;
}
static int serialize(const struct journal_binding *b, uint8_t p[192]) {
  if (!b || b->known != 255 || !b->generation || b->storage < 1 ||
      b->storage > 2 || b->boot_kind < 1 || b->boot_kind > 2 ||
      b->pg_major != 17 || b->durability != 1 || b->planned_slots != 3)
    return 0;
  memset(p, 0, 192);
  memcpy(p, b->source, 32);
  memcpy(p + 32, b->contract, 32);
  memcpy(p + 64, b->storage_lineage, 16);
  memcpy(p + 80, b->install, 16);
  memcpy(p + 96, b->state, 16);
  memcpy(p + 112, b->run, 16);
  memcpy(p + 128, b->attempt, 16);
  memcpy(p + 144, b->boot, 16);
  for (unsigned i = 0; i < 8; ++i) {
    unsigned start = i < 2 ? i * 32 : 64 + (i - 2) * 16, n = i < 2 ? 32 : 16;
    uint8_t any = 0;
    for (unsigned v = 0; v < n; ++v)
      any |= p[start + v];
    if (!any)
      return 0;
  }
  put64(p + 160, b->generation);
  put32(p + 168, b->known);
  p[172] = b->storage;
  p[173] = b->boot_kind;
  p[175] = 17;
  put32(p + 176, 1);
  put32(p + 180, 3);
  return 1;
}
static void frame(struct journal *j, uint8_t r[320], uint64_t seq) {
  memset(r, 0, 320);
  memcpy(r, "SCHJNL01", 8);
  r[9] = 1;
  r[11] = seq ? 2 : 1;
  put32(r + 12, 320);
  put64(r + 16, seq);
  if (seq)
    memcpy(r + 24, j->prefix.digest, 32);
  memcpy(r + 88, j->payload, 192);
  sha256(r + 88, 192, r + 56);
  memcpy(r + 280, "SJCOMMIT", 8);
  sha256(r, 288, r + 288);
}
static int validate(struct journal *j, const uint8_t r[320], uint64_t seq) {
  uint8_t digest[32], zero[32] = {0};
  if (memcmp(r, "SCHJNL01", 8) || r[8] || r[9] != 1 || r[10] ||
      r[11] != (seq ? 2 : 1))
    return fail(j, JOURNAL_UNSUPPORTED, JOURNAL_INSPECT, 0);
  if (be32(r + 12) > 4096)
    return fail(j, JOURNAL_OVERFLOW, JOURNAL_INSPECT, 0);
  if (be32(r + 12) != 320)
    return fail(j, JOURNAL_CORRUPT, JOURNAL_INSPECT, 0);
  uint64_t actual = be64(r + 16);
  if (actual != seq)
    return fail(j, actual < seq ? JOURNAL_DUPLICATE : JOURNAL_GAP,
                JOURNAL_INSPECT, 0);
  if (memcmp(r + 24, seq ? j->prefix.digest : zero, 32))
    return fail(j, JOURNAL_CORRUPT, JOURNAL_INSPECT, 0);
  sha256(r + 88, 192, digest);
  if (memcmp(digest, r + 56, 32) || memcmp(r + 280, "SJCOMMIT", 8))
    return fail(j, JOURNAL_CORRUPT, JOURNAL_INSPECT, 0);
  sha256(r, 288, digest);
  if (memcmp(digest, r + 288, 32))
    return fail(j, JOURNAL_CORRUPT, JOURNAL_INSPECT, 0);
  if (memcmp(r + 88, j->payload, 192))
    return fail(j, JOURNAL_BINDING, JOURNAL_INSPECT, 0);
  return 1;
}
/* EINTR retries only the interrupted syscall, never a transaction. Complete
 * positive short writes are counted and completed; zero/error retains bytes.
 * Calls may block: this synchronous primitive promises no deadline. A future
 * control worker must report pending without making a concurrent replacement.
 */
static int writeframe(struct journal *j, int fd, const uint8_t r[320]) {
  size_t offset = 0;
  j->result.effect = JOURNAL_PENDING;
  j->result.stage = JOURNAL_WRITE;
  while (offset < 320) {
    ssize_t n = write(fd, r + offset, 320 - offset);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0) {
      j->result.effect =
          offset ? JOURNAL_SHORT : (n < 0 ? JOURNAL_UNKNOWN : JOURNAL_FAILED);
      return fail(j, JOURNAL_IO, JOURNAL_WRITE, n < 0 ? errno : 0);
    }
    offset += (size_t)n;
    j->result.bytes = offset;
  }
  j->result.stage = JOURNAL_FILE_SYNC;
  if (fsync(fd) < 0) {
    j->result.effect = JOURNAL_UNKNOWN;
    return fail(j, JOURNAL_IO, JOURNAL_FILE_SYNC, errno);
  }
  return 1;
}
static int dirsync(struct journal *j) {
  j->result.stage = JOURNAL_DIR_SYNC;
  if (fsync(j->dir) < 0) {
    j->result.effect = JOURNAL_UNKNOWN;
    return fail(j, JOURNAL_IO, JOURNAL_DIR_SYNC, errno);
  }
  return 1;
}
struct journal_result journal_open(int selected_dirfd,
                                   const struct journal_binding *b,
                                   struct journal **out) {
  struct journal temp;
  memset(&temp, 0, sizeof(temp));
  temp.dir = -1;
  if (out)
    *out = NULL;
  if (!out || selected_dirfd < 0 || !serialize(b, temp.payload)) {
    (void)fail(&temp, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
    return temp.result;
  }
  temp.dir = openat(selected_dirfd, ".",
                    O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (temp.dir < 0) {
    (void)fail(&temp, JOURNAL_IO, JOURNAL_DIRECTORY, errno);
    return temp.result;
  }
  if (!metadata(&temp, temp.dir, 1)) {
    closefd(&temp, temp.dir);
    return temp.result;
  }
  if (flock(temp.dir, LOCK_EX | LOCK_NB) < 0) {
    (void)fail(&temp, JOURNAL_REFUSED, JOURNAL_LOCK, errno);
    closefd(&temp, temp.dir);
    return temp.result;
  }
  struct journal *j = calloc(1, sizeof(*j));
  if (!j) {
    (void)fail(&temp, JOURNAL_IO, JOURNAL_OPEN, ENOMEM);
    closefd(&temp, temp.dir);
    return temp.result;
  }
  *j = temp;
  *out = j;
  return j->result;
}
struct journal_result journal_initialize(struct journal *j) {
  if (!j) {
    struct journal_result r = blank();
    r.status = JOURNAL_REFUSED;
    return r;
  }
  if (j->failed)
    return j->result;
  if (!metadata(j, j->dir, 1))
    return j->result;
  if (j->writable || j->prefix.known) {
    (void)fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
    return j->result;
  }
  /* All three names must be absent. A leftover stage fences explicit new too.
   */
  const char *names[] = {"anchor.pending", "anchor", "essential"};
  for (unsigned i = 0; i < 3; ++i) {
    struct stat s;
    int observed = fstatat(j->dir, names[i], &s, AT_SYMLINK_NOFOLLOW);
    if (observed == 0) {
      (void)fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
      return j->result;
    }
    if (errno != ENOENT) {
      (void)fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
      return j->result;
    }
  }
  uint8_t r[320];
  frame(j, r, 0);
  int fd = getfile(j, "anchor.pending", O_WRONLY | O_CREAT | O_EXCL);
  if (fd < 0)
    return j->result;
  struct stat anchor_stat;
  int ok = writeframe(j, fd, r) && bound(j, fd, "anchor.pending");
  if (ok && fstat(fd, &anchor_stat) < 0)
    ok = fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
  closefd(j, fd);
  if (!ok || j->failed)
    return j->result;
  j->result.stage = JOURNAL_PUBLISH;
  if (linkat(j->dir, "anchor.pending", j->dir, "anchor", 0) < 0) {
    j->result.effect = JOURNAL_UNKNOWN;
    (void)fail(j, JOURNAL_IO, JOURNAL_PUBLISH, errno);
    return j->result;
  }
  struct stat published;
  if (fstatat(j->dir, "anchor", &published, AT_SYMLINK_NOFOLLOW) < 0) {
    j->result.effect = JOURNAL_UNKNOWN;
    (void)fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
    return j->result;
  }
  if (published.st_dev != anchor_stat.st_dev ||
      published.st_ino != anchor_stat.st_ino) {
    j->result.effect = JOURNAL_UNKNOWN;
    (void)fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
    return j->result;
  }
  if (!dirsync(j))
    return j->result;
  fd = getfile(j, "essential", O_WRONLY | O_CREAT | O_EXCL | O_APPEND);
  if (fd < 0) {
    j->result.effect = JOURNAL_UNKNOWN;
    return j->result;
  }
  struct stat essential_stat;
  ok = fstat(fd, &essential_stat) == 0;
  if (!ok)
    (void)fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
  if (ok)
    ok = fsync(fd) == 0;
  if (!ok) {
    j->result.effect = JOURNAL_UNKNOWN;
    (void)fail(j, JOURNAL_IO, JOURNAL_FILE_SYNC, errno);
  }
  closefd(j, fd);
  if (!ok || j->failed || !dirsync(j))
    return j->result;
  sha256(r, 320, j->prefix.digest);
  j->prefix.known = 1;
  j->essential_dev = essential_stat.st_dev;
  j->essential_ino = essential_stat.st_ino;
  j->writable = 1;
  j->result.effect = JOURNAL_DURABLE;
  j->result.stage = JOURNAL_DIR_SYNC;
  j->result.sequence_known = 1;
  return j->result;
}
struct journal_result journal_active(struct journal *j, uint64_t seq) {
  if (!j) {
    struct journal_result r = blank();
    r.status = JOURNAL_REFUSED;
    return r;
  }
  if (j->failed)
    return j->result;
  if (!j->writable) {
    (void)fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
    return j->result;
  }
  j->result = blank();
  if (!metadata(j, j->dir, 1))
    return j->result;
  j->result.prefix_bytes = j->prefix.bytes;
  j->result.last_sequence = j->prefix.sequence;
  j->result.sequence_known = 1;
  if (j->prefix.sequence == UINT64_MAX) {
    (void)fail(j, JOURNAL_OVERFLOW, JOURNAL_VALIDATE, 0);
    return j->result;
  }
  if (seq != j->prefix.sequence + 1) {
    (void)fail(j, seq <= j->prefix.sequence ? JOURNAL_DUPLICATE : JOURNAL_GAP,
               JOURNAL_VALIDATE, 0);
    return j->result;
  }
  if (j->prefix.bytes > JOURNAL_MAX_BYTES - 320) {
    (void)fail(j, JOURNAL_PRESSURE, JOURNAL_WRITE, 0);
    return j->result;
  }
  int fd = getfile(j, "essential", O_WRONLY | O_APPEND);
  if (fd < 0)
    return j->result;
  struct stat s;
  int ok = fstat(fd, &s) == 0;
  if (!ok)
    (void)fail(j, JOURNAL_IO, JOURNAL_VALIDATE, errno);
  else if (s.st_dev != j->essential_dev || s.st_ino != j->essential_ino ||
           s.st_size < 0 || (uint64_t)s.st_size != j->prefix.bytes)
    ok = fail(j, JOURNAL_REFUSED, JOURNAL_VALIDATE, 0);
  uint8_t r[320];
  frame(j, r, seq);
  if (ok)
    ok = writeframe(j, fd, r) && bound(j, fd, "essential");
  closefd(j, fd);
  if (!ok || j->failed || !dirsync(j))
    return j->result;
  sha256(r, 320, j->prefix.digest);
  j->prefix.bytes += 320;
  j->prefix.sequence = seq;
  j->result.prefix_bytes = j->prefix.bytes;
  j->result.last_sequence = seq;
  j->result.effect = JOURNAL_DURABLE;
  return j->result;
}
static int readframe(struct journal *j, int fd, uint8_t r[320], size_t *count) {
  *count = 0;
  while (*count < 320) {
    ssize_t n = read(fd, r + *count, 320 - *count);
    if (n < 0 && errno == EINTR)
      continue;
    if (n < 0)
      return fail(j, JOURNAL_IO, JOURNAL_READ, errno);
    if (!n)
      break;
    *count += (size_t)n;
  }
  return 1;
}
struct journal_result journal_inspect(struct journal *j) {
  if (!j) {
    struct journal_result r = blank();
    r.status = JOURNAL_REFUSED;
    return r;
  }
  j->writable = 0;
  memset(&j->prefix, 0, sizeof(j->prefix));
  if (j->failed)
    return j->result; /* preserve first error; reopen for new inspection */
  j->result = blank();
  if (!metadata(j, j->dir, 1))
    return j->result;
  int fd = getfile(j, "anchor", O_RDONLY);
  if (fd < 0)
    return j->result;
  uint8_t r[320], extra;
  size_t n;
  int ok = readframe(j, fd, r, &n);
  if (ok && n != 320)
    ok = fail(j, JOURNAL_TORN, JOURNAL_INSPECT, 0);
  if (ok)
    ok = validate(j, r, 0);
  if (ok) {
    ssize_t more = read(fd, &extra, 1);
    if (more < 0)
      ok = fail(j, JOURNAL_IO, JOURNAL_READ, errno);
    else if (more)
      ok = fail(j, JOURNAL_CORRUPT, JOURNAL_INSPECT, 0);
  }
  if (ok)
    ok = bound(j, fd, "anchor");
  closefd(j, fd);
  if (!ok || j->failed)
    return j->result;
  sha256(r, 320, j->prefix.digest);
  j->prefix.known = 1;
  j->result.sequence_known = 1;
  fd = getfile(j, "essential", O_RDONLY);
  if (fd < 0)
    return j->result;
  for (;;) {
    if (!readframe(j, fd, r, &n))
      break;
    if (!n)
      break;
    if (j->prefix.bytes > JOURNAL_MAX_BYTES - 320) {
      (void)fail(j, JOURNAL_PRESSURE, JOURNAL_INSPECT, 0);
      break;
    }
    if (n != 320) {
      (void)fail(j, JOURNAL_TORN, JOURNAL_INSPECT, 0);
      break;
    }
    if (!validate(j, r, j->prefix.sequence + 1))
      break;
    sha256(r, 320, j->prefix.digest);
    j->prefix.bytes += 320;
    ++j->prefix.sequence;
    j->result.prefix_bytes = j->prefix.bytes;
    j->result.last_sequence = j->prefix.sequence;
  }
  if (!j->failed)
    (void)bound(j, fd, "essential");
  closefd(j, fd);
  /* Structural success intentionally leaves effect NOT_ATTEMPTED. */
  return j->result;
}
struct journal_native_prefix journal_prefix(const struct journal *j) {
  struct journal_native_prefix p;
  memset(&p, 0, sizeof(p));
  return j ? j->prefix : p;
}
struct journal_result journal_close(struct journal **ptr) {
  if (!ptr || !*ptr) {
    struct journal_result r = blank();
    r.status = JOURNAL_REFUSED;
    return r;
  }
  struct journal *j = *ptr;
  closefd(j, j->dir);
  struct journal_result r = j->result;
  memset(j, 0, sizeof(*j));
  free(j);
  *ptr = NULL;
  return r;
}
