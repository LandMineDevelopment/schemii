#include "boot_compare.h"
#include "boot_identity.h"

#include <stddef.h>
#include <string.h>

/* Private classifier input, NOT a custody implementation or a trust API.
 * No production code constructs this snapshot. Future C/A producers need
 * separate source/platform review; filling these fields cannot establish C/A.
 * Host controls include this translation unit to exercise synthetic snapshots;
 * no test switch, constructor or override is compiled into this source.
 * 32 distinct epochs fit inside the essential 4096-byte record budget. Capacity
 * exhaustion refuses; there is no eviction, compaction, learning or writer.
 */
#define BOOT_COMPARE_HISTORY_MAX 32
enum prior_state {
  PRIOR_UNPROVED,
  PRIOR_ESTABLISHED,
  PRIOR_FRESH,
  PRIOR_PARTIAL
};
enum commit_state {
  COMMIT_UNOBSERVED,
  COMMIT_COMPLETE,
  COMMIT_PENDING,
  COMMIT_SHORT,
  COMMIT_FILE_SYNC_FAILED,
  COMMIT_PUBLICATION_FAILED,
  COMMIT_DIRECTORY_SYNC_FAILED
};
struct comparison_snapshot {
  int storage_available, caller_admitted;
  enum prior_state state;
  int anchor_present, anchor_well_formed;
  int schema_compatible, contract_compatible, source_compatible,
      build_compatible;
  int install_matches, state_matches, storage_matches, data_generation_matches;
  int cluster_slots_match, attempt_matches, run_matches, generation_matches;
  int original_prebirth_admitted;
  int lease_uninterrupted, context_unchanged, borrow_current;
  int prefix_checked, tip_unchanged;
  enum commit_state commit;
  int history_complete, retention_proved;
  size_t epoch_count, selected_epoch, current_epoch;
  unsigned char epochs[BOOT_COMPARE_HISTORY_MAX][BOOT_IDENTITY_BYTES];
};

static void erase_private(void *memory, size_t length) {
  volatile unsigned char *p = memory;
  while (length--)
    *p++ = 0;
}

static struct boot_compare_result unknown_result(void) {
  struct boot_compare_result r = {.relation = BOOT_COMPARE_UNKNOWN,
                                  .reason = BOOT_COMPARE_CALLER_UNPROVED,
                                  .observation_status = BOOT_COMPARE_UNOBSERVED,
                                  .observation_stage = BOOT_COMPARE_UNOBSERVED,
                                  .error = BOOT_COMPARE_UNOBSERVED,
                                  .cleanup_error = BOOT_COMPARE_UNOBSERVED,
                                  .current_admission = BOOT_COMPARE_UNPROVED,
                                  .prior_admission = BOOT_COMPARE_UNPROVED,
                                  .continuity =
                                      BOOT_COMPARE_CONTINUITY_UNPROVED};
  return r;
}

static int observation_admissible(const struct boot_identity_result *r) {
  if (!r || r->knownness != BOOT_ID_KNOWN || r->status != BOOT_ID_OK ||
      r->stage != BOOT_ID_STAGE_NONE || r->error || r->cleanup_error ||
      r->method != BOOT_ID_NATIVE_FIXED_PROC ||
      r->context != BOOT_ID_CALLER_MOUNT_NAMESPACE || r->failed_sample ||
      r->failed_node)
    return 0;
  for (size_t i = 0; i < 2; ++i) {
    const struct boot_identity_sample *s = &r->samples[i];
    if (s->origin != BOOT_ID_PROC_VERIFIED ||
        s->opened_nodes != BOOT_IDENTITY_PATH_NODES ||
        s->procfs_checks != BOOT_IDENTITY_PATH_NODES ||
        s->type_checks != BOOT_IDENTITY_PATH_NODES ||
        s->mount_checks != BOOT_IDENTITY_PATH_NODES || !s->read_calls ||
        s->read_calls > BOOT_IDENTITY_READ_MAX ||
        (s->bytes_read != 36 && s->bytes_read != 37) ||
        s->root_mount_id != s->leaf_mount_id)
      return 0;
  }
  return r->samples[0].root_mount_id == r->samples[1].root_mount_id;
}

/* Precedence: storage, R, C, fresh/missing, A (format, source, bindings,
 * lease/reload, prefix/tip, commit, history), collision, equality/novelty.
 * Null observation means not dispatched, never fabricated OK/zero.
 * This consumes a borrow without modifying it. No private identity is copied.
 */
