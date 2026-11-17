#ifndef SCHEMII_NATIVE_BOOTSTRAP_H
#define SCHEMII_NATIVE_BOOTSTRAP_H
#include "boot_authority.h"
#include "installed_provenance.h"
struct native_bootstrap;
/* Independent observation ABI. The old status struct/raw16 projection is intact. */
enum acquisition_stage {
  AQ_FRAME=1, AQ_PINS, AQ_CLASS, AQ_APPLICATION, AQ_SELECTED, AQ_FRAMEWORK,
  AQ_FIELDS, AQ_PACKAGE, AQ_MODULE, AQ_IMAGES, AQ_PAIR_PRE, AQ_OBJECTS,
  AQ_MEMBERS, AQ_CUSTODY, AQ_BOOT, AQ_MAPPING, AQ_PAIR_POST, AQ_FRAMEWORK_POST,
  AQ_FIELDS_POST, AQ_REVALIDATE, AQ_MAPPING_POST, AQ_IMAGES_POST, AQ_PAIR_PUBLISH,
  AQ_CLOSE
};
struct acquisition_observation {
  int first_stage, first_substage, last_stage, last_substage;
  int error, secondary;
  int framework_reason, image_reason, image_count;
  struct installed_observation object;
  int cleanup, secondary_cleanup;
};
void acquisition_step(struct acquisition_observation *report, int stage, int substage);
void acquisition_failure(struct acquisition_observation *report, int error);
struct acquisition_observation native_bootstrap_observe(struct native_bootstrap *owner);
struct native_bootstrap_status {
  int error, secondary, cleanup, secondary_cleanup, retained, stage;
  struct boot_compare_result boot;
};
int native_bootstrap_open(const char *apk, const char *module, const char *data,
                          const struct installed_pin *pins, size_t count,
                          struct native_bootstrap **out);
int native_bootstrap_mapping(struct native_bootstrap *owner, uintptr_t address, uintptr_t base);
int native_bootstrap_validate(struct native_bootstrap *owner,
                              const struct installed_pin *pins, size_t count);
struct native_bootstrap_status native_bootstrap_status(struct native_bootstrap *owner);
void native_bootstrap_refuse(struct native_bootstrap *owner, int error);
int native_bootstrap_close(struct native_bootstrap **owner);
/* Capture the last scalar report before successful disposal. With no owner,
 * leave the supplied report untouched so a caller can retain its last result. */
int native_bootstrap_close_report(struct native_bootstrap **owner,
                                  struct native_bootstrap_status *final);
int native_bootstrap_close_observed(struct native_bootstrap **owner,
  struct native_bootstrap_status *final, struct acquisition_observation *observation);
#endif
