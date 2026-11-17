#define _GNU_SOURCE
#include "installed_provenance.h"
#include <errno.h>
#include <stdio.h>
#include <elf.h>
#include <sys/sysmacros.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
static uint32_t be32(const uint8_t *p) {
  return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 |
         p[3];
}
static void put32(uint8_t *p, uint32_t n) {
  p[0] = (uint8_t)(n >> 24);
  p[1] = (uint8_t)(n >> 16);
  p[2] = (uint8_t)(n >> 8);
  p[3] = (uint8_t)n;
}
static void put64(uint8_t *p, uint64_t n) {
  put32(p, (uint32_t)(n >> 32));
  put32(p + 4, (uint32_t)n);
}
/* Minimal FIPS 180-4 SHA-256 compression, bounded in-memory input only.
 * No library, key scheme, dynamic crypto dependency or exported checksum API.
 */
static uint32_t rotr(uint32_t x, unsigned n) { return x >> n | x << (32 - n); }
void installed_sha256(const uint8_t *data, size_t len, uint8_t out[32]) {
  static const uint32_t k[64] = {
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
  uint32_t h[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                   0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
  size_t blocks = (len + 9 + 63) / 64;
  for (size_t b = 0; b < blocks; ++b) {
    uint8_t buf[64] = {0};
    uint32_t w[64];
    for (size_t i = 0; i < 64; ++i) {
      size_t pos = b * 64 + i;
      if (pos < len)
        buf[i] = data[pos];
      else if (pos == len)
        buf[i] = 0x80;
    }
    if (b + 1 == blocks)
      put64(buf + 56, (uint64_t)len * 8);
    for (unsigned i = 0; i < 16; ++i)
      w[i] = be32(buf + i * 4);
    for (unsigned i = 16; i < 64; ++i) {
      uint32_t x = w[i - 15], y = w[i - 2];
      w[i] = w[i - 16] + (rotr(x, 7) ^ rotr(x, 18) ^ (x >> 3)) + w[i - 7] +
             (rotr(y, 17) ^ rotr(y, 19) ^ (y >> 10));
    }
    uint32_t a = h[0], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6],
             v = h[7], bb = h[1];
    for (unsigned i = 0; i < 64; ++i) {
      uint32_t t = v + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) +
                   ((e & f) ^ (~e & g)) + k[i] + w[i];
      uint32_t u = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) +
                   ((a & bb) ^ (a & c) ^ (bb & c));
      v = g;
      g = f;
      f = e;
      e = d + t;
      d = c;
      c = bb;
      bb = a;
      a = t + u;
    }
    h[0] += a;
    h[1] += bb;
    h[2] += c;
    h[3] += d;
    h[4] += e;
    h[5] += f;
    h[6] += g;
    h[7] += v;
  }
  for (unsigned i = 0; i < 8; ++i)
    put32(out + 4 * i, h[i]);
}

#define LIMIT (32u * 1024u * 1024u)
struct installed_objects {
  int apk, module, selected;
  struct stat a, m, s;
  char *apk_path, *module_path, *selected_path;
  struct installed_errors errors;
  struct installed_observation observation;
};
static void object_failure(struct installed_objects *o, int error, int kind,
                           int hop, int operation, int syscall_error) {
  if (o && error && !o->observation.error)
    o->observation=(struct installed_observation){error,kind,hop,operation,syscall_error};
}
struct installed_observation installed_observe(struct installed_objects *o) {
  struct installed_observation empty={0}; return o ? o->observation : empty;
}
struct installed_errors installed_error_observe(struct installed_objects *o) {
  struct installed_errors empty={0}; return o ? o->errors : empty;
}
static void dispose(int fd, struct installed_errors *e) {
  if (fd >= 0 && close(fd) < 0) {
    int n = errno; ++e->close_failures;
    if (!e->cleanup) e->cleanup = n;
    else if (!e->secondary_cleanup) e->secondary_cleanup = n;
  }
}
/* Every component no-follow; no dirname/realpath normalization or symlink
 * resolution. Intermediate descriptors never become authority. */