static struct boot_compare_result
classify_current(const struct boot_identity_result *observation,
                 const struct comparison_snapshot *s) {
  struct boot_compare_result r = unknown_result();
  if (observation) {
    r.observation_status = observation->status;
    r.observation_stage = observation->stage;
    r.error = observation->error;
    r.cleanup_error = observation->cleanup_error;
  }
  if (s && s->storage_available != 1) {
    r.reason = BOOT_COMPARE_CE_LOCKED_OR_STORAGE_UNAVAILABLE;
    return r;
  }
  if (!observation_admissible(observation)) {
    r.reason = BOOT_COMPARE_OBSERVATION_UNKNOWN;
    return r;
  }
  if (!s || s->caller_admitted != 1)
    return r;
  r.current_admission = BOOT_COMPARE_ADMITTED;
  if (s->anchor_present != 1) {
    if (s->state == PRIOR_FRESH) {
      r.reason = BOOT_COMPARE_NO_PRIOR_FRESH;
      r.prior_admission = BOOT_COMPARE_PRIOR_NO_PRIOR_FRESH;
    } else if (s->state == PRIOR_PARTIAL) {
      r.reason = BOOT_COMPARE_FRESHNESS_UNPROVED;
    } else {
      r.reason = BOOT_COMPARE_ANCHOR_MISSING;
    }
    return r;
  }
  r.prior_admission = BOOT_COMPARE_REJECTED;
  if (s->state != PRIOR_ESTABLISHED)
    r.reason = BOOT_COMPARE_FRESHNESS_UNPROVED;
  else if (s->anchor_well_formed != 1)
    r.reason = BOOT_COMPARE_ANCHOR_MALFORMED;
  else if (s->schema_compatible != 1 || s->contract_compatible != 1 ||
           s->source_compatible != 1 || s->build_compatible != 1)
    r.reason = BOOT_COMPARE_SOURCE_INCOMPATIBLE;
  else if (s->install_matches != 1 || s->state_matches != 1 ||
           s->storage_matches != 1 || s->data_generation_matches != 1 ||
           s->cluster_slots_match != 1 || s->attempt_matches != 1 ||
           s->run_matches != 1 || s->generation_matches != 1 ||
           s->original_prebirth_admitted != 1)
    r.reason = BOOT_COMPARE_BINDING_MISMATCH;
  else if (s->lease_uninterrupted != 1 || s->context_unchanged != 1 ||
           s->borrow_current != 1)
    r.reason = BOOT_COMPARE_LEASE_LOST;
  else if (s->prefix_checked != 1 || s->tip_unchanged != 1)
    r.reason = BOOT_COMPARE_PREFIX_UNPROVED;
  else if (s->commit != COMMIT_COMPLETE)
    r.reason = BOOT_COMPARE_COMMIT_UNCERTAIN;
  else if (s->history_complete != 1 || s->retention_proved != 1 ||
           !s->epoch_count || s->epoch_count > BOOT_COMPARE_HISTORY_MAX ||
           s->selected_epoch >= s->epoch_count ||
           s->current_epoch >= s->epoch_count)
    r.reason = BOOT_COMPARE_HISTORY_UNPROVED;
  else {
    /* A selected anchor and the retained current candidate have distinct roles
     * from noncurrent history (review N01). Require distinct stored epochs so
     * aliases cannot conceal a collision. Never update history on any result.
     */
    for (size_t i = 0; i < s->epoch_count; ++i) {
      for (size_t j = 0; j < i; ++j) {
        if (!memcmp(s->epochs[i], s->epochs[j], BOOT_IDENTITY_BYTES)) {
          r.reason = BOOT_COMPARE_HISTORY_UNPROVED;
          return r;
        }
      }
    }
    r.prior_admission = BOOT_COMPARE_ADMITTED;
    for (size_t i = 0; i < s->epoch_count; ++i) {
      if (i != s->selected_epoch && i != s->current_epoch &&
          !memcmp(observation->uuid, s->epochs[i], BOOT_IDENTITY_BYTES)) {
        r.reason = BOOT_COMPARE_HISTORY_COLLISION;
        return r;
      }
    }
    int equal = !memcmp(observation->uuid, s->epochs[s->selected_epoch],
                        BOOT_IDENTITY_BYTES);
    r.relation = equal ? BOOT_COMPARE_SAME : BOOT_COMPARE_NEW;
    r.reason =
        equal ? BOOT_COMPARE_ANCHOR_EQUAL : BOOT_COMPARE_ANCHOR_DIFFERENT;
    r.continuity = !memcmp(observation->uuid, s->epochs[s->current_epoch],
                           BOOT_IDENTITY_BYTES)
                       ? BOOT_COMPARE_REPEAT_CURRENT
                       : BOOT_COMPARE_FIRST_CURRENT;
  }
  return r;
}

struct boot_compare_result
boot_compare_current(const struct boot_anchor_view *prior) {
  /* Deliberately do not inspect even a nonnull/fabricated pointer. Neither C
   * nor A has a source-backed producer. Opaqueness is not authentication. */
  (void)prior;
  struct boot_identity_result observation = boot_identity_read();
  struct boot_compare_result r = classify_current(&observation, NULL);
  erase_private(&observation, sizeof(observation));
  return r;
}
