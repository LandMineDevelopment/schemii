#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include "actor_channel.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ptrace.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

/* Wire has no native structs/padding, pointers, strings or secret-bearing data.
 * All integers big endian; reserved bytes must be zero. */
static void put(unsigned char *p, uint64_t v, unsigned n) {
    while (n) { p[--n]=(unsigned char)v; v>>=8; }
}
static uint64_t get(const unsigned char *p, unsigned n) {
    uint64_t v=0; while(n--) v=(v<<8)|*p++; return v;
}
void ac_loss(struct ac_channel *c,int e) {
    c->lost=1;
    if (!c->error) c->error=e?e:EPROTO;
    else if (!c->secondary) c->secondary=e?e:EPROTO;
}
int ac_pair(int p[2]) {
    return socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC|SOCK_NONBLOCK,0,p)<0?errno:0;
}
int ac_init(struct ac_channel *c,int fd,const struct ac_binding *b) {
    int type; socklen_t n=sizeof(type);
    if (!c || !b || !b->generation || !b->creator || b->profile!=1) return EINVAL;
    if (getsockopt(fd,SOL_SOCKET,SO_TYPE,&type,&n)<0) return errno;
    if (type!=SOCK_SEQPACKET) return EPROTOTYPE;
    struct sockaddr_un address; socklen_t length=sizeof(address);
    if(getsockname(fd,(struct sockaddr *)&address,&length)<0) return errno;
    if(address.sun_family!=AF_UNIX) return EAFNOSUPPORT;
    int flags=fcntl(fd,F_GETFD); if(flags<0) return errno;
    if(!(flags&FD_CLOEXEC)) return EINVAL;
    memset(c,0,sizeof(*c)); c->fd=fd; c->binding=*b; return 0;
}
static void binding(unsigned char *p,const struct ac_binding *b) {
    memcpy(p+16,b->source,32); memcpy(p+48,b->contract,32); memcpy(p+80,b->run,16);
    put(p+96,b->generation,8); put(p+104,b->creator,8); put(p+112,b->profile,4);
}
int ac_send(struct ac_channel *c,struct ac_record *r) {
    unsigned char p[AC_FRAME_SIZE]={0}; struct timespec ts;
    if (c->lost) return c->error;
    if (c->tx==UINT64_MAX) { ac_loss(c,EOVERFLOW); return EOVERFLOW; }
    if (clock_gettime(CLOCK_MONOTONIC,&ts)<0) { ac_loss(c,errno); return c->error; }
    r->sequence=c->tx+1; r->monotonic_ns=(uint64_t)ts.tv_sec*1000000000+(uint64_t)ts.tv_nsec;
    put(p,0x53434131,4); put(p+4,1,2); put(p+6,AC_FRAME_SIZE,2);
    put(p+8,r->op,4); binding(p,&c->binding);
    const uint64_t values[]={r->sequence,r->intent,r->task,r->process,r->creator,r->monotonic_ns};
    for(unsigned i=0;i<6;i++) put(p+120+i*8,values[i],8);
    put(p+168,r->kind,4); put(p+172,r->pid,4); put(p+176,r->related,4);
    put(p+180,(uint32_t)r->result,4); put(p+184,(uint32_t)r->error,4); put(p+188,(uint32_t)r->secondary,4);
    ssize_t n=send(c->fd,p,sizeof(p),MSG_DONTWAIT|MSG_NOSIGNAL);
    if(n!=(ssize_t)sizeof(p)) { ac_loss(c,n<0?errno:EIO); return c->error; }
    c->tx=r->sequence; return 0;
}
static int consume(struct ac_channel *, const struct ac_record *);
int ac_receive(struct ac_channel *c,struct ac_record *r) {
    unsigned char p[AC_FRAME_SIZE], expected[AC_FRAME_SIZE]={0};
    /* Room for maximum Linux SCM_RIGHTS batch. All installed FDs are closed
     * even on malformed frames; MSG_CMSG_CLOEXEC prevents exec races. */
    union { struct cmsghdr align; unsigned char bytes[CMSG_SPACE(256*sizeof(int))]; } control;
    struct iovec io={p,sizeof(p)}; struct msghdr m={0};
    m.msg_iov=&io; m.msg_iovlen=1; m.msg_control=control.bytes; m.msg_controllen=sizeof(control);
    ssize_t n=recvmsg(c->fd,&m,MSG_DONTWAIT|MSG_CMSG_CLOEXEC);
    if(n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) return EAGAIN;
    int ancillary=0;
    if(n>=0) for(struct cmsghdr *h=CMSG_FIRSTHDR(&m);h;h=CMSG_NXTHDR(&m,h)) {
        ancillary=1;
        if(h->cmsg_level==SOL_SOCKET && h->cmsg_type==SCM_RIGHTS && h->cmsg_len>=CMSG_LEN(0)) {
            size_t count=(h->cmsg_len-CMSG_LEN(0))/sizeof(int); int *fds=(int *)CMSG_DATA(h);
            for(size_t i=0;i<count;i++) if(close(fds[i])<0) ac_loss(c,errno);
        }
    }
    if(n!=AC_FRAME_SIZE || ancillary || (m.msg_flags&(MSG_TRUNC|MSG_CTRUNC))) {
        ac_loss(c,n<0?errno:n==0?EPIPE:EPROTO); return c->error;
    }
    binding(expected,&c->binding);
    if(get(p,4)!=0x53434131 || get(p+4,2)!=1 || get(p+6,2)!=AC_FRAME_SIZE ||
       get(p+8,4)<AC_INTENT || get(p+8,4)>AC_HIGHWATER ||
       memcmp(p+16,expected+16,100) || get(p+12,4) || get(p+116,4)) {
        ac_loss(c,EPROTO); return c->error;
    }
    for(unsigned i=192;i<AC_FRAME_SIZE;i++) if(p[i]) { ac_loss(c,EPROTO); return c->error; }
    uint64_t seq=get(p+120,8);
    if(c->rx==UINT64_MAX || seq!=c->rx+1) { ac_loss(c,EILSEQ); return c->error; }
    memset(r,0,sizeof(*r)); r->op=(uint32_t)get(p+8,4); r->sequence=seq;
    r->intent=get(p+128,8); r->task=get(p+136,8); r->process=get(p+144,8);
    r->creator=get(p+152,8); r->monotonic_ns=get(p+160,8);
    r->kind=(uint32_t)get(p+168,4); r->pid=(uint32_t)get(p+172,4); r->related=(uint32_t)get(p+176,4);
    r->result=(int32_t)get(p+180,4); r->error=(int32_t)get(p+184,4); r->secondary=(int32_t)get(p+188,4);
    if(!r->monotonic_ns || r->monotonic_ns<c->rx_monotonic || r->pid>INT_MAX || r->related>INT_MAX || r->error<0 || r->secondary<0) {
        ac_loss(c,EPROTO); return c->error;
    }
    c->rx=seq; c->rx_monotonic=r->monotonic_ns;
    if(r->op==AC_ACK) {
        if(r->intent<=c->acknowledged || r->intent>c->tx) { ac_loss(c,EPROTO); return c->error; }
        c->acknowledged=r->intent; /* Observation only. Never clears loss/slots. */
    }
    if(c->lost) return c->error;
    return consume(c,r);
}
/* Consumer mirrors only finite observations. It has no launch/journal API. */
static int consume(struct ac_channel *c,const struct ac_record *r) {
    if(r->op==AC_ACK) return 0;
    if(r->op==AC_HIGHWATER) { ac_loss(c,EPROTO); return c->error; }
    struct ac_slot *s=NULL, *intent=NULL, *free_slot=NULL;
    for(unsigned i=0;i<AC_CAPACITY;i++) {
        struct ac_slot *v=&c->slots[i];
        if(v->task==r->task && v->live) s=v;
        if(v->intent==r->intent && v->pending) intent=v;
        if(!v->live && !v->pending && !v->wait_pending) free_slot=v;
    }
    if(!r->task || !r->process || !r->creator || !r->intent || r->kind<AC_ROOT || r->kind>AC_RAW) goto bad;
    if(r->op==AC_INTENT) {
        if(r->intent!=r->sequence || (s && s->pending)) goto bad;
        if(!s) {
            if(!free_slot || r->task<=c->next_incarnation || r->kind==AC_GUEST || r->pid || r->creator!=c->binding.creator || r->process!=r->task) goto bad;
            s=free_slot; memset(s,0,sizeof(*s)); s->task=r->task; s->process=r->process;
            s->creator=r->creator; s->kind=r->kind; s->direct=1; s->live=1; s->wait_pending=1; s->native_intent=1;
            c->next_incarnation=r->task;
        }
        if(s->process!=r->process || s->creator!=r->creator || s->kind!=r->kind || s->pid!=(pid_t)r->pid) goto bad;
        s->native_intent=s->pid==0; s->intent=r->intent; s->pending=1; return 0;
    }
    if(r->op==AC_TASK) {
        if(!intent || s || !free_slot || r->task<=c->next_incarnation || r->creator!=intent->task || r->related!=(uint32_t)intent->pid || r->kind!=AC_GUEST || !r->pid || (r->process!=r->task && r->process!=intent->process)) goto bad;
        s=free_slot; memset(s,0,sizeof(*s)); s->task=r->task; s->process=r->process; s->creator=r->creator;
        s->intent=r->intent; s->pid=(pid_t)r->pid; s->kind=r->kind; s->live=1; s->wait_pending=1;
        intent->pending=0; c->next_incarnation=r->task; return 0;
    }
    if(!s || s->process!=r->process || s->creator!=r->creator || s->kind!=r->kind || s->intent!=r->intent) goto bad;
    if(r->op==AC_RESULT) {
        if(!s->pending) goto bad;
        if(s->native_intent) {
            if(r->result>0 && r->pid==(uint32_t)r->result) s->pid=(pid_t)r->pid;
            else if(r->result<0 && !r->pid) { s->live=0; s->wait_pending=0; }
            else goto bad;
        } else if(r->result>=0) goto bad;
        s->pending=0; return 0;
    }
    if(s->pid!=(pid_t)r->pid) goto bad;
    if(r->op==AC_WAIT || r->op==AC_PTRACE) {
        if((r->op==AC_WAIT)!=s->direct) goto bad;
        if(WIFEXITED(r->result)||WIFSIGNALED(r->result)) {
            if(s->pending) goto bad;
            s->live=0; s->wait_pending=s->direct?0:1;
        }
        return 0;
    }
    if(r->op==AC_EXEC) {
        if(!r->related) goto bad;
        for(unsigned i=0;i<AC_CAPACITY;i++) {
            struct ac_slot *v=&c->slots[i];
            if(v!=s && v->live && v->pid==(pid_t)r->related) {
                if(v->process!=s->process) goto bad;
                v->live=0; v->wait_pending=1;
            }
        }
        s->pid=(pid_t)r->related; return 0;
    }
    if(r->op==AC_RESUME) return 0;
bad:
    ac_loss(c,EPROTO); return c->error;
}
int ac_highwater(struct ac_channel *c) {
    if(c->lost || c->acknowledged!=c->tx) return EBUSY;
    for(unsigned i=0;i<AC_CAPACITY;i++) if(c->early[i].pid || c->slots[i].live || c->slots[i].pending || c->slots[i].wait_pending) return EBUSY;
    return 0; /* Local actor/FD observation only; never network or CLEAN. */
}
int ac_close_fds(const int *keep,size_t count,int *secondary) {
    int first=0; if(secondary) *secondary=0;
    DIR *d=opendir("/proc/self/fd"); if(!d) return errno;
    int own=dirfd(d); struct dirent *de;
    errno=0;
    while((de=readdir(d))) {
        char *end; long v=strtol(de->d_name,&end,10);
        if(!*de->d_name || *end || v<0 || v>INT_MAX || v==own) continue;
        size_t i=0; while(i<count && keep[i]!=(int)v) i++;
        if(i<count) continue;
        if(close((int)v)<0) { if(!first) first=errno; else if(secondary && !*secondary) *secondary=errno; }
        errno=0;
    }
    if(errno) { if(!first) first=errno; else if(secondary && !*secondary) *secondary=errno; }
    if(closedir(d)<0) { if(!first) first=errno; else if(secondary && !*secondary) *secondary=errno; }
    return first;
}
int ac_clone_policy(int clone3,uint64_t flags) {
    /* No reads of mutable clone_args: kernel fallback to legacy clone is safe. */
    if(clone3) return ENOSYS;
    return (flags & 0x00800000u)?EPERM:0; /* CLONE_UNTRACED */
}

