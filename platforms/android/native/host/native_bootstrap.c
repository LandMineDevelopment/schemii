#include "native_bootstrap.h"
#include <errno.h>
#include <stdlib.h>
struct native_bootstrap {
  struct installed_objects *objects;
  struct boot_authority *authority;
  struct native_bootstrap_status status;
  struct acquisition_observation observation;
  int object_closes_seen, custody_closes_seen;
};
void acquisition_step(struct acquisition_observation *r, int stage, int substage) {
  r->last_stage=stage; r->last_substage=substage;
}
void acquisition_failure(struct acquisition_observation *r, int error) {
  if (error<=0) return;
  if (!r->first_stage) { r->first_stage=r->last_stage; r->first_substage=r->last_substage; }
  if (!r->error) r->error=error;
  else if (error!=r->error && !r->secondary) r->secondary=error;
}
struct acquisition_observation native_bootstrap_observe(struct native_bootstrap *o) {
  struct acquisition_observation r={0};
  if (o) { r=o->observation; if (o->objects) r.object=installed_observe(o->objects); }
  return r;
}
static void observed_cleanup(struct native_bootstrap *o, int error) {
  if (!error) return;
  if (!o->observation.first_stage) {
    o->observation.first_stage=o->observation.last_stage;
    o->observation.first_substage=o->observation.last_substage;
  }
  if (!o->observation.cleanup) o->observation.cleanup=error;
  else if (!o->observation.secondary_cleanup) o->observation.secondary_cleanup=error;
}
static void cleanup_report(struct native_bootstrap *o, int first, int second, int count, int *seen) {
  /* Cumulative reports are not new close events. Preserve equal errno values
   * from two distinct closes without counting the same report twice. */
  for (int i=*seen;i<count && i<2;++i) observed_cleanup(o,i ? second : first);
  *seen=count;
}
static void object_report(struct native_bootstrap *o) {
  if (!o->objects) return;
  struct installed_errors e=installed_error_observe(o->objects);
  o->observation.object=installed_observe(o->objects);
  cleanup_report(o,e.cleanup,e.secondary_cleanup,e.close_failures,&o->object_closes_seen);
}
static void cleanup_error(struct native_bootstrap *o, int error) {
  if (error <= 0) return;
  if (!o->observation.first_stage) {
    o->observation.first_stage=o->observation.last_stage;
    o->observation.first_substage=o->observation.last_substage;
  }
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
  acquisition_step(&o->observation,AQ_OBJECTS,1);
  int result=installed_open(apk,module,data,&o->objects,&e);
  object_report(o);
  acquisition_failure(&o->observation,result);
  if (!result) {
    acquisition_step(&o->observation,AQ_MEMBERS,1);
    result=installed_validate(o->objects,pins,count);
    object_report(o);
    acquisition_failure(&o->observation,result);
  }
  struct boot_authority_errors custody={0};
  if (!result) {
    o->status.stage=2;
    acquisition_step(&o->observation,AQ_CUSTODY,1);
    result=boot_authority_open(installed_selected(o->objects),&o->authority,&custody);
    cleanup_report(o,custody.close_error,custody.secondary_close_error,custody.close_failures,&o->custody_closes_seen);
  }
  acquisition_failure(&o->observation,result);
  cleanup_error(o,e.cleanup);
  cleanup_error(o,e.secondary_cleanup);
  cleanup_error(o,custody.close_error);
  cleanup_error(o,custody.secondary_close_error);
  if (!result) {
    o->status.stage=3;
    acquisition_step(&o->observation,AQ_BOOT,1);
    struct boot_authority_errors observed={0};
    o->status.boot=boot_authority_compare_report(o->authority,&observed);
    /* No C/A producer or anchor: strict R/N1 reason ordering unchanged. */
    /* A pre-R custody failure skips R. Otherwise any observed R error precedes
     * the post-R custody probe. Publish both before later JNI validation. */
    native_bootstrap_refuse(o,o->status.boot.error>0 ? o->status.boot.error : 0);
    acquisition_failure(&o->observation,o->status.boot.error>0 ? o->status.boot.error : 0);
    observed_cleanup(o,o->status.boot.cleanup_error);
    cleanup_error(o,o->status.boot.cleanup_error);
    acquisition_step(&o->observation,AQ_BOOT,2);
    acquisition_failure(&o->observation,observed.error);
    cleanup_report(o,observed.close_error,observed.secondary_close_error,observed.close_failures,&o->custody_closes_seen);
    native_bootstrap_refuse(o,observed.error);
    cleanup_error(o,observed.close_error);
    cleanup_error(o,observed.secondary_close_error);
    result=o->status.error;
  }
  native_bootstrap_refuse(o,result);
  return result;
}
int native_bootstrap_validate(struct native_bootstrap *o, const struct installed_pin *pins, size_t count) {
  if (!o) return EINVAL;
  acquisition_step(&o->observation,AQ_REVALIDATE,1);
  int e=installed_validate(o->objects,pins,count);
  object_report(o);
  acquisition_failure(&o->observation,e);
  native_bootstrap_refuse(o,e); return e;
}
struct native_bootstrap_status native_bootstrap_status(struct native_bootstrap *o) {
  if (o) return o->status;
  struct native_bootstrap_status s={0}; return s;
}
int native_bootstrap_close_observed(struct native_bootstrap **ptr,
                                  struct native_bootstrap_status *final,
                                  struct acquisition_observation *observation) {
  if (!ptr || !*ptr) return 0;
  struct native_bootstrap *o=*ptr;
  acquisition_step(&o->observation,AQ_CLOSE,1);
  struct boot_authority_errors b={0};
  int result=o->authority ? boot_authority_close(&o->authority,&b) : 0;
  cleanup_report(o,b.close_error,b.secondary_close_error,b.close_failures,&o->custody_closes_seen);
  /* An error report survives disposal of its custodian. Resource disposition
   * must not decide whether an operational or cleanup error is recorded. */
  if (b.error!=b.close_error && b.error!=b.secondary_close_error)
    acquisition_failure(&o->observation,b.error);
  native_bootstrap_refuse(o,b.error);
  native_bootstrap_refuse(o,result);
  cleanup_error(o,b.close_error);
  cleanup_error(o,b.secondary_close_error);
  if (o->authority) {
    if (final) *final=o->status;
    if (observation) *observation=native_bootstrap_observe(o);
    return result ? result : EBUSY;
  }
  acquisition_step(&o->observation,AQ_CLOSE,2);
  o->observation=native_bootstrap_observe(o); /* before possible object disposal */
  struct installed_errors i={0};
  int installed_result=o->objects ? installed_close(&o->objects,&i) : 0;
  cleanup_report(o,i.cleanup,i.secondary_cleanup,i.close_failures,&o->object_closes_seen);
  native_bootstrap_refuse(o,i.error);
  native_bootstrap_refuse(o,installed_result);
  cleanup_error(o,i.cleanup);
  cleanup_error(o,i.secondary_cleanup);
  if (!result) result=installed_result ? installed_result : o->status.cleanup;
  /* Failed closes keep their existing tombstones; never retry Linux close.
   * A safely disposed owner may still report a nonzero earlier custody error. */
  int retained=o->objects!=NULL || o->status.cleanup!=0;
  o->status.retained=retained;
  if (!o->observation.first_stage && o->status.cleanup) {
    o->observation.first_stage=AQ_CLOSE; o->observation.first_substage=2;
  }
  if (final) *final=o->status;
  if (observation) *observation=o->observation;
  if (!retained) { free(o); *ptr=NULL; }
  return result;
}
int native_bootstrap_close_report(struct native_bootstrap **ptr, struct native_bootstrap_status *final) {
  return native_bootstrap_close_observed(ptr,final,NULL);
}
int native_bootstrap_close(struct native_bootstrap **ptr) {
  return native_bootstrap_close_report(ptr,NULL);
}

int native_bootstrap_mapping(struct native_bootstrap *o, uintptr_t address, uintptr_t base) {
  if (o) acquisition_step(&o->observation,AQ_MAPPING,1);
  int e=o ? installed_mapping(o->objects,address,base) : EINVAL;
  if (o) object_report(o);
  if (o) acquisition_failure(&o->observation,e);
  native_bootstrap_refuse(o,e); return e;
}
