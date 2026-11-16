#define _GNU_SOURCE
/* Separate feasibility executable. Existing 6242 observer is linked read-only.
 * Only the dedicated candidate owns/reaps its subtree. Oracle data never enters
 * candidate discovery; the candidate closes the oracle's read descriptor. */
#include "proc_observer.h"
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <signal.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#ifndef SUBREAPER_PROBE_SOURCE_ID
#define SUBREAPER_PROBE_SOURCE_ID "unknown"
#endif
#define ROLES 6
#define PEER 5
#define MASK(n) (1u << (n))
enum scenario {
  FAST,
  NESTED,
  SESSION,
  GROUP,
  INT_STOP,
  DENY_PRCTL,
  NO_PRCTL,
  DENY_PROC,
  NO_PROC,
  DENY_PIDFD,
  NO_PIDFD,
  TIMEOUT,
  OWNER_LOSS,
  STALE_OWNER,
  STALE_GROUP,
  STALE_SESSION,
  CASE_COUNT
};
static const char *case_names[] = {
    "fast-orphan",      "nested-orphan", "new-session",       "new-group",
    "sigint",           "deny-prctl",    "unsupported-prctl", "deny-proc",
    "unsupported-proc", "deny-pidfd",    "unsupported-pidfd", "timeout",
    "owner-loss",       "stale-owner",   "stale-group",       "stale-session"};
static int oracle_pipe[2], candidate_pipe[2], owner_command[2],
    leaf_command[ROLES][2];
static volatile sig_atomic_t stopped;
static void on_signal(int sig) { stopped = sig; }
struct witness {
  int role, kind, error, signal;
  struct identity id;
};
struct result {
  int kind, error, subreaper_before, subreaper_after, prctl_error;
  int fault_installed, fault_error, launched, intermediates_reaped, first_scan;
  int scan_complete, scan_error, admitted, sent, reaped, no_children, complete;
  int waitability_verified, owner_verified, live_after_failure;
  size_t scan_unknown;
  struct identity owner;
  struct ledger members;
};
_Static_assert(sizeof(struct witness) <= PIPE_BUF,
               "oracle message must be atomic");
_Static_assert(sizeof(struct result) <= PIPE_BUF,
               "candidate message must be atomic");
static void sleep_ms(int ms) {
  struct timespec t = {.tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L};
  while (nanosleep(&t, &t) && errno == EINTR) {
  }
}
static int write_message(int fd, const void *data, size_t length) {
  ssize_t n;
  do {
    n = write(fd, data, length);
  } while (n < 0 && errno == EINTR);
  return n == (ssize_t)length ? 0 : n < 0 ? errno : EIO;
}
static int read_message(int fd, void *data, size_t length, int ms) {
  long long deadline = proc_now_ms() + ms;
  for (;;) {
    long long left = deadline - proc_now_ms();
    if (left <= 0)
      return ETIMEDOUT;
    struct pollfd p = {.fd = fd, .events = POLLIN};
    int n = poll(&p, 1, (int)left);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      return n < 0 ? errno : ETIMEDOUT;
    ssize_t got = read(fd, data, length);
    if (got < 0 && errno == EINTR)
      continue;
    return got == (ssize_t)length ? 0 : got < 0 ? errno : EPIPE;
  }
}
/* Independent child-side witness parser: direct syscalls for ancestry/group;
 * own proc reads for birth/host UID. No proc_scan/candidate ledger as oracle.
 */
