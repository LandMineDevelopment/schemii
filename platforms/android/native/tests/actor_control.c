#if defined(AC_OWNER_FAULT)
#define _GNU_SOURCE
#include "owner.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <unistd.h>
static int fail_pair,fail_thread,fail_close,pair_calls,watched[8],counts[8],watched_count,close_errors;
int __real_socketpair(int,int,int,int[2]);
int __real_pthread_create(pthread_t *,const pthread_attr_t *,void *(*)(void *),void *);
int __real_openat(int,const char *,int,...);
int __real_close(int);
static void watch(int fd) { assert(watched_count<8);watched[watched_count++]=fd; }
int __wrap_openat(int fd,const char *path,int flags,...) {
    int r=__real_openat(fd,path,flags);if(r>=0) watch(r);return r;
}
int __wrap_socketpair(int family,int type,int protocol,int pair[2]) {
    if(++pair_calls==fail_pair) {errno=EMFILE;return -1;}
    int r=__real_socketpair(family,type,protocol,pair);
    if(!r) {watch(pair[0]);watch(pair[1]);}return r;
}
int __wrap_pthread_create(pthread_t *t,const pthread_attr_t *a,void *(*fn)(void *),void *v) {
    if(fail_thread) return EAGAIN;return __real_pthread_create(t,a,fn,v);
}
int __wrap_close(int fd) {
    int found=0;
    for(int i=0;i<watched_count;i++) if(watched[i]==fd) {counts[i]++;found=1;}
    int r=__real_close(fd);
    if(found && fail_close && close_errors<2) {errno=close_errors++?EINTR:EIO;return -1;}
    return r;
}
int main(int argc,char **argv) {
    assert(argc==3);
    fail_pair=!strcmp(argv[1],"second-pair")?2:0;
    fail_thread=!strcmp(argv[1],"pthread") || !strcmp(argv[1],"pthread-close-errors");
    fail_close=strstr(argv[1],"close-errors")!=NULL;
    int borrowed=open(argv[2],O_RDONLY|O_DIRECTORY|O_CLOEXEC);assert(borrowed>=0);
    struct apk_owner *o=NULL;struct apk_owner_errors e;
    int result=apk_owner_open_diagnostic(borrowed,&o,&e);
    if(fail_pair || fail_thread) {
        assert(!o && result==(fail_pair?EMFILE:EAGAIN) && e.error==result);
    } else {assert(!result && o);assert(apk_owner_close_diagnostic(&o,&e)==(fail_close?EIO:0));assert(!o);}
    assert(watched_count==(fail_pair?3:5));
    for(int i=0;i<watched_count;i++) assert(counts[i]==1);
    if(fail_close) assert(e.close_error==EIO && e.secondary_close_error==EINTR && e.close_failures==2);
    else assert(!e.close_error && !e.secondary_close_error && !e.close_failures);
    assert(fcntl(borrowed,F_GETFD)>=0);int other=open(argv[2],O_RDONLY|O_DIRECTORY|O_CLOEXEC);
    assert(other>=0 && !flock(other,LOCK_EX|LOCK_NB));__real_close(other);__real_close(borrowed);
    printf("owner-primary=%d close-primary=%d close-secondary=%d exactly-once=%d\n",e.error,e.close_error,e.secondary_close_error,watched_count);
    return 0;
}

#elif defined(AC_INTEGRATED)
#define _GNU_SOURCE
#include "actor_channel.h"
#include "tracee/tracee.h"
#include "tracee/event.h"
#include "tracee/reg.h"
#include "syscall/syscall.h"
#include "syscall/sysnum.h"
#include "path/f2fs-bug.h"
#include "fixture-sysvipc.h"
#include <dirent.h>
#include <sys/socket.h>
#include <sys/un.h>
extern struct sockaddr_un sysvipc_shm_helper_addr;
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/futex.h>
#include <sched.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <talloc.h>
#include <unistd.h>

