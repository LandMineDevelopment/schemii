#ifndef SCHEMII_HOST_JOURNAL_H
#define SCHEMII_HOST_JOURNAL_H
#include <stdint.h>

/* Native-private storage API. NEVER serialize these inputs, handles or prefixes
 * to Java, guest, logs, support export or backup. No boot_compare view
 * producer. Caller selects CE(1) or explicitly opted-in DE(2); no path
 * selection/fallback. All identities are supplied claims, not authenticated
 * observations. */
struct journal_binding {
  uint8_t source[32], contract[32];
  uint8_t storage_lineage[16], install[16], state[16];
  uint8_t run[16], attempt[16], boot[16];
  uint64_t generation;
  uint32_t known;      /* exactly 255: eight identity fields present */
  uint8_t storage;     /* 1 CE, 2 DE */
  uint8_t boot_kind;   /* 1 synthetic, 2 caller-claimed observation; neither
                          trusted */
  uint16_t pg_major;   /* 17; planned slots only, NO established PG identity */
  uint32_t durability; /* 1: planned shared durable policy */
  uint32_t planned_slots; /* 3: metadata + target, not initialized */
};
#define JOURNAL_RECORD_BYTES 320u
#define JOURNAL_MAX_BYTES (16u * 1024u * 1024u)

enum journal_status {
  JOURNAL_OK,
  JOURNAL_MISSING,
  JOURNAL_REFUSED,
  JOURNAL_IO,
  JOURNAL_TORN,
  JOURNAL_CORRUPT,
  JOURNAL_GAP,
  JOURNAL_DUPLICATE,
  JOURNAL_UNSUPPORTED,
  JOURNAL_OVERFLOW,
  JOURNAL_BINDING,
  JOURNAL_PRESSURE
};
enum journal_stage {
  JOURNAL_NONE,
  JOURNAL_DIRECTORY,
  JOURNAL_LOCK,
  JOURNAL_VALIDATE,
  JOURNAL_OPEN,
  JOURNAL_READ,
  JOURNAL_WRITE,
  JOURNAL_FILE_SYNC,
  JOURNAL_PUBLISH,
  JOURNAL_DIR_SYNC,
  JOURNAL_CLOSE,
  JOURNAL_INSPECT
};
enum journal_effect {
  JOURNAL_NOT_ATTEMPTED,
  JOURNAL_PENDING,
  JOURNAL_FAILED,
  JOURNAL_SHORT,
  JOURNAL_UNKNOWN,
  JOURNAL_DURABLE
};
/* Safe closed scalar projection. prefix_bytes excludes the independent anchor.
 * sequence_known refers only to structurally validated bytes, NEVER a recovered
 * durable ACK. loss_known=0 means unsent/after-crash loss is unmeasured.
 * First failure and secondary close failure survive until handle destruction.
 */
struct journal_result {
  int32_t status, stage, error, cleanup_error, effect;
  uint64_t bytes, prefix_bytes, last_sequence;
  uint32_t sequence_known, loss_known;
};
struct journal;
/* Opens a NEW open-file description on selected_dirfd and locks the directory.
 * Caller keeps the selected directory private, stable, and exclusively owned;
 * cooperative flock is storage serialization, NOT historical owner authority.
 * Calls on a handle must be serialized by the caller; do not share across fork.
 * Replacing paths by hostile same-UID actors cannot be secured by this API. */
struct journal_result journal_open(int selected_dirfd,
                                   const struct journal_binding *binding,
                                   struct journal **out);
/* Explicit NEW namespace only. Missing on an existing namespace is not fresh.
 * No directory creation, reset, repair, retry, truncation or deletion. Staging
 * anchor remains retained even after publication; it is bounded essential data.
 * Only this uninterrupted successful initialization enables ACTIVE appends. */
struct journal_result journal_initialize(struct journal *journal);
/* seq must be exact next; duplicate/gap fences this handle. Repeated ACTIVE
 * only records the SAME initial run's hazard. No new-run/retry/terminal policy.
 */
struct journal_result journal_active(struct journal *journal, uint64_t seq);
/* Always invalidates continuation, even on a valid prefix or failed inspection.
 * Reload cannot reconstruct the prior writer's durable-ACK observation. */
struct journal_result journal_inspect(struct journal *journal);
/* Native-only structural digest access, no admission capability. Invalidated
 * by each mutation/inspection; never retain as trusted custody across reload.
 */
struct journal_native_prefix {
  uint8_t digest[32];
  uint64_t bytes, sequence;
  uint32_t known;
};
struct journal_native_prefix journal_prefix(const struct journal *journal);
struct journal_result journal_close(struct journal **journal);
#endif
