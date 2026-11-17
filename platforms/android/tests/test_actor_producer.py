"""Finite controls on complete patched production units and supporting snippets.

Requires a reviewed read-only donor via SCHEMII_ACTOR_DONOR. Host controls use
only bounded native fixtures; no packaged guest, Android binary, app or deployment
runs. Kernel transport substitutions and observer-only waits are explicit.
"""

from __future__ import annotations
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOST = ROOT / "platforms/android/native/host"


@pytest.fixture(scope="module")
def patched(tmp_path_factory):
    configured = os.environ.get("SCHEMII_ACTOR_DONOR")
    if not configured:
        pytest.skip("explicit pinned read-only source donor required")
    donor = Path(configured)
    out = tmp_path_factory.mktemp("patched-proot")
    lock = json.loads(
        (ROOT / "platforms/android/runtime/payload.lock.json").read_text()
    )["actor_producer"]
    archive = subprocess.check_output(
        ["git", "-C", str(donor), "archive", lock["proot_commit"], "src"]
    )
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(out, filter="data")
    spec = importlib.util.spec_from_file_location(
        "apk_build", ROOT / "platforms/android/build.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.prepare_actor_source(donor.parent, out / "src")
    # Test evidence binds the actual consumed patched leaves and compiler.
    (out / "fingerprints.json").write_text(
        json.dumps(
            {
                "compiler": subprocess.check_output(["cc", "--version"], text=True),
                "patch": hashlib.sha256(
                    (
                        ROOT / "platforms/android/native/proot-actor-channel.patch"
                    ).read_bytes()
                ).hexdigest(),
                "patched": {
                    n: hashlib.sha256((out / "src" / n).read_bytes()).hexdigest()
                    for n in lock["donor_inputs"]
                },
            },
            indent=2,
        )
    )
    return out / "src"


def function(text, start):
    begin = text.index(start)
    brace = text.index("{", begin)
    depth = 1
    end = brace + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[begin:end]


def compile_control(tmp_path, source, extra=()):
    path = tmp_path / "fixture.c"
    path.write_text(source)
    output = tmp_path / "fixture"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-D_GNU_SOURCE",
            "-DSCHEMII_MANAGED_ACTORS",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wno-unused-function",
            "-I",
            str(HOST),
            str(path),
            str(HOST / "actor_channel.c"),
            *extra,
            "-o",
            str(output),
        ],
        check=True,
    )
    subprocess.run([str(output)], check=True, timeout=10)