static int held_path(const char *path, int directory, struct installed_errors *e,
                     struct installed_objects *o, int kind, int operation) {
  if (!path || path[0] != '/' || strlen(path) > 4095) { object_failure(o,EINVAL,kind,0,operation,0); errno = EINVAL; return -1; }
  char *copy = strdup(path + 1);
  if (!copy) { int error=errno; object_failure(o,error,kind,0,operation,0); errno=error; return -1; }
  int fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
  if (fd<0) { int error=errno; object_failure(o,error,kind,0,operation,error); errno=error; }
  int hop=0;
  char *p = copy;
  while (fd >= 0 && *p) {
    char *slash = strchr(p, '/');
    if (slash) *slash = 0;
    if (!*p || !strcmp(p, ".") || !strcmp(p, "..")) {
      object_failure(o,EINVAL,kind,hop,operation,0); dispose(fd, e); fd = -1; errno = EINVAL; break;
    }
    ++hop;
    int next = openat(fd, p, O_RDONLY | O_CLOEXEC | O_NOFOLLOW |
                     ((slash || directory) ? O_DIRECTORY : O_NONBLOCK));
    int error = errno;
    if (next<0) object_failure(o,error,kind,hop,operation,error);
    dispose(fd, e); fd = next; errno = error;
    if (!slash) break;
    p = slash + 1;
    if (!*p) { object_failure(o,EINVAL,kind,hop,operation,0); dispose(fd,e); fd = -1; errno = EINVAL; break; }
  }
  free(copy);
  return fd;
}
static int same(const struct stat *a, const struct stat *b) {
  return a->st_dev == b->st_dev && a->st_ino == b->st_ino &&
    a->st_size == b->st_size && a->st_mode == b->st_mode &&
    a->st_uid == b->st_uid && a->st_gid == b->st_gid && b->st_nlink &&
    a->st_mtim.tv_sec == b->st_mtim.tv_sec && a->st_mtim.tv_nsec == b->st_mtim.tv_nsec &&
    a->st_ctim.tv_sec == b->st_ctim.tv_sec && a->st_ctim.tv_nsec == b->st_ctim.tv_nsec;
}
static int current(int fd, const char *path, const struct stat *original,
                   struct installed_errors *e, struct installed_objects *o, int kind) {
  struct stat s;
  if (fstat(fd, &s) < 0) { int error=errno; object_failure(o,error,kind,0,INST_STAT,error); return error; }
  if (!same(original,&s)) { object_failure(o,ESTALE,kind,0,INST_STAT,0); return ESTALE; }
  int check = held_path(path, S_ISDIR(original->st_mode), e,o,kind,INST_REOPEN);
  if (check < 0) return errno;
  int stat_result=fstat(check,&s), stat_error=errno;
  int result = stat_result < 0 ? stat_error : same(original,&s) ? 0 : ESTALE;
  object_failure(o,result,kind,0,INST_STAT,stat_result<0 ? stat_error : 0);
  dispose(check,e);
  return result ? result : e->cleanup;
}
int installed_open(const char *apk, const char *module, const char *data,
                   struct installed_objects **out, struct installed_errors *e) {
  if (!out || !e) return EINVAL;
  *out = NULL; memset(e,0,sizeof(*e));
  struct installed_objects *o = calloc(1,sizeof(*o));
  if (!o) return e->error = ENOMEM;
  o->apk = o->module = o->selected = -1;
  *out = o; /* Retain even failed cleanup; sole caller must dispose. */
  o->apk_path = apk ? strdup(apk) : NULL;
  o->module_path = module ? strdup(module) : NULL;
  if (data && strlen(data) < 4000) {
    o->selected_path = malloc(strlen(data) + 40);
    if (o->selected_path) strcpy(stpcpy(o->selected_path,data),"/files/connected-host-v1/control");
  }
  if (!o->apk_path || !o->module_path || !o->selected_path) return e->error = ENOMEM;
  o->apk = held_path(o->apk_path,0,&o->errors,o,INST_APK,INST_OPEN);
  if (o->apk < 0) return e->error = errno;
  o->module = held_path(o->module_path,0,&o->errors,o,INST_MODULE,INST_OPEN);
  if (o->module < 0) return e->error = errno;
  o->selected = held_path(o->selected_path,1,&o->errors,o,INST_SELECTED,INST_OPEN);
  if (o->selected < 0) return e->error = errno;
  if (fstat(o->apk,&o->a)) { int n=errno; object_failure(o,n,INST_APK,0,INST_STAT,n); return e->error=n; }
  if (fstat(o->module,&o->m)) { int n=errno; object_failure(o,n,INST_MODULE,0,INST_STAT,n); return e->error=n; }
  if (fstat(o->selected,&o->s)) { int n=errno; object_failure(o,n,INST_SELECTED,0,INST_STAT,n); return e->error=n; }
  int invalid_kind=!S_ISREG(o->a.st_mode) ? INST_APK : !S_ISREG(o->m.st_mode) ? INST_MODULE :
    (!S_ISDIR(o->s.st_mode) || o->s.st_uid!=geteuid() || (o->s.st_mode&07777)!=0700) ? INST_SELECTED :
    (o->a.st_mode&0022) ? INST_APK : (o->m.st_mode&0022) ? INST_MODULE : INST_NONE;
  if (invalid_kind) { object_failure(o,EACCES,invalid_kind,0,INST_MODE,0); return e->error=EACCES; }
  return e->error = o->errors.cleanup;
}
static unsigned le16(const uint8_t *p) { return p[0] | (unsigned)p[1]<<8; }
static uint32_t le32(const uint8_t *p) { return le16(p) | (uint32_t)le16(p+2)<<16; }
static int content(int fd, uint8_t **bytes, size_t *size, struct installed_objects *o, int kind) {
  struct stat s;
  if (fstat(fd,&s)) { int error=errno; object_failure(o,error,kind,0,INST_STAT,error); return error; }
  if (s.st_size <= 0 || s.st_size > LIMIT) {
    object_failure(o,EFBIG,kind,0,INST_CONTENT,0); return EFBIG;
  }
  *size = (size_t)s.st_size; *bytes = malloc(*size);
  if (!*bytes) { object_failure(o,ENOMEM,kind,0,INST_CONTENT,0); return ENOMEM; }
  size_t done = 0;
  while (done < *size) {
    ssize_t n = pread(fd,*bytes+done,*size-done,(off_t)done);
    if (n <= 0) { int e = n < 0 ? errno : EIO; object_failure(o,e,kind,0,INST_READ,n<0 ? e : 0); free(*bytes); *bytes=NULL; return e; }
    done += (size_t)n;
  }
  return 0;
}
static int digest_is(const uint8_t *bytes, size_t length, const char *expected) {
  static const char hex[] = "0123456789abcdef";
  uint8_t hash[32]; char text[65]; installed_sha256(bytes,length,hash);
  for (unsigned i=0;i<32;i++) { text[i*2]=hex[hash[i]>>4]; text[i*2+1]=hex[hash[i]&15]; }
  text[64]=0; return expected && strlen(expected)==64 && !strcmp(text,expected);
}
/* Closed stored-only ZIP dialect. Pins cover EVERY ordinary entry. Signing
 * block is verified by the stock package manager, not self-hashed as an APK.
 * Self module bytes are compared with the held extracted module: no self hash
 * cycle and no claim that signer alone pins every APK byte. */
