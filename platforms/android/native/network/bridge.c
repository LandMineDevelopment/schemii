/* Compiled once by the Android-only cgo shim in the existing Go module. */
#include "bridge.h"
#include <jni.h>
#include <pthread.h>
#include <string.h>

extern char *SchemiiNetworkProbe(void);
extern char *SchemiiNetworkStart(char *configuration);
extern int SchemiiNetworkStop(int timeout_ms);
extern void SchemiiNetworkChanged(void);

#define STRINGIFY_INNER(x) #x
#define STRINGIFY(x) STRINGIFY_INNER(x)
const char *schemii_android_bridge_identity(void) { return STRINGIFY(SCHEMII_NETWORK_BRIDGE_SHA256); }

static pthread_mutex_t owner_lock = PTHREAD_MUTEX_INITIALIZER;
static JavaVM *owner_vm;
static jobject owner_bridge;

struct call { JNIEnv *env; jobject bridge; int attached; };
static struct call borrow(void) {
    struct call c = {0};
    pthread_mutex_lock(&owner_lock);
    JavaVM *vm = owner_vm;
    if (vm && owner_bridge) {
        jint status = (*vm)->GetEnv(vm, (void **)&c.env, JNI_VERSION_1_6);
        if (status == JNI_EDETACHED) {
            if ((*vm)->AttachCurrentThread(vm, &c.env, NULL) == JNI_OK) c.attached = 1;
            else c.env = NULL;
        } else if (status != JNI_OK) c.env = NULL;
        if (c.env) c.bridge = (*c.env)->NewLocalRef(c.env, owner_bridge);
    }
    pthread_mutex_unlock(&owner_lock);
    return c;
}
static void release(struct call c) {
    if (c.env) {
        if ((*c.env)->ExceptionCheck(c.env)) (*c.env)->ExceptionClear(c.env);
        if (c.bridge) (*c.env)->DeleteLocalRef(c.env, c.bridge);
    }
    if (c.attached) (*owner_vm)->DetachCurrentThread(owner_vm);
}
static jmethodID method(struct call c, const char *name, const char *signature) {
    if (!c.bridge) return NULL;
    jclass cls = (*c.env)->GetObjectClass(c.env, c.bridge);
    jmethodID id = cls ? (*c.env)->GetMethodID(c.env, cls, name, signature) : NULL;
    if (cls) (*c.env)->DeleteLocalRef(c.env, cls);
    return id;
}
static char *text_call(const char *name, int limit) {
    struct call c = borrow(); char *out = NULL;
    jmethodID id = method(c, name, "()Ljava/lang/String;");
    if (id) {
        jstring text = (*c.env)->CallObjectMethod(c.env, c.bridge, id);
        if (!(*c.env)->ExceptionCheck(c.env) && text && (*c.env)->GetStringUTFLength(c.env, text) <= limit) {
            const char *bytes = (*c.env)->GetStringUTFChars(c.env, text, NULL);
            if (bytes) { out = strdup(bytes); (*c.env)->ReleaseStringUTFChars(c.env, text, bytes); }
        }
        if (text) (*c.env)->DeleteLocalRef(c.env, text);
    }
    release(c); return out;
}
char *schemii_android_snapshot(void) { return text_call("snapshotJson", 16384); }
char *schemii_android_roots(void) { return text_call("systemRootsPem", 2 * 1024 * 1024); }
int schemii_android_bind(uint64_t generation, int fd) {
    struct call c = borrow(); int ok = 0;
    jmethodID id = method(c, "bindSocket", "(JI)Z");
    if (id) ok = (*c.env)->CallBooleanMethod(c.env, c.bridge, id, (jlong)generation, (jint)fd) == JNI_TRUE;
    if (c.env && (*c.env)->ExceptionCheck(c.env)) ok = 0;
    release(c); return ok;
}
unsigned char *schemii_android_dns(uint64_t generation, uint64_t request,
        const unsigned char *query, int length, int timeout_ms, int *answer_length) {
    struct call c = borrow(); unsigned char *out = NULL; *answer_length = 0;
    jmethodID id = method(c, "queryDNS", "(JJ[BJ)[B");
    if (id && length >= 12 && length <= 65535) {
        jbyteArray input = (*c.env)->NewByteArray(c.env, length);
        if (input) {
            (*c.env)->SetByteArrayRegion(c.env, input, 0, length, (const jbyte *)query);
            jbyteArray result = (*c.env)->CallObjectMethod(c.env, c.bridge, id,
                (jlong)generation, (jlong)request, input, (jlong)timeout_ms);
            if (!(*c.env)->ExceptionCheck(c.env) && result) {
                jsize n = (*c.env)->GetArrayLength(c.env, result);
                if (n >= 12 && n <= 65535 && (out = malloc(n))) {
                    (*c.env)->GetByteArrayRegion(c.env, result, 0, n, (jbyte *)out);
                    *answer_length = n;
                }
            }
            if (result) (*c.env)->DeleteLocalRef(c.env, result);
            (*c.env)->DeleteLocalRef(c.env, input);
        }
    }
    release(c); return out;
}
int schemii_android_prepare_dns(uint64_t request) {
    struct call c = borrow(); int ok = 0;
    jmethodID id = method(c, "prepareDNS", "(J)Z");
    if (id) ok = (*c.env)->CallBooleanMethod(c.env, c.bridge, id, (jlong)request) == JNI_TRUE;
    if (c.env && (*c.env)->ExceptionCheck(c.env)) ok = 0;
    release(c); return ok;
}
void schemii_android_cancel_dns(uint64_t request) {
    struct call c = borrow(); jmethodID id = method(c, "cancelDNS", "(J)V");
    if (id) (*c.env)->CallVoidMethod(c.env, c.bridge, id, (jlong)request);
    release(c);
}

