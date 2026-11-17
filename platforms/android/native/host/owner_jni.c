#define _GNU_SOURCE
#include "native_bootstrap.h"
#include <dlfcn.h>
#include <errno.h>
#include <jni.h>
#include <link.h>
#include <stddef.h>
#include <pthread.h>
#include <string.h>
#include <time.h>
#ifdef SCHEMII_CLOSED_PINS
#include "closed_pins.h"
#else
static const char closed_package[]="", closed_signer[]="";
static const struct installed_pin closed_pins[]={{"", ""}};
#endif
static struct native_bootstrap *owner;
static struct native_bootstrap_status snapshot;
static pthread_mutex_t calls=PTHREAD_MUTEX_INITIALIZER;
static pthread_t worker;
static int reserved, busy;
#ifndef SCHEMII_OBSERVATION_SOURCE
#define SCHEMII_OBSERVATION_SOURCE "unbound"
#endif
static struct acquisition_observation observation;
static jlong observation_begin=-1, observation_end=-1;
static int observation_phase, worker_known;
static jlong monotonic_ns(void) {
  struct timespec t;
  return clock_gettime(CLOCK_MONOTONIC,&t) ? -1 : (jlong)t.tv_sec*1000000000+t.tv_nsec;
}
static void step(struct acquisition_observation *r, int stage, int substage) {
  acquisition_step(r,stage,substage);
  pthread_mutex_lock(&calls); observation=*r; pthread_mutex_unlock(&calls);
}
static void merge_observation(struct acquisition_observation *r, struct acquisition_observation b) {
  if (!r->first_stage && b.first_stage) { r->first_stage=b.first_stage; r->first_substage=b.first_substage; }
  if (b.error) { if (!r->error) r->error=b.error; else if (r->error!=b.error && !r->secondary) r->secondary=b.error; }
  if (!r->secondary) r->secondary=b.secondary;
  if (!r->object.error) r->object=b.object;
  r->cleanup=b.cleanup; r->secondary_cleanup=b.secondary_cleanup;
  if (b.last_stage) { r->last_stage=b.last_stage; r->last_substage=b.last_substage; }
}
/* JNI is entered synchronously on Java's sole reserving executor thread.
 * No native attach, global/weak ref, asynchronous Java borrow or JNIEnv escape.
 * All locals belong to this explicit frame; PopLocalFrame disposes them once.
 * Java reservation cannot be replaced while this call or cleanup is unresolved. */