static struct ac_channel writer, reader;
static const char *scenario, *fixture_binary;
extern char **environ;
static pid_t owned[256];
static unsigned owned_count, event_count, task_count, exec_count, wait_count;
static int sentinel=-1, scripted, event_index, scripted_count, loop_wait_calls;
static pid_t helper_pid, script_event_pid;
static int last_kernel_status;
static Tracee *register_source;
static struct {pid_t pid; int status;} scripted_events[8];
static void helper_eof(void) {
    int keep[]={0,1,2,writer.fd,reader.fd,sentinel}, secondary=0;
    assert(!ac_close_fds(keep,6,&secondary));
}
static void queue(pid_t pid,int status) {
    assert(scripted_count<8); scripted_events[scripted_count].pid=pid;
    scripted_events[scripted_count++].status=status;
}
static void cleanup_owned(void);
static void own(pid_t pid) {
    assert(pid>0);
    for(unsigned i=0;i<owned_count;i++) if(owned[i]==pid) return;
    assert(owned_count<256); owned[owned_count++]=pid;
}
static void forget(pid_t pid) {
    for(unsigned i=0;i<owned_count;i++) if(owned[i]==pid) owned[i]=0;
}
static void drain(void) {
    struct ac_record r; int e;
    while((e=ac_receive(&reader,&r))==0) {
        event_count++; task_count+=r.op==AC_TASK; exec_count+=r.op==AC_EXEC; wait_count+=r.op==AC_WAIT;
        if(r.op==AC_TASK) {assert(r.task && r.process && r.creator && r.task!=r.creator);
            int thread=!strcmp(scenario,"kernel-thread") || !strcmp(scenario,"kernel-nonleader-exec") || !strcmp(scenario,"script-nonleader-exec");
            if(thread) assert(r.process!=r.task);else assert(r.process==r.task);
        }
    }
    if(e!=EAGAIN) { fprintf(stderr,"consumer error=%d op=%u seq=%llu\n",e,r.op,(unsigned long long)r.sequence); cleanup_owned(); _exit(90); }
}
long __real_ptrace(enum __ptrace_request,...);
pid_t __real_fork(void);
pid_t __real_waitpid(pid_t,int *,int);
int __real_kill(pid_t,int);
int __real_execvp(const char *,char *const[]);

