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

static int fail_fork, fail_send_at, send_calls, fork_calls;
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
int main(int argc,char **argv) {
    assert(argc==2);
    if(!strcmp(argv[1],"native") || !strcmp(argv[1],"loss-before-export") || !strcmp(argv[1],"fork-failure") || !strcmp(argv[1],"birth-before-export-loss") || !strcmp(argv[1],"before-birth-loss")) native(argv[1]);
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
