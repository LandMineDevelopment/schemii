#ifndef SCHEMII_INSTALLED_PROVENANCE_H
#define SCHEMII_INSTALLED_PROVENANCE_H
#include <stddef.h>
#include <stdint.h>
/* Native-private build pins; never populated from Java or installed metadata. */
struct installed_pin { const char *name, *sha256; };
struct installed_objects;
struct installed_errors { int error, cleanup, secondary_cleanup, close_failures; };
int installed_open(const char *apk, const char *module, const char *data,
                   struct installed_objects **out, struct installed_errors *errors);
int installed_validate(struct installed_objects *objects,
                       const struct installed_pin *pins, size_t count);
int installed_mapping(struct installed_objects *objects, uintptr_t address, uintptr_t base);
int installed_selected(struct installed_objects *objects);
int installed_close(struct installed_objects **objects, struct installed_errors *errors);
/* SHA implementation shared verbatim with the bounded journal primitive. */
void installed_sha256(const uint8_t *data, size_t length, uint8_t out[32]);
#endif
