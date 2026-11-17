#define _GNU_SOURCE
#include "native_bootstrap.h"
#include <dlfcn.h>
#include <errno.h>
#include <jni.h>
#include <link.h>
#include <stddef.h>
#include <pthread.h>
#include <string.h>
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
  jmethodID id=(*e)->GetStaticMethodID(e,source,"framework",
    "(Landroid/content/Context;)Ldevelopment/landmine/schemii/android/host/InstalledSource$Framework;");
  return id && !(*e)->ExceptionCheck(e) ? (*e)->CallStaticObjectMethod(e,source,id,app) : NULL;
}
static jstring field(JNIEnv *e, jclass cls, jobject object, const char *name) {
  jfieldID id=(*e)->GetFieldID(e,cls,name,"Ljava/lang/String;");
  return id && !(*e)->ExceptionCheck(e) ? (*e)->GetObjectField(e,object,id) : NULL;
}
struct image_inventory { const char *module; unsigned own; int refused; };
static int images(struct dl_phdr_info *info, size_t size, void *arg) {
  struct image_inventory *inventory=arg;
  if (size < offsetof(struct dl_phdr_info,dlpi_phnum)+sizeof(info->dlpi_phnum)) {
    inventory->refused=1; return 1;
  }
  const char *name=info->dlpi_name;
  if (!name) { inventory->refused=1; return 1; }
  if (!strcmp(name,inventory->module)) ++inventory->own;
  else if (!*name || !strcmp(name,"linux-vdso.so.1") || !strcmp(name,"[vdso]") ||
           !strncmp(name,"/system/",8) || !strncmp(name,"/apex/",6)) {
    /* Stock OS runtime/ART dependency boundary; no application plugin ELF. */
  } else { inventory->refused=1; return 1; }
  return 0;
}
static int closed_images(const char *module) {
  struct image_inventory inventory={module,0,0};
  dl_iterate_phdr(images,&inventory);
  return inventory.refused || inventory.own!=1 ? EACCES : 0;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_open(JNIEnv *e,jclass cls) {
  pthread_mutex_lock(&calls);
  if (reserved) { pthread_mutex_unlock(&calls); return EBUSY; }
  reserved=busy=1; worker=pthread_self(); memset(&snapshot,0,sizeof(snapshot));
  snapshot.retained=1; snapshot.stage=1;
  snapshot.boot=(struct boot_compare_result){BOOT_COMPARE_UNKNOWN,BOOT_COMPARE_CALLER_UNPROVED,
    -1,-1,0,0,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_UNPROVED,BOOT_COMPARE_CONTINUITY_UNPROVED};
  pthread_mutex_unlock(&calls);
  int result=EACCES, frame=0;
  struct native_bootstrap *acquired=NULL;
  const char *text[5]={0}; jstring strings[5]={0};
  if ((*e)->PushLocalFrame(e,64)<0) { result=ENOMEM; goto done; }
  frame=1;
  if (!*closed_package || !*closed_signer) goto done;
  jclass native_class=(*e)->FindClass(e,"development/landmine/schemii/android/host/NativeOwner");
  if ((*e)->ExceptionCheck(e) || !native_class || !(*e)->IsSameObject(e,cls,native_class)) goto done;
  jclass service=(*e)->FindClass(e,"development/landmine/schemii/android/host/HostService");
  if ((*e)->ExceptionCheck(e) || !service) goto done;
  jclass source=(*e)->FindClass(e,"development/landmine/schemii/android/host/InstalledSource");
  if ((*e)->ExceptionCheck(e) || !source) goto done;
  jclass values=(*e)->FindClass(e,"development/landmine/schemii/android/host/InstalledSource$Framework");
  if ((*e)->ExceptionCheck(e) || !service || !source || !values) goto done;
  jobject app=callback(e,service,"frameworkOwnerApplication");
  if ((*e)->ExceptionCheck(e) || !app) goto done;
  jobject selected=callback(e,service,"frameworkSelectedStorage");
  if ((*e)->ExceptionCheck(e) || !selected || !(*e)->IsSameObject(e,app,selected)) goto done;
  jobject observed=inspect(e,source,app);
  if ((*e)->ExceptionCheck(e) || !observed) goto done;
  const char *names[]={"packageName","signer","apk","module","data"};
  for (unsigned i=0;i<5;i++) {
    strings[i]=field(e,values,observed,names[i]);
    if ((*e)->ExceptionCheck(e) || !strings[i]) goto done;
    text[i]=(*e)->GetStringUTFChars(e,strings[i],NULL);
    if (!text[i] || (*e)->ExceptionCheck(e)) { result=ENOMEM; goto done; }
  }
  if (strcmp(text[0],closed_package) || strcmp(text[1],closed_signer)) goto done;
  /* dladdr identifies the current mapped image. The installed APK and extracted
   * image are held and compared. Actual process/mapping/install witness remains
   * separately required: this is cooperative stock-OS acquisition, not C. */
  Dl_info image;
  if (!dladdr((void *)&Java_development_landmine_schemii_android_host_NativeOwner_open,&image) ||
      !image.dli_fname || strcmp(image.dli_fname,text[3])) goto done;
  result=closed_images(text[3]);
  if (result) goto done;
  if (!paired(e,service,app,selected)) { result=ECANCELED; goto done; }
  result=native_bootstrap_open(text[2],text[3],text[4],closed_pins,
    sizeof(closed_pins)/sizeof(closed_pins[0]),&acquired);
  if (!result) result=native_bootstrap_mapping(acquired,(uintptr_t)image.dli_saddr,(uintptr_t)image.dli_fbase);
  if (result) goto done;
  /* Both reference identity and framework observations are revalidated after
   * held-object acquisition and again immediately before scalar publication. */
  if (!paired(e,service,app,selected)) { result=ECANCELED; goto done; }
  jobject again=inspect(e,source,app);
  if ((*e)->ExceptionCheck(e) || !again) { result=EACCES; goto done; }
  for (unsigned i=0;i<5;i++) {
    jstring value=field(e,values,again,names[i]);
    if ((*e)->ExceptionCheck(e) || !value) { result=EACCES; goto done; }
    const char *s=(*e)->GetStringUTFChars(e,value,NULL);
    if (!s) { result=ENOMEM; goto done; }
    int equal=!strcmp(s,text[i]); (*e)->ReleaseStringUTFChars(e,value,s);
    if (!equal) { result=ESTALE; goto done; }
  }
  result=native_bootstrap_validate(acquired,closed_pins,sizeof(closed_pins)/sizeof(closed_pins[0]));
  if (!result) result=native_bootstrap_mapping(acquired,(uintptr_t)image.dli_saddr,(uintptr_t)image.dli_fbase);
  if (!result) result=closed_images(text[3]);
  if (!result && !paired(e,service,app,selected)) result=ECANCELED;
done:
  if ((*e)->ExceptionCheck(e)) { (*e)->ExceptionClear(e); if (!result) result=EACCES; }
  for (unsigned i=0;i<5;i++) if (text[i]) (*e)->ReleaseStringUTFChars(e,strings[i],text[i]);
  if (frame) (*e)->PopLocalFrame(e,NULL);
  native_bootstrap_refuse(acquired,result);
  pthread_mutex_lock(&calls);
  owner=acquired;
  if (owner) snapshot=native_bootstrap_status(owner);
  else { snapshot.error=result; snapshot.retained=0; reserved=0; }
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
  busy=1; struct native_bootstrap *closing=owner;
  pthread_mutex_unlock(&calls);
  int result=native_bootstrap_close(&closing);
  pthread_mutex_lock(&calls);
  owner=closing;
  if (owner) snapshot=native_bootstrap_status(owner);
  else { snapshot.retained=0; if (!snapshot.cleanup) snapshot.cleanup=result; reserved=0; }
  busy=0; pthread_mutex_unlock(&calls); return result;
}
