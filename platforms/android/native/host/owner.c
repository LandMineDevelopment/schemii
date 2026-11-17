#define _GNU_SOURCE
#include "owner.h"
#include "boot_authority.h"
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
    int sockets[2], actors[2];
    struct boot_authority *authority;
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
            struct boot_compare_result b=boot_authority_compare(o->authority);
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
static void close_once(int fd, struct apk_owner_errors *errors) {
    if (fd<0) return;
    if (close(fd)<0) {
        int e=errno;
        errors->close_failures++;
        if (!errors->close_error) errors->close_error=e;
        else if (!errors->secondary_close_error) errors->secondary_close_error=e;
    }
}
static void merge_custody(struct apk_owner_errors *errors, const struct boot_authority_errors *custody) {
    const int extra[]={custody->close_error,custody->secondary_close_error};
    for (unsigned i=0;i<2;i++) {
        if (!extra[i]) continue;
        if (!errors->close_error) errors->close_error=extra[i];
        else if (!errors->secondary_close_error) errors->secondary_close_error=extra[i];
    }
    errors->close_failures+=custody->close_failures;
}
int apk_owner_open_diagnostic(int borrowed,struct apk_owner **out,struct apk_owner_errors *report) {
    struct apk_owner_errors local={0};
    struct apk_owner_errors *errors=report?report:&local;
    memset(errors,0,sizeof(*errors));
    if (!out) { errors->error=EINVAL; return EINVAL; }
    *out=NULL;
    struct boot_authority *authority=NULL;
    struct boot_authority_errors custody={0};
    errors->error=boot_authority_open(borrowed,&authority,&custody);
    if (errors->error) {
        merge_custody(errors,&custody);
        return errors->error;
    }
    struct apk_owner *o=NULL;
    int sockets[2]={-1,-1}, actors[2]={-1,-1};
    int mutex_ready=0;
    o=calloc(1,sizeof(*o));
    if (!o) { errors->error=ENOMEM; goto failed; }
    if (socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0,sockets)<0) {
        errors->error=errno; goto failed;
    }
    /* Reserve the separate actor pair before starting the worker. Neither end
     * is exported to Java/Python/PRoot; C/A and launch remain absent. */
    if (socketpair(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC|SOCK_NONBLOCK,0,actors)<0) {
        errors->error=errno; goto failed;
    }
    o->authority=authority; memcpy(o->sockets,sockets,sizeof(sockets)); memcpy(o->actors,actors,sizeof(actors));
    errors->error=pthread_mutex_init(&o->lock,NULL);
    if (errors->error) goto failed;
    mutex_ready=1;
    o->status.boot_relation=BOOT_COMPARE_UNKNOWN;
    o->status.boot_reason=BOOT_COMPARE_CALLER_UNPROVED;
    o->status.journal_status=-1;
    o->status.journal_effect=0;
    errors->error=pthread_create(&o->worker,NULL,serve,o);
    if (errors->error) goto failed;
    *out=o; return 0;
failed:
    if (mutex_ready) pthread_mutex_destroy(&o->lock);
    for (int i=0;i<2;i++) { close_once(actors[i],errors); close_once(sockets[i],errors); }
    (void)boot_authority_close(&authority,&custody);
    merge_custody(errors,&custody);
    free(o);
    return errors->error;
}
int apk_owner_open(int borrowed,struct apk_owner **out) {
    return apk_owner_open_diagnostic(borrowed,out,NULL);
}
int apk_owner_request(struct apk_owner *o,uint32_t operation) {
    if (!o || operation<1 || operation>2) return EINVAL;
    if (operation==2) { atomic_store(&o->cancel,1); boot_authority_cancel(o->authority); }
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
int apk_owner_close_diagnostic(struct apk_owner **owner,struct apk_owner_errors *report) {
    struct apk_owner_errors local={0};
    struct apk_owner_errors *errors=report?report:&local;
    memset(errors,0,sizeof(*errors));
    if (!owner || !*owner) { errors->error=EINVAL; return EINVAL; }
    struct apk_owner *o=*owner;
    if (atomic_load(&o->busy)) { errors->error=EBUSY; return EBUSY; }
    if (!atomic_load(&o->finished)) {
        if (o->sent==UINT32_MAX) { errors->error=EOVERFLOW; return EOVERFLOW; }
        struct envelope frame={MAGIC,1,++o->sent,3};
        if (send(o->sockets[0],&frame,sizeof(frame),MSG_DONTWAIT|MSG_NOSIGNAL)!=(ssize_t)sizeof(frame)) {
            errors->error=errno?errno:EIO; return errors->error;
        }
    }
    int e=pthread_join(o->worker,NULL); if (e) { errors->error=e; return e; }
    for (int i=0;i<2;i++) { close_once(o->actors[i],errors); close_once(o->sockets[i],errors); }
    struct boot_authority_errors custody={0};
    errors->error=boot_authority_close(&o->authority,&custody);
    merge_custody(errors,&custody);
    pthread_mutex_destroy(&o->lock); memset(o,0,sizeof(*o)); free(o); *owner=NULL;
    if (!errors->error) errors->error=errors->close_error;
    return errors->error;
}

int apk_owner_close(struct apk_owner **owner) {
    return apk_owner_close_diagnostic(owner,NULL);
}
