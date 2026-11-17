#ifndef SCHEMII_BOOT_AUTHORITY_H
#define SCHEMII_BOOT_AUTHORITY_H
#include "boot_compare.h"

/* Native custodian prerequisite, NOT a positive C/A constructor. The selected
 * descriptor conveys neither installed-artifact provenance nor CE/DE lineage.
 * No identities, trust flags, imported bindings or anchor views are accepted.
 * This owner retains an independent selected-directory lease until close.
 * Calls must be serialized, except cancel may run during comparison. */
struct boot_authority;
struct boot_authority_errors {
  int error, close_error, secondary_close_error, close_failures;
};
int boot_authority_open(int selected_dirfd, struct boot_authority **out,
                        struct boot_authority_errors *errors);
/* Strict unchanged R reader via comparator; C remains unproved. No state
 * creation, journal initialization, history import or managed birth. */
struct boot_compare_result boot_authority_compare(struct boot_authority *owner);
/* Same comparison, with its retained custody errors captured while serialized.
 * These errors are separate from R's observation fields and prove no C/A. */
struct boot_compare_result boot_authority_compare_report(
    struct boot_authority *owner, struct boot_authority_errors *errors);
/* Irreversible revocation; never interrupts an in-flight syscall or releases
 * the lease. Close refuses EBUSY until comparison returns. */
void boot_authority_cancel(struct boot_authority *owner);
int boot_authority_close(struct boot_authority **owner,
                         struct boot_authority_errors *errors);
#endif