def test_native_recipe_actor_link_inputs(patched, tmp_path, monkeypatch):
    """Parse the actual prepared pinned Makefile; never compile or run a guest."""
    spec = importlib.util.spec_from_file_location(
        "actor_recipe_build", ROOT / "platforms/android/build.py"
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    original_run = builder.run
    observed = []

    def inspect_run(*args, **kwargs):
        if str(args[0]) == "patch":
            return original_run(*args, **kwargs)
        if str(args[0]) != "make":
            return None  # NDK compiler and archiver are outside this graph control.
        directory = kwargs["cwd"]
        with (directory / "GNUmakefile").open("a") as output:
            output.write("\n.PHONY: actor_graph_query\nactor_graph_query: ;\n")
        result = subprocess.run(
            [
                "make",
                "-qp",
                "-rR",
                "actor_graph_query",
                "CC=true",
                "LD=true",
                "GIT=true",
                "PROOT_UNBUNDLE_LOADER=/nonexistent",
            ],
            cwd=directory,
            text=True,
            capture_output=True,
            timeout=5,
        )
        assert result.returncode == 0, result.stderr
        target = [
            line for line in result.stdout.splitlines() if line.startswith("proot:")
        ]
        assert len(target) == 1
        assert target[0].split().count("actor_channel.o") == 1
        # GNU Make expands the real recipe's $^ from these target prerequisites.
        makefile = (directory / "GNUmakefile").read_text()
        assert "$(LINK)" in makefile and "$^" in makefile
        observed.append(target[0])
        (directory / "proot").write_bytes(b"graph-control-only")
        (directory / "loader/loader").write_bytes(b"graph-control-only")
        return None

    monkeypatch.setattr(builder, "run", inspect_run)
    donor = Path(os.environ["SCHEMII_ACTOR_DONOR"])
    builder.native(donor.parent, tmp_path / "native-out", "x86_64")
    assert len(observed) == 1


def test_actual_clone_entry_cases(patched, tmp_path):
    text = (patched / "syscall/enter.c").read_text()
    cases = text[
        text.index("\tcase PR_clone: {") : text.index(
            "\n\t/* mount(2) and pivot_root", text.index("\tcase PR_clone: {")
        )
    ]
    # The production cases run against actual mutable shared memory, including
    # an inaccessible pointer. clone3 must not touch any of it or reach resume.
    compile_control(
        tmp_path,
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <errno.h>
#include <sched.h>
#include <stdint.h>
#include <signal.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <stdatomic.h>
#include <stdbool.h>
typedef uint64_t word_t;
typedef struct { int pid, clone_stripped_newns, clone_stripped_newnet; word_t flags; } Tracee;
enum {CURRENT, SYSARG_1, PR_clone, PR_clone3, PR_fork, PR_vfork};
#define CLONE_NS_MASK (CLONE_NEWNS|CLONE_NEWNET)
static word_t peek_reg(Tracee *t,int a,int b) {(void)a;(void)b;return t->flags;}
static void poke_reg(Tracee *t,int a,word_t b) {(void)a;t->flags=b;}
static word_t peek_word(Tracee *t,word_t a) {(void)t;(void)a;assert(0);return 0;}
static void poke_word(Tracee *t,word_t a,word_t b) {(void)t;(void)a;(void)b;assert(0);}
static void force_fork_sysexit(Tracee *t) {(void)t;}
static int entry(Tracee *tracee,int call) {int status=-123; switch(call) {
"""
        + cases
        + r"""
} return status;}
int main(void) {
 Tracee t={0}; t.flags=CLONE_UNTRACED|SIGCHLD; assert(entry(&t,PR_clone)==-EPERM);
 t.flags=1; assert(entry(&t,PR_clone3)==-ENOSYS);
 _Atomic uint64_t *shared=mmap(NULL,4096,PROT_READ|PROT_WRITE,MAP_ANONYMOUS|MAP_SHARED,-1,0);
 assert(shared!=MAP_FAILED); pid_t child=fork(); assert(child>=0);
 if(!child) { for(int i=0;i<100000;i++) atomic_store(shared,i&1?CLONE_UNTRACED:0); _exit(0); }
 t.flags=(uintptr_t)shared;
 for(int i=0;i<1000;i++) assert(entry(&t,PR_clone3)==-ENOSYS);
 int status; assert(waitpid(child,&status,0)==child && WIFEXITED(status));
 t.flags=0; assert(entry(&t,PR_clone)==-ENOTCONN); assert(entry(&t,PR_fork)==-ENOTCONN);
 munmap((void *)shared,4096); return 0;
}
""",
    )


def test_actual_root_failure_and_noexec_fd_path(patched, tmp_path):
    body = function((patched / "tracee/event.c").read_text(), "int launch_process(")
    compile_control(
        tmp_path,
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
typedef struct {int verbose,pid; char *exe;} Tracee;
#define ERROR 0
#define SYSTEM 0
static void note(Tracee *t,int a,int b,char *c) {(void)t;(void)a;(void)b;(void)c;}
static void mem_prepare_before_first_execve(Tracee *t) {(void)t;}
static void list_open_fd(Tracee *t) {(void)t;}
static int enable_syscall_filtering(Tracee *t) {(void)t;return 0;}
static long fail_traceme(int request,int pid,void *a,void *b) {(void)request;(void)pid;(void)a;(void)b;errno=EPERM;return -1;}
#define ptrace fail_traceme
"""
        + body
        + r"""
int main(void) {
 Tracee t={0,0,"/no-such-fixture-executable"}; char *args[]={"fixture",NULL};
 assert(launch_process(&t,args)==-ENOTCONN);
 struct ac_binding b={{1},{2},{3},1,42,1}; struct ac_channel c; int pair[2];
 assert(!ac_pair(pair)); assert(!ac_init(&c,pair[0],&b)); ac_proot_bind(&c);
 int parent=getpid(); int result=launch_process(&t,args);
 if(getpid()!=parent) _exit(result==-EPERM?0:1);
 assert(result==0 && t.pid>0); int status;
 assert(waitpid(t.pid,&status,0)==t.pid); assert(WIFEXITED(status)&&!WEXITSTATUS(status));
 ac_observe(t.pid,status); return 0;
}
""",
    )


def test_managed_helper_is_direct_and_wait_dispatch_precedes_tracee(patched):
    text = (patched / "extension/sysvipc/sysvipc_shm.c").read_text()
    branch = text.split("#ifdef SCHEMII_MANAGED_ACTORS")[-1].split("#else")[0]
    assert "ac_child_fds(keep, 5)" in branch
    assert "sysvipc_shm_helper_main();" in branch
    assert "fork(" not in branch and "execl(" not in branch
    events = (patched / "tracee/event.c").read_text()
    assert events.index("ac_helper_wait(pid, tracee_status)") < events.index(
        "get_tracee(NULL, pid, true)"
    )
    mem = (patched / "tracee/mem.c").read_text()
    assert "ac_target_wait(tracee->pid, &wstatus, 0, 0) != tracee->pid" in mem


def test_actual_sysvipc_service_and_exact_parent_wait(patched, tmp_path):
    text = (patched / "extension/sysvipc/sysvipc_shm.c").read_text()
    definitions = text[
        text.index("enum SysVIpcShmHelperRequestOp") : text.index(
            "static void sysvipc_shm_send_helper_request"
        )
    ]
    request = function(text, "static void sysvipc_shm_send_helper_request(")
    allocate = function(text, "static int sysvipc_shm_do_allocate(")
    service = function(text, "void sysvipc_shm_helper_main()")
    preamble = r"""
#include "actor_channel.h"
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
typedef uintptr_t word_t;
#define TALLOC_FREE(p) free(p)
static char *create_temp_name(void *p,const char *suffix) {
 (void)p;(void)suffix; return strdup("./shm-XXXXXX");
}
void sysvipc_shm_helper_main(void);
""".replace("FIXTURE_DIRECTORY", str(tmp_path))
    compile_control(
        tmp_path,
        preamble
        + definitions
        + request
        + allocate
        + service
        + r"""
int main(void) {
 struct ac_binding b={{1},{2},{3},1,42,1}; struct ac_channel c; int pair[2];
 assert(!ac_pair(pair)); assert(!ac_init(&c,pair[0],&b)); ac_proot_bind(&c);
 assert(!chdir("FIXTURE_DIRECTORY"));
 struct SysVIpcShmHelperRequest request={SHMHELPER_ALLOC,17,4096,0}; int response=-1;
 sysvipc_shm_send_helper_request(&request,&response,sizeof(response));
 pid_t helper=0;
 for(unsigned i=0;i<AC_CAPACITY;i++) if(c.slots[i].kind==AC_SYSVIPC && c.slots[i].live) helper=c.slots[i].pid;
 assert(helper>0); int status;
 if(response<0) { assert(ac_target_wait(helper,&status,0,1)==helper); assert(response>=0); }
 assert(waitpid(helper,&status,WNOHANG)==0); /* live service, still our child */
 request.op=SHMHELPER_FREE; request.fd=response;
 sysvipc_shm_send_helper_request(&request,NULL,0);
 /* A separate direct child is not routed through the helper table. */
 pid_t other=fork(); assert(other>=0); if(!other) _exit(23);
 assert(waitpid(other,&status,0)==other && WEXITSTATUS(status)==23);
 assert(!ac_helper_wait(other,status));
 int keep[]={0,1,2,pair[0],pair[1]}, secondary=0;
 assert(!ac_close_fds(keep,5,&secondary)); /* close service pipes: EOF, not wait */
 assert(ac_highwater(&c)==EBUSY);
 assert(ac_target_wait(helper,&status,0,1)==helper);
 assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
 assert(ac_highwater(&c)==EBUSY); /* receipts pending */
 return 0;
}
""".replace("FIXTURE_DIRECTORY", str(tmp_path)),
    )


def test_actual_f2fs_noexec_child_and_targeted_wait(patched, tmp_path):
    body = function(
        (patched / "path/f2fs-bug.c").read_text(), "static bool probe_f2fs_bug("
    )
    compile_control(
        tmp_path,
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>
typedef struct { int unused; } Tracee;
#define WARNING 0
#define SYSTEM 0
#define VERBOSE(...) ((void)0)
static void note(const Tracee *t,int a,int b,const char *fmt,...) {(void)t;(void)a;(void)b;(void)fmt;}
static const char *get_temp_directory(void) {return "FIXTURE_DIRECTORY";}
""".replace("FIXTURE_DIRECTORY", str(tmp_path))
        + body
        + r"""
int main(void) {
 struct ac_binding b={{1},{2},{3},1,42,1}; struct ac_channel c; int pair[2];
 assert(!ac_pair(pair)); assert(!ac_init(&c,pair[0],&b)); ac_proot_bind(&c);
 Tracee tracee={0}; assert(!probe_f2fs_bug(&tracee));
 int found=0;
 for(unsigned i=0;i<AC_CAPACITY;i++) if(c.slots[i].kind==AC_F2FS) {
  found++; assert(!c.slots[i].live && !c.slots[i].wait_pending && !c.slots[i].pending);
 }
 assert(found==1); assert(ac_highwater(&c)==EBUSY); return 0;
}
""",
    )


def test_actual_resume_on_directly_owned_tracee(patched, tmp_path):
    body = function((patched / "tracee/event.c").read_text(), "bool restart_tracee(")
    compile_control(
        tmp_path,
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <signal.h>
#include <stdint.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
typedef struct { struct { int wait_pid; } as_ptracer;
 int restart_how,pid,last_restart_how,running,terminated; } Tracee;
"""
        + body
        + r"""
int main(void) {
 struct ac_binding b={{1},{2},{3},1,42,1}; struct ac_channel c; int pair[2];
 assert(!ac_pair(pair)); assert(!ac_init(&c,pair[0],&b)); ac_proot_bind(&c);
 pid_t child=ac_native_fork(AC_ROOT); assert(child>=0);
 if(!child) {
  const int keep[]={0,1,2}; if(ac_child_fds(keep,3)) _exit(125);
  if(ptrace(PTRACE_TRACEME,0,NULL,NULL)<0) _exit(126);
  raise(SIGSTOP); _exit(0);
 }
 int status; assert(ac_target_wait(child,&status,0,0)==child && WIFSTOPPED(status));
 Tracee t={{0},PTRACE_CONT,child,0,0,0}; assert(restart_tracee(&t,0));
 assert(t.running && t.last_restart_how==PTRACE_CONT && !t.restart_how);
 assert(ac_target_wait(child,&status,0,0)==child && WIFEXITED(status));
 t.terminated=1;assert(!restart_tracee(&t,0) && !c.lost);
 assert(ac_highwater(&c)==EBUSY); return 0;
}
""",
    )


def test_actual_arm64_raw_resume_wait_block_on_owned_host_tracee(patched, tmp_path):
    text = (patched / "tracee/mem.c").read_text()
    start = text.rfind(
        "#ifdef SCHEMII_MANAGED_ACTORS", 0, text.index("if (ac_resume(PTRACE_CONT")
    )
    block = text[start:].split("\n", 1)[1].split("#else", 1)[0]
    assert "ac_resume(PTRACE_CONT" in block and "ac_target_wait" in block
    compile_control(
        tmp_path,
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
typedef struct { int pid; } Tracee;
static int raw(Tracee *tracee) {int wstatus=0;
"""
        + block
        + r"""
return wstatus;managed_raw_restore:return -1;}
int main(void) {
 struct ac_binding b={{1},{2},{3},1,42,1}; struct ac_channel c; int pair[2];
 assert(!ac_pair(pair)); assert(!ac_init(&c,pair[0],&b)); ac_proot_bind(&c);
 pid_t child=ac_native_fork(AC_ROOT); assert(child>=0);
 if(!child) {
  const int keep[]={0,1,2}; if(ac_child_fds(keep,3)) _exit(125);
  if(ptrace(PTRACE_TRACEME,0,NULL,NULL)<0) _exit(126);
  raise(SIGSTOP); _exit(0);
 }
 int status; assert(ac_target_wait(child,&status,0,0)==child && WIFSTOPPED(status));
 Tracee t={child}; status=raw(&t); assert(WIFEXITED(status));
 /* No external PID is resumed: unknown own-boundary identity refuses first. */
 t.pid=getpid(); assert(raw(&t)==-1 && c.lost && c.error==ESRCH);
 return 0;
}
""",
    )


def test_actual_actor_parity_rejects_changed_source(tmp_path):
    import shutil

    from scripts.check_runtime_parity import ParityError, check_actor_producer

    lock = json.loads(
        (ROOT / "platforms/android/runtime/payload.lock.json").read_text()
    )
    names = list(lock["actor_producer"]["inputs"]) + [
        "platforms/android/runtime/payload.lock.json",
        "platforms/android/toolchain.lock.json",
        "platforms/android/runtime-interface.json",
    ]
    for name in names:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    check_actor_producer(tmp_path)
    path = tmp_path / "platforms/android/native/host/actor_channel.c"
    path.write_bytes(path.read_bytes() + b"\n/* changed */\n")
    with pytest.raises(ParityError, match="actor source drift"):
        check_actor_producer(tmp_path)


@pytest.fixture(scope="module")
def integrated_control(patched):
    """Whole production translation units, linked together; no snippet substitutes."""
    import re
    import shutil

    donor = Path(os.environ["SCHEMII_ACTOR_DONOR"])
    talloc = donor.parent / "talloc-2.5.0"
    out = patched.parent / "integrated"
    out.mkdir()
    # Host glibc already declares memset_explicit; use its implementation.
    # Target build retains the unchanged bionic portability header.
    replace_header = (ROOT / "platforms/android/native/replace.h").read_text()
    (out / "replace.h").write_text(
        replace_header.split("/* C23 memset_explicit")[0] + "\n#include <limits.h>\n"
    )
    subprocess.run(
        [
            "cc",
            "-c",
            "-O1",
            "-D_GNU_SOURCE",
            "-I" + str(out),
            "-I" + str(talloc),
            str(talloc / "talloc.c"),
            "-o",
            str(out / "talloc.o"),
        ],
        check=True,
    )
    subprocess.run(
        ["ar", "rcs", str(out / "libtalloc.a"), str(out / "talloc.o")], check=True
    )
    recipe = patched / "GNUmakefile"
    recipe.write_text("OBJECTS += actor_channel.o\n" + recipe.read_text())
    subprocess.run(
        [
            "patch",
            "--batch",
            "--fuzz=0",
            "-p1",
            "-i",
            str(ROOT / "platforms/android/native/proot-string-header.patch"),
        ],
        cwd=patched,
        check=True,
    )
    args = [
        "make",
        "-j4",
        "V=1",
        "HAS_LOADER_32BIT=",
        "PROOT_UNBUNDLE_LOADER=/nonexistent",
        "CPPFLAGS=-D_GNU_SOURCE -D_FILE_OFFSET_BITS=64 -DSCHEMII_MANAGED_ACTORS -DPROOT_UNBUNDLE_LOADER='\"/nonexistent\"' -I. -I"
        + str(talloc),
        "LDFLAGS=-L" + str(out) + " -ltalloc -Wl,-z,noexecstack",
        "CFLAGS=-O1 -g -ffunction-sections -fdata-sections",
    ]
    database = subprocess.check_output(args + ["-pn"], cwd=patched, text=True)
    objects = re.search(r"^OBJECTS = (.+)$", database, re.M)
    assert objects
    names = objects[1].split()
    assert names.count("actor_channel.o") == 1
    subprocess.run(args + names, cwd=patched, check=True)
    cli = out / "cli-renamed.o"
    shutil.copyfile(patched / "cli/cli.o", cli)
    subprocess.run(
        ["objcopy", "--redefine-sym", "main=schemii_proot_main", str(cli)], check=True
    )
    helper_object = out / "sysvipc-exposed.o"
    shutil.copyfile(patched / "extension/sysvipc/sysvipc_shm.o", helper_object)
    subprocess.run(
        [
            "objcopy",
            "--globalize-symbol",
            "sysvipc_shm_send_helper_request",
            "--globalize-symbol",
            "sysvipc_shm_helper_addr",
            str(helper_object),
        ],
        check=True,
    )
    text = (patched / "extension/sysvipc/sysvipc_shm.c").read_text()
    (out / "fixture-sysvipc.h").write_text(
        text[
            text.index("enum SysVIpcShmHelperRequestOp") : text.index(
                "static struct sockaddr_un sysvipc_shm_helper_addr;"
            )
        ]
        + "\nvoid sysvipc_shm_send_helper_request(struct SysVIpcShmHelperRequest *, void *, size_t);\n"
    )
    output = out / "control"
    subprocess.run(
        [
            "cc",
            "-D_GNU_SOURCE",
            "-DAC_INTEGRATED",
            "-DSCHEMII_MANAGED_ACTORS",
            "-g",
            "-O1",
            "-I" + str(patched),
            "-I" + str(out),
            "-I" + str(talloc),
            "-I" + str(HOST),
            str(ROOT / "platforms/android/native/tests/actor_control.c"),
            *[
                str(
                    cli
                    if n == "cli/cli.o"
                    else helper_object
                    if n == "extension/sysvipc/sysvipc_shm.o"
                    else patched / n
                )
                for n in names
            ],
            str(out / "libtalloc.a"),
            "-Wl,--wrap=ptrace",
            "-Wl,--wrap=waitpid",
            "-Wl,--wrap=execvp",
            "-Wl,--wrap=fork",
            "-Wl,--wrap=kill",
            "-Wl,--wrap=translate_syscall_enter",
            "-Wl,--wrap=translate_syscall_exit",
            "-o",
            str(output),
        ],
        check=True,
    )
    (out / "objects.json").write_text(json.dumps(names))
    return output


@pytest.mark.parametrize(
    "mode",
    [
        "kernel-fork",
        "kernel-vfork",
        "kernel-clone",
        "kernel-thread",
        "kernel-untraced",
        "kernel-clone3",
        "kernel-rejected-clone",
        "root-exec-failure",
        "root-stop-failure",
        "root-options-failure",
        "helper-first",
        "helper-last",
        "script-stop-first",
        "script-death-first",
        "script-nonleader-exec",
        "script-exec-collision",
        "guest-capacity",
        "guest-incarnation",
        "guest-competition",
        "guest-rejected",
        "guest-reordered",
        "guest-reservation-loss",
        "tracer-live-helper-loss",
        "root-fork-failure",
        "root-traceme-failure",
        "loop-wait-EINTR",
        "loop-wait-ECHILD",
        "loop-wait-EIO",
        "loop-wait-invalid",
        "kernel-nonleader-exec",
        "f2fs-full",
        "f2fs-fork-failure",
        "helper-fork-failure",
        "helper-startup-failure",
    ],
)
def test_integrated_production_paths(integrated_control, mode):
    subprocess.run(
        [str(integrated_control), mode],
        cwd=integrated_control.parent,
        check=True,
        timeout=20,
    )


@pytest.mark.parametrize("fault", range(14))
def test_complete_raw_workaround_injected_boundaries(patched, tmp_path, fault):
    body = function(
        (patched / "tracee/mem.c").read_text(),
        "static int ptrace_pokedata_or_via_stub(",
    )
    source = (
        r"""
#include "actor_channel.h"
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <signal.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <unistd.h>
#define HAS_POKEDATA_WORKAROUND 1
#define VERBOSE(...) ((void)0)
#define CURRENT 0
#define INSTR_POINTER 1
#define SYSARG_2 2
#define SYSARG_3 3
#define PR_void 0
#define ERROR 0
#define INFO 1
#define INTERNAL 0
typedef uintptr_t word_t;
struct user_regs_struct {word_t value;};
typedef struct {struct user_regs_struct _regs[1];int pid,verbose;bool restore_original_regs,is_aarch32,_regs_were_changed,pokedata_workaround_cancelled_syscall;word_t pokedata_workaround_stub_addr;} Tracee;
static int fault=RAW_CASE, mask_sets, resumes, waits, redeliver;
static void note(Tracee *t,int a,int b,const char *fmt,...) {(void)t;(void)a;(void)b;(void)fmt;}
static void poke_reg(Tracee *t,int reg,word_t value) {(void)reg;t->_regs[0].value=value;}
static void set_sysnum(Tracee *t,int n) {(void)t;(void)n;}
static int push_specific_regs(Tracee *t,bool all) {(void)t;(void)all;if(fault==4 || fault==12) {errno=EIO;return -1;}return 0;}
static void print_current_regs(Tracee *t,int n,const char *s) {(void)t;(void)n;(void)s;}
static long injected_ptrace(int request,int pid,uintptr_t address,void *data) {
 (void)pid;(void)address;
 if(request==PTRACE_POKEDATA) {errno=EIO;return -1;}
 if(request==PTRACE_GETSIGMASK) {if(fault==1) {errno=EACCES;return -1;}sigemptyset(data);return 0;}
 if(request==PTRACE_SETSIGMASK) {mask_sets++;if((fault==2 && mask_sets==1)||((fault==3 || fault==12) && mask_sets==2)) {errno=EPERM;return -1;}return 0;}
 if(request==PTRACE_CONT) {resumes++;if(fault==5) {errno=EIO;return -1;}return 0;}
 return 0;
}
long __wrap_ptrace(enum __ptrace_request request,...) {
 va_list ap;va_start(ap,request);int pid=va_arg(ap,int);void *a=va_arg(ap,void *),*b=va_arg(ap,void *);va_end(ap);
 return injected_ptrace(request,pid,(uintptr_t)a,b);
}
pid_t __real_waitpid(pid_t,int *,int);
pid_t __wrap_waitpid(pid_t pid,int *status,int options) {
 (void)options;waits++;
 if(fault==6 || fault==7) {errno=fault==6?ECHILD:EIO;return -1;}
 if(fault==8) {*status=0xffff;return pid;}
 int sig=SIGILL;
 if(fault==9 && waits==1) {errno=EINTR;return -1;}
 if(fault==9 && waits==2) sig=SIGSYS;
 if(fault==9 && waits==3) sig=SIGSTOP;
 if(fault==13 && waits==1) sig=SIGSTOP;
 if(fault==10) sig=SIGTRAP;
 *status=fault==11?0:(sig<<8)|0x7f;return pid;
}
static int injected_kill(int pid,int sig) {(void)pid;(void)sig;redeliver++;if(fault==13) {errno=ESRCH;return -1;}return 0;}
#define kill injected_kill
#define ptrace(r,p,a,b) injected_ptrace(r,p,(uintptr_t)(a),(void *)(uintptr_t)(b))
"""
        + body
        + r"""
int main(void) {
 int pair[2];struct ac_channel c;struct ac_binding b={{1},{2},{3},1,42,1};
 assert(!ac_pair(pair));assert(!ac_init(&c,pair[0],&b));ac_proot_bind(&c);
 pid_t child=ac_native_fork(AC_ROOT);assert(child>=0);
 if(!child) {const int keep[]={0,1,2};if(ac_child_fds(keep,3)) _exit(125);_exit(0);}
 Tracee t={0};t.pid=child;t._regs[0].value=123;t.restore_original_regs=true;
 int result=ptrace_pokedata_or_via_stub(&t,456,789);
 int status;assert(__real_waitpid(child,&status,0)==child && WIFEXITED(status));
 if(fault==0 || fault==9) {assert(!result && !c.lost && t._regs[0].value==123 && t.restore_original_regs);if(fault==9) assert(waits==4 && redeliver==1);}
 else {assert(result==-1 && c.lost);assert(ac_highwater(&c)==EBUSY);if(fault==12) assert(c.error==EIO && c.secondary==EPERM);}
 assert(t._regs[0].value==123 && t.restore_original_regs);
 return 0;
}
"""
    )
    compile_control(
        tmp_path,
        source.replace("RAW_CASE", str(fault)),
        ["-Wl,--wrap=ptrace", "-Wl,--wrap=waitpid"],
    )
