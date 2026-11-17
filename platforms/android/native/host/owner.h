#ifndef SCHEMII_APK_OWNER_H
#define SCHEMII_APK_OWNER_H
#include <stdint.h>
/* Source-only connected owner. No API imports C/A, freshness or closure claims.
 * Directory is borrowed for the call; an independent CLOEXEC description is
 * retained. Private actor endpoints are reserved before the worker starts;
 * no endpoint is exported and no child launch API exists. No managed birth is currently
 * admitted by the production comparator or pinned PRoot helper inventory. */
struct apk_owner;
enum apk_owner_phase { APK_IDLE, APK_PENDING, APK_REFUSED, APK_CLOSING };
enum apk_owner_reason { APK_NONE, APK_BOOT_AUTHORITY, APK_STORAGE, APK_PROTOCOL,
                        APK_JOURNAL, APK_CANCELLED };
struct apk_owner_status {
    uint64_t sequence, monotonic_ns;
    int32_t phase, reason, error, cleanup_error;
    int32_t journal_status, journal_effect, boot_relation, boot_reason;
    int32_t births, closure_known;
};
int apk_owner_open(int borrowed_dirfd, struct apk_owner **out);
/* Private bounded AF_UNIX channel; exact op1 Start, op2 Stop. Status never waits
 * on a writer syscall. Pending work retains the sole lease; no replacement. */
int apk_owner_request(struct apk_owner *owner, uint32_t operation);
struct apk_owner_status apk_owner_status(struct apk_owner *owner);
/* EBUSY until worker has returned. No cancellation/close of in-flight writer. */
int apk_owner_close(struct apk_owner **owner);
#endif
