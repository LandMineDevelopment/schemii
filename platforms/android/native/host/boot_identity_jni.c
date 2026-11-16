#define _GNU_SOURCE
#include "boot_identity.h"
#include <jni.h>
#include <stdio.h>
#include <string.h>
#include <sys/utsname.h>
#include <unistd.h>

#ifndef BOOT_IDENTITY_SOURCE_SHA256
#error Immutable component source identity required
#endif

/* Only scalar allowlisted facts cross JNI. The UUID never leaves this frame. */
JNIEXPORT jstring JNICALL
Java_development_landmine_schemii_runtimeprobe_BootIdentityProbe_observe(
    JNIEnv *env, jclass klass) {
  (void)klass;
  struct boot_identity_result r = boot_identity_read();
  struct utsname kernel;
  char samples[2][384], report[2048];
  if (uname(&kernel) != 0)
    strcpy(kernel.release, "unknown");
  for (size_t i = 0; kernel.release[i]; ++i) {
    char c = kernel.release[i];
    if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
          (c >= '0' && c <= '9') || c == '.' || c == '+' || c == '_' ||
          c == '-'))
      kernel.release[i] = '_';
  }
  for (unsigned i = 0; i < 2; ++i) {
    const struct boot_identity_sample *s = &r.samples[i];
    int n =
        snprintf(samples[i], sizeof(samples[i]),
                 "{\"origin\":%d,\"opened_nodes\":%u,\"procfs_checks\":%u,"
                 "\"type_checks\":%u,\"mount_checks\":%u,\"read_calls\":%u,"
                 "\"bytes_read\":%zu}",
                 s->origin, s->opened_nodes, s->procfs_checks, s->type_checks,
                 s->mount_checks, s->read_calls, s->bytes_read);
    if (n < 0 || (size_t)n >= sizeof(samples[i]))
      return NULL;
  }
  int n = snprintf(
      report, sizeof(report),
      "{\"source_sha256\":\"%s\",\"pid\":%ld,\"uid\":%ld,\"pages\":%ld,"
      "\"kernel\":\"%s\",\"knownness\":%d,\"status\":%d,\"stage\":%d,"
      "\"method\":%d,\"context\":%d,\"untraced_owner_verified\":false,"
      "\"errno\":%d,\"cleanup_errno\":%d,\"failed_sample\":%u,"
      "\"failed_node\":%u,\"samples\":[%s,%s]}",
      BOOT_IDENTITY_SOURCE_SHA256, (long)getpid(), (long)getuid(),
      sysconf(_SC_PAGESIZE), kernel.release, r.knownness, r.status, r.stage,
      r.method, r.context, r.error, r.cleanup_error, r.failed_sample,
      r.failed_node, samples[0], samples[1]);
  /* Volatile erasure survives optimization; no raw-result array or formatter.
   */
  volatile unsigned char *private_bytes = (volatile unsigned char *)&r;
  for (size_t i = 0; i < sizeof(r); ++i)
    private_bytes[i] = 0;
  if (n < 0 || (size_t)n >= sizeof(report))
    return NULL;
  return (*env)->NewStringUTF(env, report);
}
