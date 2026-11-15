#define _GNU_SOURCE
#include "proc_observer.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

long long proc_now_ms(void) {
  struct timespec t;
  if (clock_gettime(CLOCK_MONOTONIC, &t))
    return -1;
  return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}
static int read_text(const char *path, char *out, size_t size) {
  int fd = open(path, O_RDONLY | O_CLOEXEC);
  if (fd < 0)
    return errno;
  size_t used = 0;
  while (used < size - 1) {
    ssize_t n = read(fd, out + used, size - 1 - used);
    if (n < 0) {
      if (errno == EINTR)
        continue;
      int e = errno;
      close(fd);
      return e;
    }
    if (!n)
      break;
    used += (size_t)n;
  }
  char extra;
  ssize_t more = read(fd, &extra, 1);
  int e = more < 0 ? errno : more ? EOVERFLOW : 0;
  close(fd);
  out[used] = 0;
  return e;
}
static int stat_identity(pid_t pid, struct identity *out, char *state) {
  char path[80], line[8192];
  snprintf(path, sizeof(path), "/proc/%d/stat", pid);
  int e = read_text(path, line, sizeof(line));
  if (e)
    return e;
  char *end = strrchr(line, ')');
  long first;
  if (!end || sscanf(line, "%ld", &first) != 1 || first != pid || end[1] != ' ')
    return EPROTO;
  char *save = NULL, *word = strtok_r(end + 2, " ", &save);
  long fields[3] = {0};
  for (int i = 0; i <= 19; ++i) {
    if (!word)
      return EPROTO;
    if (i == 0) {
      if (strlen(word) != 1)
        return EPROTO;
      *state = word[0];
    }
    if (i >= 1 && i <= 3) {
      char *tail;
      errno = 0;
      long v = strtol(word, &tail, 10);
      if (errno || *tail || v < 0 || v > INT_MAX)
        return EPROTO;
      fields[i - 1] = v;
    }
    if (i == 19) {
      char *tail;
      errno = 0;
      out->ticks = strtoull(word, &tail, 10);
      if (errno || (*tail && *tail != '\n') || word[0] == '-')
        return EPROTO;
    }
    word = strtok_r(NULL, " ", &save);
  }
  out->pid = pid;
  out->ppid = (pid_t)fields[0];
  out->pgid = (pid_t)fields[1];
  out->sid = (pid_t)fields[2];
  return 0;
}
struct observed proc_observe(pid_t pid) {
  struct observed value = {.state = OBS_UNKNOWN};
  if (pid <= 1) {
    value.error = EINVAL;
    return value;
  }
  char state, second_state, path[80], status[8192];
  int e = stat_identity(pid, &value.id, &state);
  if (e) {
    /* ENOENT alone may mean hidepid, not death. ESRCH from kill(0) is evidence.
     */
    if ((e == ENOENT || e == ESRCH) && kill(pid, 0) == -1 && errno == ESRCH)
      value.state = OBS_GONE;
    value.error = e;
    return value;
  }
  if (state == 'Z' || state == 'X' || state == 'x') {
    value.state = OBS_GONE;
    return value;
  }
  snprintf(path, sizeof(path), "/proc/%d/status", pid);
  if ((e = read_text(path, status, sizeof(status)))) {
    value.error = e;
    return value;
  }
  char *uid = strstr(status, "\nUid:");
  unsigned real_uid, effective, saved, fs;
  if (!uid ||
      sscanf(uid + 1, "Uid:\t%u\t%u\t%u\t%u", &real_uid, &effective, &saved,
             &fs) != 4 ||
      real_uid != effective || real_uid != saved || real_uid != fs) {
    value.error = EPROTO;
    return value;
  }
  value.id.uid =
      (uid_t)real_uid; /* kernel text, not PRoot's getuid()/stat emulation */
  e = read_text("/proc/sys/kernel/random/boot_id", value.id.boot,
                sizeof(value.id.boot));
  if (e) {
    value.error = e;
    return value;
  }
  value.id.boot[strcspn(value.id.boot, "\r\n")] = 0;
  if (!value.id.boot[0]) {
    value.error = EPROTO;
    return value;
  }
  struct identity again = {0};
  e = stat_identity(pid, &again, &second_state);
  if (e || again.ticks != value.id.ticks || again.ppid != value.id.ppid ||
      again.pgid != value.id.pgid || again.sid != value.id.sid ||
      second_state == 'Z') {
    value.error = e ? e : EAGAIN;
    return value;
  }
  value.state = OBS_LIVE;
  return value;
}
int proc_same_birth(const struct identity *a, const struct identity *b) {
  return a->pid > 1 && a->pid == b->pid && a->ticks == b->ticks &&
         a->uid == b->uid && a->boot[0] && !strcmp(a->boot, b->boot);
}
int proc_same_membership(const struct identity *a, const struct identity *b) {
  return proc_same_birth(a, b) && a->pgid == b->pgid && a->sid == b->sid;
}
void proc_scan(struct snapshot *out, uid_t uid, size_t limit,
               long long deadline, const volatile sig_atomic_t *cancel) {
  memset(out, 0, sizeof(*out));
  DIR *directory = opendir("/proc");
  if (!directory) {
    out->error = errno;
    return;
  }
  for (;;) {
    if (cancel && *cancel) {
      out->error = ECANCELED;
      break;
    }
    long long now = proc_now_ms();
    if (now < 0 || now >= deadline) {
      out->error = ETIMEDOUT;
      break;
    }
    errno = 0;
    struct dirent *entry = readdir(directory);
    if (!entry) {
      if (errno)
        out->error = errno;
      out->complete = !out->error && !out->unknown;
      break;
    }
    char *tail;
    errno = 0;
    long pid = strtol(entry->d_name, &tail, 10);
    if (errno || *tail || pid <= 1 || pid > INT_MAX)
      continue;
    if (++out->entries > limit) {
      out->error = EOVERFLOW;
      break;
    }
    /* Read kernel UID text first: foreign stat/exe permissions are not an
     * observation failure for this same-UID-only diagnostic. No guest stat UID.
     */
    char path[80], status[8192];
    snprintf(path, sizeof(path), "/proc/%ld/status", pid);
    int uid_error = read_text(path, status, sizeof(status));
    unsigned kernel_uid, effective, saved, fs;
    char *uid_line = uid_error ? NULL : strstr(status, "\nUid:");
    if (uid_line &&
        sscanf(uid_line + 1, "Uid: %u %u %u %u", &kernel_uid, &effective,
               &saved, &fs) == 4 &&
        (kernel_uid != uid || effective != uid || saved != uid || fs != uid))
      continue;
    struct observed value = proc_observe((pid_t)pid);
    if (value.state == OBS_GONE) {
      ++out->vanished;
      continue;
    }
    if (value.state == OBS_UNKNOWN) {
      ++out->unknown;
      if (!out->error)
        out->error = value.error;
      continue;
    }
    if (value.id.uid != uid)
      continue;
    if (out->count == PROC_MAX) {
      out->error = EOVERFLOW;
      break;
    }
    out->ids[out->count++] = value.id;
  }
  if (out->unknown && !out->error)
    out->error = EACCES;
  closedir(directory);
}
static const struct identity *find(const struct identity *ids, size_t count,
                                   pid_t pid) {
  for (size_t i = 0; i < count; ++i)
    if (ids[i].pid == pid)
      return &ids[i];
  return NULL;
}
int proc_discover(const struct snapshot *scan, const struct identity *root,
                  struct ledger *owned) {
  if (!scan->complete)
    return scan->error ? scan->error : EIO;
  const struct identity *in_scan = find(scan->ids, scan->count, root->pid);
  if (!in_scan || !proc_same_membership(in_scan, root))
    return ESTALE;
  struct observed parent = proc_observe(root->pid);
  if (parent.state != OBS_LIVE || !proc_same_membership(&parent.id, root))
    return ESTALE;
  if (!owned->count)
    owned->ids[owned->count++] = *root;
  for (size_t pass = 0; pass < OWNED_MAX; ++pass) {
    size_t before_count = owned->count;
    for (size_t n = 0; n < scan->count; ++n) {
      const struct identity *candidate = &scan->ids[n];
      if (find(owned->ids, owned->count, candidate->pid))
        continue;
      if (candidate->uid != root->uid || candidate->pgid != root->pgid ||
          candidate->sid != root->sid)
        continue;
      const struct identity *ancestor =
          find(owned->ids, owned->count, candidate->ppid);
      if (!ancestor)
        continue;
      struct observed a = proc_observe(ancestor->pid),
                      c = proc_observe(candidate->pid);
      if (a.state != OBS_LIVE || !proc_same_membership(&a.id, ancestor) ||
          c.state != OBS_LIVE || !proc_same_membership(&c.id, candidate) ||
          c.id.ppid != ancestor->pid)
        return EAGAIN;
      if (owned->count == OWNED_MAX)
        return EOVERFLOW;
      owned->ids[owned->count++] = *candidate;
    }
    if (before_count == owned->count)
      break;
  }
  /* A same-group unproven orphan is uncertainty, never an adopted member. */
  for (size_t n = 0; n < scan->count; ++n) {
    const struct identity *id = &scan->ids[n];
    if (id->pgid == root->pgid && id->sid == root->sid &&
        !find(owned->ids, owned->count, id->pid))
      return ENOLINK;
  }
  return 0;
}
int proc_cancel(const struct snapshot *scan, const struct ledger *owned,
                const struct identity *receipt, int sig, int timeout_ms,
                int *sent) {
  *sent = 0;
  if (timeout_ms <= 0 || timeout_ms > 2000)
    return EINVAL;
  if (!scan->complete)
    return scan->error ? scan->error : EIO;
  if (sig != SIGTERM && sig != SIGINT)
    return EINVAL;
  const struct identity *known = find(owned->ids, owned->count, receipt->pid);
  if (!known || !proc_same_membership(known, receipt))
    return ESTALE;
  struct observed before = proc_observe(receipt->pid);
  if (before.state != OBS_LIVE || !proc_same_membership(&before.id, receipt))
    return ESTALE;
#if defined(SYS_pidfd_open) && defined(SYS_pidfd_send_signal)
  int fd = (int)syscall(SYS_pidfd_open, receipt->pid, 0);
  if (fd < 0)
    return errno;
  struct observed after = proc_observe(receipt->pid);
  if (after.state != OBS_LIVE || !proc_same_membership(&after.id, receipt)) {
    close(fd);
    return ESTALE;
  }
  if (syscall(SYS_pidfd_send_signal, fd, sig, NULL, 0)) {
    int e = errno;
    close(fd);
    return e;
  }
  *sent = 1;
  struct pollfd p = {.fd = fd, .events = POLLIN};
  long long deadline = proc_now_ms() + timeout_ms;
  int result;
  do {
    long long left = deadline - proc_now_ms();
    result = poll(&p, 1, left > 0 ? (int)left : 0);
  } while (result < 0 && errno == EINTR && proc_now_ms() < deadline);
  int e = result > 0 && (p.revents & POLLIN) ? 0
          : result < 0                       ? errno
                                             : ETIMEDOUT;
  close(fd);
  return e;
#else
  (void)timeout_ms;
  return ENOSYS;
#endif
}
