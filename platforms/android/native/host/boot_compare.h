#ifndef SCHEMII_BOOT_COMPARE_H
#define SCHEMII_BOOT_COMPARE_H

#include <stdint.h>

/* A future sole native custody owner may lend this under its uninterrupted
 * exclusive lease. No producer exists today. A pointer is NOT authentication;
 * there is deliberately no constructor, import, serialization or trust setter.
 */
struct boot_anchor_view;

enum boot_compare_relation {
  BOOT_COMPARE_SAME = 0,
  BOOT_COMPARE_NEW = 1,
  BOOT_COMPARE_UNKNOWN = 2
};

/* Stable order from trusted-boot-anchor-1673-v1. */
enum boot_compare_reason {
  BOOT_COMPARE_ANCHOR_EQUAL = 0,
  BOOT_COMPARE_ANCHOR_DIFFERENT,
  BOOT_COMPARE_CE_LOCKED_OR_STORAGE_UNAVAILABLE,
  BOOT_COMPARE_OBSERVATION_UNKNOWN,
  BOOT_COMPARE_CALLER_UNPROVED,
  BOOT_COMPARE_NO_PRIOR_FRESH,
  BOOT_COMPARE_FRESHNESS_UNPROVED,
  BOOT_COMPARE_ANCHOR_MISSING,
  BOOT_COMPARE_ANCHOR_MALFORMED,
  BOOT_COMPARE_BINDING_MISMATCH,
  BOOT_COMPARE_SOURCE_INCOMPATIBLE,
  BOOT_COMPARE_PREFIX_UNPROVED,
  BOOT_COMPARE_HISTORY_UNPROVED,
  BOOT_COMPARE_HISTORY_COLLISION,
  BOOT_COMPARE_LEASE_LOST,
  BOOT_COMPARE_COMMIT_UNCERTAIN
};

enum boot_compare_admission {
  BOOT_COMPARE_ADMITTED = 0,
  BOOT_COMPARE_UNPROVED,
  BOOT_COMPARE_REJECTED,
  BOOT_COMPARE_PRIOR_NO_PRIOR_FRESH /* Prior classification only. */
};

enum boot_compare_continuity {
  BOOT_COMPARE_FIRST_CURRENT = 0,
  BOOT_COMPARE_REPEAT_CURRENT,
  BOOT_COMPARE_CONTINUITY_UNPROVED
};

/* Applies independently to all four observation scalars. Observed status/stage
 * use the unchanged boot_identity enums; observed errors are 0..4095. */
#define BOOT_COMPARE_UNOBSERVED (-1)

/* Closed scalar projection: no identity/hash, pointer, capability, free text,
 * recovery permission or diagnostic equality field. Fixed-width ABI; enum
 * domains above apply to their corresponding fields. */
struct boot_compare_result {
  int32_t relation;
  int32_t reason;
  int32_t observation_status;
  int32_t observation_stage;
  int32_t error;
  int32_t cleanup_error;
  int32_t current_admission;
  int32_t prior_admission;
  int32_t continuity;
};

/* Reads only via boot_identity_read(). Production has neither caller (C) nor
 * prior-custody (A) admission: it never dereferences prior and returns UNKNOWN,
 * with OBSERVATION_UNKNOWN for failed R, otherwise CALLER_UNPROVED.
 * SAME is continuity only; NEW would be only a boot-difference predicate.
 * Neither grants restart, ownership, database recovery or readiness.
 */
struct boot_compare_result
boot_compare_current(const struct boot_anchor_view *prior);

#endif
