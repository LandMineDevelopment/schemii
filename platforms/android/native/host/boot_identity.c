#define _GNU_SOURCE
#include "boot_identity.h"

#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <linux/stat.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <unistd.h>

static int hex_value(unsigned char c) {
  if (c >= '0' && c <= '9')
    return c - '0';
  if (c >= 'a' && c <= 'f')
    return c - 'a' + 10;
  return -1;
}

enum boot_identity_status boot_identity_parse(
    const unsigned char *text, size_t length,
    unsigned char uuid[BOOT_IDENTITY_BYTES]) {
  unsigned char parsed[BOOT_IDENTITY_BYTES] = {0};
  memset(uuid, 0, BOOT_IDENTITY_BYTES);
  if (!length)
    return BOOT_ID_EMPTY;
  if (length < 36)
    return BOOT_ID_SHORT;
  if (length > BOOT_IDENTITY_TEXT_MAX)
    return BOOT_ID_OVERSIZED;
  if (!text || (length == 37 && text[36] != '\n'))
    return BOOT_ID_MALFORMED;
  size_t digits = 0;
  for (size_t i = 0; i < 36; ++i) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (text[i] != '-')
        return BOOT_ID_MALFORMED;
      continue;
    }
    int v = hex_value(text[i]);
    if (v < 0)
      return BOOT_ID_MALFORMED;
    parsed[digits / 2] |= (unsigned char)(v << (digits % 2 ? 0 : 4));
    ++digits;
  }
  if ((parsed[6] >> 4) != 4 || (parsed[8] & 0xc0) != 0x80)
    return BOOT_ID_MALFORMED;
  memcpy(uuid, parsed, sizeof(parsed));
  return BOOT_ID_OK;
}

static void fail(struct boot_identity_result *r, enum boot_identity_status status,
                 enum boot_identity_stage stage, int error) {
  /* A cleanup failure must not replace the original failure. */
  if (r->status == BOOT_ID_OK) {
    r->status = status;
    r->stage = stage;
    r->error = error;
  }
}

static void close_node(struct boot_identity_result *r, int fd) {
  if (close(fd) == 0)
    return;
  int error = errno;
  if (r->status == BOOT_ID_OK)
    fail(r, BOOT_ID_CLOSE_FAILED, BOOT_ID_STAGE_CLOSE, error);
  else if (!r->cleanup_error)
    r->cleanup_error = error;
  /* Never retry close: the descriptor may already have been released. */
}

static int verify_node(struct boot_identity_result *r,
                       struct boot_identity_sample *s, int fd, unsigned node) {
  struct statfs fs;
  if (fstatfs(fd, &fs) < 0) {
    fail(r, BOOT_ID_ORIGIN_FAILED, BOOT_ID_STAGE_FSTATFS, errno);
    return 0;
  }
  if ((unsigned long)fs.f_type != PROC_SUPER_MAGIC) {
    s->origin = BOOT_ID_PROC_REJECTED;
    fail(r, BOOT_ID_ORIGIN_REJECTED, BOOT_ID_STAGE_FSTATFS, 0);
    return 0;
  }
  ++s->procfs_checks;
  struct stat st;
  if (fstat(fd, &st) < 0) {
    fail(r, BOOT_ID_ORIGIN_FAILED, BOOT_ID_STAGE_FSTAT, errno);
    return 0;
  }
  if ((node < BOOT_IDENTITY_PATH_NODES && !S_ISDIR(st.st_mode)) ||
      (node == BOOT_IDENTITY_PATH_NODES && !S_ISREG(st.st_mode))) {
    s->origin = BOOT_ID_PROC_REJECTED;
    fail(r, BOOT_ID_ORIGIN_REJECTED, BOOT_ID_STAGE_FSTAT, 0);
    return 0;
  }
  ++s->type_checks;
#if defined(SYS_statx) && defined(STATX_MNT_ID)
  struct statx sx = {0};
  if (syscall(SYS_statx, fd, "", AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW,
              STATX_MNT_ID, &sx) < 0) {
    fail(r, BOOT_ID_ORIGIN_FAILED, BOOT_ID_STAGE_STATX, errno);
    return 0;
  }
  if (!(sx.stx_mask & STATX_MNT_ID)) {
    fail(r, BOOT_ID_ORIGIN_UNKNOWN, BOOT_ID_STAGE_STATX, 0);
    return 0;
  }
  if (node == 1)
    s->root_mount_id = sx.stx_mnt_id;
  if (sx.stx_mnt_id != s->root_mount_id) {
    s->origin = BOOT_ID_PROC_REJECTED;
    fail(r, BOOT_ID_ORIGIN_REJECTED, BOOT_ID_STAGE_STATX, 0);
    return 0;
  }
  ++s->mount_checks;
  if (node == BOOT_IDENTITY_PATH_NODES) {
    s->leaf_mount_id = sx.stx_mnt_id;
    s->origin = BOOT_ID_PROC_VERIFIED;
  }
  return 1;
#else
  fail(r, BOOT_ID_ORIGIN_UNKNOWN, BOOT_ID_STAGE_STATX, 0);
  return 0;
#endif
}

