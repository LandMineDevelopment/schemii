/* Finite collector controls only. No services, signal cleanup or ownership adoption. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef COLLECTOR_FIXTURE_SOURCE_ID
#error Collector fixture source identity required
#endif

#if defined(__aarch64__)
#define ABI "arm64-v8a"
#elif defined(__x86_64__)
#define ABI "x86_64"
#else
#error Unsupported fixture ABI
#endif

static void delay_ms(long ms) {
    struct timespec t = {.tv_sec=ms/1000, .tv_nsec=(ms%1000)*1000000};
    while (nanosleep(&t, &t) && errno == EINTR) { }
}

static unsigned long long birth(pid_t pid) {
    char name[80], data[4096];
    snprintf(name, sizeof(name), "/proc/%ld/stat", (long)pid);
    FILE *f = fopen(name, "r");
    if (!f) return 0;
    if (!fgets(data, sizeof(data), f)) { fclose(f); return 0; }
    fclose(f);
    char *p = strrchr(data, ')');
    if (!p) return 0;
    char *save = NULL, *token = strtok_r(p + 2, " ", &save);
    for (int field=3; token && field<22; ++field) token = strtok_r(NULL, " ", &save);
    return token ? strtoull(token, NULL, 10) : 0;
}

static int marker(const char *name, const char *text) {
    int fd = open(name, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
    if (fd < 0) return -1;
    size_t n = strlen(text);
    int ok = write(fd, text, n) == (ssize_t)n && fsync(fd) == 0;
    if (close(fd)) ok = 0;
    return ok ? 0 : -1;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "cwd") && argc == 2) {
        DIR *d = opendir(".");
        if (!d) return 3;
        int count = 0; struct dirent *entry;
        while ((entry = readdir(d))) if (strcmp(entry->d_name, ".") && strcmp(entry->d_name, "..")) ++count;
        closedir(d);
        char cwd[PATH_MAX];
        if (!getcwd(cwd, sizeof(cwd)) || count || marker("fixture-created", "fresh\n")) return 3;
        printf("{\"fixture\":\"cwd\",\"source_id\":\"%s\",\"abi\":\"%s\",\"cwd\":\"%s\",\"empty_before_launch\":true,\"pid\":%ld,\"start_ticks\":%llu}\n", COLLECTOR_FIXTURE_SOURCE_ID, ABI, cwd, (long)getpid(), birth(getpid()));
        fflush(stdout); delay_ms(100); return 0;
    }
    if (!strcmp(argv[1], "bytes") && argc == 3) {
        char *end = NULL; long n = strtol(argv[2], &end, 10);
        const char *prefix = "{\"fixture\":\"bytes\",\"padding\":\"", *suffix = "\"}\n";
        if (!end || *end || n < 100 || n > 65537) return 2;
        fputs(prefix, stdout);
        for (long i=(long)(strlen(prefix)+strlen(suffix)); i<n; ++i) putchar('a');
        fputs(suffix, stdout); fflush(stdout); delay_ms(100); return ferror(stdout) ? 3 : 0;
    }
    if (!strcmp(argv[1], "partial") && argc == 2) { fputs("{\"unfinished\":true", stdout); return 0; }
    if (!strcmp(argv[1], "trailing") && argc == 2) { fputs("{}{}\n", stdout); return 0; }
    if (!strcmp(argv[1], "family") && argc == 2) {
        /* Exactly one finite child; parent uses specific waitpid, never a sweep. */
        unsigned long long owner_birth = birth(getpid());
        if (!owner_birth) return 3;
        pid_t child = fork();
        if (child < 0) return 3;
        if (!child) {
            delay_ms(1500);
            char evidence[192];
            snprintf(evidence, sizeof(evidence), "{\"pid\":%ld,\"start_ticks\":%llu,\"finite_child_done\":true}\n", (long)getpid(), birth(getpid()));
            _exit(marker("child.done", evidence) ? 3 : 0);
        }
        unsigned long long child_birth = birth(child);
        printf("{\"fixture\":\"family\",\"source_id\":\"%s\",\"owner_pid\":%ld,\"owner_start_ticks\":%llu,\"child_pid\":%ld,\"child_start_ticks\":%llu,", COLLECTOR_FIXTURE_SOURCE_ID, (long)getpid(), owner_birth, (long)child, child_birth);
        fflush(stdout); /* Intentionally incomplete while child remains alive. */
        int status = 0; pid_t waited;
        do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
        int ok = child_birth && waited == child && WIFEXITED(status) && WEXITSTATUS(status) == 0;
        printf("\"specific_child_reaped\":%s,\"child_status\":%d,\"owned_children\":1}\n", ok ? "true" : "false", status);
        fflush(stdout);
        return ok ? 0 : 3;
    }
    return 2;
}
