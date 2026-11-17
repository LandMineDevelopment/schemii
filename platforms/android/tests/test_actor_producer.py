"""Controls extracted from exact patched production leaves, not a policy model.

Requires the reviewed read-only donor via SCHEMII_ACTOR_DONOR. This test never
runs PRoot/guest/SDK or a deployment. Children belong directly to each fixture.
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


def compile_control(tmp_path, source):
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
            "-o",
            str(output),
        ],
        check=True,
    )
    subprocess.run([str(output)], check=True, timeout=10)


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
 int restart_how,pid,last_restart_how,running; } Tracee;
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
 Tracee t={{0},PTRACE_CONT,child,0,0}; assert(restart_tracee(&t,0));
 assert(t.running && t.last_restart_how==PTRACE_CONT && !t.restart_how);
 assert(ac_target_wait(child,&status,0,0)==child && WIFEXITED(status));
 assert(ac_highwater(&c)==EBUSY); return 0;
}
""",
    )


def test_actual_arm64_raw_resume_wait_block_on_owned_host_tracee(patched, tmp_path):
    text = (patched / "tracee/mem.c").read_text()
    block = text.split("#ifdef SCHEMII_MANAGED_ACTORS", 2)[-1].split("#else", 1)[0]
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
return wstatus;}
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