#define JNI_NAME(name) Java_development_landmine_schemii_android_network_NetworkBridge_##name
JNIEXPORT jboolean JNICALL JNI_NAME(nativeAttach)(JNIEnv *env, jobject object) {
    pthread_mutex_lock(&owner_lock);
    int ok = !owner_bridge && (*env)->GetJavaVM(env, &owner_vm) == JNI_OK;
    if (ok) { owner_bridge = (*env)->NewGlobalRef(env, object); ok = owner_bridge != NULL; }
    pthread_mutex_unlock(&owner_lock); return ok ? JNI_TRUE : JNI_FALSE;
}
JNIEXPORT void JNICALL JNI_NAME(nativeDetach)(JNIEnv *env, jobject object) {
    pthread_mutex_lock(&owner_lock);
    if (owner_bridge && (*env)->IsSameObject(env, owner_bridge, object)) {
        (*env)->DeleteGlobalRef(env, owner_bridge); owner_bridge = NULL;
    }
    pthread_mutex_unlock(&owner_lock);
}
JNIEXPORT void JNICALL JNI_NAME(nativeChanged)(JNIEnv *env, jobject object) {
    (void)env; (void)object; SchemiiNetworkChanged();
}
static jstring result_string(JNIEnv *env, char *result) {
    if (!result) return (*env)->NewStringUTF(env, "{\"error\":\"native allocation failed\",\"ready\":false}");
    jstring out = (*env)->NewStringUTF(env, result); free(result); return out;
}
JNIEXPORT jstring JNICALL JNI_NAME(nativeProbe)(JNIEnv *env, jobject object) {
    (void)object; return result_string(env, SchemiiNetworkProbe());
}
JNIEXPORT jstring JNICALL JNI_NAME(nativeStart)(JNIEnv *env, jobject object, jstring configuration) {
    (void)object;
    if (!configuration || (*env)->GetStringUTFLength(env, configuration) > 16384)
        return (*env)->NewStringUTF(env, "{\"error\":\"invalid configuration size\",\"ready\":false}");
    const char *bytes = (*env)->GetStringUTFChars(env, configuration, NULL);
    if (!bytes) return NULL;
    char *out = SchemiiNetworkStart((char *)bytes);
    (*env)->ReleaseStringUTFChars(env, configuration, bytes);
    return result_string(env, out);
}
JNIEXPORT jboolean JNICALL JNI_NAME(nativeStop)(JNIEnv *env, jobject object, jint timeout_ms) {
    (void)env; (void)object; return SchemiiNetworkStop(timeout_ms) ? JNI_TRUE : JNI_FALSE;
}