static struct ac_channel *producer;
void ac_proot_fault(int e) { if(producer) ac_loss(producer,e); }
void ac_proot_bind(struct ac_channel *c) { if(!producer) producer=c; else ac_loss(producer,EALREADY); }
int ac_proot_require(void) { return !producer?ENOTCONN:producer->lost?producer->error:0; }
static struct ac_slot *slot(pid_t pid) {
    if(!producer) return NULL;
    for(unsigned i=0;i<AC_CAPACITY;i++) if(producer->slots[i].pid==pid && producer->slots[i].live) return &producer->slots[i];
    return NULL;
}
static struct ac_slot *allocate(pid_t pid,unsigned kind,int direct,uint64_t process) {
    if(ac_proot_require()) return NULL;
    if(slot(pid)) { ac_loss(producer,EEXIST); return NULL; }
    for(unsigned i=0;i<AC_CAPACITY;i++) if(!producer->slots[i].live && !producer->slots[i].pending && !producer->slots[i].wait_pending) {
        if(producer->next_incarnation==UINT64_MAX) break;
        struct ac_slot *s=&producer->slots[i]; memset(s,0,sizeof(*s));
        s->task=++producer->next_incarnation; s->process=process?process:s->task;
        s->creator=producer->binding.creator; s->pid=pid; s->kind=kind; s->direct=direct; s->live=1; s->wait_pending=1; return s;
    }
    ac_loss(producer,ENOSPC); return NULL;
}
static int emit(struct ac_slot *s,unsigned op,long result,pid_t related) {
    struct ac_record r={0}; r.op=op; r.result=(int32_t)result; r.related=(uint32_t)related;
    r.creator=producer->binding.creator;
    if(op==AC_RESULT && result<0 && result>=-INT_MAX) r.error=(int32_t)-result;
    r.secondary=producer->secondary;
    if(s) { r.creator=s->creator; r.task=s->task; r.process=s->process; r.intent=s->intent; r.kind=s->kind; r.pid=(uint32_t)s->pid; }
    return ac_send(producer,&r);
}
int ac_birth_enter(pid_t pid) {
    if(ac_proot_require()) return ac_proot_require();
    struct ac_slot *s=slot(pid);
    if(!s || s->pending) { ac_loss(producer,EPROTO); return EPROTO; }
    s->native_intent=0; s->pending=1; s->intent=producer->tx+1;
    return emit(s,AC_INTENT,0,0);
}
void ac_birth_result(pid_t pid,long result) {
    struct ac_slot *s=slot(pid);
    if(!s || !s->pending) return;
    /* Successful birth is resolved only by attach_child with exact identity. */
    if(result<0) { if(!emit(s,AC_RESULT,result,0)) s->pending=0; }
}
int ac_task(pid_t parent,pid_t pid,int thread) {
    if(ac_proot_require()) return ac_proot_require();
    struct ac_slot *p=slot(parent);
    if(!p || !p->pending) { ac_loss(producer,EPROTO); return EPROTO; }
    struct ac_slot *s=allocate(pid,AC_GUEST,0,thread?p->process:0);
    if(!s) return producer->error;
    s->intent=p->intent; s->creator=p->task;
    if(emit(s,AC_TASK,0,parent)) return producer->error;
    p->pending=0;
    /* Linux may report the child's initial stop before its creator event.
     * Hold those actual observations; never invent an unrelated Tracee. */
    for(unsigned i=0;i<AC_CAPACITY;i++) if(producer->early[i].pid==pid) {
        int status=producer->early[i].status; producer->early[i].pid=0;
        ac_observe(pid,status);
    }
    return producer->lost?producer->error:0;
}
pid_t ac_native_fork(unsigned kind) {
    int e=ac_proot_require(); if(e) { errno=e; return -1; }
    struct ac_slot *s=allocate(0,kind,1,0); if(!s) { errno=producer->error; return -1; }
    s->native_intent=1; s->pending=1; s->intent=producer->tx+1;
    if(emit(s,AC_INTENT,0,getpid())) { errno=producer->error; return -1; }
    pid_t pid=fork(); int saved=errno;
    if(pid==0) return 0; /* Caller closes branch-specific descriptors immediately. */
    if(pid<0) {
        if(!emit(s,AC_RESULT,-saved,0)) { s->pending=0; s->live=0; s->wait_pending=0; }
    } else {
        s->pid=pid;
        if(!emit(s,AC_RESULT,pid,0)) s->pending=0;
    }
    errno=saved; return pid;
}
int ac_child_fds(const int *keep,size_t count) {
    int second=0, e=ac_close_fds(keep,count,&second);
    /* Child must not write the parent's channel sequence, even on failure. */
    producer=NULL; return e?e:second;
}
int ac_helper_wait(pid_t pid,int status) {
    struct ac_slot *s=slot(pid);
    if(!s || !s->direct || (s->kind!=AC_SYSVIPC && s->kind!=AC_F2FS)) return 0;
    if(emit(s,AC_WAIT,status,1)==0 && (WIFEXITED(status)||WIFSIGNALED(status))) { s->live=0; s->wait_pending=0; }
    return 1;
}
void ac_observe(pid_t pid,int status) {
    if(!producer) return;
    struct ac_slot *s=slot(pid);
    if(!s) {
        for(unsigned i=0;i<AC_CAPACITY;i++) if(!producer->early[i].pid) {
            producer->early[i].pid=pid; producer->early[i].status=status; return;
        }
        ac_loss(producer,ENOSPC); return;
    }
    if(!emit(s,s->direct?AC_WAIT:AC_PTRACE,status,s->direct) && (WIFEXITED(status)||WIFSIGNALED(status))) {
        s->live=0; s->wait_pending=s->direct?0:1;
        if(s->pending) ac_loss(producer,EPROTO);
    }
}
pid_t ac_target_wait(pid_t pid,int *status,int options,int direct) {
    int e=ac_proot_require(); if(e) { errno=e; return -1; }
    pid_t result; do { result=waitpid(pid,status,options); } while(result<0 && errno==EINTR);
    if(result==pid) {
        if(direct) { if(!ac_helper_wait(pid,*status)) ac_loss(producer,EPROTO); }
        else ac_observe(pid,*status);
    } else if(result<0) ac_loss(producer,errno);
    return result;
}
long ac_resume(int request,pid_t pid,void *address,void *data) {
    int e=ac_proot_require(); if(e) { errno=e; return -1; }
    struct ac_slot *s=slot(pid);
    if(!s) { ac_loss(producer,ESRCH); errno=ESRCH; return -1; }
    if(emit(s,AC_RESUME,request,0)) { errno=producer->error; return -1; }
    long result=ptrace(request,pid,address,data);
    if(result<0) ac_loss(producer,errno);
    return result;
}
void ac_exec(pid_t oldpid,pid_t newpid) {
    if(!producer) return;
    struct ac_slot *s=slot(oldpid), *leader=slot(newpid);
    if(!s) { ac_loss(producer,ESRCH); return; }
    if(leader && leader!=s && leader->process!=s->process) {
        ac_loss(producer,ESTALE); return;
    }
    if(!emit(s,AC_EXEC,0,newpid)) {
        /* Kernel exec remaps the task identity but supplies no separate
         * real-parent terminal wait for displaced siblings. Retain that
         * obligation while allowing supported guest execution to continue. */
        if(leader && leader!=s) { leader->live=0; leader->wait_pending=1; }
        s->pid=newpid;
    }
}