static int witness_self(struct identity *id) {
  memset(id, 0, sizeof(*id));
  id->pid = getpid();
  id->ppid = getppid();
  id->pgid = getpgrp();
  id->sid = getsid(0);
  FILE *f = fopen("/proc/self/stat", "r");
  if (!f)
    return errno;
  char text[8192];
  if (!fgets(text, sizeof(text), f)) {
    fclose(f);
    return EIO;
  }
  fclose(f);
  char *p = strrchr(text, ')');
  if (!p)
    return EPROTO;
  p += 2;
  for (int field = 3; field < 22; ++field) {
    p = strchr(p, ' ');
    if (!p)
      return EPROTO;
    ++p;
  }
  char *end;
  errno = 0;
  id->ticks = strtoull(p, &end, 10);
  if (errno || end == p || (*end != ' ' && *end != '\n'))
    return EPROTO;
  f = fopen("/proc/self/status", "r");
  if (!f)
    return errno;
  int found = 0;
  while (fgets(text, sizeof(text), f)) {
    unsigned uid;
    if (sscanf(text, "Uid: %u", &uid) == 1) {
      id->uid = uid;
      found = 1;
      break;
    }
  }
  fclose(f);
  if (!found)
    return EPROTO;
  f = fopen("/proc/sys/kernel/random/boot_id", "r");
  if (!f)
    return errno;
  if (!fgets(id->boot, sizeof(id->boot), f)) {
    fclose(f);
    return EIO;
  }
  fclose(f);
  id->boot[strcspn(id->boot, "\r\n")] = 0;
  return id->boot[0] ? 0 : EPROTO;
}
static void witness(int role, int kind, int error) {
  struct witness w = {
      .role = role, .kind = kind, .error = error, .signal = stopped};
  if (!error)
    w.error = witness_self(&w.id);
  if (write_message(oracle_pipe[1], &w, sizeof(w)))
    _exit(121);
}
static void synthetic_leaf(int role, enum scenario which, int born_fd) {
  stopped = 0;
  struct sigaction sa = {0};
  sa.sa_handler = on_signal;
  sigemptyset(&sa.sa_mask);
  sigaction(SIGTERM, &sa, NULL);
  sigaction(SIGINT, &sa, NULL);
  int e = 0;
  if ((role == PEER || (which == SESSION && role == 0)) && setsid() < 0)
    e = errno;
  if (which == GROUP && role == 0 && setpgid(0, 0))
    e = errno;
  witness(role, 1, e);
  if (born_fd >= 0) {
    char ready = 'r';
    if (write_message(born_fd, &ready, 1))
      _exit(122);
    close(born_fd);
  }
  if (e)
    _exit(123);
  long long deadline = proc_now_ms() + 10000;
  int released = 0;
  while (proc_now_ms() < deadline) {
    if (stopped && !(which == TIMEOUT && stopped == SIGTERM && role != PEER))
      break;
    if (which == TIMEOUT && stopped == SIGTERM)
      stopped = 0;
    struct pollfd p = {.fd = leaf_command[role][0], .events = POLLIN};
    int n = poll(&p, 1, 20);
    if (n < 0 && errno == EINTR)
      continue;
    if (n < 0) {
      e = errno;
      break;
    }
    if (!n)
      continue;
    char command;
    if (read(leaf_command[role][0], &command, 1) != 1 || command == 'Q') {
      released = 1;
      break;
    }
    if (command == 'H')
      witness(role, 2, 0);
    else if (command == 'G')
      witness(role, 3, setpgid(0, 0) ? errno : 0);
    else if (command == 'S')
      witness(role, 3, setsid() < 0 ? errno : 0);
  }
  if (!released && !stopped && proc_now_ms() >= deadline)
    e = ETIMEDOUT;
  witness(role, 4, e);
  _exit(e ? 124 : 0);
}
static int wait_exact(pid_t child, int ms) {
  long long deadline = proc_now_ms() + ms;
  do {
    int status;
    pid_t n = waitpid(child, &status, WNOHANG);
    if (n == child)
      return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : ECHILD;
    if (n < 0)
      return errno;
    sleep_ms(2);
  } while (proc_now_ms() < deadline);
  return ETIMEDOUT;
}
/* Each intermediate exits before candidate discovery. Only readiness bytes,
 * never PID/birth inventory, flow toward the candidate via wait completion. */
static void middle(enum scenario which, int nested) {
  close(candidate_pipe[1]);
  close(owner_command[0]);
again:
  witness(nested ? 3 : 4, 1, 0);
  if (nested) {
    pid_t inner = fork();
    if (inner == 0) {
      nested = 0;
      goto again;
    }
    _exit(inner < 0 || wait_exact(inner, 3000) ? 125 : 0);
  }
  int born[2];
  if (pipe(born))
    _exit(125);
  int count = which == NESTED ? 3 : 2;
  for (int role = 0; role < count; ++role) {
    pid_t child = fork();
    if (child < 0)
      _exit(125);
    if (child == 0) {
      close(born[0]);
      synthetic_leaf(role, which, born[1]);
    }
  }
  close(born[1]);
  for (int i = 0; i < count; ++i) {
    char ready;
    if (read_message(born[0], &ready, 1, 2000) || ready != 'r')
      _exit(125);
  }
  close(born[0]);
  _exit(0);
}
/* Real per-process seccomp errno injection, not a mocked success or Android
 * policy claim. Descendants already running are unaffected by later filters. */