static void read_sample(struct boot_identity_result *r, unsigned sample,
                        unsigned char uuid[BOOT_IDENTITY_BYTES]) {
  static const char *const nodes[] = {"/proc", "sys", "kernel", "random",
                                      "boot_id"};
  struct boot_identity_sample *s = &r->samples[sample];
  int parent = AT_FDCWD;
  r->failed_sample = sample + 1;
  for (unsigned i = 0; i < BOOT_IDENTITY_PATH_NODES; ++i) {
    r->failed_node = i + 1;
    int flags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK;
    if (i + 1 < BOOT_IDENTITY_PATH_NODES)
      flags |= O_DIRECTORY;
    int fd = openat(parent, nodes[i], flags);
    if (fd < 0)
      fail(r, BOOT_ID_OPEN_FAILED, BOOT_ID_STAGE_OPEN, errno);
    if (parent != AT_FDCWD)
      close_node(r, parent);
    if (fd < 0)
      return;
    ++s->opened_nodes;
    if (r->status != BOOT_ID_OK || !verify_node(r, s, fd, i + 1)) {
      close_node(r, fd);
      return;
    }
    if (i + 1 < BOOT_IDENTITY_PATH_NODES) {
      parent = fd;
      continue;
    }
    unsigned char text[BOOT_IDENTITY_READ_MAX];
    while (s->bytes_read < sizeof(text)) {
      ++s->read_calls;
      ssize_t n = read(fd, text + s->bytes_read, sizeof(text) - s->bytes_read);
      if (n < 0) {
        fail(r, BOOT_ID_READ_FAILED, BOOT_ID_STAGE_READ, errno);
        break;
      }
      if (!n)
        break;
      s->bytes_read += (size_t)n;
    }
    if (r->status == BOOT_ID_OK) {
      enum boot_identity_status status =
          boot_identity_parse(text, s->bytes_read, uuid);
      if (status != BOOT_ID_OK)
        fail(r, status, BOOT_ID_STAGE_PARSE, 0);
    }
    close_node(r, fd);
  }
}

struct boot_identity_result boot_identity_read(void) {
  struct boot_identity_result r = {
      .method = BOOT_ID_NATIVE_FIXED_PROC,
      .context = BOOT_ID_CALLER_MOUNT_NAMESPACE};
  unsigned char first[BOOT_IDENTITY_BYTES], second[BOOT_IDENTITY_BYTES];
  read_sample(&r, 0, first);
  if (r.status != BOOT_ID_OK)
    return r;
  read_sample(&r, 1, second);
  if (r.status != BOOT_ID_OK)
    return r;
  r.failed_node = 0;
  if (memcmp(first, second, sizeof(first)) ||
      r.samples[0].root_mount_id != r.samples[1].root_mount_id) {
    fail(&r, BOOT_ID_INCONSISTENT, BOOT_ID_STAGE_COMPARE, 0);
    return r;
  }
  memcpy(r.uuid, first, sizeof(first));
  r.knownness = BOOT_ID_KNOWN;
  r.failed_sample = 0;
  return r;
}
