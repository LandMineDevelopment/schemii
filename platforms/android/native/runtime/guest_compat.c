/* Android x86_64 diagnostic glibc adapter: POSIX rename via permitted renameat.
 * Android app seccomp traps the legacy x86_64 rename syscall. Bionic uses
 * renameat; glibc still uses rename. Preserve the exact atomic operation and
 * errno instead of changing application/report/PostgreSQL behavior.
 * ARM64 has no legacy rename syscall and does not load this diagnostic shim.
 */
#define _GNU_SOURCE
#include <fcntl.h>
#include <unistd.h>
#include <sys/syscall.h>

int rename(const char *old_path, const char *new_path) {
    return (int)syscall(SYS_renameat, AT_FDCWD, old_path, AT_FDCWD, new_path);
}