static int zip_validate(const uint8_t *a, size_t n, const uint8_t *m, size_t mn,
                        const struct installed_pin *pins, size_t count) {
  if (n<22 || count<3 || count>128) return EINVAL;
  size_t end=n-22;
  if (le32(a+end)!=0x06054b50 || le16(a+end+20) || le16(a+end+4) || le16(a+end+6) ||
      le16(a+end+8)!=count || le16(a+end+10)!=count) return EPROTO;
  size_t central=le32(a+end+16), length=le32(a+end+12);
  if (central>end || length!=end-central) return EPROTO;
  size_t p=central, last=0; unsigned self=0;
  for (size_t i=0;i<count;i++) {
    if (p>end || end-p<46 || le32(a+p)!=0x02014b50) return EPROTO;
    size_t names=le16(a+p+28), extra=le16(a+p+30), comment=le16(a+p+32);
    size_t bytes=le32(a+p+24), local=le32(a+p+42);
    unsigned mode=le32(a+p+38)>>16;
    if (!pins[i].name || strlen(pins[i].name)!=names || p+46+names+extra+comment>end ||
        memcmp(a+p+46,pins[i].name,names) || le16(a+p+8)&~0x800u || le16(a+p+10) ||
        le16(a+p+34) || (mode&0170000 && (mode&0170000)!=0100000) ||
        le32(a+p+20)!=bytes || local!=last || local>central || central-local<30 ||
        le32(a+local)!=0x04034b50 || le16(a+local+6)!=le16(a+p+8) || le16(a+local+8) ||
        le32(a+local+14)!=le32(a+p+16) || le32(a+local+18)!=bytes || le32(a+local+22)!=bytes ||
        le16(a+local+26)!=names) return EPROTO;
    size_t start=local+30+names+le16(a+local+28);
    if (start>central || bytes>central-start || memcmp(a+local+30,pins[i].name,names)) return EPROTO;
    if (!pins[i].sha256) {
      if (strncmp(pins[i].name,"lib/",4) || !strstr(pins[i].name,"/libschemii_owner.so") ||
          bytes!=mn || memcmp(a+start,m,mn)) return EACCES;
      ++self;
    } else if (!digest_is(a+start,bytes,pins[i].sha256)) return EACCES;
    last=start+bytes; p+=46+names+extra+comment;
  }
  /* Unsigned fixture has no gap. Installed products require OS-verified
   * v2/v3 signing. Reject arbitrary gap/post-native metadata and unknown block
   * IDs rather than guessing what a signer covered. No v1 META-INF members. */
  if (last!=central) {
    size_t gap=central-last;
    if (gap<32 || memcmp(a+central-16,"APK Sig Block 42",16)) return EPROTO;
    uint64_t footer=(uint64_t)le32(a+central-24)|((uint64_t)le32(a+central-20)<<32);
    if (footer<24 || footer>gap-8) return EPROTO;
    size_t block=central-(size_t)footer-8;
    size_t prefix=block-last;
    /* Pinned apksigner pads its signed data domain to a 4KiB boundary.
     * Only the minimal zero prefix is allowed; no arbitrary post-member gap.
     * The OS signature check remains required, not inferred from this syntax. */
    if (prefix) {
      if (prefix>=4096 || block%4096) return EPROTO;
      for (size_t k=last;k<block;k++) if (a[k]) return EPROTO;
    }
    uint64_t size=(uint64_t)le32(a+block)|((uint64_t)le32(a+block+4)<<32);
    if (size!=footer) return EPROTO;
    size_t cursor=block+8; unsigned signatures=0;
    while (cursor<central-24) {
      if (central-24-cursor<12) return EPROTO;
      uint64_t pair=(uint64_t)le32(a+cursor)|((uint64_t)le32(a+cursor+4)<<32);
      if (pair<4 || pair>central-24-cursor-8) return EPROTO;
      uint32_t id=le32(a+cursor+8);
      if (id==0x7109871a || id==0xf05368c0 || id==0x1b93ad61) ++signatures;
      else if (id==0x42726577) {
        for (size_t k=cursor+12;k<cursor+8+pair;k++) if (a[k]) return EPROTO;
      } else return EPROTO;
      cursor+=8+(size_t)pair;
    }
    if (cursor!=central-24 || !signatures || signatures>3) return EPROTO;
  }
  return p==end && self==1 ? 0 : EPROTO;
}
int installed_validate(struct installed_objects *o, const struct installed_pin *pins, size_t count) {
  if (!o || o->errors.cleanup) return EINVAL;
  int e=current(o->apk,o->apk_path,&o->a,&o->errors,o,INST_APK);
  if (!e) e=current(o->module,o->module_path,&o->m,&o->errors,o,INST_MODULE);
  if (!e) e=current(o->selected,o->selected_path,&o->s,&o->errors,o,INST_SELECTED);
  uint8_t *a=NULL,*m=NULL; size_t an=0,mn=0;
  if (!e) e=content(o->apk,&a,&an,o,INST_APK);
  if (!e) e=content(o->module,&m,&mn,o,INST_MODULE);
  if (!e) {
    e=zip_validate(a,an,m,mn,pins,count);
    object_failure(o,e,INST_APK,0,INST_CONTENT,0);
  }
  free(a); free(m);
  if (!e) e=current(o->apk,o->apk_path,&o->a,&o->errors,o,INST_APK);
  if (!e) e=current(o->module,o->module_path,&o->m,&o->errors,o,INST_MODULE);
  if (!e) e=current(o->selected,o->selected_path,&o->s,&o->errors,o,INST_SELECTED);
  return e;
}
int installed_selected(struct installed_objects *o) { return o ? o->selected : -1; }
int installed_close(struct installed_objects **ptr, struct installed_errors *e) {
  if (!ptr || !*ptr || !e) return EINVAL;
  struct installed_objects *o=*ptr;
  dispose(o->selected,&o->errors); o->selected=-1;
  dispose(o->module,&o->errors); o->module=-1;
  dispose(o->apk,&o->errors); o->apk=-1;
  *e=o->errors;
  /* Linux close errors are never retried. Retain an unresolved tombstone so
   * callers cannot replace the acquisition or infer release from an errno. */
  if (e->cleanup) return e->cleanup;
  free(o->apk_path); free(o->module_path); free(o->selected_path); free(o); *ptr=NULL;
  return 0;
}

