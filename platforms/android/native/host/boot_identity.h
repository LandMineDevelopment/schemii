#ifndef SCHEMII_BOOT_IDENTITY_H
#define SCHEMII_BOOT_IDENTITY_H

#include <stddef.h>
#include <stdint.h>

#define BOOT_IDENTITY_BYTES 16
#define BOOT_IDENTITY_TEXT_MAX 37
#define BOOT_IDENTITY_READ_MAX 38
#define BOOT_IDENTITY_PATH_NODES 5

enum boot_identity_knownness { BOOT_ID_UNKNOWN, BOOT_ID_KNOWN };
enum boot_identity_status {
  BOOT_ID_OK,
  BOOT_ID_OPEN_FAILED,
  BOOT_ID_ORIGIN_FAILED,
  BOOT_ID_ORIGIN_UNKNOWN,
  BOOT_ID_ORIGIN_REJECTED,
  BOOT_ID_READ_FAILED,
  BOOT_ID_EMPTY,
  BOOT_ID_SHORT,
  BOOT_ID_MALFORMED,
  BOOT_ID_OVERSIZED,
  BOOT_ID_CLOSE_FAILED,
  BOOT_ID_INCONSISTENT
};
enum boot_identity_stage {
  BOOT_ID_STAGE_NONE,
  BOOT_ID_STAGE_OPEN,
  BOOT_ID_STAGE_FSTATFS,
  BOOT_ID_STAGE_FSTAT,
  BOOT_ID_STAGE_STATX,
  BOOT_ID_STAGE_READ,
  BOOT_ID_STAGE_PARSE,
  BOOT_ID_STAGE_CLOSE,
  BOOT_ID_STAGE_COMPARE
};
enum boot_identity_origin {
  BOOT_ID_PROC_UNKNOWN,
  BOOT_ID_PROC_VERIFIED,
  BOOT_ID_PROC_REJECTED
};
enum boot_identity_method { BOOT_ID_NATIVE_FIXED_PROC = 1 };
enum boot_identity_context { BOOT_ID_CALLER_MOUNT_NAMESPACE = 1 };

struct boot_identity_sample {
  enum boot_identity_origin origin;
  /* Counts retain partial verification; verified requires all five nodes. */
  unsigned opened_nodes, procfs_checks, type_checks, mount_checks;
  unsigned read_calls;
  size_t bytes_read;
  uint64_t root_mount_id, leaf_mount_id;
};

struct boot_identity_result {
  enum boot_identity_knownness knownness;
  enum boot_identity_status status;
  enum boot_identity_stage stage;
  enum boot_identity_method method;
  enum boot_identity_context context;
  /* This primitive cannot attest that its caller is outside a tracer/guest. */
  int untraced_owner_verified;
  /* Exact first syscall errno, or zero for a non-syscall refusal. No retries. */
  int error;
  int cleanup_error; /* First additional close error, never hides first failure. */
  unsigned failed_sample; /* One-based; zero on success. */
  unsigned failed_node;   /* One-based fixed-path component; zero for compare. */
  struct boot_identity_sample samples[2];
  /* Private control data, valid ONLY when knownness == BOOT_ID_KNOWN.
   * Never print, log, or include these bytes in support evidence. */
  unsigned char uuid[BOOT_IDENTITY_BYTES];
};

/* Strict kernel form: lowercase canonical RFC 4122 version-4 UUID, optionally
 * followed by one LF, no other bytes. Output is zeroed on every refusal.
 * This parses syntax only: a parsed UUID has no origin or boot authority. */
enum boot_identity_status boot_identity_parse(
    const unsigned char *text, size_t length,
    unsigned char uuid[BOOT_IDENTITY_BYTES]);

/* Observation only, no path/FD/environment override or fallback source.
 * Two independent no-symlink walks of /proc/sys/kernel/random/boot_id verify
 * procfs, expected node types, and one statx mount ID throughout EACH walk;
 * the root mount ID must also agree across walks. Missing statx mount-ID
 * support is unknown, not permission to relax origin checks.
 * Each sample consumes at most 38 bytes / 38 read calls; EINTR is a failure.
 * Work is finite; no wall-time guarantee against a stalled kernel syscall.
 *
 * KNOWN means a consistent observation in the caller's proc mount context,
 * under stock kernel/syscall trust. Invoke from the untraced native owner,
 * never a PRoot guest. This API does not establish that caller precondition,
 * Android app-domain capability, remote attestation, prior-anchor validity,
 * new boot, process absence, restart safety, recovery, or readiness.
 * Android target-36 nondebug app-domain and actual new-boot proof are pending.
 */
struct boot_identity_result boot_identity_read(void);

#endif
