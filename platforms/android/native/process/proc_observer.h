#ifndef SCHEMII_DIAGNOSTIC_PROC_OBSERVER_H
#define SCHEMII_DIAGNOSTIC_PROC_OBSERVER_H
/* Diagnostic only: not a production lifecycle or process adoption API. */
#include <signal.h>
#include <stddef.h>
#include <sys/types.h>
#define PROC_MAX 4096
#define OWNED_MAX 16
struct identity {
  pid_t pid, ppid, pgid, sid;
  uid_t uid;
  unsigned long long ticks;
  char boot[64];
};
enum observation { OBS_UNKNOWN, OBS_LIVE, OBS_GONE };
struct observed {
  enum observation state;
  int error;
  struct identity id;
};
struct snapshot {
  struct identity ids[PROC_MAX];
  size_t count, entries, vanished, unknown;
  int complete, error;
};
struct ledger {
  struct identity ids[OWNED_MAX];
  size_t count;
};
long long proc_now_ms(void);
struct observed proc_observe(pid_t pid);
int proc_same_birth(const struct identity *a, const struct identity *b);
int proc_same_membership(const struct identity *a, const struct identity *b);
void proc_scan(struct snapshot *out, uid_t uid, size_t limit,
               long long deadline, const volatile sig_atomic_t *cancel);
/* Only root and witnessed ancestry admit members. A group number never does. */
int proc_discover(const struct snapshot *scan, const struct identity *root,
                  struct ledger *owned);
/* No PID-only fallback: revalidate birth+membership around pidfd acquisition.
 */
int proc_cancel(const struct snapshot *scan, const struct ledger *owned,
                const struct identity *receipt, int sig, int timeout_ms,
                int *sent);
#endif
