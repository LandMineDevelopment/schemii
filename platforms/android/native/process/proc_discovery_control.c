#define _GNU_SOURCE
/* Disposable topology and independent pipe oracle. Never hosts a real service.
 */
#include "proc_observer.h"
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <unistd.h>
#ifndef PROCESS_PROBE_SOURCE_ID
#define PROCESS_PROBE_SOURCE_ID "unknown"
#endif
#define ROLES 7
#define BIT(n) (1u << (n))
static int commands[ROLES][2], events[2];
static const char *executable;
static volatile sig_atomic_t stopping;
static pid_t creator_tid;
static void stop_signal(int sig) { stopping = sig; }
struct witness {
  int kind, role, sig, error;
  pid_t creator_tid;
  struct identity id;
};
struct fixture {
  struct witness witnesses[ROLES];
  unsigned seen;
  struct identity root;
  pid_t root_pid, peer_pid;
  int terminal_signals[ROLES];
};
static void text(const char *s) {
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
static void print_identity(const struct identity *id) {
  printf("{\"pid\":%d,\"ppid\":%d,\"pgid\":%d,\"sid\":%d,\"kernel_uid\":%u,"
         "\"start_ticks\":%llu,\"boot_id\":",
         id->pid, id->ppid, id->pgid, id->sid, (unsigned)id->uid, id->ticks);
  text(id->boot);
  putchar('}');
}
/* The oracle reads its own birth independently; PID/ancestry/group come from
 * direct syscalls. Its pipe data is never passed to proc_scan/proc_discover. */
static int own_witness(struct identity *id) {
  memset(id, 0, sizeof(*id));
  id->pid = getpid();
  id->ppid = getppid();
  id->pgid = getpgrp();
  id->sid = getsid(0);
  FILE *f = fopen("/proc/self/stat", "r");
  if (!f)
    return errno;
  char buffer[8192];
  if (!fgets(buffer, sizeof(buffer), f)) {
    fclose(f);
    return EIO;
  }
  fclose(f);
  char *p = strrchr(buffer, ')');
  if (!p)
    return EPROTO;
  p += 2;
  for (int field = 3; field < 22; ++field) {
    p = strchr(p, ' ');
    if (!p)
      return EPROTO;
    ++p;
  }
  char *tail;
  errno = 0;
  id->ticks = strtoull(p, &tail, 10);
  if (errno || tail == p || (*tail != ' ' && *tail != '\n'))
    return EPROTO;
  f = fopen("/proc/self/status", "r");
  if (!f)
    return errno;
  int found = 0;
  while (fgets(buffer, sizeof(buffer), f)) {
    unsigned uid;
    if (sscanf(buffer, "Uid: %u", &uid) == 1) {
      id->uid = (uid_t)uid;
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
static void emit(int role, int kind, int error) {
  struct witness value = {.kind = kind,
                          .role = role,
                          .sig = stopping,
                          .error = error,
                          .creator_tid = creator_tid};
  if (!error)
    value.error = own_witness(&value.id);
  ssize_t n;
  do {
    n = write(events[1], &value, sizeof(value));
  } while (n < 0 && errno == EINTR);
  if (n != (ssize_t)sizeof(value))
    _exit(121);
}
static int receive(struct fixture *f, int wanted_kind, int wanted_role,
                   unsigned mask) {
  long long deadline = proc_now_ms() + 2000;
  for (;;) {
    if (!wanted_kind && (f->seen & mask) == mask)
      return 0;
    long long left = deadline - proc_now_ms();
    if (left <= 0)
      return ETIMEDOUT;
    struct pollfd p = {.fd = events[0], .events = POLLIN};
    int n = poll(&p, 1, (int)left);
    if (n < 0 && errno == EINTR)
      continue;
    if (n <= 0)
      return n < 0 ? errno : ETIMEDOUT;
    struct witness w;
    ssize_t got = read(events[0], &w, sizeof(w));
    if (got != sizeof(w) || w.role < 0 || w.role >= ROLES)
      return EPROTO;
    if (w.error)
      return w.error;
    if (w.kind == 2 && (!(f->seen & BIT(w.role)) ||
                        !proc_same_birth(&w.id, &f->witnesses[w.role].id)))
      return ESTALE;
    if (w.kind == 1 || w.kind == 3) {
      f->witnesses[w.role] = w;
      f->seen |= BIT(w.role);
    }
    if (w.kind == 5)
      f->terminal_signals[w.role] = w.sig;
    if (w.kind == wanted_kind && w.role == wanted_role)
      return 0;
  }
}
static void leaf(int role);
static void child_start(int role) {
  close(events[0]);
  for (int i = 0; i < ROLES; ++i)
    close(commands[i][1]);
  if ((role == 0 || role == 4) && setsid() < 0) {
    emit(role, 1, errno);
    _exit(122);
  }
  leaf(role);
}
struct thread_args {
  char fd[24], event[24], tid[24];
  pid_t pid;
};
static void *fork_thread(void *data) {
  struct thread_args *args = data;
  snprintf(args->tid, sizeof(args->tid), "%ld", syscall(SYS_gettid));
  args->pid = fork();
  if (args->pid == 0) {
    /* Only async-signal-safe calls between the multithreaded fork and exec. */
    char *argv[] = {(char *)executable, "--internal-thread-leaf",
                    args->fd,           args->event,
                    args->tid,          NULL};
    execv(executable, argv);
    _exit(123);
  }
  return NULL;
}
static void leaf(int role) {
begin:;
  stopping = 0;
  struct sigaction action = {0};
  action.sa_handler = stop_signal;
  sigemptyset(&action.sa_mask);
  sigaction(SIGTERM, &action, NULL);
  sigaction(SIGINT, &action, NULL);
  if (role == 0) {
    pid_t branch = fork();
    if (branch < 0) {
      emit(role, 1, errno);
      _exit(124);
    }
    if (branch == 0) {
      role = 1;
      goto begin;
    }
    struct thread_args args = {0};
    snprintf(args.fd, sizeof(args.fd), "%d", commands[3][0]);
    snprintf(args.event, sizeof(args.event), "%d", events[1]);
    pthread_t thread;
    int e = pthread_create(&thread, NULL, fork_thread, &args);
    if (e) {
      emit(role, 1, e);
      _exit(124);
    }
    e = pthread_join(thread, NULL);
    if (e || args.pid < 0) {
      emit(role, 1, e ? e : ECHILD);
      _exit(124);
    }
  } else if (role == 1) {
    pid_t child = fork();
    if (child < 0) {
      emit(role, 1, errno);
      _exit(124);
    }
    if (child == 0) {
      role = 2;
      goto begin;
    }
  }
  emit(role, 1, 0);
  long long deadline = proc_now_ms() + 8000;
  int ignore_term = 0, released = 0, leaf_error = 0;
  while (proc_now_ms() < deadline) {
    if (stopping && !(ignore_term && stopping == SIGTERM))
      break;
    if (ignore_term && stopping == SIGTERM)
      stopping = 0;
    struct pollfd p = {.fd = commands[role][0], .events = POLLIN};
    int n = poll(&p, 1, 20);
    if (n < 0 && errno == EINTR)
      continue;
    if (n < 0) {
      leaf_error = errno;
      break;
    }
    if (!n)
      continue;
    char command;
    if (read(commands[role][0], &command, 1) != 1 || command == 'Q') {
      released = 1;
      break;
    }
    if (command == 'H')
      emit(role, 2, 0);
    else if (command == 'G')
      emit(role, 3, setpgid(0, 0) < 0 ? errno : 0);
    else if (command == 'S')
      emit(role, 3, setsid() < 0 ? errno : 0);
    else if (command == 'I') {
      ignore_term = 1;
      emit(role, 2, 0);
    } else if (command == 'U' && role == 0) {
      /* No scan occurs until the intermediate is reaped. The pipe oracle
       * knows its grandchild, but the candidate never witnesses that edge. */
      pid_t middle = fork();
      if (middle == 0) {
        emit(5, 1, 0);
        pid_t child = fork();
        if (child < 0)
          _exit(124);
        if (child == 0) {
          role = 6;
          goto begin;
        }
        _exit(0);
      }
      if (middle < 0) {
        emit(role, 4, errno);
        continue;
      }
      int status;
      if (waitpid(middle, &status, 0) != middle || !WIFEXITED(status) ||
          WEXITSTATUS(status))
        emit(role, 4, ECHILD);
      else
        emit(role, 4, 0);
    }
  }
  if (!released && !stopping && proc_now_ms() >= deadline)
    leaf_error = ETIMEDOUT;
  emit(role, 5, leaf_error);
  _exit(leaf_error ? 125 : 0);
}
static int send_command(int role, char command) {
  ssize_t n;
  do {
    n = write(commands[role][1], &command, 1);
  } while (n < 0 && errno == EINTR);
  return n == 1 ? 0 : errno ? errno : EIO;
}
static int setup(struct fixture *f) {
  memset(f, 0, sizeof(*f));
  for (int i = 0; i < ROLES; ++i)
    commands[i][0] = commands[i][1] = -1;
  events[0] = events[1] = -1;
  if (pipe(events))
    return errno;
  for (int i = 0; i < ROLES; ++i)
    if (pipe(commands[i]))
      return errno;
  f->root_pid = fork();
  if (f->root_pid == 0)
    child_start(0);
  if (f->root_pid < 0)
    return errno;
  f->peer_pid = fork();
  if (f->peer_pid == 0)
    child_start(4);
  if (f->peer_pid < 0)
    return errno;
  close(events[1]);
  events[1] = -1;
  /* Retain read ends: EPIPE is not a substitute for a probe verdict. */
  int e = receive(f, 0, 0, BIT(0) | BIT(1) | BIT(2) | BIT(3) | BIT(4));
  if (e)
    return e;
  struct observed root = proc_observe(f->root_pid);
  if (root.state != OBS_LIVE)
    return root.error ? root.error : EIO;
  f->root = root.id;
  return 0;
}
static int cleanup(void) {
  /* Oracle release is not candidate cancellation. Closing all writer handles
   * also releases a child whose birth report was lost. No broad/group kill. */
  for (int i = 0; i < ROLES; ++i)
    if (commands[i][1] >= 0) {
      (void)send_command(i, 'Q');
      close(commands[i][1]);
      commands[i][1] = -1;
    }
  long long deadline = proc_now_ms() + 8500;
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
    struct timespec delay = {0, 5000000};
    nanosleep(&delay, NULL);
  }
  for (int i = 0; i < ROLES; ++i)
    if (commands[i][0] >= 0)
      close(commands[i][0]);
  if (events[0] >= 0)
    close(events[0]);
  if (events[1] >= 0)
    close(events[1]);
  return e;
}
static const char *cases[] = {"stable",
                              "pthread",
                              "witnessed-parent-loss",
                              "leader-loss",
                              "unwitnessed-orphan",
                              "group-change",
                              "session-change",
                              "stale-birth",
                              "stale-boot",
                              "scan-limit",
                              "scan-deadline",
                              "scan-cancel",
                              "unknown",
                              "sigterm",
                              "sigint",
                              "cancel-timeout",
                              "complete-helper-stop"};
static int has(const struct ledger *owned, const struct identity *id) {
  for (size_t i = 0; i < owned->count; ++i)
    if (proc_same_birth(&owned->ids[i], id))
      return 1;
  return 0;
}
static int stable_matches(const struct fixture *f, const struct ledger *owned) {
  if (owned->count != 4)
    return 0;
  for (int i = 0; i < 4; ++i)
    if (!has(owned, &f->witnesses[i].id))
      return 0;
  return !has(owned, &f->witnesses[4].id);
}
static int run_case(const char *name) {
  struct fixture f;
  struct snapshot *scan = calloc(1, sizeof(*scan));
  struct ledger owned = {0};
  int e = scan ? setup(&f) : ENOMEM;
  int passed = !e, complete = !e, refusal = 0, sent = 0, signal_error = 0;
  struct observed observation = {.state = OBS_UNKNOWN, .error = EIO};
  int peer_alive = 0, refused_alive = 0, oracle_intervention = 0;
  const char *reason = "";
  if (!scan) {
    printf("{\"name\":");
    text(name);
    printf(",\"control_pass\":false,\"complete\":false,\"error\":%d}", e);
    return 0;
  }
  if (e) {
    reason = "fixture_or_observation_unavailable";
    goto finish;
  }
  observation = proc_observe(f.root_pid);
  proc_scan(scan, f.root.uid, 32768, proc_now_ms() + 1500, NULL);
  e = proc_discover(scan, &f.root, &owned);
  if (e || !stable_matches(&f, &owned)) {
    passed = complete = 0;
    reason = "initial_inventory_mismatch";
    goto finish;
  }
  if (!strcmp(name, "witnessed-parent-loss") || !strcmp(name, "leader-loss")) {
    int role = !strcmp(name, "leader-loss") ? 0 : 1;
    e = send_command(role, 'Q');
    if (!e)
      e = receive(&f, 5, role, 0);
    int survivor = role == 0 ? 1 : 2;
    long long deadline = proc_now_ms() + 1000;
    struct observed orphan = {.state = OBS_UNKNOWN};
    while (!e && proc_now_ms() < deadline) {
      orphan = proc_observe(f.witnesses[survivor].id.pid);
      if (orphan.state == OBS_LIVE &&
          orphan.id.ppid != f.witnesses[role].id.pid)
        break;
      struct timespec delay = {0, 1000000};
      nanosleep(&delay, NULL);
    }
    passed = !e && orphan.state == OBS_LIVE && has(&owned, &orphan.id) &&
             orphan.id.ppid != f.witnesses[role].id.pid;
    proc_scan(scan, f.root.uid, 32768, proc_now_ms() + 1500, NULL);
    if (passed) {
      const struct identity *receipt = NULL;
      for (size_t i = 0; i < owned.count; ++i)
        if (owned.ids[i].pid == orphan.id.pid)
          receipt = &owned.ids[i];
      signal_error =
          receipt ? proc_cancel(scan, &owned, receipt, SIGTERM, 1000, &sent)
                  : ESTALE;
      if (!signal_error)
        e = receive(&f, 5, survivor, 0);
      passed = !signal_error && sent && !e &&
               f.terminal_signals[survivor] == SIGTERM;
    }
  } else if (!strcmp(name, "unwitnessed-orphan")) {
    e = send_command(0, 'U');
    if (!e)
      e = receive(&f, 4, 0, 0);
    if (!e)
      e = receive(&f, 0, 0, BIT(5) | BIT(6));
    proc_scan(scan, f.root.uid, 32768, proc_now_ms() + 1500, NULL);
    if (!e)
      e = proc_discover(scan, &f.root, &owned);
    refusal = e == ENOLINK && !has(&owned, &f.witnesses[6].id);
    passed = refusal;
    complete = 0;
    oracle_intervention = 1;
    reason = "unwitnessed_orphan_lineage_unproven";
  } else if (!strcmp(name, "group-change") || !strcmp(name, "session-change")) {
    e = send_command(3, !strcmp(name, "group-change") ? 'G' : 'S');
    if (!e)
      e = receive(&f, 3, 3, 0);
    const struct identity *old = NULL;
    for (size_t i = 0; i < owned.count; ++i)
      if (owned.ids[i].pid == f.witnesses[3].id.pid)
        old = &owned.ids[i];
    if (!e && old)
      signal_error = proc_cancel(scan, &owned, old, SIGTERM, 200, &sent);
    refusal = !e && old && signal_error == ESTALE && !sent;
    passed = refusal;
  } else if (!strcmp(name, "stale-birth") || !strcmp(name, "stale-boot") ||
             !strcmp(name, "unknown")) {
    struct identity receipt = f.root;
    if (!strcmp(name, "stale-birth"))
      ++receipt.ticks;
    else if (!strcmp(name, "stale-boot"))
      strcpy(receipt.boot, "previous-boot");
    else
      receipt.pid = 0;
    struct observed bad = proc_observe(0);
    if (!strcmp(name, "unknown"))
      observation = bad;
    signal_error = proc_cancel(scan, &owned, &receipt, SIGTERM, 200, &sent);
    refusal = signal_error == ESTALE && !sent;
    passed = refusal && (strcmp(name, "unknown") ||
                         (bad.state == OBS_UNKNOWN && bad.error == EINVAL));
  } else if (!strncmp(name, "scan-", 5)) {
    volatile sig_atomic_t cancel = !strcmp(name, "scan-cancel");
    size_t limit = !strcmp(name, "scan-limit") ? 1 : 32768;
    long long deadline =
        proc_now_ms() + (!strcmp(name, "scan-deadline") ? -1 : 1500);
    proc_scan(scan, f.root.uid, limit, deadline, &cancel);
    signal_error = proc_cancel(scan, &owned, &f.root, SIGTERM, 200, &sent);
    int expected = !strcmp(name, "scan-limit") ? EOVERFLOW
                   : cancel                    ? ECANCELED
                                               : ETIMEDOUT;
    refusal = !scan->complete && scan->error == expected &&
              signal_error == expected && !sent;
    passed = refusal;
  } else if (!strcmp(name, "complete-helper-stop")) {
    const int order[] = {3, 2, 1, 0};
    for (size_t n = 0; n < sizeof(order) / sizeof(order[0]) && passed; ++n) {
      const struct identity *receipt = NULL;
      for (size_t i = 0; i < owned.count; ++i)
        if (owned.ids[i].pid == f.witnesses[order[n]].id.pid)
          receipt = &owned.ids[i];
      signal_error =
          receipt ? proc_cancel(scan, &owned, receipt, SIGTERM, 1000, &sent)
                  : ESTALE;
      if (!signal_error)
        e = receive(&f, 5, order[n], 0);
      passed = !signal_error && sent && !e &&
               f.terminal_signals[order[n]] == SIGTERM;
    }
  } else if (!strcmp(name, "sigterm") || !strcmp(name, "sigint") ||
             !strcmp(name, "cancel-timeout")) {
    int timeout_case = !strcmp(name, "cancel-timeout");
    if (timeout_case) {
      e = send_command(3, 'I');
      if (!e)
        e = receive(&f, 2, 3, 0);
    }
    signal_error = e ? e
                     : proc_cancel(scan, &owned, &f.witnesses[3].id,
                                   !strcmp(name, "sigint") ? SIGINT : SIGTERM,
                                   timeout_case ? 20 : 1000, &sent);
    if (timeout_case) {
      refusal = signal_error == ETIMEDOUT && sent;
      observation = proc_observe(f.witnesses[3].id.pid);
      passed = refusal && observation.state == OBS_LIVE &&
               has(&owned, &observation.id);
      complete = 0;
      oracle_intervention = 1;
      reason = "cancellation_timed_out_still_owned";
    } else {
      if (!signal_error)
        e = receive(&f, 5, 3, 0);
      observation = proc_observe(f.witnesses[3].id.pid);
      passed =
          !signal_error && sent && !e && observation.state == OBS_GONE &&
          f.terminal_signals[3] == (!strcmp(name, "sigint") ? SIGINT : SIGTERM);
    }
  }
  if (!passed && !reason[0])
    reason = "case_invariant_failed";
finish:
  if (refusal && !sent) {
    int target =
        !strcmp(name, "unwitnessed-orphan") ? 6
        : (!strcmp(name, "group-change") || !strcmp(name, "session-change"))
            ? 3
            : 0;
    refused_alive = !send_command(target, 'H') && !receive(&f, 2, target, 0);
    if (!refused_alive) {
      passed = complete = 0;
      reason = "refused_target_not_alive";
    }
  }
  if (f.seen & BIT(4)) {
    int h = send_command(4, 'H');
    peer_alive = !h && !receive(&f, 2, 4, 0);
  }
  if (!peer_alive) {
    passed = complete = 0;
    if (!reason[0])
      reason = "peer_not_proven_alive";
  }
  int cleanup_error = cleanup();
  if (cleanup_error) {
    passed = complete = 0;
    reason = "oracle_cleanup_incomplete";
  }
  complete = complete && passed;
  printf("{\"name\":");
  text(name);
  printf(",\"control_pass\":%s,\"complete\":%s,\"candidate_refused\":%s,"
         "\"error\":%d,\"reason\":",
         passed ? "true" : "false", complete ? "true" : "false",
         refusal ? "true" : "false", e);
  text(reason);
  printf(",\"observation\":{\"state\":");
  text(observation.state == OBS_LIVE   ? "live"
       : observation.state == OBS_GONE ? "gone"
                                       : "unknown");
  printf(",\"errno\":%d}", observation.error);
  printf(",\"scan\":{\"complete\":%s,\"errno\":%d,\"entries\":%zu,\"same_uid_"
         "count\":%zu,\"unknown\":%zu,\"vanished\":%zu},\"expected\":[",
         scan->complete ? "true" : "false", scan->error, scan->entries,
         scan->count, scan->unknown, scan->vanished);
  int comma = 0;
  for (int role = 0; role < ROLES; ++role)
    if (f.seen & BIT(role)) {
      if (comma++)
        putchar(',');
      printf("{\"role\":%d,\"creator_tid\":%d,\"identity\":", role,
             f.witnesses[role].creator_tid);
      print_identity(&f.witnesses[role].id);
      putchar('}');
    }
  printf("],\"discovered\":[");
  for (size_t i = 0; i < owned.count; ++i) {
    if (i)
      putchar(',');
    print_identity(&owned.ids[i]);
  }
  printf("],\"peer_survived\":%s,\"refused_target_survived\":%s,\"signal_"
         "sent\":%s,\"signal_errno\":%d,"
         "\"oracle_release\":true,\"oracle_intervention\":%s,\"cleanup_errno\":"
         "%d,\"emergency_cleanup\":%s,\"acknowledged_signals\":[",
         peer_alive ? "true" : "false", refused_alive ? "true" : "false",
         sent ? "true" : "false", signal_error,
         oracle_intervention ? "true" : "false", cleanup_error,
         cleanup_error ? "true" : "false");
  for (int role = 0; role < ROLES; ++role) {
    if (role)
      putchar(',');
    printf("%d", f.terminal_signals[role]);
  }
  printf("]}");
  free(scan);
  return (passed ? 1 : 0) | (complete ? 2 : 0);
}
int main(int argc, char **argv) {
  executable = argv[0];
  if (argc == 5 && !strcmp(argv[1], "--internal-thread-leaf")) {
    commands[3][0] = atoi(argv[2]);
    events[1] = atoi(argv[3]);
    creator_tid = (pid_t)atoi(argv[4]);
    struct stat command_info, event_info;
    if (commands[3][0] < 3 || events[1] < 3 || creator_tid <= 1 ||
        fstat(commands[3][0], &command_info) || fstat(events[1], &event_info) ||
        !S_ISFIFO(command_info.st_mode) || !S_ISFIFO(event_info.st_mode))
      return 2;
    leaf(3);
  }
  const char *selected = NULL;
  if (argc == 4 && !strcmp(argv[1], "proc-discovery-control") &&
      !strcmp(argv[2], "--case"))
    selected = argv[3];
  else if (argc != 2 || strcmp(argv[1], "proc-discovery-control"))
    return 2;
  if (selected) {
    int found = 0;
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i)
      if (!strcmp(selected, cases[i]))
        found = 1;
    if (!found)
      return 2;
  }
  /* Test oracle only. The observer neither requests nor relies on subreaping.
   */
  int subreaper = prctl(PR_SET_CHILD_SUBREAPER, 1);
  int subreaper_error = subreaper < 0 ? errno : 0;
  int subreaper_value = 0;
  if (!subreaper &&
      (prctl(PR_GET_CHILD_SUBREAPER, &subreaper_value) || !subreaper_value)) {
    subreaper = -1;
    subreaper_error = errno ? errno : EPROTO;
  }
  struct observed self = proc_observe(getpid());
  struct utsname kernel;
  memset(&kernel, 0, sizeof(kernel));
  (void)uname(&kernel);
  printf("{\"schema\":1,\"probe\":\"proc-discovery-control\",\"source_id\":");
  text(PROCESS_PROBE_SOURCE_ID);
  printf(",\"reported_uid\":%u,\"kernel_uid\":%u,\"kernel_release\":",
         (unsigned)getuid(), (unsigned)self.id.uid);
  text(kernel.release);
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
  printf(",\"kernel_uid_known\":%s,\"page_size\":%ld",
         self.state == OBS_LIVE ? "true" : "false", sysconf(_SC_PAGESIZE));
  printf(",\"harness_subreaper\":%s,\"harness_subreaper_errno\":%d,\"children_"
         "node\":{",
         subreaper == 0 ? "true" : "false", subreaper_error);
  char path[128];
  snprintf(path, sizeof(path), "/proc/%d/task/%d/children", getpid(), getpid());
  int fd = open(path, O_RDONLY | O_CLOEXEC),
      children_error = fd < 0 ? errno : 0;
  if (fd >= 0)
    close(fd);
  printf("\"readable\":%s,\"errno\":%d},\"cases\":[",
         fd >= 0 ? "true" : "false", children_error);
  int complete = 1, controls = 1, first = 1;
  if (subreaper || self.state != OBS_LIVE) {
    printf("{\"name\":\"prerequisite\",\"control_pass\":false,\"complete\":"
           "false,\"errno\":%d}",
           subreaper_error ? subreaper_error : self.error);
    complete = controls = 0;
  } else
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
      if (selected && strcmp(selected, cases[i]))
        continue;
      if (!first)
        putchar(',');
      first = 0;
      int verdict = run_case(cases[i]);
      if (!(verdict & 1))
        controls = 0;
      if (!(verdict & 2))
        complete = 0;
    }
  printf("],\"controls_pass\":%s,\"selected_cases_complete\":%s,\"runtime_"
         "acceptance\":false,"
         "\"android_acceptance\":\"pending\",\"ownership_complete\":false}\n",
         controls ? "true" : "false", complete ? "true" : "false");
  return complete ? 0 : 1;
}