static jobject callback(JNIEnv *e, jclass service, const char *name) {
  jmethodID id=(*e)->GetStaticMethodID(e,service,name,"()Landroid/content/Context;");
  return id && !(*e)->ExceptionCheck(e) ? (*e)->CallStaticObjectMethod(e,service,id) : NULL;
}
static int paired(JNIEnv *e, jclass service, jobject app, jobject selected) {
  jobject a=callback(e,service,"frameworkOwnerApplication");
  if ((*e)->ExceptionCheck(e) || !a || !(*e)->IsSameObject(e,a,app)) return 0;
  jobject s=callback(e,service,"frameworkSelectedStorage");
  return !(*e)->ExceptionCheck(e) && s && (*e)->IsSameObject(e,s,selected) && (*e)->IsSameObject(e,a,s);
}
static jobject inspect(JNIEnv *e, jclass source, jobject app) {
  /* Consume stale diagnostic state before the actual invocation. Diagnostic
   * lookup/read failure is cleared, never substituted for a framework guard. */
  jmethodID previous=(*e)->GetStaticMethodID(e,source,"frameworkReason","()I");
  if (previous && !(*e)->ExceptionCheck(e)) (void)(*e)->CallStaticIntMethod(e,source,previous);
  if ((*e)->ExceptionCheck(e)) (*e)->ExceptionClear(e);
  jmethodID id=(*e)->GetStaticMethodID(e,source,"framework",
    "(Landroid/content/Context;)Ldevelopment/landmine/schemii/android/host/InstalledSource$Framework;");
  return id && !(*e)->ExceptionCheck(e) ? (*e)->CallStaticObjectMethod(e,source,id,app) : NULL;
}
static jstring field(JNIEnv *e, jclass cls, jobject object, const char *name) {
  jfieldID id=(*e)->GetFieldID(e,cls,name,"Ljava/lang/String;");
  return id && !(*e)->ExceptionCheck(e) ? (*e)->GetObjectField(e,object,id) : NULL;
}
/* Observation only: bounded loader-name bytes, never emitted directly. */
struct first_image_refusal {
  unsigned count, phnum; int complete, stage;
  size_t length; unsigned char name[512];
};
static struct first_image_refusal first_image;
struct image_inventory {
  const char *module; unsigned own; int refused, reason, count;
  struct first_image_refusal first;
};
static void capture_first_image(struct image_inventory *inventory, const struct dl_phdr_info *info) {
  inventory->first.count=(unsigned)inventory->count;
  inventory->first.phnum=info->dlpi_phnum;
  size_t i=0;
  for (; i<sizeof(inventory->first.name) && info->dlpi_name[i]; ++i)
    inventory->first.name[i]=(unsigned char)info->dlpi_name[i];
  inventory->first.length=i;
  inventory->first.complete=i<sizeof(inventory->first.name);
}
static int images(struct dl_phdr_info *info, size_t size, void *arg) {
  struct image_inventory *inventory=arg;
  if (inventory->count<4096) ++inventory->count;
  if (size < offsetof(struct dl_phdr_info,dlpi_phnum)+sizeof(info->dlpi_phnum)) {
    inventory->refused=1; inventory->reason=1; return 1;
  }
  const char *name=info->dlpi_name;
  if (!name) { inventory->refused=1; inventory->reason=2; return 1; }
  if (!strcmp(name,inventory->module)) ++inventory->own;
  else if (!*name || !strcmp(name,"linux-vdso.so.1") || !strcmp(name,"[vdso]") ||
           !strncmp(name,"/system/",8) || !strncmp(name,"/apex/",6)) {
    /* Stock OS runtime/ART dependency boundary; no application plugin ELF. */
  } else { inventory->refused=1; inventory->reason=3; capture_first_image(inventory,info); return 1; }
  return 0;
}
static int closed_images(const char *module, struct acquisition_observation *r) {
  struct image_inventory inventory={.module=module};
  dl_iterate_phdr(images,&inventory);
  int result=inventory.refused || inventory.own!=1 ? EACCES : 0;
  if (result && !r->image_reason) { r->image_reason=inventory.reason ? inventory.reason : 4; r->image_count=inventory.count; }
  if (inventory.first.count) {
    int saved_errno=errno;
    pthread_mutex_lock(&calls);
    if (!first_image.count) { first_image=inventory.first; first_image.stage=r->last_stage; }
    pthread_mutex_unlock(&calls);
    errno=saved_errno;
  }
  return result;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_open(JNIEnv *e,jclass cls) {
  pthread_mutex_lock(&calls);
  if (reserved) { pthread_mutex_unlock(&calls); return EBUSY; }
  reserved=busy=1; worker=pthread_self(); memset(&snapshot,0,sizeof(snapshot));
  observation=(struct acquisition_observation){0};
  first_image=(struct first_image_refusal){0};
  observation_begin=monotonic_ns(); observation_end=-1; observation_phase=1; worker_known=1;
  snapshot.retained=1; snapshot.stage=1;
  snapshot.boot=(struct boot_compare_result){BOOT_COMPARE_UNKNOWN,BOOT_COMPARE_CALLER_UNPROVED,
    -1,-1,0,0,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_CONTINUITY_UNPROVED};
  pthread_mutex_unlock(&calls);
  struct acquisition_observation diagnostic={0};
  jclass source=NULL;
  int result=EACCES, frame=0;
  struct native_bootstrap *acquired=NULL;
  const char *text[5]={0}; jstring strings[5]={0};
  step(&diagnostic,AQ_FRAME,1);
  if ((*e)->PushLocalFrame(e,64)<0) { result=ENOMEM; goto done; }
  frame=1;
  step(&diagnostic,AQ_PINS,1);
  if (!*closed_package || !*closed_signer) goto done;
  step(&diagnostic,AQ_CLASS,1);
  jclass native_class=(*e)->FindClass(e,"development/landmine/schemii/android/host/NativeOwner");
  if ((*e)->ExceptionCheck(e) || !native_class || !(*e)->IsSameObject(e,cls,native_class)) goto done;
  step(&diagnostic,AQ_CLASS,2);
  jclass service=(*e)->FindClass(e,"development/landmine/schemii/android/host/HostService");
  if ((*e)->ExceptionCheck(e) || !service) goto done;
  step(&diagnostic,AQ_CLASS,3);
  source=(*e)->FindClass(e,"development/landmine/schemii/android/host/InstalledSource");
  if ((*e)->ExceptionCheck(e) || !source) goto done;
  step(&diagnostic,AQ_CLASS,4);
  jclass values=(*e)->FindClass(e,"development/landmine/schemii/android/host/InstalledSource$Framework");
  if ((*e)->ExceptionCheck(e) || !service || !source || !values) goto done;
  step(&diagnostic,AQ_APPLICATION,1);
  jobject app=callback(e,service,"frameworkOwnerApplication");
  if ((*e)->ExceptionCheck(e) || !app) goto done;
  step(&diagnostic,AQ_SELECTED,1);
  jobject selected=callback(e,service,"frameworkSelectedStorage");
  if ((*e)->ExceptionCheck(e) || !selected || !(*e)->IsSameObject(e,app,selected)) goto done;
  step(&diagnostic,AQ_FRAMEWORK,1);
  jobject observed=inspect(e,source,app);
  if ((*e)->ExceptionCheck(e) || !observed) goto done;
  const char *names[]={"packageName","signer","apk","module","data"};
  for (unsigned i=0;i<5;i++) {
    step(&diagnostic,AQ_FIELDS,(int)i+1);
    strings[i]=field(e,values,observed,names[i]);
    if ((*e)->ExceptionCheck(e) || !strings[i]) goto done;
    text[i]=(*e)->GetStringUTFChars(e,strings[i],NULL);
    if (!text[i] || (*e)->ExceptionCheck(e)) { result=ENOMEM; goto done; }
  }
  step(&diagnostic,AQ_PACKAGE,1);
  if (strcmp(text[0],closed_package) || strcmp(text[1],closed_signer)) goto done;
  /* dladdr identifies the current mapped image. The installed APK and extracted
   * image are held and compared. Actual process/mapping/install witness remains
   * separately required: this is cooperative stock-OS acquisition, not C. */
  step(&diagnostic,AQ_MODULE,1);
  Dl_info image;
  if (!dladdr((void *)&Java_development_landmine_schemii_android_host_NativeOwner_open,&image) ||
      !image.dli_fname || strcmp(image.dli_fname,text[3])) goto done;
  step(&diagnostic,AQ_IMAGES,1);
  result=closed_images(text[3],&diagnostic);
  if (result) goto done;
  step(&diagnostic,AQ_PAIR_PRE,1);
  if (!paired(e,service,app,selected)) { result=ECANCELED; goto done; }
  step(&diagnostic,AQ_OBJECTS,1);
  result=native_bootstrap_open(text[2],text[3],text[4],closed_pins,
    sizeof(closed_pins)/sizeof(closed_pins[0]),&acquired);
  merge_observation(&diagnostic,native_bootstrap_observe(acquired));
  if (!result) {
    step(&diagnostic,AQ_MAPPING,1);
    result=native_bootstrap_mapping(acquired,(uintptr_t)image.dli_saddr,(uintptr_t)image.dli_fbase);
    merge_observation(&diagnostic,native_bootstrap_observe(acquired));
  }
  if (result) goto done;
  /* Both reference identity and framework observations are revalidated after
   * held-object acquisition and again immediately before scalar publication. */
  step(&diagnostic,AQ_PAIR_POST,1);
  if (!paired(e,service,app,selected)) { result=ECANCELED; goto done; }
  step(&diagnostic,AQ_FRAMEWORK_POST,1);
  jobject again=inspect(e,source,app);
  if ((*e)->ExceptionCheck(e) || !again) { result=EACCES; goto done; }
  for (unsigned i=0;i<5;i++) {
    step(&diagnostic,AQ_FIELDS_POST,(int)i+1);
    jstring value=field(e,values,again,names[i]);
    if ((*e)->ExceptionCheck(e) || !value) { result=EACCES; goto done; }
    const char *s=(*e)->GetStringUTFChars(e,value,NULL);
    if (!s) { result=ENOMEM; goto done; }
    int equal=!strcmp(s,text[i]); (*e)->ReleaseStringUTFChars(e,value,s);
    if (!equal) { result=ESTALE; goto done; }
  }
  step(&diagnostic,AQ_REVALIDATE,1);
  result=native_bootstrap_validate(acquired,closed_pins,sizeof(closed_pins)/sizeof(closed_pins[0]));
  merge_observation(&diagnostic,native_bootstrap_observe(acquired));
  if (!result) {
    step(&diagnostic,AQ_MAPPING_POST,1);
    result=native_bootstrap_mapping(acquired,(uintptr_t)image.dli_saddr,(uintptr_t)image.dli_fbase);
    struct acquisition_observation mapped=native_bootstrap_observe(acquired);
    if (mapped.first_stage==AQ_MAPPING) mapped.first_stage=AQ_MAPPING_POST;
    mapped.last_stage=AQ_MAPPING_POST;
    merge_observation(&diagnostic,mapped);
  }
  if (!result) { step(&diagnostic,AQ_IMAGES_POST,1); result=closed_images(text[3],&diagnostic); }
  if (!result) { step(&diagnostic,AQ_PAIR_PUBLISH,1); if (!paired(e,service,app,selected)) result=ECANCELED; }
done:
  if ((*e)->ExceptionCheck(e)) { (*e)->ExceptionClear(e); if (!result) result=EACCES; }
  acquisition_failure(&diagnostic,result);
  /* Only finite reason is read, after clearing the original exception exactly
   * once. This diagnostic getter cannot change result, callbacks or admission. */
  if (source && (diagnostic.last_stage==AQ_FRAMEWORK || diagnostic.last_stage==AQ_FRAMEWORK_POST)) {
    jmethodID reason=(*e)->GetStaticMethodID(e,source,"frameworkReason","()I");
    if (reason && !(*e)->ExceptionCheck(e)) {
      jint value=(*e)->CallStaticIntMethod(e,source,reason);
      if (!(*e)->ExceptionCheck(e) && value>=0 && value<=9) diagnostic.framework_reason=value;
    }
    if ((*e)->ExceptionCheck(e)) (*e)->ExceptionClear(e);
  }
  for (unsigned i=0;i<5;i++) if (text[i]) (*e)->ReleaseStringUTFChars(e,strings[i],text[i]);
  if (frame) (*e)->PopLocalFrame(e,NULL);
  native_bootstrap_refuse(acquired,result);
  pthread_mutex_lock(&calls);
  owner=acquired;
  if (owner) snapshot=native_bootstrap_status(owner);
  else { snapshot.error=result; snapshot.retained=0; reserved=0; }
  observation=diagnostic; observation_end=monotonic_ns(); observation_phase=2;
  busy=0;
  pthread_mutex_unlock(&calls);
  return result;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_request(JNIEnv *e,jclass cls,jint op) {
  (void)e; (void)cls; (void)op;
  return EACCES; /* No A, journal or managed birth ingress. */
}
JNIEXPORT jlongArray JNICALL Java_development_landmine_schemii_android_host_NativeOwner_status(JNIEnv *e,jclass cls) {
  (void)cls; pthread_mutex_lock(&calls);
  struct native_bootstrap_status s=snapshot; int pending=busy;
  pthread_mutex_unlock(&calls);
  jlong v[]={s.error,s.secondary,s.cleanup,s.secondary_cleanup,s.retained,pending,s.stage,
    s.boot.relation,s.boot.reason,s.boot.observation_status,s.boot.observation_stage,
    s.boot.error,s.boot.cleanup_error,s.boot.current_admission,s.boot.prior_admission,s.boot.continuity};
  jlongArray out=(*e)->NewLongArray(e,16);
  if (out) (*e)->SetLongArrayRegion(e,out,0,16,v);
  return out;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_close(JNIEnv *e,jclass cls) {
  (void)e; (void)cls; pthread_mutex_lock(&calls);
  if (busy || (reserved && !pthread_equal(worker,pthread_self()))) {
    pthread_mutex_unlock(&calls); return EBUSY;
  }
  busy=1; observation_phase=3;
  struct acquisition_observation final_observation=observation, disposed={0};
  struct native_bootstrap *closing=owner;
  struct native_bootstrap_status final=snapshot;
  pthread_mutex_unlock(&calls);
  int result=native_bootstrap_close_observed(&closing,&final,&disposed);
  merge_observation(&final_observation,disposed);
  acquisition_step(&final_observation,AQ_CLOSE,2);
  pthread_mutex_lock(&calls);
  owner=closing;
  snapshot=final;
  if (!owner) { snapshot.retained=0; reserved=0; }
  observation=final_observation; observation_end=monotonic_ns(); observation_phase=owner ? 5 : 4;
  busy=0; pthread_mutex_unlock(&calls); return result;
}

/* Separate versioned read-only diagnostic projection; legacy status() stays16. */
JNIEXPORT jlongArray JNICALL Java_development_landmine_schemii_android_host_NativeOwner_observation(JNIEnv *e,jclass cls) {
  (void)cls; pthread_mutex_lock(&calls);
  struct acquisition_observation d=observation; struct native_bootstrap_status s=snapshot;
  if (!worker_known) s.boot=(struct boot_compare_result){BOOT_COMPARE_UNKNOWN,BOOT_COMPARE_CALLER_UNPROVED,
    -1,-1,0,0,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_CONTINUITY_UNPROVED};
  jlong v[]={1,34,d.first_stage,d.first_substage,d.last_stage,d.last_substage,
    d.framework_reason,d.image_reason,d.image_count,d.object.kind,d.object.hop,
    d.object.operation,d.object.syscall_error,d.object.error,d.error,d.secondary,
    d.cleanup,d.secondary_cleanup,s.retained,busy,reserved,observation_phase,
    s.boot.relation,s.boot.reason,s.boot.observation_status,s.boot.observation_stage,
    s.boot.error,s.boot.cleanup_error,s.boot.current_admission,s.boot.prior_admission,
    s.boot.continuity,observation_begin,observation_end,worker_known && pthread_equal(worker,pthread_self())};
  pthread_mutex_unlock(&calls);
  jlongArray out=(*e)->NewLongArray(e,34);
  if (out) (*e)->SetLongArrayRegion(e,out,0,34,v);
  return out;
}
/* Separate private observation ABI; raw16/raw34 and all decisions stay unchanged.
 * Same-worker consumer must redact the name bytes before any logging. */
JNIEXPORT jlongArray JNICALL Java_development_landmine_schemii_android_host_NativeOwner_imageRefusal(JNIEnv *e,jclass cls) {
  (void)cls; pthread_mutex_lock(&calls);
  int readable=!busy && worker_known && pthread_equal(worker,pthread_self());
  struct first_image_refusal d=first_image; jlong begin=observation_begin;
  pthread_mutex_unlock(&calls);
  if (!readable || !d.count) return NULL;
  jlong v[8+512]={1,(jlong)(8+d.length),d.count,d.phnum,d.complete,begin,d.stage,(jlong)d.length};
  for (size_t i=0;i<d.length;++i) v[8+i]=d.name[i];
  jlongArray out=(*e)->NewLongArray(e,(jsize)(8+d.length));
  if (out) (*e)->SetLongArrayRegion(e,out,0,(jsize)(8+d.length),v);
  return out;
}
JNIEXPORT jstring JNICALL Java_development_landmine_schemii_android_host_NativeOwner_observationSource(JNIEnv *e,jclass cls) {
  (void)cls; return (*e)->NewStringUTF(e,SCHEMII_OBSERVATION_SOURCE);
}
