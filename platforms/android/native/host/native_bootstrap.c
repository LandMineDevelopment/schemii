#include "native_bootstrap.h"
#include <errno.h>
#include <stdlib.h>
struct native_bootstrap {
  struct installed_objects *objects;
  struct boot_authority *authority;
  struct native_bootstrap_status status;
};
static void cleanup_error(struct native_bootstrap *o, int error) {
  if (error <= 0) return;
  if (!o->status.cleanup) o->status.cleanup=error;
  else if (error!=o->status.cleanup && !o->status.secondary_cleanup)
    o->status.secondary_cleanup=error;
}
void native_bootstrap_refuse(struct native_bootstrap *o, int e) {
  if (o && e) {
    if (!o->status.error) o->status.error=e;
    else if (e!=o->status.error && !o->status.secondary) o->status.secondary=e;
  }
}
int native_bootstrap_open(const char *apk, const char *module, const char *data,
                          const struct installed_pin *pins, size_t count,
                          struct native_bootstrap **out) {
  if (!out) return EINVAL;
  *out=calloc(1,sizeof(**out));
  if (!*out) return ENOMEM;
  struct native_bootstrap *o=*out;
  o->status.retained=1; o->status.stage=1;
  o->status.boot=(struct boot_compare_result){BOOT_COMPARE_UNKNOWN,BOOT_COMPARE_CALLER_UNPROVED,
    -1,-1,0,0,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_CONTINUITY_UNPROVED};
  struct installed_errors e={0};
  int result=installed_open(apk,module,data,&o->objects,&e);
  if (!result) result=installed_validate(o->objects,pins,count);
  struct boot_authority_errors custody={0};
  if (!result) {
    o->status.stage=2;
    result=boot_authority_open(installed_selected(o->objects),&o->authority,&custody);
  }
  cleanup_error(o,e.cleanup);
  cleanup_error(o,e.secondary_cleanup);
  cleanup_error(o,custody.close_error);
  cleanup_error(o,custody.secondary_close_error);
  if (!result) {
    o->status.stage=3;
    o->status.boot=boot_authority_compare(o->authority);
    /* No C/A producer or anchor: strict R/N1 reason ordering unchanged. */
    result=o->status.boot.error>0 ? o->status.boot.error : 0;
    cleanup_error(o,o->status.boot.cleanup_error);
  }
  native_bootstrap_refuse(o,result);
  return result;
}
int native_bootstrap_validate(struct native_bootstrap *o, const struct installed_pin *pins, size_t count) {
  if (!o) return EINVAL;
  int e=installed_validate(o->objects,pins,count);
  native_bootstrap_refuse(o,e); return e;
}
struct native_bootstrap_status native_bootstrap_status(struct native_bootstrap *o) {
  if (o) return o->status;
  struct native_bootstrap_status s={0}; return s;
}
int native_bootstrap_close_report(struct native_bootstrap **ptr,
                                  struct native_bootstrap_status *final) {
  if (!ptr || !*ptr) return 0;
  struct native_bootstrap *o=*ptr;
  struct boot_authority_errors b={0};
  int result=o->authority ? boot_authority_close(&o->authority,&b) : 0;
  /* An error report survives disposal of its custodian. Resource disposition
   * must not decide whether an operational or cleanup error is recorded. */
  native_bootstrap_refuse(o,b.error);
  native_bootstrap_refuse(o,result);
  cleanup_error(o,b.close_error);
  cleanup_error(o,b.secondary_close_error);
  if (o->authority) {
    if (final) *final=o->status;
    return result ? result : EBUSY;
  }
  struct installed_errors i={0};
  int installed_result=o->objects ? installed_close(&o->objects,&i) : 0;
  native_bootstrap_refuse(o,i.error);
  native_bootstrap_refuse(o,installed_result);
  cleanup_error(o,i.cleanup);
  cleanup_error(o,i.secondary_cleanup);
  if (!result) result=installed_result ? installed_result : o->status.cleanup;
  /* Failed closes keep their existing tombstones; never retry Linux close.
   * A safely disposed owner may still report a nonzero earlier custody error. */
  int retained=o->objects!=NULL || o->status.cleanup!=0;
  o->status.retained=retained;
  if (final) *final=o->status;
  if (!retained) { free(o); *ptr=NULL; }
  return result;
}
int native_bootstrap_close(struct native_bootstrap **ptr) {
  return native_bootstrap_close_report(ptr,NULL);
}

int native_bootstrap_mapping(struct native_bootstrap *o, uintptr_t address, uintptr_t base) {
  int e=o ? installed_mapping(o->objects,address,base) : EINVAL;
  native_bootstrap_refuse(o,e); return e;
}