static void cleanup_owned(void) {
    for(unsigned i=0;i<owned_count;i++) if(owned[i]) (void)__real_kill(owned[i],SIGKILL);
    for(unsigned round=0;round<2000;round++) {
        unsigned remaining=0;
        for(unsigned i=0;i<owned_count;i++) if(owned[i]) {
            int status; pid_t p=__real_waitpid(owned[i],&status,__WALL|WNOHANG);
            if(p>0 && (WIFEXITED(status)||WIFSIGNALED(status))) owned[i]=0;
            else if(p<0 && errno==ECHILD) owned[i]=0;
            else { remaining++; if(p>0 && WIFSTOPPED(status)) (void)__real_ptrace(PTRACE_CONT,p,NULL,NULL); }
        }
        if(!remaining) return;
        usleep(500);
    }
    fprintf(stderr,"fixture cleanup unresolved exact identities\n");
}
pid_t __wrap_fork(void) {
    if(!strcmp(scenario,"root-fork-failure") || !strcmp(scenario,"f2fs-fork-failure") || !strcmp(scenario,"helper-fork-failure")) {errno=EAGAIN;return -1;}
    pid_t p=__real_fork(); if(p>0) own(p); return p;
}
long __wrap_ptrace(enum __ptrace_request req,...) {
    va_list ap; va_start(ap,req); pid_t pid=va_arg(ap,pid_t); void *a=va_arg(ap,void *), *b=va_arg(ap,void *); va_end(ap);
    if(!strcmp(scenario,"root-options-failure") && req==PTRACE_SETOPTIONS) {errno=EACCES;return -1;}
    if(scripted) {
        if(req==PTRACE_GETEVENTMSG) *(unsigned long *)b=(unsigned long)script_event_pid;
        else if(req==PTRACE_GETREGS) memcpy(b,&register_source->_regs[CURRENT],sizeof(register_source->_regs[CURRENT]));
        return 0; /* Explicit kernel register/event/resume injection, no external target. */
    }
    if(req==PTRACE_TRACEME) {
    DIR *d=opendir("/proc/self/fd");if(!d) _exit(81);
    struct dirent *entry;int unexpected=0;
    while((entry=readdir(d))) {char *end;long fd=strtol(entry->d_name,&end,10);
        if(*entry->d_name && !*end && fd>2 && fd!=dirfd(d)) unexpected++;}
    closedir(d);if(unexpected) _exit(82);
    }
    if(req==PTRACE_TRACEME && !strcmp(scenario,"root-traceme-failure")) {errno=EPERM;return -1;}
    long r=__real_ptrace(req,pid,a,b);
    if(req==PTRACE_GETEVENTMSG && !r && *(unsigned long *)b>0 && *(unsigned long *)b<=INT32_MAX) {
        if((unsigned)last_kernel_status>>16==PTRACE_EVENT_EXEC) forget((pid_t)*(unsigned long *)b);
        else own((pid_t)*(unsigned long *)b);
    }
    return r;
}
pid_t __wrap_waitpid(pid_t pid,int *status,int flags) {
    if(pid!=-1) {pid_t p=__real_waitpid(pid,status,flags);if(p>0 && (WIFEXITED(*status)||WIFSIGNALED(*status))) forget(p);return p;}
    loop_wait_calls++;
    if(loop_wait_calls==1 && !strncmp(scenario,"loop-wait-",10)) {
        if(!strcmp(scenario,"loop-wait-invalid")) {*status=0xffff;return owned[0];}
        errno=!strcmp(scenario,"loop-wait-EINTR")?EINTR:!strcmp(scenario,"loop-wait-ECHILD")?ECHILD:EIO;
        return -1;
    }
    if(scripted) {
        drain();
        if(event_index==scripted_count) {errno=ECHILD;return -1;}
        *status=scripted_events[event_index].status;return scripted_events[event_index++].pid;
    }
    /* Production event_loop asks -1. Fixture transport polls only the exact
     * children/TIDs returned by its own fork and kernel ptrace birth messages.
     * It cannot reap unrelated children or discover/adopt by proc/UID/group. */
    for(unsigned rounds=0;rounds<20000;rounds++) {
        drain(); int live=0;
        if(helper_pid && !strcmp(scenario,"helper-last") && task_count) helper_eof();
        for(unsigned i=0;i<owned_count;i++) if(owned[i]) {
            live++;
            if(owned[i]==helper_pid && !strcmp(scenario,"helper-last") && !task_count) continue;
            pid_t p=__real_waitpid(owned[i],status,flags|WNOHANG);
            if(p>0) {
                last_kernel_status=*status;
                if(WIFEXITED(*status)||WIFSIGNALED(*status)) forget(p);
                return p;
            }
            if(p<0 && errno!=ECHILD && errno!=EINTR) return p;
        }
        if(!live) { errno=ECHILD; return -1; }
        usleep(500);
    }
    fprintf(stderr,"fixture exact-wait deadline; source_error=%d live=%u\n",writer.error,owned_count);
    cleanup_owned(); _exit(91);
}
int __wrap_kill(pid_t pid,int sig) {
    if(!strcmp(scenario,"root-stop-failure") && pid==getpid() && sig==SIGSTOP) {errno=EIO;return -1;}
    return __real_kill(pid,sig);
}
static int thread_done(void *arg) { atomic_store((_Atomic int *)arg,1); return 0; }
int __real_translate_syscall_enter(Tracee *);
void __real_translate_syscall_exit(Tracee *);
static int birth_number(word_t call) {return call==PR_clone || call==PR_clone3 || call==PR_fork || call==PR_vfork;}
int __wrap_translate_syscall_enter(Tracee *t) {
    /* Native exec-only transport: no guest filesystem/loader is installed.
     * Birth admission is ALWAYS the full production entry function. Other
     * modes keep the entire unmodified translation path as well. */
    if(!strcmp(scenario,"kernel-nonleader-exec") && !birth_number(get_sysnum(t,ORIGINAL))) return 0;
    return __real_translate_syscall_enter(t);
}
void __wrap_translate_syscall_exit(Tracee *t) {
    if(!strcmp(scenario,"kernel-nonleader-exec") && !birth_number(get_sysnum(t,ORIGINAL))) return;
    __real_translate_syscall_exit(t);
}
static int exec_from_thread(void *arg) {
    (void)arg;char *args[]={(char *)fixture_binary,"kernel-exec-target",NULL};
    syscall(SYS_execve,fixture_binary,args,environ);_exit(83);
}
static int kernel_script(void) {
    if(fcntl(sentinel,F_GETFD)!=-1 || errno!=EBADF) return 70;
    if(!strcmp(scenario,"root-exec-failure")) { errno=ENOENT; return -1; }
    if(!strcmp(scenario,"kernel-untraced")) return syscall(SYS_clone,CLONE_UNTRACED|SIGCHLD,0,0,0,0)==-1 && errno==EPERM?0:78;
    if(!strcmp(scenario,"kernel-clone3")) return syscall(SYS_clone3,(void *)1,1)==-1 && errno==ENOSYS?0:79;
    if(!strcmp(scenario,"kernel-rejected-clone")) return syscall(SYS_clone,CLONE_SIGHAND|SIGCHLD,0,0,0,0)==-1 && errno==EINVAL?0:80;
    if(!strcmp(scenario,"kernel-nonleader-exec")) {
        void *stack=malloc(1024*1024);if(!stack) return 84;
        if(clone(exec_from_thread,(char *)stack+1024*1024,CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND|CLONE_THREAD,NULL)<0) return 85;
        for(;;) syscall(SYS_pause);
    }
    if(!strcmp(scenario,"kernel-thread")) {
        void *stack=malloc(1024*1024); if(!stack) return 71;
        _Atomic int done=0; int tid=0;
        int flags=CLONE_VM|CLONE_FS|CLONE_FILES|CLONE_SIGHAND|CLONE_THREAD|CLONE_PARENT_SETTID|CLONE_CHILD_CLEARTID;
        int t=clone(thread_done,(char *)stack+1024*1024,flags,&done,&tid,NULL,&tid);
        if(t<0) return 72;
        while(atomic_load((_Atomic int *)&tid)) syscall(SYS_futex,&tid,FUTEX_WAIT,tid,NULL,NULL,0);
        return atomic_load(&done)?0:73;
    }
    pid_t child;
    if(!strcmp(scenario,"kernel-vfork")) child=vfork();
    else if(!strcmp(scenario,"kernel-clone")) child=(pid_t)syscall(SYS_clone,SIGCHLD,0,0,0,0);
    else child=(pid_t)syscall(SYS_fork);
    if(!child) _exit(0);
    if(child<0) return 74;
    int status;
    if(__real_waitpid(child,&status,0)!=child) return 75;
    return WIFEXITED(status)&&!WEXITSTATUS(status)?0:76;
}
int __wrap_execvp(const char *name,char *const argv[]) {
    (void)name;(void)argv;
    int r=kernel_script(); if(r==-1) return -1; _exit(r);
}
static void initialize(void) {
    struct ac_binding b={{1},{2},{3},1,42,1}; int pair[2];
    assert(!ac_pair(pair)); assert(!ac_init(&writer,pair[0],&b)); assert(!ac_init(&reader,pair[1],&b));
    ac_proot_bind(&writer); setenv("PROOT_NO_SECCOMP","1",1); setenv("PROOT_TMP_DIR",getenv("SCHEMII_ACTOR_SHORT_TMP")?getenv("SCHEMII_ACTOR_SHORT_TMP"):".",1);
}
static Tracee *configure(pid_t pid) {
    Tracee *root=get_tracee(NULL,pid,true); assert(root);
    root->exe=talloc_strdup(root,"/owned-native-fixture"); root->tool_name="finite-control";
    root->fs->cwd=talloc_strdup(root->fs,"/"); return root;
}
static int script_matrix(void) {
    pid_t parent=ac_native_fork(AC_ROOT); assert(parent>=0);
    if(!parent) {const int keep[]={0,1,2}; if(ac_child_fds(keep,3)) _exit(125); _exit(0);}
    int parent_status;assert(__real_waitpid(parent,&parent_status,0)==parent);forget(parent);
    Tracee *root=configure(parent); register_source=root;
    int is_exec=strstr(scenario,"exec")!=NULL, collision=strstr(scenario,"collision")!=NULL;
    set_sysnum(root,PR_clone);poke_reg(root,SYSARG_1,is_exec&&!collision?CLONE_THREAD|CLONE_VM|CLONE_SIGHAND:SIGCHLD);
    save_current_regs(root,ORIGINAL);
    assert(translate_syscall_enter(root)==0);
    /* The actual fixture birth supplies exact child IDs/status. Kernel event
     * ordering and register transport are then injected into full PRoot units. */
    pid_t child=__real_fork();assert(child>=0);
    if(!child) {const int keep[]={0,1,2};if(ac_child_fds(keep,3)) _exit(125);_exit(0);}
    int child_status;assert(__real_waitpid(child,&child_status,0)==child);
    scripted=1;script_event_pid=child;
    if(!strcmp(scenario,"script-death-first")) queue(child,child_status);
    else {get_tracee(NULL,child,true);queue(child,(SIGSTOP<<8)|0x7f);}
    if(is_exec) {
        assert(new_child(root,0)==0); /* whole attach path before remap */
        if(collision) {
            assert(get_execve_tracee(root)==root);
            assert(writer.lost && writer.error==ESTALE);
            assert(root->pid==parent);drain();return 0;
        }
        queue(parent,((SIGTRAP|PTRACE_EVENT_EXEC<<8)<<8)|0x7f);
        queue(parent,parent_status);
    } else {
        queue(parent,((SIGTRAP|PTRACE_EVENT_FORK<<8)<<8)|0x7f);
        if(strcmp(scenario,"script-death-first")) queue(child,child_status);
        queue(parent,parent_status);
    }
    int result=event_loop();drain();assert(!result);assert(!writer.lost);assert(task_count==1);
    assert(exec_count==(unsigned)is_exec);assert(ac_highwater(&writer)==EBUSY);
    fprintf(stdout,"mode=%s task=%u exec=%u early-and-wait-preserved=1\n",scenario,task_count,exec_count);return 0;
}
static int tracer_loss(void) {
    struct ac_binding b={{1},{2},{3},1,42,1}; int channel[2], report[2];
    assert(!ac_pair(channel) && !pipe2(report,O_CLOEXEC));
    pid_t tracer=__real_fork();assert(tracer>=0);
    struct loss_identity {pid_t helper; struct sockaddr_un address;} identity;
    if(!tracer) {
        close(channel[1]);close(report[0]);
        assert(!ac_init(&writer,channel[0],&b));ac_proot_bind(&writer);
        setenv("PROOT_TMP_DIR",getenv("SCHEMII_ACTOR_SHORT_TMP"),1);
        struct SysVIpcShmHelperRequest request={SHMHELPER_ALLOC,17,4096,0};int fd=-1;
        sysvipc_shm_send_helper_request(&request,&fd,sizeof(fd));
        if(fd<0) _exit(120);
        identity.helper=0;
        for(unsigned i=0;i<AC_CAPACITY;i++) if(writer.slots[i].kind==AC_SYSVIPC) identity.helper=writer.slots[i].pid;
        identity.address=sysvipc_shm_helper_addr;
        /* Block the REAL service in accept: parent EOF cannot make it exit.
         * This is the pinned native-helper owner-loss counterexample. */
        request.op=SHMHELPER_DISTRIBUTE;request.fd=fd;
        sysvipc_shm_send_helper_request(&request,NULL,0);
        if(write(report[1],&identity,sizeof(identity))!=(ssize_t)sizeof(identity)) _exit(121);
        raise(SIGSTOP);_exit(122);
    }
    close(channel[0]);close(report[1]);assert(!ac_init(&reader,channel[1],&b));
    assert(read(report[0],&identity,sizeof(identity))==(ssize_t)sizeof(identity));close(report[0]);
    int status;assert(__real_waitpid(tracer,&status,WUNTRACED)==tracer && WIFSTOPPED(status));
    /* Observer attaches ONLY to the exact fixture-exported native child so it
     * can collect ptrace terminal evidence after its real parent dies. This is
     * test resource custody, never real-parent wait recovery or proc adoption. */
    assert(!__real_ptrace(PTRACE_SEIZE,identity.helper,NULL,NULL));
    assert(!__real_kill(tracer,SIGKILL));assert(__real_waitpid(tracer,&status,0)==tracer && WIFSIGNALED(status));
    struct ac_record r;int e;do {e=ac_receive(&reader,&r);} while(!e);
    assert(e==EPIPE && reader.lost && ac_highwater(&reader)==EBUSY);
    assert(__real_waitpid(identity.helper,&status,__WALL|WNOHANG)==0); /* still live after tracer death */
    int client=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0);assert(client>=0);
    assert(!connect(client,(struct sockaddr *)&identity.address,sizeof(identity.address)));
    char payload;struct iovec io={&payload,1};
    union {struct cmsghdr align;char bytes[CMSG_SPACE(sizeof(int))];} control;
    struct msghdr m={0};m.msg_iov=&io;m.msg_iovlen=1;m.msg_control=control.bytes;m.msg_controllen=sizeof(control);
    assert(recvmsg(client,&m,MSG_CMSG_CLOEXEC)==1);
    struct cmsghdr *h=CMSG_FIRSTHDR(&m);assert(h && h->cmsg_type==SCM_RIGHTS);
    int received;memcpy(&received,CMSG_DATA(h),sizeof(received));close(received);close(client);
    assert(__real_waitpid(identity.helper,&status,__WALL)==identity.helper && WIFEXITED(status));
    /* This terminal is an OBSERVER ptrace wait, not the dead real parent's
     * authoritative helper receipt. The actual consumer must retain UNKNOWN. */
    assert(ac_highwater(&reader)==EBUSY && reader.slots[AC_CAPACITY-1].wait_pending);
    printf("tracer-death live-helper=observed peer-loss=retained observer-terminal-only=1\n");return 0;
}
int main(int argc,char **argv) {
    assert(argc==2); scenario=argv[1];fixture_binary=argv[0];
    if(!strcmp(scenario,"kernel-exec-target")) _exit(0);
    if(!strcmp(scenario,"tracer-live-helper-loss")) return tracer_loss();
    initialize();
    sentinel=open("/dev/null",O_RDONLY|O_CLOEXEC); assert(sentinel>=0);
    if(!strncmp(scenario,"script-",7)) return script_matrix();
    if(!strncmp(scenario,"f2fs-",5)) {
        unsetenv("PROOT_F2FS_WORKAROUND");Tracee *t=configure(0);
        assert(!should_skip_file_access_due_to_f2fs_bug(t,"/native-fixture"));drain();
        unsigned count=0;
        for(unsigned i=0;i<AC_CAPACITY;i++) if(writer.slots[i].kind==AC_F2FS) {
            count++;assert(!writer.slots[i].live && !writer.slots[i].pending && !writer.slots[i].wait_pending);
        }
        assert(count==1 && !writer.lost && ac_highwater(&writer)==EBUSY);return 0;
    }
    if(!strcmp(scenario,"helper-fork-failure") || !strcmp(scenario,"helper-startup-failure")) {
        if(!strcmp(scenario,"helper-startup-failure")) {
            char cwd[4096];assert(getcwd(cwd,sizeof(cwd)));setenv("PROOT_TMP_DIR",cwd,1);
        }
        struct SysVIpcShmHelperRequest request={SHMHELPER_ALLOC,17,4096,0};int fd=-1;
        sysvipc_shm_send_helper_request(&request,&fd,sizeof(fd));assert(fd==-1);
        if(!strcmp(scenario,"helper-startup-failure")) {
            assert(owned_count==1); /* Failed readiness is NOT a terminal wait. */
            sysvipc_shm_send_helper_request(&request,&fd,sizeof(fd));assert(owned_count==1 && errno==EBUSY);
            int status;pid_t helper=owned[0];assert(ac_target_wait(helper,&status,0,1)==helper && WIFEXITED(status) && WEXITSTATUS(status)==1);
        } else assert(!owned_count);
        drain();assert(!writer.lost);return 0;
    }
    if(!strncmp(scenario,"helper-",7)) {
        struct SysVIpcShmHelperRequest request={SHMHELPER_ALLOC,17,4096,0};int fd=-1;
        sysvipc_shm_send_helper_request(&request,&fd,sizeof(fd));
        for(unsigned i=0;i<AC_CAPACITY;i++) if(writer.slots[i].kind==AC_SYSVIPC) helper_pid=writer.slots[i].pid;
        if(fd<0) {cleanup_owned();assert(fd>=0);}
        request.op=SHMHELPER_FREE;request.fd=fd;sysvipc_shm_send_helper_request(&request,NULL,0);
        if(!strcmp(scenario,"helper-first")) helper_eof();
    }
    Tracee *root=configure(0);
    char *args[]={"fixture",NULL}; pid_t parent=getpid();
    int result=launch_process(root,args);
    if(getpid()!=parent) _exit(result==-ENOENT || (!strcmp(scenario,"root-stop-failure") && result==-EIO) || (!strcmp(scenario,"root-traceme-failure") && result==-EPERM)?0:77);
    if(!strcmp(scenario,"root-fork-failure")) {assert(result==-EAGAIN && !owned_count && !writer.lost);drain();return 0;}
    assert(!result); result=event_loop();
    if(!strcmp(scenario,"root-options-failure") || (!strncmp(scenario,"loop-wait-",10) && strcmp(scenario,"loop-wait-EINTR"))) {
        int expected=!strcmp(scenario,"root-options-failure")?EACCES:!strcmp(scenario,"loop-wait-ECHILD")?ECHILD:!strcmp(scenario,"loop-wait-invalid")?EPROTO:EIO;
        assert(result==EXIT_FAILURE && writer.error==expected && writer.lost);
        cleanup_owned();assert(ac_highwater(&writer)==EBUSY);return 0;
    }
    drain();
    fprintf(stdout,"mode=%s result=%d task=%u exec=%u directwait=%u source_error=%d\n",scenario,result,task_count,exec_count,wait_count,writer.error);
    fflush(stdout); assert(result==0); assert(!writer.lost); assert(wait_count);
    int no_birth=!strncmp(scenario,"root-",5) || !strcmp(scenario,"kernel-untraced") || !strcmp(scenario,"kernel-clone3") || !strcmp(scenario,"kernel-rejected-clone");
    assert(task_count==(unsigned)!no_birth);
    if(!strcmp(scenario,"kernel-nonleader-exec")) assert(exec_count==1);
    if(helper_pid) {assert(!get_tracee(NULL,helper_pid,false));assert(wait_count>=2);}
    assert(ac_highwater(&writer)==EBUSY); /* receipt and/or guest waits remain */
    close(sentinel); return 0;
}

