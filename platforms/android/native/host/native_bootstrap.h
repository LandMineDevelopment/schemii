#ifndef SCHEMII_NATIVE_BOOTSTRAP_H
#define SCHEMII_NATIVE_BOOTSTRAP_H
#include "boot_authority.h"
#include "installed_provenance.h"
struct native_bootstrap;
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
#endif
