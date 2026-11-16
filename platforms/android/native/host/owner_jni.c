#include "owner.h"
#include <jni.h>
#include <pthread.h>
static struct apk_owner *owner;
static pthread_mutex_t calls=PTHREAD_MUTEX_INITIALIZER;
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_open(JNIEnv *env,jclass cls,jint fd) {
    (void)env; (void)cls; pthread_mutex_lock(&calls);
    int result=owner?16:apk_owner_open(fd,&owner);
    pthread_mutex_unlock(&calls); return result;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_request(JNIEnv *env,jclass cls,jint op) {
    (void)env; (void)cls; pthread_mutex_lock(&calls);
    int result=apk_owner_request(owner,(uint32_t)op);
    pthread_mutex_unlock(&calls); return result;
}
JNIEXPORT jlongArray JNICALL Java_development_landmine_schemii_android_host_NativeOwner_status(JNIEnv *env,jclass cls) {
    (void)cls; pthread_mutex_lock(&calls);
    struct apk_owner_status s=apk_owner_status(owner); pthread_mutex_unlock(&calls);
    jlong values[]={ (jlong)s.sequence,(jlong)s.monotonic_ns,s.phase,s.reason,s.error,s.cleanup_error,s.journal_status,s.journal_effect,s.boot_relation,s.boot_reason,s.births,s.closure_known };
    jlongArray out=(*env)->NewLongArray(env,12);
    if (out) (*env)->SetLongArrayRegion(env,out,0,12,values);
    return out;
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_NativeOwner_close(JNIEnv *env,jclass cls) {
    (void)env; (void)cls; pthread_mutex_lock(&calls); int result=apk_owner_close(&owner); pthread_mutex_unlock(&calls); return result;
}
