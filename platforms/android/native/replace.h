/* Minimal talloc portability surface for bionic API29+; no Samba fallback code. */
#pragma once
#include <stdlib.h>
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdarg.h>
#include <string.h>
#include <errno.h>
#include <unistd.h>
#include <sys/types.h>
#define TALLOC_BUILD_VERSION_MAJOR 2
#define TALLOC_BUILD_VERSION_MINOR 5
#define TALLOC_BUILD_VERSION_RELEASE 0
#define HAVE_SYS_AUXV_H 1
#define HAVE_GETAUXVAL 1
#define HAVE_CONSTRUCTOR_ATTRIBUTE 1
#define HAVE_INTPTR_T 1
#define HAVE_VA_COPY 1
#define MIN(a,b) ((a)<(b)?(a):(b))
#define MAX(a,b) ((a)>(b)?(a):(b))
#define ZERO_STRUCT(x) memset(&(x),0,sizeof(x))
#define ZERO_STRUCTP(x) memset((x),0,sizeof(*(x)))
#define discard_const(ptr) ((void *)(uintptr_t)(ptr))
#define _PUBLIC_ __attribute__((visibility("default")))
#define PRINTF_ATTRIBUTE(a,b) __attribute__((format(printf,a,b)))
/* C23 memset_explicit is absent from bionic API29. Volatile stores retain wiping. */
static inline void *memset_explicit(void *ptr, int value, size_t length) {
    volatile unsigned char *p = (volatile unsigned char *)ptr;
    for (size_t i = 0; i < length; ++i) p[i] = (unsigned char)value;
    return ptr;
}