static int deny_syscall(int nr, int error) {
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0))
    return errno;
  struct sock_filter code[] = {
      BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
      BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, (unsigned)nr, 0, 1),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (unsigned)error),
      BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW)};
  struct sock_fprog filter = {.len = sizeof(code) / sizeof(code[0]),
                              .filter = code};
  return prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &filter) ? errno : 0;
}
static void publish(struct result *r, int kind) {
  r->kind = kind;
  if (write_message(candidate_pipe[1], r, sizeof(*r)))
    _exit(126);
}
static int current_owner(const struct identity *owner) {
  struct observed now = proc_observe(getpid());
  return now.state == OBS_LIVE && proc_same_birth(owner, &now.id);
}
static void candidate(enum scenario which) {
  close(oracle_pipe[0]); /* cannot read independent expected inventory */
  close(candidate_pipe[0]);
  close(owner_command[1]);
  for (int i = 0; i < ROLES; ++i)
    close(leaf_command[i][1]);
  struct sigaction normal = {0};
  normal.sa_handler = SIG_DFL;
  sigemptyset(&normal.sa_mask);
  sigaction(SIGCHLD, &normal, NULL);
  sigaction(SIGTERM, &normal, NULL);
  struct result r = {0};
  struct snapshot *scan = NULL;
  if (setsid() < 0) {
    r.error = errno;
    goto done;
  }
  struct observed self = proc_observe(getpid());
  if (self.state != OBS_LIVE) {
    r.error = self.error ? self.error : EIO;
    goto done;
  }
  r.owner = self.id;
  if (prctl(PR_GET_CHILD_SUBREAPER, &r.subreaper_before)) {
    r.prctl_error = r.error = errno;
    goto done;
  }
  if (which == DENY_PRCTL || which == NO_PRCTL) {
    r.fault_error =
        deny_syscall(SYS_prctl, which == DENY_PRCTL ? EPERM : ENOSYS);
    if (r.fault_error) {
      r.error = r.fault_error;
      goto done;
    }
    r.fault_installed = 1;
  }
  if (prctl(PR_SET_CHILD_SUBREAPER, 1)) {
    r.prctl_error = r.error = errno;
    goto done;
  }
  int get_flag = prctl(PR_GET_CHILD_SUBREAPER, &r.subreaper_after);
  if (get_flag || r.subreaper_after != 1) {
    r.prctl_error = r.error = get_flag ? errno : EPROTO;
    goto done;
  }
  publish(&r,
          1); /* external direct parent independently verifies owner birth */
  char go;
  r.error = read_message(owner_command[0], &go, 1, 5000);
  if (r.error || go != 'G') {
    if (!r.error)
      r.error = EPROTO;
    goto done;
  }
  struct identity authority = r.owner;
  if (which == STALE_OWNER)
    ++authority.ticks;
  r.owner_verified = current_owner(&authority);
  if (!r.owner_verified) {
    r.error = ESTALE;
    goto done;
  }
  pid_t intermediate = fork();
  if (intermediate < 0) {
    r.error = errno;
    goto done;
  }
  if (intermediate == 0)
    middle(which, which == NESTED);
  r.launched = 1;
  r.error = wait_exact(intermediate, 4000);
  if (r.error)
    goto done;
  r.intermediates_reaped = 1;
  if (which == DENY_PROC || which == NO_PROC) {
    r.fault_error =
        deny_syscall(SYS_openat, which == DENY_PROC ? EACCES : ENOSYS);
    if (r.fault_error) {
      r.error = r.fault_error;
      goto done;
    }
    r.fault_installed = 1;
  }
  scan = calloc(1, sizeof(*scan));
  if (!scan) {
    r.error = ENOMEM;
    goto done;
  }
  r.first_scan = 1;
  proc_scan(scan, r.owner.uid, 32768, proc_now_ms() + 1500, NULL);
  r.scan_complete = scan->complete;
  r.scan_error = scan->error;
  r.scan_unknown = scan->unknown;
  if (!scan->complete) {
    r.error = scan->error ? scan->error : EIO;
    goto done;
  }
  if (!current_owner(&r.owner)) {
    r.error = ESTALE;
    goto done;
  }
  for (size_t i = 0; i < scan->count; ++i) {
    const struct identity *id = &scan->ids[i];
    if (id->ppid != r.owner.pid || id->uid != r.owner.uid ||
        strcmp(id->boot, r.owner.boot))
      continue;
    struct observed child = proc_observe(id->pid);
    if (child.state != OBS_LIVE || !proc_same_membership(id, &child.id) ||
        child.id.ppid != r.owner.pid) {
      r.error = EAGAIN;
      goto done;
    }
    siginfo_t info = {0};
    int wait_error =
        waitid(P_PID, (id_t)id->pid, &info, WEXITED | WNOHANG | WNOWAIT);
    if (wait_error || info.si_pid) {
      r.error = wait_error ? errno : ECHILD;
      goto done;
    }
    if (!current_owner(&r.owner)) {
      r.error = ESTALE;
      goto done;
    }
    if (r.members.count == OWNED_MAX) {
      r.error = EOVERFLOW;
      goto done;
    }
    r.members.ids[r.members.count++] = *id;
  }
  r.admitted = (int)r.members.count;
  r.waitability_verified = r.admitted > 0;
  if (!r.admitted) {
    r.error = ENOLINK;
    goto done;
  }
  publish(&r,
          2); /* observer inventory goes out, no oracle inventory comes in */
  r.error = read_message(owner_command[0], &go, 1, 5000);
  if (r.error || go != 'C') {
    if (!r.error)
      r.error = EPROTO;
    goto done;
  }
  if (which == DENY_PIDFD || which == NO_PIDFD) {
#if defined(SYS_pidfd_open)
    r.fault_error =
        deny_syscall(SYS_pidfd_open, which == DENY_PIDFD ? EPERM : ENOSYS);
#else
    r.fault_error = ENOSYS;
#endif
    if (r.fault_error) {
      r.error = r.fault_error;
      goto done;
    }
    r.fault_installed = 1;
  }
  /* Validate every admitted receipt before beginning the cancellation batch.
   * Each pidfd operation revalidates again; no ordering assumption about proc.
   */
  for (size_t i = 0; i < r.members.count; ++i) {
    struct observed live = proc_observe(r.members.ids[i].pid);
    if (live.state != OBS_LIVE ||
        !proc_same_membership(&live.id, &r.members.ids[i]) ||
        live.id.ppid != r.owner.pid) {
      r.error = live.state == OBS_UNKNOWN && live.error ? live.error : ESTALE;
      r.live_after_failure = live.state == OBS_LIVE &&
                             proc_same_birth(&live.id, &r.members.ids[i]);
      goto done;
    }
  }
  for (size_t i = 0; i < r.members.count; ++i) {
    if (!current_owner(&r.owner)) {
      r.error = ESTALE;
      goto done;
    }
    int sent;
    r.error = proc_cancel(scan, &r.members, &r.members.ids[i],
                          which == INT_STOP ? SIGINT : SIGTERM,
                          which == TIMEOUT ? 20 : 1000, &sent);
    r.sent += sent;
    if (r.error) {
      struct observed live = proc_observe(r.members.ids[i].pid);
      r.live_after_failure = live.state == OBS_LIVE &&
                             proc_same_birth(&live.id, &r.members.ids[i]);
      goto done;
    }
    r.error = wait_exact(r.members.ids[i].pid, 500);
    if (r.error)
      goto done;
    ++r.reaped;
  }
  siginfo_t remaining = {0};
  int waited = waitid(P_ALL, 0, &remaining, WEXITED | WNOHANG | WNOWAIT);
  r.no_children = waited == -1 && errno == ECHILD;
  if (!r.no_children || !current_owner(&r.owner)) {
    r.error = ENOLINK;
    goto done;
  }
  r.complete = 1;
done:
  free(scan);
  publish(&r, 3);
  _exit(r.complete ? 0 : 1);
}

