#define _GNU_SOURCE
#include "boot_authority.h"
#include <errno.h>
#include <fcntl.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>

struct boot_authority {
  int dir;
  pid_t process;
  dev_t device;
  ino_t inode;
  atomic_int busy, cancelled;
  int lost;
  struct boot_authority_errors errors;
};

static void close_once(int fd, struct boot_authority_errors *e) {
  if (fd >= 0 && close(fd) < 0) {
    int error = errno;
    ++e->close_failures;
    if (!e->close_error)
      e->close_error = error;
    else if (!e->secondary_close_error)
      e->secondary_close_error = error;
  }
}

static int private_directory(int fd, struct stat *s) {
  if (fstat(fd, s) < 0)
    return errno;
  return S_ISDIR(s->st_mode) && s->st_uid == geteuid() && s->st_nlink &&
                 (s->st_mode & 07777) == 0700
             ? 0
             : EACCES;
}

int boot_authority_open(int selected, struct boot_authority **out,
                        struct boot_authority_errors *report) {
  struct boot_authority_errors local = {0};
  struct boot_authority_errors *e = report ? report : &local;
  memset(e, 0, sizeof(*e));
  if (!out) {
    e->error = EINVAL;
    return e->error;
  }
  *out = NULL;
  struct stat s;
  e->error = private_directory(selected, &s);
  if (e->error)
    return e->error;
  int dir = openat(selected, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (dir < 0) {
    e->error = errno;
    return e->error;
  }
  struct stat retained;
  e->error = private_directory(dir, &retained);
  if (!e->error && (s.st_dev != retained.st_dev || s.st_ino != retained.st_ino))
    e->error = ESTALE;
  if (!e->error && flock(dir, LOCK_EX | LOCK_NB) < 0)
    e->error = errno;
  struct boot_authority *o = NULL;
  if (!e->error) {
    o = calloc(1, sizeof(*o));
    if (!o)
      e->error = ENOMEM;
  }
  if (e->error) {
    close_once(dir, e);
    return e->error;
  }
  atomic_init(&o->busy, 0);
  atomic_init(&o->cancelled, 0);
  o->dir = dir;
  o->process = getpid();
  o->device = retained.st_dev;
  o->inode = retained.st_ino;
  *out = o;
  return 0;
}

/* This is cooperative storage custody only. The private retained description
 * is never exported. A second independent description must conflict; probing
 * never reacquires/retries the owner's lock after a gap. No hostile same-UID
 * or CAP_SYS_PTRACE attacker is covered by this contract. */
static int selected_current(struct boot_authority *o) {
  struct stat s;
  int error = 0;
  if (o->lost)
    return 0;
  if (getpid() != o->process)
    error = ECHILD;
  if (!error)
    error = private_directory(o->dir, &s);
  if (!error && (s.st_dev != o->device || s.st_ino != o->inode))
    error = ESTALE;
  if (!error) {
    int probe = openat(o->dir, ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (probe < 0)
      error = errno;
    else {
      int locked = flock(probe, LOCK_EX | LOCK_NB);
      int observed = locked < 0 ? errno : 0;
      if (locked == 0)
        error = ESTALE;
      else if (observed != EWOULDBLOCK && observed != EAGAIN)
        error = observed;
      close_once(probe, &o->errors);
      if (!error)
        error = o->errors.close_error;
    }
  }
  if (error && !o->errors.error)
    o->errors.error = error;
  return error == 0;
}

static struct boot_compare_result unavailable(void) {
  struct boot_compare_result r = {
      BOOT_COMPARE_UNKNOWN, BOOT_COMPARE_CE_LOCKED_OR_STORAGE_UNAVAILABLE,
      BOOT_COMPARE_UNOBSERVED, BOOT_COMPARE_UNOBSERVED,
      BOOT_COMPARE_UNOBSERVED, BOOT_COMPARE_UNOBSERVED,
      BOOT_COMPARE_UNPROVED, BOOT_COMPARE_UNPROVED,
      BOOT_COMPARE_CONTINUITY_UNPROVED};
  return r;
}

struct boot_compare_result boot_authority_compare(struct boot_authority *o) {
  if (!o || atomic_exchange(&o->busy, 1))
    return unavailable();
  struct boot_compare_result r = unavailable();
  if (!selected_current(o)) {
    o->lost = 1;
  } else {
    /* No opaque view is issued: original prebirth C, source/install admission,
     * selected CE/DE lineage and A cannot be derived from this descriptor.
     * Neither native allocation nor a successful R can supply those facts. */
    r = boot_compare_current(NULL);
    if (!selected_current(o)) {
      o->lost = 1;
      r.relation = BOOT_COMPARE_UNKNOWN;
      r.reason = BOOT_COMPARE_CE_LOCKED_OR_STORAGE_UNAVAILABLE;
      r.current_admission = r.prior_admission = BOOT_COMPARE_UNPROVED;
      r.continuity = BOOT_COMPARE_CONTINUITY_UNPROVED;
    }
  }
  /* Cancellation cannot promote a result or erase R's first/cleanup failure.
   * Even absent cancellation C/A remain unproved at this source boundary. */
  if (atomic_load(&o->cancelled))
    o->lost = 1;
  atomic_store(&o->busy, 0);
  return r;
}

void boot_authority_cancel(struct boot_authority *o) {
  if (o)
    atomic_store(&o->cancelled, 1);
}

int boot_authority_close(struct boot_authority **ptr,
                         struct boot_authority_errors *report) {
  struct boot_authority_errors local = {0};
  struct boot_authority_errors *e = report ? report : &local;
  memset(e, 0, sizeof(*e));
  if (!ptr || !*ptr)
    return e->error = EINVAL;
  struct boot_authority *o = *ptr;
  if (atomic_load(&o->busy))
    return e->error = EBUSY;
  *e = o->errors;
  close_once(o->dir, e); /* no LOCK_UN: an inherited child cannot unlock parent */
  memset(o, 0, sizeof(*o));
  free(o);
  *ptr = NULL;
  if (!e->error)
    e->error = e->close_error;
  return e->error;
}
