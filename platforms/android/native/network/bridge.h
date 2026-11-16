#ifndef SCHEMII_NETWORK_BRIDGE_H
#define SCHEMII_NETWORK_BRIDGE_H
#ifndef SCHEMII_NETWORK_BRIDGE_SHA256
#error "Build with native/network/compile.py to bind external JNI source in cgo cache keys"
#endif
#include <stdint.h>
#include <stdlib.h>
const char *schemii_android_bridge_identity(void);
char *schemii_android_snapshot(void);
char *schemii_android_roots(void);
int schemii_android_bind(uint64_t generation, int fd);
unsigned char *schemii_android_dns(uint64_t generation, uint64_t request,
    const unsigned char *query, int length, int timeout_ms, int *answer_length);
int schemii_android_prepare_dns(uint64_t request);
void schemii_android_cancel_dns(uint64_t request);
#endif