struct oracle {
  struct witness w[ROLES], changed[ROLES];
  unsigned seen, changed_mask;
  int acknowledgements[ROLES];
};
static int read_oracle(struct oracle *o, unsigned mask, int wanted_role,
                       int kind) {
  long long deadline = proc_now_ms() + 3000;
  for (;;) {
    if ((!kind && (o->seen & mask) == mask) ||
        (kind == 4 && o->acknowledgements[wanted_role]))
      return 0;
    struct witness w;
    long long left = deadline - proc_now_ms();
    if (left <= 0)
      return ETIMEDOUT;
    int e = read_message(oracle_pipe[0], &w, sizeof(w), (int)left);
    if (e)
      return e;
    if (w.role < 0 || w.role >= ROLES || w.error)
      return w.error ? w.error : EPROTO;
    if (w.kind == 1) {
      if (o->seen & MASK(w.role))
        return EPROTO;
      o->w[w.role] = w;
      o->seen |= MASK(w.role);
    } else if (w.kind == 3) {
      if (!(o->seen & MASK(w.role)) ||
          !proc_same_birth(&w.id, &o->w[w.role].id))
        return ESTALE;
      o->changed[w.role] = w;
      o->changed_mask |= MASK(w.role);
    } else if (w.kind == 2 && (!(o->seen & MASK(w.role)) ||
                               !proc_same_birth(&w.id, &o->w[w.role].id)))
      return ESTALE;
    if (w.kind == 4)
      o->acknowledgements[w.role] = w.signal;
    if (w.kind == kind && w.role == wanted_role)
      return 0;
  }
}
static int command(int role, char letter) {
  return write_message(leaf_command[role][1], &letter, 1);
}
static int matches(const struct result *r, const struct oracle *o, int count) {
  if (r->members.count != (size_t)count)
    return 0;
  for (int role = 0; role < count; ++role) {
    int found = 0;
    for (size_t n = 0; n < r->members.count; ++n)
      if (proc_same_birth(&r->members.ids[n], &o->w[role].id) &&
          r->members.ids[n].ppid == r->owner.pid)
        found = 1;
    if (!found)
      return 0;
  }
  for (size_t n = 0; n < r->members.count; ++n)
    if (proc_same_birth(&r->members.ids[n], &o->w[PEER].id))
      return 0;
  return 1;
}
static int outside_cleanup(void) {
  /* This process owns only its disposable test fixture, never runtime statuses.
   */
  for (int i = 0; i < ROLES; ++i)
    if (leaf_command[i][1] >= 0) {
      (void)command(i, 'Q');
      close(leaf_command[i][1]);
    }
  if (owner_command[1] >= 0)
    close(owner_command[1]);
  long long deadline = proc_now_ms() + 12000;
  int e = 0;
  for (;;) {
    int status;
    pid_t child = waitpid(-1, &status, WNOHANG);
    if (child > 0) {
      if (!WIFEXITED(status) || WEXITSTATUS(status))
        e = ECHILD;
      continue;
    }
    if (child < 0) {
      if (errno != ECHILD)
        e = errno;
      break;
    }
    if (proc_now_ms() >= deadline) {
      e = ETIMEDOUT;
      break;
    }
    sleep_ms(2);
  }
  for (int i = 0; i < ROLES; ++i)
    if (leaf_command[i][0] >= 0)
      close(leaf_command[i][0]);
  if (oracle_pipe[0] >= 0)
    close(oracle_pipe[0]);
  if (oracle_pipe[1] >= 0)
    close(oracle_pipe[1]);
  if (candidate_pipe[0] >= 0)
    close(candidate_pipe[0]);
  if (candidate_pipe[1] >= 0)
    close(candidate_pipe[1]);
  if (owner_command[0] >= 0)
    close(owner_command[0]);
  return e;
}
static int owner_exit(pid_t owner, int *status) {
  long long deadline = proc_now_ms() + 1500;
  for (;;) {
    pid_t value = waitpid(owner, status, WNOHANG);
    if (value == owner)
      return 0;
    if (value < 0)
      return errno;
    if (proc_now_ms() >= deadline)
      return ETIMEDOUT;
    sleep_ms(2);
  }
}
static void string(const char *s) {
  putchar('"');
  for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
    if (*p == '"' || *p == '\\') {
      putchar('\\');
      putchar(*p);
    } else if (*p < 32 || *p > 126)
      printf("\\u%04x", *p);
    else
      putchar(*p);
  }
  putchar('"');
}
static void identity_json(const struct identity *id) {
  printf("{\"pid\":%d,\"ppid\":%d,\"pgid\":%d,\"sid\":%d,\"kernel_uid\":%u,"
         "\"start_ticks\":%llu,\"boot_id\":",
         id->pid, id->ppid, id->pgid, id->sid, (unsigned)id->uid, id->ticks);
  string(id->boot);
  putchar('}');
}
static int run_case(enum scenario which) {
  struct oracle o = {0};
  struct result r = {0};
  int e = 0, passed = 0, complete = 0;
  pid_t owner = -1;
  int owner_waited = 0, owner_status = 0;
  struct identity orphan_after_loss = {0};
  int peer_alive = 0, orphan_alive = 0, owner_lost = 0,
      outside_owner_verified = 0;
  int count = which == NESTED ? 3 : 2;
  for (int i = 0; i < ROLES; ++i)
    leaf_command[i][0] = leaf_command[i][1] = -1;
  oracle_pipe[0] = oracle_pipe[1] = candidate_pipe[0] = candidate_pipe[1] =
      owner_command[0] = owner_command[1] = -1;
  if (pipe(oracle_pipe) || pipe(candidate_pipe) || pipe(owner_command)) {
    e = errno;
    goto finish;
  }
  for (int i = 0; i < ROLES; ++i)
    if (pipe(leaf_command[i])) {
      e = errno;
      goto finish;
    }
  pid_t peer = fork();
  if (peer == 0) {
    close(oracle_pipe[0]);
    close(candidate_pipe[0]);
    close(candidate_pipe[1]);
    close(owner_command[0]);
    close(owner_command[1]);
    for (int i = 0; i < ROLES; ++i)
      close(leaf_command[i][1]);
    synthetic_leaf(PEER, which, -1);
  }
  if (peer < 0) {
    e = errno;
    goto finish;
  }
  owner = fork();
  if (owner == 0)
    candidate(which);
  if (owner < 0) {
    e = errno;
    goto finish;
  }
  close(oracle_pipe[1]);
  oracle_pipe[1] = -1;
  close(candidate_pipe[1]);
  candidate_pipe[1] = -1;
  close(owner_command[0]);
  owner_command[0] = -1;
  e = read_message(candidate_pipe[0], &r, sizeof(r), 5000);
  if (e)
    goto finish;
  if (r.kind == 1) {
    struct observed actual = proc_observe(owner);
    outside_owner_verified =
        actual.state == OBS_LIVE && proc_same_birth(&actual.id, &r.owner);
    if (!outside_owner_verified) {
      e = ESTALE;
      goto finish;
    }
    char go = 'G';
    e = write_message(owner_command[1], &go, 1);
    if (!e)
      e = read_message(candidate_pipe[0], &r, sizeof(r), 6000);
    if (e)
      goto finish;
  }
  if (r.kind == 2) {
    unsigned expected = MASK(PEER) | MASK(4);
    if (which == NESTED)
      expected |= MASK(3);
    for (int i = 0; i < count; ++i)
      expected |= MASK(i);
    e = read_oracle(&o, expected, 0, 0);
    if (e || !matches(&r, &o, count)) {
      if (!e)
        e = ENOLINK;
      goto finish;
    }
    if (which == OWNER_LOSS) {
      struct snapshot *scan = calloc(1, sizeof(*scan));
      if (!scan) {
        e = ENOMEM;
        goto finish;
      }
      struct ledger direct = {.count = 1};
      direct.ids[0] = r.owner;
      proc_scan(scan, r.owner.uid, 32768, proc_now_ms() + 1500, NULL);
      int sent;
      e = proc_cancel(scan, &direct, &r.owner, SIGTERM, 1000, &sent);
      free(scan);
      if (!e && sent) {
        owner_lost = 1;
        struct observed child = proc_observe(o.w[0].id.pid);
        orphan_alive = child.state == OBS_LIVE &&
                       proc_same_birth(&child.id, &o.w[0].id) &&
                       child.id.ppid != r.owner.pid;
        if (orphan_alive)
          orphan_after_loss = child.id;
        passed = orphan_alive;
      }
      goto finish;
    }
    if (which == STALE_GROUP || which == STALE_SESSION) {
      e = command(0, which == STALE_GROUP ? 'G' : 'S');
      if (!e)
        e = read_oracle(&o, 0, 0, 3);
      if (e)
        goto finish;
    }
    char go = 'C';
    e = write_message(owner_command[1], &go, 1);
    if (!e)
      e = read_message(candidate_pipe[0], &r, sizeof(r), 6000);
    if (e)
      goto finish;
  }
  if (r.kind != 3) {
    e = EPROTO;
    goto finish;
  }
  if (which == DENY_PRCTL || which == NO_PRCTL)
    passed = r.fault_installed && !r.launched &&
             r.prctl_error == (which == DENY_PRCTL ? EPERM : ENOSYS);
  else if (which == STALE_OWNER)
    passed = outside_owner_verified && !r.launched && r.error == ESTALE;
  else if (which == DENY_PROC || which == NO_PROC)
    passed = outside_owner_verified && r.fault_installed && r.launched &&
             !r.admitted && !r.sent &&
             r.error == (which == DENY_PROC ? EACCES : ENOSYS);
  else if (which == DENY_PIDFD || which == NO_PIDFD)
    passed = r.fault_installed && r.admitted == count && !r.sent &&
             r.error == (which == DENY_PIDFD ? EPERM : ENOSYS) &&
             r.live_after_failure;
  else if (which == TIMEOUT)
    passed = r.error == ETIMEDOUT && r.sent == 1 && r.live_after_failure;
  else if (which == STALE_GROUP || which == STALE_SESSION)
    passed = r.error == ESTALE && !r.sent && r.live_after_failure;
  else {
    complete = passed = r.complete && !r.error && r.reaped == count &&
                        r.no_children && r.sent == count;
    for (int i = 0; i < count && passed; ++i) {
      e = read_oracle(&o, 0, i, 4);
      /* Exit acknowledgements may arrive in any order; use accumulated
       * evidence. */
      if (e && o.acknowledgements[i] != (which == INT_STOP ? SIGINT : SIGTERM))
        passed = complete = 0;
    }
    for (int i = 0; i < count; ++i)
      if (o.acknowledgements[i] != (which == INT_STOP ? SIGINT : SIGTERM))
        passed = complete = 0;
  }
finish:
  if (owner > 1 && (r.kind == 3 || owner_lost)) {
    int exit_error = owner_exit(owner, &owner_status);
    owner_waited = !exit_error;
    int expected =
        owner_lost
            ? WIFSIGNALED(owner_status) && WTERMSIG(owner_status) == SIGTERM
            : WIFEXITED(owner_status) &&
                  WEXITSTATUS(owner_status) == (r.complete ? 0 : 1);
    if (exit_error || !expected) {
      e = exit_error ? exit_error : ECHILD;
      passed = complete = 0;
    }
  }
  if (oracle_pipe[0] >= 0 && leaf_command[PEER][1] >= 0) {
    int peer_error = command(PEER, 'H');
    if (!peer_error)
      peer_error = read_oracle(&o, 0, PEER, 2);
    peer_alive = !peer_error;
  }
  if (!peer_alive)
    passed = complete = 0;
  int cleanup_error = outside_cleanup();
  if (cleanup_error)
    passed = complete = 0;
  printf("{\"name\":");
  string(case_names[which]);
  printf(",\"control_pass\":%s,\"candidate_complete\":%s,\"error\":%d,"
         "\"candidate_errno\":%d,\"owner_verified_outside\":%s,\"owner\":",
         passed ? "true" : "false", complete ? "true" : "false", e, r.error,
         outside_owner_verified ? "true" : "false");
  identity_json(&r.owner);
  printf(",\"subreaper_before\":%d,\"subreaper_after\":%d,\"prctl_errno\":%d,"
         "\"fault_installed\":%s,\"fault_install_errno\":%d,\"launched\":%s,"
         "\"intermediates_reaped_before_first_scan\":%s,\"scan_complete\":%s,"
         "\"scan_errno\":%d,\"scan_unknown\":%zu,\"owner_verified_inside\":%s,"
         "\"waitability_verified\":%s,\"expected\":[",
         r.subreaper_before, r.subreaper_after, r.prctl_error,
         r.fault_installed ? "true" : "false", r.fault_error,
         r.launched ? "true" : "false",
         r.intermediates_reaped && r.first_scan ? "true" : "false",
         r.scan_complete ? "true" : "false", r.scan_error, r.scan_unknown,
         r.owner_verified ? "true" : "false",
         r.waitability_verified ? "true" : "false");
  int comma = 0;
  for (int i = 0; i < ROLES; ++i)
    if (o.seen & MASK(i)) {
      if (comma++)
        putchar(',');
      printf("{\"role\":%d,\"identity\":", i);
      identity_json(&o.w[i].id);
      putchar('}');
    }
  printf("],\"changed\":[");
  comma = 0;
  for (int i = 0; i < ROLES; ++i)
    if (o.changed_mask & MASK(i)) {
      if (comma++)
        putchar(',');
      printf("{\"role\":%d,\"identity\":", i);
      identity_json(&o.changed[i].id);
      putchar('}');
    }
  printf("],\"discovered\":[");
  for (size_t i = 0; i < r.members.count; ++i) {
    if (i)
      putchar(',');
    identity_json(&r.members.ids[i]);
  }
  printf(
      "],\"candidate_waited\":%s,\"candidate_exit_code\":%d,\"candidate_exit_"
      "signal\":%d",
      owner_waited ? "true" : "false",
      owner_waited && WIFEXITED(owner_status) ? WEXITSTATUS(owner_status) : -1,
      owner_waited && WIFSIGNALED(owner_status) ? WTERMSIG(owner_status) : 0);
  printf(",\"orphan_after_owner_loss\":");
  identity_json(&orphan_after_loss);
  printf(",\"sent\":%d,\"reaped\":%d,\"no_children\":%s,\"peer_survived\":%s,"
         "\"live_after_refusal\":%s,\"owner_lost\":%s,\"orphan_survived_owner_"
         "loss\":%s,\"oracle_intervention\":%s,\"cleanup_errno\":%d,"
         "\"acknowledged_signals\":[",
         r.sent, r.reaped, r.no_children ? "true" : "false",
         peer_alive ? "true" : "false", r.live_after_failure ? "true" : "false",
         owner_lost ? "true" : "false", orphan_alive ? "true" : "false",
         r.launched && !complete ? "true" : "false", cleanup_error);
  for (int i = 0; i < ROLES; ++i) {
    if (i)
      putchar(',');
    printf("%d", o.acknowledgements[i]);
  }
  printf("]}");
  return (passed ? 1 : 0) | (complete ? 2 : 0);
}
int main(int argc, char **argv) {
  int selected = -1;
  if (argc == 4 && !strcmp(argv[1], "subreaper-orphan-control") &&
      !strcmp(argv[2], "--case")) {
    for (int i = 0; i < CASE_COUNT; ++i)
      if (!strcmp(argv[3], case_names[i]))
        selected = i;
    if (selected < 0)
      return 2;
  } else if (argc != 2 || strcmp(argv[1], "subreaper-orphan-control"))
    return 2;
  /* This executable owns only its own test children; do not inherit an app's
   * ignored SIGCHLD disposition and silently auto-reap these test statuses. */
  struct sigaction default_child = {0};
  default_child.sa_handler = SIG_DFL;
  sigemptyset(&default_child.sa_mask);
  if (sigaction(SIGCHLD, &default_child, NULL))
    return 1;
  /* Outer oracle subreaper is containment only. It never informs admission. */
  int harness = 0, e = prctl(PR_SET_CHILD_SUBREAPER, 1) ? errno : 0;
  if (!e && prctl(PR_GET_CHILD_SUBREAPER, &harness))
    e = errno;
  struct observed self = proc_observe(getpid());
  printf("{\"schema\":1,\"probe\":\"subreaper-orphan-control\",\"source_id\":");
  string(SUBREAPER_PROBE_SOURCE_ID);
  struct utsname kernel;
  memset(&kernel, 0, sizeof(kernel));
  (void)uname(&kernel);
  printf(",\"kernel_release\":");
  string(kernel.release);
#ifdef __ANDROID__
  printf(",\"compiled_platform\":\"android\",\"compiled_api\":%d",
         __ANDROID_API__);
#else
  printf(",\"compiled_platform\":\"linux\",\"compiled_api\":0");
#endif
#if defined(__aarch64__)
  printf(",\"compiled_arch\":\"aarch64\"");
#elif defined(__x86_64__)
  printf(",\"compiled_arch\":\"x86_64\"");
#else
  printf(",\"compiled_arch\":\"other\"");
#endif
  printf(
      ",\"page_size\":%ld,\"reported_uid\":%u,\"kernel_uid\":%u,\"kernel_uid_"
      "known\":%s,\"oracle_subreaper\":%s,\"harness_errno\":%d,\"cases\":[",
      sysconf(_SC_PAGESIZE), (unsigned)getuid(), (unsigned)self.id.uid,
      self.state == OBS_LIVE ? "true" : "false",
      harness == 1 ? "true" : "false", e);
  int controls = 1, complete = 1, first = 1;
  if (e || harness != 1 || self.state != OBS_LIVE) {
    printf("{\"name\":\"prerequisite\",\"control_pass\":false,\"candidate_"
           "complete\":false,\"errno\":%d}",
           e ? e : self.error);
    controls = complete = 0;
  } else
    for (int i = 0; i < CASE_COUNT; ++i) {
      if (selected >= 0 && selected != i)
        continue;
      if (!first)
        putchar(',');
      first = 0;
      int outcome = run_case((enum scenario)i);
      if (!(outcome & 1))
        controls = 0;
      if (!(outcome & 2))
        complete = 0;
    }
  printf("],\"controls_pass\":%s,\"selected_candidate_cases_complete\":%s,"
         "\"owner_death_solution\":false,\"production_ownership\":false,"
         "\"runtime_acceptance\":false,\"android_acceptance\":\"pending\"}\n",
         controls ? "true" : "false", complete ? "true" : "false");
  return complete && controls ? 0 : 1;
}
