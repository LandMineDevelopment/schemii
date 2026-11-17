"""Actual native P2a storage on private, synthetic host fixtures.

Wrappers inject bounded faults into real file IO, never real app/boot state.
Python independently constructs/inspects the frozen wire with hashlib. The one
included translation unit exercises private SHA/budget edges without a public
production test seam. Separate hardened production-object controls follow.
No host result establishes Android durability, owner custody or admission.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import struct
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platforms/android/native/host"
STRICT = [
    "-std=c11",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-pedantic",
    "-fstack-protector-strong",
    "-D_FORTIFY_SOURCE=3",
]
WRAPS = [
    "write",
    "read",
    "fsync",
    "close",
    "linkat",
    "fstat",
    "fstatat",
    "pthread_key_create",
    "pthread_setspecific",
    "pthread_key_delete",
]
HARNESS = r"""
#define _GNU_SOURCE
#include "journal.c"
#include <stdio.h>
#include <signal.h>
#include <sys/wait.h>
#include <pthread.h>
static const char *fault="";
static int calls, syncs, reads, stats, ats, closes;
static char events[4096];
static size_t used;
static void event(char c) { if (used+1<sizeof(events)) events[used++]=c; }
static int has(const char *s) { return strstr(fault,s)!=NULL; }
ssize_t __real_write(int,const void *,size_t);
ssize_t __real_read(int,void *,size_t);
int __real_fsync(int);
int __real_close(int);
int __real_linkat(int,const char *,int,const char *,int);
int __real_fstat(int,struct stat *);
int __real_fstatat(int,const char *,struct stat *,int);
ssize_t __wrap_write(int fd,const void *p,size_t n) {
  event('W'); ++calls;
  if (has("pending")) { puts("PENDING"); fflush(stdout); for (;;) pause(); }
  if (has("eintr") && calls==1) { errno=EINTR; return -1; }
  if (has("zero")) return 0;
  if (has("unknown")) { ssize_t r=__real_write(fd,p,n); if (r!=(ssize_t)n) abort(); errno=EIO; return -1; }
  if (has("enospc") || has("error")) { errno=has("enospc") ? ENOSPC : EACCES; return -1; }
  if (has("torn") && calls>1) { errno=ENOSPC; return -1; }
  if ((has("short") || has("torn")) && n>7) n=7;
  return __real_write(fd,p,n);
}
ssize_t __wrap_read(int fd,void *p,size_t n) {
  ++reads;
  if (has("read_eintr") && reads==1) { errno=EINTR; return -1; }
  if (has("read_fail")) { errno=EIO; return -1; }
  if (has("read_short") && n>9) n=9;
  return __real_read(fd,p,n);
}
int __wrap_fsync(int fd) {
  event('S'); ++syncs;
  int target=0;
  if (has("sync1")) target=1;
  if (has("sync2")) target=2;
  if (has("sync3")) target=3;
  if (has("sync4")) target=4;
  if (syncs==target) { errno=EIO; return -1; }
  return __real_fsync(fd);
}
int __wrap_close(int fd) {
  event('C'); ++closes; int r=__real_close(fd);
  if (has("close") && closes==1) { errno=EIO; return -1; }
  return r;
}
int __wrap_linkat(int a,const char *b,int c,const char *d,int f) {
  event('L');
  if (has("publish")) { errno=EACCES; return -1; }
  if (has("link_race")) {
    if (renameat(a,b,a,"retained-raced-anchor")<0) abort();
    int fd=openat(a,b,O_WRONLY|O_CREAT|O_EXCL,0600);
    if (fd<0 || __real_close(fd)) abort();
  }
  int r=__real_linkat(a,b,c,d,f);
  if (has("link_unknown")) { errno=EIO; return -1; }
  return r;
}
int __wrap_fstat(int fd,struct stat *s) {
  ++stats; int r=__real_fstat(fd,s);
  if (has("stat_fail")) { errno=EIO; return -1; }
  if (!r && has("owner")) s->st_uid=geteuid()+1;
  return r;
}
int __wrap_fstatat(int fd,const char *n,struct stat *s,int f) {
  ++ats;
  if (has("statat_fail")) { errno=EACCES; return -1; }
  int r=__real_fstatat(fd,n,s,f);
  if (!r && has("path_race")) ++s->st_ino;
  return r;
}
int __real_pthread_key_create(pthread_key_t *,void (*)(void *));
int __real_pthread_setspecific(pthread_key_t,const void *);
int __real_pthread_key_delete(pthread_key_t);
int __wrap_pthread_key_create(pthread_key_t *key,void (*destroy)(void *)) {
  return has("key_pressure") ? EAGAIN : __real_pthread_key_create(key,destroy);
}
int __wrap_pthread_setspecific(pthread_key_t key,const void *value) {
  return has("key_binding") ? EACCES : __real_pthread_setspecific(key,value);
}
int __wrap_pthread_key_delete(pthread_key_t key) {
  int r=__real_pthread_key_delete(key);
  return has("key_delete_secondary") ? EPERM : has("key_delete") ? EIO : r;
}
static struct journal_binding binding(void) {
  struct journal_binding b; memset(&b,0,sizeof(b));
  memset(b.source,1,32); memset(b.contract,2,32);
  memset(b.storage_lineage,3,16); memset(b.install,4,16); memset(b.state,5,16);
  memset(b.run,6,16); memset(b.attempt,7,16); memset(b.boot,8,16);
  b.generation=1; b.known=255; b.storage=1; b.boot_kind=1;
  b.pg_major=17; b.durability=1; b.planned_slots=3; return b;
}
struct opened_thread { int dir; struct journal *journal; };
static void *initial_writer(void *arg) {
  struct opened_thread *o=arg;
  struct journal_binding b=binding();
  if (journal_open(o->dir,&b,&o->journal).status ||
      journal_initialize(o->journal).status || journal_active(o->journal,1).status) abort();
  return NULL;
}
static void *wrong_thread(void *j) { (void)journal_custody_check(j); return NULL; }
static void print_result(struct journal_result r) {
  printf("{\"status\":%d,\"stage\":%d,\"error\":%d,\"cleanup_error\":%d,"
         "\"effect\":%d,\"bytes\":%llu,\"prefix_bytes\":%llu,\"last_sequence\":%llu,"
         "\"sequence_known\":%u,\"loss_known\":%u}\n",
         r.status,r.stage,r.error,r.cleanup_error,r.effect,
         (unsigned long long)r.bytes,(unsigned long long)r.prefix_bytes,
         (unsigned long long)r.last_sequence,r.sequence_known,r.loss_known);
}
static void arm(const char *f) { fault=f; calls=syncs=reads=stats=ats=closes=0; used=0; memset(events,0,sizeof(events)); }
int main(int argc,char **argv) {
  if (argc<4) return 2;
  if (!strcmp(argv[2],"sha")) {
    uint8_t input[1000],digest[32]; size_t n=(size_t)strtoul(argv[3],NULL,10);
    if (n>1000) return 2;
    for (size_t i=0;i<n;++i) input[i]=(uint8_t)i;
    sha256(input,n,digest);
    /* Synthetic checksum test only, never journal identity output. */
    for (unsigned i=0;i<32;++i) printf("%02x",digest[i]);
    puts(""); return 0;
  }
  int dir=open(argv[1],O_RDONLY|O_DIRECTORY|O_CLOEXEC|O_NOFOLLOW);
  if (dir<0) return 3;
  struct journal_binding b=binding(); struct journal *j=NULL;
  if (!strcmp(argv[2],"binding")) {
    if (!strcmp(argv[3],"known")) b.known=0;
    if (!strcmp(argv[3],"zero")) memset(b.boot,0,16);
    if (!strcmp(argv[3],"storage")) b.storage=0;
    if (!strcmp(argv[3],"generation")) b.generation=0;
    if (!strcmp(argv[3],"kind")) b.boot_kind=3;
    if (!strcmp(argv[3],"pg")) b.pg_major=0;
    if (!strcmp(argv[3],"slots")) b.planned_slots=1;
    if (!strcmp(argv[3],"durability")) b.durability=0;
  }
  if (!strcmp(argv[2],"open")) arm(argv[3]);
  struct journal_result r=journal_open(dir,&b,&j);
  print_result(r);
  if (!j) { if (__real_close(dir)) abort(); return 0; }
  if (!strcmp(argv[2],"custody")) {
    print_result(journal_initialize(j));
    print_result(journal_active(j,1));
    print_result(journal_custody_check(j));
    if (!strcmp(argv[3],"owner-exit")) {
      print_result(journal_close(&j));
      /* The prior namespace is retained; use an explicitly NEW disposable one. */
      if (mkdirat(dir,"new-writer",0700)) abort();
      int next=openat(dir,"new-writer",O_RDONLY|O_DIRECTORY|O_CLOEXEC);
      struct opened_thread o={next,NULL}; pthread_t worker;
      if (next<0 || pthread_create(&worker,NULL,initial_writer,&o) || pthread_join(worker,NULL)) abort();
      j=o.journal;
      if (__real_close(next)) abort();
    } else if (!strcmp(argv[3],"thread")) {
      pthread_t worker;
      if (pthread_create(&worker,NULL,wrong_thread,j) || pthread_join(worker,NULL)) abort();
    } else if (!strcmp(argv[3],"reopen")) {
      print_result(journal_close(&j));
      print_result(journal_open(dir,&b,&j));
      if (!j) abort();
      print_result(journal_inspect(j));
    } else if (!strcmp(argv[3],"inspect")) print_result(journal_inspect(j));
    else if (!strcmp(argv[3],"cancel")) print_result(journal_cancel(j));
    else if (!strcmp(argv[3],"lease")) { if (flock(j->dir,LOCK_UN)) abort(); }
    else if (!strcmp(argv[3],"fork")) {
      pid_t child=fork(); if (child<0) abort();
      if (!child) _exit(journal_custody_check(j).status==JOURNAL_REFUSED ? 0 : 8);
      int status; if (waitpid(child,&status,0)!=child || status) abort();
    } else if (!strcmp(argv[3],"replace-anchor")) {
      if (renameat(dir,"anchor",dir,"retained-anchor")) abort();
      if (linkat(dir,"anchor.pending",dir,"anchor",0)) abort();
      /* Same original inode is continuous, unlike replacement bytes. */
    } else if (!strcmp(argv[3],"replace-essential")) {
      if (renameat(dir,"essential",dir,"retained-essential")) abort();
      int fd=openat(dir,"essential",O_WRONLY|O_CREAT|O_EXCL,0600);
      if (fd<0 || __real_close(fd)) abort();
    } else if (!strcmp(argv[3],"partial")) {
      int fd=openat(dir,"essential",O_WRONLY|O_APPEND);
      if (fd<0 || __real_write(fd,"x",1)!=1 || __real_close(fd)) abort();
    } else if (!strcmp(argv[3],"damage")) {
      int fd=openat(dir,"anchor",O_WRONLY);
      if (fd<0 || __real_write(fd,"x",1)!=1 || __real_close(fd)) abort();
    } else if (!strcmp(argv[3],"mode")) { if (fchmod(dir,0755)) abort(); }
    else if (!strcmp(argv[3],"error")) { arm("read_fail"); }
    else if (!strcmp(argv[3],"key-delete-after-error")) { arm("read_fail_key_delete_secondary"); }
    print_result(journal_custody_check(j));
    print_result(journal_custody_check(j));
  } else if (!strcmp(argv[2],"concurrent")) {
    struct journal *other=NULL; print_result(journal_open(dir,&b,&other));
    if (other) abort();
  } else if (!strcmp(argv[2],"inspect") || !strcmp(argv[2],"inspect_continue") || !strcmp(argv[2],"inspect_digest")) {
    arm(argv[3]); print_result(journal_inspect(j));
    if (!strcmp(argv[2],"inspect_continue")) print_result(journal_active(j,1));
    if (!strcmp(argv[2],"inspect_digest")) {
      struct journal_native_prefix prefix=journal_prefix(j);
      uint8_t expected[32];
      if (argc!=5 || strlen(argv[4])!=64) abort();
      for (unsigned i=0;i<32;++i) {
        unsigned byte; if (sscanf(argv[4]+i*2,"%2x",&byte)!=1) abort();
        expected[i]=(uint8_t)byte;
      }
      printf("{\"digest_matches\":%d}\n",prefix.known && !memcmp(prefix.digest,expected,32));
    }
  } else if (strcmp(argv[2],"open") && strcmp(argv[2],"binding")) {
    if (!strcmp(argv[2],"init_fault")) arm(argv[3]);
    r=journal_initialize(j); print_result(r);
    if (r.status==JOURNAL_OK && strcmp(argv[2],"init_fault")) {
      if (!strcmp(argv[2],"replace")) {
        if (renameat(dir,"essential",dir,"retained-essential")<0) abort();
        int f=openat(dir,"essential",O_WRONLY|O_CREAT|O_EXCL,0600);
        if (f<0 || __real_close(f)) abort();
      }
      if (!strcmp(argv[2],"mode_change")) { if (fchmod(dir,0755)) abort(); }
      if (!strcmp(argv[2],"pressure")) j->prefix.bytes=JOURNAL_MAX_BYTES;
      if (!strcmp(argv[2],"seq_overflow")) j->prefix.sequence=UINT64_MAX;
      int after_active=!strcmp(argv[2],"after_active_fault") ||
                       !strcmp(argv[2],"after_active_validate_fault");
      if (after_active) print_result(journal_active(j,1));
      if (!strcmp(argv[2],"after_active_validate_fault") &&
          !strcmp(argv[3],"mode_change")) { if (fchmod(dir,0755)) abort(); }
      arm(argv[3]);
      uint64_t seq=after_active ? 2 : 1;
      if (!strcmp(argv[2],"gap")) seq=2;
      if (!strcmp(argv[2],"duplicate")) seq=0;
      r=journal_active(j,seq); print_result(r);
      if (!strcmp(argv[2],"twice") && !r.status) print_result(journal_active(j,2));
      if (!strcmp(argv[2],"invalidate")) {
        print_result(journal_inspect(j)); print_result(journal_active(j,2));
      }
      if (!strcmp(argv[2],"active_fault")) print_result(journal_active(j,2));
      if (after_active) print_result(journal_active(j,3));
      if (!strcmp(argv[2],"after_active_validate_fault")) print_result(journal_inspect(j));
    }
  }
  fault=!strcmp(argv[3],"key-delete-after-error") ? "key_delete_secondary" : "";
  print_result(journal_close(&j));
  if (__real_close(dir)) abort();
  printf("{\"events\":\"%s\"}\n",events); return 0;
}
"""


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    compiler = shutil.which("cc")
    assert compiler, "strict host C compiler required"
    work = tmp_path_factory.mktemp("native-journal")
    harness = work / "harness.c"
    harness.write_text(HARNESS)
    binary = work / "journal"
    subprocess.run(
        [
            compiler,
            *STRICT,
            "-O2",
            "-pthread",
            "-I",
            str(SOURCE),
            str(harness),
            *[f"-Wl,--wrap={name}" for name in WRAPS],
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    return binary


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


def run(native, store, operation="twice", fault=""):
    result = subprocess.run(
        [str(native), str(store), operation, fault],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return [json.loads(line) for line in result.stdout.splitlines()]


def payload():
    return (
        b"\1" * 32
        + b"\2" * 32
        + b"".join(bytes([n]) * 16 for n in range(3, 9))
        + struct.pack(">QIBBHII", 1, 255, 1, 1, 17, 1, 3)
        + bytes(8)
    )


def frame(seq, previous, kind=None, data=None):
    data = payload() if data is None else data
    header = struct.pack(">8sHHIQ", b"SCHJNL01", 1, kind or (2 if seq else 1), 320, seq)
    body = header + previous + hashlib.sha256(data).digest() + data + b"SJCOMMIT"
    assert len(body) == 288
    return body + hashlib.sha256(body).digest()


def fixture_chain(store, count=2):
    anchor = frame(0, bytes(32))
    (store / "anchor").write_bytes(anchor)
    (store / "anchor").chmod(0o600)
    previous = hashlib.sha256(anchor).digest()
    records = []
    for seq in range(1, count + 1):
        record = frame(seq, previous)
        records.append(record)
        previous = hashlib.sha256(record).digest()
    (store / "essential").write_bytes(b"".join(records))
    (store / "essential").chmod(0o600)
    return anchor, records


@pytest.mark.parametrize(
    "size", [0, 1, 3, 55, 56, 63, 64, 65, 119, 120, 192, 288, 320, 1000]
)
def test_sha_independent_known_answers(native, store, size):
    result = subprocess.check_output(
        [str(native), str(store), "sha", str(size)], text=True
    )
    assert (
        result.strip()
        == hashlib.sha256(bytes(i % 256 for i in range(size))).hexdigest()
    )


def test_real_publication_order_and_exact_bytes(native, store):
    rows = run(native, store)
    assert all(row["status"] == 0 for row in rows[:-1])
    assert rows[1]["effect"] == rows[2]["effect"] == rows[3]["effect"] == 5
    anchor = frame(0, bytes(32))
    first = frame(1, hashlib.sha256(anchor).digest())
    second = frame(2, hashlib.sha256(first).digest())
    assert (
        (store / "anchor").read_bytes()
        == (store / "anchor.pending").read_bytes()
        == anchor
    )
    assert (store / "essential").read_bytes() == first + second
    assert rows[-1]["events"] == "WSCSWSCS C".replace(
        " ", ""
    )  # two appends + lease close
    assert rows[3]["prefix_bytes"] == 640 and rows[3]["last_sequence"] == 2
    assert (store / "anchor").stat().st_ino == (store / "anchor.pending").stat().st_ino
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in store.iterdir())


def test_anchor_publication_order(native, store):
    rows = run(native, store, "init_fault")
    assert rows[1]["effect"] == 5
    assert rows[-1]["events"] == "WSCLS SCSC".replace(" ", "")


@pytest.mark.parametrize("fault", ["short", "eintr", "short_eintr"])
def test_complete_write_and_interrupt(native, store, fault):
    rows = run(native, store, "active_fault", fault)
    assert rows[2]["status"] == 0 and rows[2]["effect"] == 5
    anchor = (store / "anchor").read_bytes()
    first = frame(1, hashlib.sha256(anchor).digest())
    assert (store / "essential").read_bytes() == first + frame(
        2, hashlib.sha256(first).digest()
    )


@pytest.mark.parametrize(
    "fault,effect,stage",
    [
        ("error", 4, 6),
        ("enospc", 4, 6),
        ("zero", 2, 6),
        ("torn", 3, 6),
        ("unknown", 4, 6),
        ("sync1", 4, 7),
        ("sync2", 4, 9),
        ("close", 4, 10),
        ("error_close", 4, 6),
        ("path_race", 0, 3),
    ],
)
def test_append_failure_retains_first_failure_and_bytes(
    native, store, fault, effect, stage
):
    rows = run(native, store, "active_fault", fault)
    failed = rows[2]
    assert (
        failed["status"] != 0
        and failed["effect"] == effect
        and failed["stage"] == stage
    )
    assert rows[3] == failed  # fenced, no retry of transaction
    assert (store / "anchor").read_bytes() == frame(0, bytes(32))
    actual = (store / "essential").read_bytes()
    expected = frame(1, hashlib.sha256((store / "anchor").read_bytes()).digest())
    assert expected.startswith(actual) and len(actual) in [0, 7, 320]
    assert failed["prefix_bytes"] == failed["last_sequence"] == 0
    if fault == "error_close":
        assert failed["error"] == 13 and failed["cleanup_error"] == 5
    if fault in ["unknown", "sync1", "sync2", "close"]:
        assert len(actual) == 320
        reloaded = run(native, store, "inspect")[1]
        assert reloaded["status"] == 0 and reloaded["effect"] == 0
        assert reloaded["last_sequence"] == 1  # bytes never reconstruct ACK


@pytest.mark.parametrize(
    "fault",
    [
        "torn",
        "error_close",
        "sync1",
        "sync2",
        "sync3",
        "sync4",
        "publish",
        "link_unknown",
        "close",
        "link_race",
    ],
)
def test_initial_publication_failure_preserves_artifacts(native, store, fault):
    rows = run(native, store, "init_fault", fault)
    assert rows[1]["status"] != 0 and rows[1]["effect"] != 5
    before = {p.name: p.read_bytes() for p in store.iterdir()}
    assert before
    again = run(native, store, "init_fault")[1]
    assert again["status"] != 0
    assert before == {p.name: p.read_bytes() for p in store.iterdir()}


@pytest.mark.parametrize(
    "operation,status",
    [("gap", 6), ("duplicate", 7), ("seq_overflow", 9), ("pressure", 11)],
)
def test_prewrite_fences_no_deletion(native, store, operation, status):
    rows = run(native, store, operation)
    assert rows[2]["status"] == status and rows[2]["effect"] == 0
    assert (store / "essential").read_bytes() == b""
    assert (store / "anchor").read_bytes() == frame(0, bytes(32))


@pytest.mark.parametrize(
    "field",
    ["known", "zero", "storage", "generation", "kind", "pg", "slots", "durability"],
)
def test_required_binding_fields(native, store, field):
    assert run(native, store, "binding", field)[0]["status"] == 2
    assert not list(store.iterdir())


@pytest.mark.parametrize("fault", ["", "read_eintr", "read_short"])
def test_independent_prefix_reload_and_invalidation(native, store, fault):
    fixture_chain(store)
    rows = run(native, store, "inspect_continue", fault)
    assert rows[1]["status"] == 0 and rows[1]["effect"] == 0
    assert rows[1]["last_sequence"] == 2 and rows[1]["prefix_bytes"] == 640
    assert rows[1]["sequence_known"] == 1 and rows[1]["loss_known"] == 0
    assert rows[2]["status"] == 2 and rows[2]["effect"] == 0


def test_same_handle_inspection_invalidates_observed_commit(native, store):
    rows = run(native, store, "invalidate")
    assert rows[2]["effect"] == 5
    assert rows[3]["effect"] == 0 and rows[3]["status"] == 0
    assert rows[4]["status"] == 2


@pytest.mark.parametrize(
    "damage,status",
    [
        ("torn", 4),
        ("payload", 5),
        ("trailer", 5),
        ("previous", 5),
        ("digest", 5),
        ("gap", 6),
        ("duplicate", 7),
        ("conflict", 7),
        ("kind", 8),
        ("version", 8),
        ("length", 5),
        ("overflow", 9),
        ("lineage", 10),
        ("source", 10),
        ("contract", 10),
    ],
)
def test_prefix_damage_classified_without_repair(native, store, damage, status):
    _, records = fixture_chain(store, 1)
    previous = hashlib.sha256(records[0]).digest()
    tail = bytearray(frame(2, previous))
    if damage == "torn":
        tail = tail[:71]
    if damage == "payload":
        tail[100] ^= 1
    if damage == "trailer":
        tail[280] ^= 1
    if damage == "previous":
        tail[24] ^= 1
    if damage == "digest":
        tail[300] ^= 1
    if damage == "gap":
        tail = bytearray(frame(3, previous))
    if damage == "duplicate":
        tail = bytearray(records[0])
    if damage == "conflict":
        tail = bytearray(frame(1, previous, data=bytes([1]) * 192))
    if damage == "kind":
        tail[11] = 3
    if damage == "version":
        tail[9] = 2
    if damage == "length":
        tail[12:16] = struct.pack(">I", 319)
    if damage == "overflow":
        tail[12:16] = struct.pack(">I", 4097)
    if damage in ["lineage", "source", "contract"]:
        data = bytearray(payload())
        data[{"lineage": 64, "source": 0, "contract": 32}[damage]] ^= 1
        tail = bytearray(frame(2, previous, data=data))
    original = records[0] + tail
    (store / "essential").write_bytes(original)
    row = run(native, store, "inspect")[1]
    assert row["status"] == status and row["effect"] == 0
    assert row["prefix_bytes"] == 320 and row["last_sequence"] == 1
    assert (store / "essential").read_bytes() == original


@pytest.mark.parametrize("size", [0, 1, 319, 321])
def test_anchor_torn_or_extra(native, store, size):
    anchor, _ = fixture_chain(store)
    (store / "anchor").write_bytes((anchor + b"x")[:size])
    row = run(native, store, "inspect")[1]
    assert row["status"] in [4, 5] and row["sequence_known"] == 0


def test_missing_is_not_fresh(native, store):
    row = run(native, store, "inspect_continue")[1]
    assert row["status"] == 1 and row["effect"] == 0 and not list(store.iterdir())


def test_writer_serialization(native, store):
    rows = run(native, store, "concurrent")
    assert rows[1]["status"] == 2 and rows[1]["stage"] == 2
    # flock releases on actual descriptor close, no stale lockfile deletion.
    assert run(native, store, "open")[0]["status"] == 0


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o1700])
def test_directory_modes(native, store, mode):
    store.chmod(mode)
    assert run(native, store, "open")[0]["status"] == 2


@pytest.mark.parametrize("fault", ["owner", "stat_fail"])
def test_metadata_failures(native, store, fault):
    assert run(native, store, "open", fault)[0]["status"] != 0


@pytest.mark.parametrize("name", ["anchor", "essential", "anchor.pending"])
@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "mode", "hardlink"])
def test_existing_names_never_replaced_or_followed(native, store, name, kind):
    target = store / name
    if kind == "symlink":
        target.symlink_to("never-follow")
    elif kind == "fifo":
        os.mkfifo(target, 0o600)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    else:
        target.write_bytes(b"retained")
        target.chmod(0o644 if kind == "mode" else 0o600)
        if kind == "hardlink":
            os.link(target, store / "peer")
    before = target.lstat()
    assert run(native, store, "init_fault")[1]["status"] != 0
    assert target.lstat().st_ino == before.st_ino
    if kind in ["mode", "hardlink"]:
        assert target.read_bytes() == b"retained"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "mode", "hardlink"])
def test_reload_file_validation(native, store, kind):
    fixture_chain(store)
    target = store / "essential"
    target.rename(store / "retained")
    if kind == "symlink":
        target.symlink_to("retained")
    elif kind == "fifo":
        os.mkfifo(target, 0o600)
    elif kind == "directory":
        target.mkdir(mode=0o700)
    elif kind == "hardlink":
        os.link(store / "retained", target)
    else:
        target.write_bytes(b"")
        target.chmod(0o644)
    row = run(native, store, "inspect")[1]
    assert row["status"] != 0
    assert (store / "retained").stat().st_size == 640


@pytest.mark.parametrize("operation", ["replace", "mode_change"])
def test_live_fixture_path_and_mode_changes_fence(native, store, operation):
    assert run(native, store, operation)[2]["status"] == 2
    assert (store / "anchor").read_bytes() == frame(0, bytes(32))


def test_read_error_retains_prefix(native, store):
    fixture_chain(store)
    assert run(native, store, "inspect", "read_fail")[1]["status"] == 3


def test_pending_has_no_ack_and_reopen_is_structural(native, store):
    process = subprocess.Popen(
        [str(native), str(store), "active_fault", "pending"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready
        # fflush in wrapper publishes open/init scalars followed by pending.
        assert json.loads(process.stdout.readline())["status"] == 0
        assert json.loads(process.stdout.readline())["effect"] == 5
        assert process.stdout.readline() == b"PENDING\n"
        assert process.poll() is None
        assert (store / "essential").read_bytes() == b""
    finally:
        process.terminate()
        process.communicate(timeout=5)
    assert run(native, store, "inspect")[1]["effect"] == 0


def test_real_bounded_prefix_pressure_preserves_all_bytes(native, store):
    anchor, _ = fixture_chain(store, 0)
    previous = hashlib.sha256(anchor).digest()
    path = store / "essential"
    # 16MiB + one record, finite host input. No 52k fsync stress campaign.
    with path.open("wb") as stream:
        for seq in range(1, 16 * 1024 * 1024 // 320 + 2):
            record = frame(seq, previous)
            stream.write(record)
            previous = hashlib.sha256(record).digest()
    before = hashlib.sha256(path.read_bytes()).digest()
    row = run(native, store, "inspect")[1]
    assert row["status"] == 11 and row["prefix_bytes"] == 16 * 1024 * 1024 // 320 * 320
    assert hashlib.sha256(path.read_bytes()).digest() == before


@pytest.mark.parametrize("optimization", ["-O1", "-O2"])
def test_hardened_production_object_has_no_trust_or_test_dependencies(
    tmp_path, optimization
):
    obj = tmp_path / "journal.o"
    subprocess.run(
        ["cc", *STRICT, optimization, "-c", str(SOURCE / "journal.c"), "-o", str(obj)],
        check=True,
        capture_output=True,
    )
    symbols = subprocess.check_output(["nm", "-g", str(obj)], text=True)
    exports = {line.split()[-1] for line in symbols.splitlines() if " T " in line}
    assert exports == {
        "journal_open",
        "journal_initialize",
        "journal_active",
        "journal_inspect",
        "journal_prefix",
        "journal_close",
        "journal_cancel",
        "journal_custody_check",
    }
    for forbidden in [
        "boot_compare",
        "boot_identity",
        "__wrap",
        "printf",
        "crypto",
        "JNI",
        "getenv",
        "unlink",
        "truncate",
    ]:
        assert forbidden not in symbols
    assert "__stack_chk_fail" in symbols


@pytest.mark.parametrize("fault", ["torn", "unknown", "sync1", "sync2", "error_close"])
def test_append_after_prior_active_preserves_prefix(native, store, fault):
    rows = run(native, store, "after_active_fault", fault)
    assert rows[2]["effect"] == 5
    failed = rows[3]
    assert failed["status"] != 0 and failed["effect"] != 5
    assert rows[4] == failed
    assert failed["prefix_bytes"] == 320 and failed["last_sequence"] == 1
    anchor = frame(0, bytes(32))
    first = frame(1, hashlib.sha256(anchor).digest())
    assert (store / "anchor").read_bytes() == anchor
    assert (store / "essential").read_bytes().startswith(first)
    inspected = run(native, store, "inspect")[1]
    assert inspected["effect"] == 0
    assert inspected["prefix_bytes"] == (
        640 if fault in ["unknown", "sync1", "sync2"] else 320
    )


@pytest.mark.parametrize(
    ("fault", "status", "error"),
    [("mode_change", 2, 0), ("stat_fail", 3, errno.EIO), ("owner", 2, 0)],
)
def test_directory_validation_after_prior_active_retains_diagnostics(
    native, store, fault, status, error
):
    rows = run(native, store, "after_active_validate_fault", fault)
    assert rows[2]["effect"] == 5
    failed = rows[3]
    assert failed["status"] == status and failed["stage"] == 3
    assert failed["error"] == error and failed["cleanup_error"] == 0
    assert failed["effect"] == failed["bytes"] == failed["loss_known"] == 0
    assert failed["prefix_bytes"] == 320 and failed["last_sequence"] == 1
    assert failed["sequence_known"] == 1
    # Repeat, same-handle inspection and checked close retain the first failure.
    assert rows[4] == rows[5] == rows[6] == failed
    anchor = frame(0, bytes(32))
    assert (store / "anchor").read_bytes() == anchor
    assert (store / "essential").read_bytes() == frame(
        1, hashlib.sha256(anchor).digest()
    )


def test_native_prefix_digest_matches_independent_oracle(native, store):
    _, records = fixture_chain(store)
    expected = hashlib.sha256(records[-1]).hexdigest()
    result = subprocess.check_output(
        [str(native), str(store), "inspect_digest", "", expected], text=True
    )
    assert json.loads(result.splitlines()[2]) == {"digest_matches": 1}


def test_initial_namespace_stat_failure_preserves_errno(native, store):
    row = run(native, store, "init_fault", "statat_fail")[1]
    assert row["status"] == 3 and row["stage"] == 3 and row["error"] == 13
    assert row["effect"] == 0 and not list(store.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        "",
        "fork",
        "inspect",
        "cancel",
        "lease",
        "replace-essential",
        "thread",
        "owner-exit",
        "reopen",
        "partial",
        "damage",
        "mode",
        "error",
    ],
)
def test_uninterrupted_custody_is_live_handle_only(native, store, change):
    rows = run(native, store, "custody", change)
    assert rows[3]["status"] == 0 and rows[3]["effect"] == 5
    tail = rows[-4:-1] if change in ("inspect", "cancel") else rows[-3:-1]
    checked = tail[-1]
    if change in ("", "fork"):
        assert checked["status"] == 0 and checked["effect"] == 5
    else:
        assert checked["status"] != 0 and checked["effect"] != 5
        assert rows[-3] == rows[-2]  # original refusal retained, no retry/repair
    assert (store / "essential").exists() and (store / "anchor.pending").exists()


@pytest.mark.parametrize(
    "fault,error,cleanup",
    [
        ("key_pressure", errno.EAGAIN, 0),
        ("key_binding", errno.EACCES, 0),
        ("key_binding_key_delete_close", errno.EACCES, errno.EIO),
    ],
)
def test_native_thread_binding_failure_has_no_writer_or_state(
    native, store, fault, error, cleanup
):
    first = run(native, store, "open", fault)[0]
    assert first["status"] == 3 and first["stage"] == 4
    assert first["error"] == error and first["cleanup_error"] == cleanup
    assert not list(store.iterdir())


def test_tls_close_retains_secondary_error_after_original_read_failure(native, store):
    rows = run(native, store, "custody", "key-delete-after-error")
    first, closed = rows[-3], rows[-2]
    assert first["status"] == 3 and first["error"] == errno.EIO
    assert first["cleanup_error"] == 0
    assert closed["status"] == first["status"] and closed["error"] == errno.EIO
    assert closed["cleanup_error"] == errno.EPERM
    assert (store / "essential").stat().st_size == 320