/* Current mapped object correlation, not never-traced history or executable
 * memory attestation. Fixed own-process proc entry; no caller map/path import. */
static int mapping_read(struct installed_objects *o, uintptr_t address, uintptr_t base) {
  if (!o || address < base) return EINVAL;
  Elf64_Ehdr elf;
  ssize_t read=pread(o->module,&elf,sizeof(elf),0);
  if (read!=(ssize_t)sizeof(elf)) { int error=errno; object_failure(o,EPROTO,INST_MODULE,0,INST_READ,read<0 ? error : 0); return EPROTO; }
  if (memcmp(elf.e_ident,ELFMAG,SELFMAG) || elf.e_ident[EI_CLASS]!=ELFCLASS64 ||
      elf.e_ident[EI_DATA]!=ELFDATA2LSB || elf.e_type!=ET_DYN ||
      elf.e_phentsize!=sizeof(Elf64_Phdr) || !elf.e_phnum || elf.e_phnum>32) return EPROTO;
  uint64_t expected=UINT64_MAX;
  for (unsigned i=0;i<elf.e_phnum;i++) {
    Elf64_Phdr ph;
    ssize_t count=pread(o->module,&ph,sizeof(ph),(off_t)(elf.e_phoff+i*sizeof(ph)));
    if (count!=(ssize_t)sizeof(ph)) { int error=errno; object_failure(o,EIO,INST_MODULE,0,INST_READ,count<0 ? error : 0); return EIO; }
    if (ph.p_type!=PT_LOAD) continue;
    if ((ph.p_flags&(PF_W|PF_X))==(PF_W|PF_X)) return EACCES;
    uint64_t relative=address-base;
    if ((ph.p_flags&PF_X) && relative>=ph.p_vaddr && relative-ph.p_vaddr<ph.p_filesz) {
      if (expected!=UINT64_MAX || ph.p_offset>(uint64_t)o->m.st_size ||
          relative-ph.p_vaddr>(uint64_t)o->m.st_size-ph.p_offset) return EPROTO;
      expected=ph.p_offset+relative-ph.p_vaddr;
    }
  }
  if (expected==UINT64_MAX) return ESTALE;
  char path[80];
  if (snprintf(path,sizeof(path),"/proc/%ld/maps",(long)getpid()) >= (int)sizeof(path)) return EOVERFLOW;
  int fd=held_path(path,0,&o->errors,o,INST_MAPS,INST_OPEN);
  if (fd<0) return errno;
  FILE *stream=fdopen(fd,"r");
  if (!stream) { int e=errno; object_failure(o,e,INST_MAPS,0,INST_READ,e); dispose(fd,&o->errors); return e; }
  char line[8192]; size_t bytes=0; int found=0, error=0;
  while (fgets(line,sizeof(line),stream)) {
    bytes+=strlen(line);
    if (bytes>1024*1024 || !strchr(line,'\n')) { error=EOVERFLOW; break; }
    unsigned long begin,end,offset,inode; unsigned major_id,minor_id; char permissions[5];
    if (sscanf(line,"%lx-%lx %4s %lx %x:%x %lu",&begin,&end,permissions,&offset,&major_id,&minor_id,&inode)!=7) {
      error=EPROTO; break;
    }
    if (address>=begin && address<end) {
      if (inode!=(unsigned long)o->m.st_ino || makedev(major_id,minor_id)!=o->m.st_dev ||
          permissions[2]!='x' || permissions[1]=='w' || offset>(unsigned long)o->m.st_size ||
          (uint64_t)offset+address-begin!=expected) error=ESTALE;
      else found=1;
    }
  }
  if (ferror(stream) && !error) error=EIO;
  if (fclose(stream)<0) {
    ++o->errors.close_failures;
    if (!o->errors.cleanup) o->errors.cleanup=errno;
    else if (!o->errors.secondary_cleanup) o->errors.secondary_cleanup=errno;
  }
  int result=error ? error : o->errors.cleanup ? o->errors.cleanup : found ? 0 : ESTALE;
  object_failure(o,result,INST_MAPS,0,INST_MAPPING,0);
  return result;
}

int installed_mapping(struct installed_objects *o, uintptr_t address, uintptr_t base) {
  int result=mapping_read(o,address,base);
  object_failure(o,result,INST_MODULE,0,INST_MAPPING,0);
  return result;
}
