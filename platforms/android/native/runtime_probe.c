/* Real syscall controls; this executable never represents stack acceptance. */
#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <time.h>
#include <sys/syscall.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

static unsigned long long start_ticks(pid_t pid) {
    char path[80], line[4096];
    snprintf(path, sizeof(path), "/proc/%d/stat", pid);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    if (!fgets(line, sizeof(line), f)) { fclose(f); return 0; }
    fclose(f);
    char *p = strrchr(line, ')');
    if (!p || p[1] != ' ') return 0;
    p += 2;
    char *save = NULL, *word = strtok_r(p, " ", &save);
    for (int field = 0; field < 19 && word; ++field) word = strtok_r(NULL, " ", &save);
    return word ? strtoull(word, NULL, 10) : 0;
}

static volatile sig_atomic_t stopped;
static void stop_probe(int sig) { (void)sig; stopped = 1; }

static int owner_control(void) {
    int channel[2];
    if (pipe(channel)) return 2;
    pid_t child = fork();
    if (child < 0) return 2;
    if (child == 0) {
        close(channel[0]);
        struct sigaction action = {0};
        action.sa_handler = stop_probe;
        sigaction(SIGTERM, &action, NULL);
        char ready = 'r';
        if (write(channel[1], &ready, 1) != 1) _exit(2);
        while (!stopped) {
            struct timespec delay = {0, 10000000};
            nanosleep(&delay, NULL);
        }
        close(channel[1]);
        _exit(0);
    }
    close(channel[1]);
    char ready;
    if (read(channel[0], &ready, 1) != 1) return 2;
    close(channel[0]);
    unsigned long long birth = start_ticks(child);
    char boot[128] = {0};
    FILE *f = fopen("/proc/sys/kernel/random/boot_id", "r");
    int boot_errno = f ? 0 : errno;
    if (f) { if (!fgets(boot, sizeof(boot), f)) boot[0] = 0; fclose(f); }
    // Capture exactly the /proc surface the existing supervisor depends on.
    char path[128], link[4096];
    snprintf(path, sizeof(path), "/proc/%d/exe", child);
    int exe_ok = readlink(path, link, sizeof(link)) > 0;
    snprintf(path, sizeof(path), "/proc/%d/cwd", child);
    int cwd_ok = readlink(path, link, sizeof(link)) > 0;
    snprintf(path, sizeof(path), "/proc/%d/cmdline", child);
    int fd = open(path, O_RDONLY); int cmd_ok = fd >= 0;
    if (fd >= 0) close(fd);
    snprintf(path, sizeof(path), "/proc/%d/task/%d/children", getpid(), getpid());
    fd = open(path, O_RDONLY); int children_ok = fd >= 0;
    int children_errno = fd >= 0 ? 0 : errno;
    if (fd >= 0) close(fd);
    int signalled = birth && start_ticks(child) == birth && kill(child, SIGTERM) == 0;
    int status = 0, reaped = 0;
    for (int n = 0; n < 300; ++n) {
        if (waitpid(child, &status, WNOHANG) == child) { reaped = 1; break; }
        struct timespec delay = {0, 10000000}; nanosleep(&delay, NULL);
    }
    int graceful = signalled && reaped && WIFEXITED(status) && WEXITSTATUS(status) == 0;
    if (!reaped) { kill(child, SIGKILL); waitpid(child, &status, 0); }
    printf("{\"probe\":\"owner-control\",\"birth_readable\":%s,"
           "\"boot_id_readable\":%s,\"boot_id_errno\":%d,\"exe_readable\":%s,"
           "\"cwd_readable\":%s,\"cmdline_readable\":%s,\"children_readable\":%s,"
           "\"children_errno\":%d,\"graceful_sigterm\":%s}\n",
           birth ? "true" : "false", boot[0] ? "true" : "false", boot_errno,
           exe_ok ? "true" : "false", cwd_ok ? "true" : "false", cmd_ok ? "true" : "false",
           children_ok ? "true" : "false", children_errno, graceful ? "true" : "false");
    return graceful && boot[0] && exe_ok && cwd_ok && cmd_ok && children_ok ? 0 : 1;
}

static int filesystem_syscalls(void) {
    int legacy_errno = -1, at_errno = -1, at2_errno = -1;
    int fd = open("rename-source", O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return 2;
    close(fd);
#ifdef __NR_rename
    errno = 0;
    if (syscall(__NR_rename, "rename-source", "rename-target") < 0) legacy_errno = errno;
    else { legacy_errno = 0; rename("rename-target", "rename-source"); }
#endif
#ifdef __NR_renameat
    errno = 0;
    if (syscall(__NR_renameat, AT_FDCWD, "rename-source", AT_FDCWD, "rename-target") < 0) at_errno = errno;
    else { at_errno = 0; rename("rename-target", "rename-source"); }
#endif
#ifdef __NR_renameat2
    errno = 0;
    if (syscall(__NR_renameat2, AT_FDCWD, "rename-source", AT_FDCWD, "rename-target", 0) < 0) at2_errno = errno;
    else at2_errno = 0;
#endif
    unlink("rename-source"); unlink("rename-target");
    printf("{\"probe\":\"filesystem-syscalls\",\"rename_errno\":%d,\"renameat_errno\":%d,\"renameat2_errno\":%d}\n",
           legacy_errno, at_errno, at2_errno);
    return at_errno == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc == 2 && !strcmp(argv[1], "filesystem-syscalls")) return filesystem_syscalls();
    if (argc == 2 && !strcmp(argv[1], "owner-control")) return owner_control();
    if (argc == 2 && !strcmp(argv[1], "identity")) {
#if defined(__aarch64__)
        const char *abi = "arm64-v8a";
#elif defined(__x86_64__)
        const char *abi = "x86_64";
#else
#error Unsupported probe ABI
#endif
        printf("{\"probe\":\"packaged-native\",\"abi\":\"%s\",\"uid\":%u,\"page_size\":%ld}\n",
               abi, (unsigned)getuid(), sysconf(_SC_PAGESIZE));
        return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "writable-exec")) {
        char *args[] = {argv[2], "identity", NULL};
        execv(argv[2], args);
        int saved = errno;
        printf("{\"probe\":\"writable-exec\",\"errno\":%d,\"denied\":%s}\n",
               saved, saved == EACCES ? "true" : "false");
        return saved == EACCES ? 0 : 1;
    }
    fprintf(stderr, "usage: runtime-probe identity | writable-exec COPY\n");
    return 2;
}
