#define _GNU_SOURCE
#include "owner.h"
#include "boot_compare.h"
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

struct apk_owner {
    int dirfd, sockets[2];
    pthread_t worker;
    pthread_mutex_t lock;
    atomic_int busy, cancel, finished;
    uint32_t sent, received;
    struct apk_owner_status status;
};
struct envelope { uint32_t magic, version, sequence, operation; };
#define MAGIC 0x53434831u
static void project(struct apk_owner *o, int phase, int reason, int error) {
    struct timespec now;
    int clock_ok=clock_gettime(CLOCK_MONOTONIC,&now)==0;
    pthread_mutex_lock(&o->lock);
    o->status.sequence++;
    o->status.monotonic_ns=clock_ok ? (uint64_t)now.tv_sec*1000000000u+(uint64_t)now.tv_nsec : 0;
    o->status.phase=phase; o->status.reason=reason;
    if (!o->status.error) o->status.error=error;
    pthread_mutex_unlock(&o->lock);
}
static void *serve(void *arg) {
    struct apk_owner *o=arg;
    for (;;) {
        struct envelope frame;
        struct iovec io={&frame,sizeof(frame)};
        struct msghdr msg; memset(&msg,0,sizeof(msg)); msg.msg_iov=&io; msg.msg_iovlen=1;
        ssize_t n=recvmsg(o->sockets[1],&msg,0);
        if (!n) break;
        if (n!=(ssize_t)sizeof(frame) || (msg.msg_flags&(MSG_TRUNC|MSG_CTRUNC)) ||
            frame.magic!=MAGIC || frame.version!=1 || frame.sequence!=o->received+1 ||
            frame.operation<1 || frame.operation>3) {
            project(o,APK_REFUSED,APK_PROTOCOL,n<0?errno:EPROTO); break;
        }
        o->received=frame.sequence;
        if (frame.operation==3) break;
        if (frame.operation==2 || atomic_load(&o->cancel)) {
            project(o,APK_REFUSED,APK_CANCELLED,0);
        } else {
            project(o,APK_PENDING,APK_NONE,0);
            /* No journal initialization from empty directory, supplied flags,
             * parsed bytes or UID. R is the actual source-backed reader, but
             * absent C/A remains UNKNOWN. No managed fork can precede this gate.
             * Existing journal bytes remain untouched for independent diagnosis.
             */
            struct boot_compare_result b=boot_compare_current(NULL);
            pthread_mutex_lock(&o->lock);
            o->status.boot_relation=b.relation; o->status.boot_reason=b.reason;
            o->status.cleanup_error=b.cleanup_error;
            pthread_mutex_unlock(&o->lock);
            project(o,APK_REFUSED,APK_BOOT_AUTHORITY,b.error>0?b.error:0);
        }
        atomic_store(&o->busy,0);
    }
    atomic_store(&o->finished,1);
    return NULL;
}
int apk_owner_open(int borrowed,struct apk_owner **out) {
    if (!out) return EINVAL;
    *out=NULL;
    struct stat st;
    if (fstat(borrowed,&st)<0) return errno;
    if (!S_ISDIR(st.st_mode) || st.st_uid!=geteuid() || (st.st_mode&0777)!=0700) return EACCES;
    int dir=openat(borrowed,".",O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
    if (dir<0) return errno;
    if (flock(dir,LOCK_EX|LOCK_NB)<0) { int e=errno; close(dir); return e; }
    struct apk_owner *o=calloc(1,sizeof(*o));
    if (!o) { close(dir); return ENOMEM; }
    o->dirfd=dir;
    if (socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,o->sockets)<0) {
        int e=errno; close(dir); free(o); return e;
    }
    int e=pthread_mutex_init(&o->lock,NULL);
    if (e) { close(o->sockets[0]); close(o->sockets[1]); close(dir); free(o); return e; }
    o->status.boot_relation=BOOT_COMPARE_UNKNOWN;
    o->status.boot_reason=BOOT_COMPARE_CALLER_UNPROVED;
    o->status.journal_status=-1; /* Not observed; never JOURNAL_OK. */
    o->status.journal_effect=0; /* NOT_ATTEMPTED; no durable ACK invented. */
    e=pthread_create(&o->worker,NULL,serve,o);
    if (e) { pthread_mutex_destroy(&o->lock); close(o->sockets[0]); close(o->sockets[1]); close(dir); free(o); return e; }
    *out=o; return 0;
}
int apk_owner_request(struct apk_owner *o,uint32_t operation) {
    if (!o || operation<1 || operation>2) return EINVAL;
    if (operation==2) atomic_store(&o->cancel,1);
    if (atomic_exchange(&o->busy,1)) return EBUSY;
    if (atomic_load(&o->finished) || o->sent==UINT32_MAX) { atomic_store(&o->busy,0); return EPIPE; }
    struct envelope frame={MAGIC,1,++o->sent,operation};
    ssize_t n=send(o->sockets[0],&frame,sizeof(frame),MSG_DONTWAIT|MSG_NOSIGNAL);
    if (n!=(ssize_t)sizeof(frame)) { int e=n<0?errno:EIO; project(o,APK_REFUSED,APK_PROTOCOL,e); atomic_store(&o->busy,0); return e; }
    return 0;
}
struct apk_owner_status apk_owner_status(struct apk_owner *o) {
    struct apk_owner_status s; memset(&s,0,sizeof(s));
    if (!o) { s.phase=APK_REFUSED; s.reason=APK_STORAGE; s.error=EINVAL; return s; }
    pthread_mutex_lock(&o->lock); s=o->status; pthread_mutex_unlock(&o->lock);
    if (atomic_load(&o->busy)) s.phase=APK_PENDING;
    return s;
}
int apk_owner_close(struct apk_owner **owner) {
    if (!owner || !*owner) return EINVAL;
    struct apk_owner *o=*owner;
    if (atomic_load(&o->busy)) return EBUSY;
    if (!atomic_load(&o->finished)) {
        if (o->sent==UINT32_MAX) return EOVERFLOW;
        struct envelope frame={MAGIC,1,++o->sent,3};
        if (send(o->sockets[0],&frame,sizeof(frame),MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof(frame)) return errno?errno:EIO;
    }
    int e=pthread_join(o->worker,NULL); if (e) return e;
    for (int i=0;i<2;i++) if (close(o->sockets[i])<0 && !e) e=errno;
    if (close(o->dirfd)<0 && !e) e=errno;
    pthread_mutex_destroy(&o->lock); memset(o,0,sizeof(*o)); free(o); *owner=NULL; return e;
}
