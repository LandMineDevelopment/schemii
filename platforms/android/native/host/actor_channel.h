#ifndef SCHEMII_ACTOR_CHANNEL_H
#define SCHEMII_ACTOR_CHANNEL_H
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#define AC_FRAME_SIZE 224
#define AC_CAPACITY 128
/* Private source instrumentation, not caller/custody or launch authority. */
enum ac_op { AC_INTENT=1, AC_RESULT, AC_TASK, AC_PTRACE, AC_WAIT, AC_EXEC,
             AC_RESUME, AC_ACK, AC_HIGHWATER };
enum ac_kind { AC_ROOT=1, AC_GUEST, AC_SYSVIPC, AC_F2FS, AC_RAW };
struct ac_binding { unsigned char source[32], contract[32], run[16];
    uint64_t generation, creator; uint32_t profile; };
struct ac_record { uint64_t sequence, intent, task, process, creator, monotonic_ns;
    uint32_t op, kind, pid, related; int32_t result, error, secondary; };
struct ac_slot { uint64_t task, process, intent, creator; pid_t pid; unsigned kind;
    int live, pending, direct, wait_pending, native_intent; };
struct ac_channel { int fd, error, secondary, lost; uint64_t tx, rx, acknowledged,
    next_incarnation, rx_monotonic; struct { pid_t pid; int status; } early[AC_CAPACITY];
    struct ac_binding binding; struct ac_slot slots[AC_CAPACITY]; };
int ac_pair(int pair[2]);
int ac_init(struct ac_channel *, int fd, const struct ac_binding *);
int ac_send(struct ac_channel *, struct ac_record *);
int ac_receive(struct ac_channel *, struct ac_record *);
void ac_loss(struct ac_channel *, int error);
int ac_highwater(struct ac_channel *);
/* Single close owner; never retries close, including EINTR. */
int ac_close_fds(const int *keep, size_t count, int *secondary);
int ac_clone_policy(int clone3, uint64_t flags);
/* Source-only PRoot attachment. No CLI/env/JSON/FD-number discovery. The APK
 * never invokes this; it conveys no C/A and cannot change admission. */
void ac_proot_bind(struct ac_channel *);
int ac_proot_require(void);
void ac_proot_fault(int error);
int ac_proot_wait_end(void);
pid_t ac_native_fork(unsigned kind);
int ac_child_fds(const int *keep, size_t count);
int ac_birth_enter(pid_t pid);
void ac_birth_result(pid_t pid, long result);
int ac_task(pid_t parent, pid_t pid, int thread);
void ac_observe(pid_t pid, int status);
int ac_helper_wait(pid_t pid, int status);
pid_t ac_target_wait(pid_t pid, int *status, int options, int direct);
long ac_resume(int request, pid_t pid, void *address, void *data);
int ac_exec(pid_t oldpid, pid_t newpid);
#endif