#else
#define _GNU_SOURCE
#include "actor_channel.h"
#include <assert.h>
#include <errno.h>
#include <dirent.h>
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

static int fail_fork, fail_send_at, send_calls, fork_calls, wait_injection, wait_calls;
pid_t __real_waitpid(pid_t,int *,int);
pid_t __wrap_waitpid(pid_t pid,int *status,int options) {
    wait_calls++;
    if(wait_injection==1 && wait_calls==1) {errno=EINTR;return -1;}
    if(wait_injection==2 || wait_injection==3) {errno=wait_injection==2?ECHILD:EIO;return -1;}
    if(wait_injection==4) {*status=0;return pid+1;}
    if(wait_injection==5) {*status=0xffff;return pid;}
    return __real_waitpid(pid,status,options);
}
static int close_error_fds[2]={-1,-1}, close_counts[2];
int __real_close(int);
int __wrap_close(int fd) {
    int r=__real_close(fd);
    for(int i=0;i<2;i++) if(fd==close_error_fds[i]) { close_counts[i]++; errno=i?EIO:EINTR; return -1; }
    return r;
}
pid_t __real_fork(void);
ssize_t __real_send(int,const void *,size_t,int);
pid_t __wrap_fork(void) { fork_calls++; if(fail_fork) { errno=EAGAIN; return -1; } return __real_fork(); }
ssize_t __wrap_send(int fd,const void *p,size_t n,int flags) {
    if(++send_calls==fail_send_at) { errno=EPIPE; return -1; }
    return __real_send(fd,p,n,flags);
}
static struct ac_channel sender, receiver;
static int pair[2];
static void setup(void) {
    struct ac_binding b={{1},{2},{3},1,42,1};
    assert(ac_pair(pair)==0); assert(ac_init(&sender,pair[0],&b)==0);
    assert(ac_init(&receiver,pair[1],&b)==0);
}
static void drain(void) {
    struct ac_record r;
    int e;
    while((e=ac_receive(&receiver,&r))==0) {}
    assert(e==EAGAIN);
}
static struct ac_record intent(void) {
    struct ac_record r={0}; r.op=AC_INTENT; r.task=1; r.process=1;
    r.intent=1; r.creator=42; r.kind=AC_ROOT; return r;
}
static int fd_count(void) {
    DIR *d=opendir("/proc/self/fd"); assert(d); int count=0; struct dirent *e;
    while((e=readdir(d))) if(e->d_name[0]!='.') count++;
    assert(!closedir(d)); return count;
}
static void wire(const char *mode) {
    setup(); struct ac_record r=intent();
    if(!strcmp(mode,"peer-loss")) { close(pair[1]); assert(ac_send(&sender,&r)==EPIPE); assert(sender.lost); return; }
    if(!strcmp(mode,"pressure")) {
        int size=1024; assert(setsockopt(pair[0],SOL_SOCKET,SO_SNDBUF,&size,sizeof(size))==0);
        unsigned i=0; while(!ac_send(&sender,&r)) { assert(++i<100000); }
        assert(sender.lost && (sender.error==EAGAIN || sender.error==EWOULDBLOCK));
        assert(ac_highwater(&sender)==EBUSY); return;
    }
    assert(ac_send(&sender,&r)==0);
    unsigned char frame[AC_FRAME_SIZE+1];
    assert(recv(pair[1],frame,sizeof(frame),0)==AC_FRAME_SIZE);
    if(!strcmp(mode,"duplicate")) {
        assert(send(pair[0],frame,AC_FRAME_SIZE,0)==AC_FRAME_SIZE);
        assert(ac_receive(&receiver,&r)==0);
    } else if(!strcmp(mode,"gap")) frame[127]=2;
    else if(!strcmp(mode,"source")) frame[16]^=1;
    else if(!strcmp(mode,"creator")) frame[159]^=1;
    else if(!strcmp(mode,"version")) frame[5]=2;
    else if(!strcmp(mode,"reserved")) frame[223]=1;
    else if(!strcmp(mode,"wrap")) receiver.rx=UINT64_MAX;
    else if(!strcmp(mode,"tx-wrap")) { sender.tx=UINT64_MAX; assert(ac_send(&sender,&r)==EOVERFLOW); return; }
    else if(!strcmp(mode,"rights")) {
        int before=fd_count();
        int fd=open("/dev/null",O_RDONLY|O_CLOEXEC); assert(fd>=0);
        struct iovec io={frame,AC_FRAME_SIZE};
        union { struct cmsghdr h; char b[CMSG_SPACE(sizeof(int))]; } u;
        struct msghdr m={0}; m.msg_iov=&io; m.msg_iovlen=1; m.msg_control=u.b; m.msg_controllen=sizeof(u.b);
        struct cmsghdr *h=CMSG_FIRSTHDR(&m); h->cmsg_level=SOL_SOCKET; h->cmsg_type=SCM_RIGHTS; h->cmsg_len=CMSG_LEN(sizeof(int));
        memcpy(CMSG_DATA(h),&fd,sizeof(fd));
        assert(sendmsg(pair[0],&m,0)==AC_FRAME_SIZE); close(fd);
        assert(ac_receive(&receiver,&r)!=0 && receiver.lost); assert(fd_count()==before); return;
    } else if(!strcmp(mode,"valid")) {
        assert(send(pair[0],frame,AC_FRAME_SIZE,0)==AC_FRAME_SIZE);
        assert(ac_receive(&receiver,&r)==0); assert(ac_highwater(&receiver)==EBUSY); return;
    }
    size_t n=!strcmp(mode,"short")?12:!strcmp(mode,"truncated")?sizeof(frame):AC_FRAME_SIZE;
    assert(send(pair[0],frame,n,0)==(ssize_t)n);
    assert(ac_receive(&receiver,&r)!=0 && receiver.lost);
    assert(ac_highwater(&receiver)==EBUSY);
}
static void native(const char *mode) {
    setup(); ac_proot_bind(&sender);
    int sentinel=open("/dev/null",O_RDONLY|O_CLOEXEC); assert(sentinel>=0);
    if(!strcmp(mode,"fork-failure")) fail_fork=1;
    if(!strcmp(mode,"birth-before-export-loss")) fail_send_at=2;
    if(!strcmp(mode,"before-birth-loss")) fail_send_at=1;
    pid_t pid=ac_native_fork(AC_SYSVIPC);
    if(fail_send_at==1) { assert(pid==-1 && sender.lost && !fork_calls); return; }
    if(fail_fork) {
        assert(pid==-1 && errno==EAGAIN); drain();
        assert(!sender.slots[0].pending && !sender.slots[0].live);
        assert(ac_highwater(&sender)==EBUSY); return;
    }
    assert(pid>=0);
    if(!pid) {
        const int keep[]={0,1,2};
        if(ac_child_fds(keep,3)) _exit(120);
        if(fcntl(sentinel,F_GETFD)!=-1 || errno!=EBADF) _exit(121);
        if(ac_proot_require()!=ENOTCONN) _exit(122);
        _exit(0);
    }
    close(sentinel); assert(ac_highwater(&sender)==EBUSY);
    if(!strcmp(mode,"loss-before-export")) { ac_loss(&sender,EPIPE); }
    int status; assert(waitpid(pid,&status,0)==pid); assert(WIFEXITED(status)&&WEXITSTATUS(status)==0);
    assert(ac_helper_wait(pid,status)==1); assert(ac_helper_wait(getpid(),status)==0);
    drain();
    if(!strcmp(mode,"birth-before-export-loss")) { assert(sender.lost && sender.slots[0].pending && sender.slots[0].wait_pending); return; }
    if(!strcmp(mode,"loss-before-export")) { assert(ac_highwater(&sender)==EBUSY); return; }
    struct ac_record ack={0}; ack.op=AC_ACK; ack.intent=sender.tx;
    assert(ac_send(&receiver,&ack)==0); assert(ac_receive(&sender,&ack)==0);
    assert(ac_highwater(&sender)==0);
    /* A late ACK never repairs the export/receipt loss window. */
    struct ac_record frame={0}; frame.op=AC_RESUME;
    assert(!ac_send(&sender,&frame));
    ack.op=AC_ACK; ack.intent=sender.tx; assert(!ac_send(&receiver,&ack));
    ac_loss(&sender,EPIPE); assert(ac_receive(&sender,&ack)==EPIPE);
    assert(ac_highwater(&sender)==EBUSY);
}
static void tables(const char *mode) {
    setup(); ac_proot_bind(&sender);
    if(!strcmp(mode,"early-saturation")) {
        for(unsigned i=0;i<AC_CAPACITY;i++) ac_observe((pid_t)(100000+i),SIGSTOP<<8|0x7f);
        assert(!sender.lost && ac_highwater(&sender)==EBUSY);
        ac_observe(200000,SIGSTOP<<8|0x7f); assert(sender.lost && sender.error==ENOSPC);
        for(unsigned i=0;i<AC_CAPACITY;i++) assert(sender.early[i].pid==(pid_t)(100000+i));
        return;
    }
    if(!strcmp(mode,"incarnation-wrap")) {
        sender.next_incarnation=UINT64_MAX;
        assert(ac_native_fork(AC_ROOT)==-1 && sender.error==EOVERFLOW && !fork_calls); return;
    }
    pid_t parent=ac_native_fork(AC_ROOT);assert(parent>=0);
    if(!parent) {const int keep[]={0,1,2};if(ac_child_fds(keep,3)) _exit(125);_exit(0);}
    int status;assert(__real_waitpid(parent,&status,0)==parent);drain();
    for(unsigned i=1;i<AC_CAPACITY;i++) {
        assert(!ac_birth_enter(parent)); assert(!ac_task(parent,(pid_t)(100000+i),1)); drain();
        assert(sender.slots[i].process==sender.slots[0].process);
        assert(sender.slots[i].creator==sender.slots[0].task);
    }
    int calls=fork_calls;assert(ac_native_fork(AC_SYSVIPC)==-1);
    assert(fork_calls==calls && sender.error==ENOSPC && ac_highwater(&sender)==EBUSY);
}
static void waits(const char *mode) {
    setup();ac_proot_bind(&sender);
    pid_t child=ac_native_fork(AC_F2FS);assert(child>=0);
    if(!child) {const int keep[]={0,1,2};if(ac_child_fds(keep,3)) _exit(125);_exit(0);}
    wait_injection=atoi(mode+5);int status=0;
    pid_t result=ac_target_wait(child,&status,0,1);
    if(wait_injection==1) {assert(result==child && wait_calls==2 && !sender.lost);drain();}
    else {assert(result==-1 && sender.lost && sender.slots[0].live && sender.slots[0].wait_pending);
        assert(__real_waitpid(child,&status,0)==child);assert(ac_highwater(&sender)==EBUSY);}
}
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"slot-saturation") || !strcmp(argv[1],"early-saturation") || !strcmp(argv[1],"incarnation-wrap")) tables(argv[1]);
    else if(!strncmp(argv[1],"wait-",5)) waits(argv[1]);
    else if(!strcmp(argv[1],"native") || !strcmp(argv[1],"loss-before-export") || !strcmp(argv[1],"fork-failure") || !strcmp(argv[1],"birth-before-export-loss") || !strcmp(argv[1],"before-birth-loss")) native(argv[1]);
    else if(!strcmp(argv[1],"close-errors")) {
        close_error_fds[0]=open("/dev/null",O_RDONLY); close_error_fds[1]=open("/dev/null",O_RDONLY);
        assert(close_error_fds[0]>=0 && close_error_fds[1]>=0);
        int keep[]={0,1,2}, secondary=0;
        int error=ac_close_fds(keep,3,&secondary);
        assert((error==EINTR && secondary==EIO) || (error==EIO && secondary==EINTR));
        assert(close_counts[0]==1 && close_counts[1]==1);
    }
    else if(!strcmp(argv[1],"policy")) {
        assert(ac_clone_policy(0,SIGCHLD)==0);
        assert(ac_clone_policy(0,CLONE_VM|CLONE_THREAD|CLONE_SIGHAND)==0);
        assert(ac_clone_policy(0,CLONE_UNTRACED|SIGCHLD)==EPERM);
        assert(ac_clone_policy(1,0)==ENOSYS);
        assert(ac_proot_require()==ENOTCONN);
    } else wire(argv[1]);
    return 0;
}

#endif
