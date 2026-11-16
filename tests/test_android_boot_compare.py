"""N1 actual-code synthetic classifier and separate production-object controls.

No real host identity is read: both binaries link the accepted reader only to
closed synthetic syscall wrappers (no __real_* fallback). Including the comparator
translation unit in ONE temporary harness permits private synthetic construction
without adding a test seam to production. These positives prove no Android C/A,
owner authority, restart, durability, kernel renewal or recovery capability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platforms/android/native/host"
STRICT = ["-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic", "-fno-builtin"]
WRAPS = ["openat", "fstatfs", "fstat", "syscall", "read", "close", "memcmp"]
REASONS = (
    "ANCHOR_EQUAL ANCHOR_DIFFERENT CE_LOCKED_OR_STORAGE_UNAVAILABLE "
    "OBSERVATION_UNKNOWN CALLER_UNPROVED NO_PRIOR_FRESH FRESHNESS_UNPROVED "
    "ANCHOR_MISSING ANCHOR_MALFORMED BINDING_MISMATCH SOURCE_INCOMPATIBLE "
    "PREFIX_UNPROVED HISTORY_UNPROVED HISTORY_COLLISION LEASE_LOST COMMIT_UNCERTAIN"
).split()
FIELDS = (
    "relation reason observation_status observation_stage error cleanup_error "
    "current_admission prior_admission continuity"
).split()

# All descriptors, metadata, text and identities below are fabricated. Wrappers
# abort on an unexpected syscall contract and NEVER delegate to the host kernel.
WRAPPER = r"""
#define _GNU_SOURCE
#include "boot_compare.h"
#include "boot_identity.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <linux/stat.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <unistd.h>
static int opens, closes, offset, comparisons;
static const char *fault;
static char candidate = 'A';
static const char *const nodes[] = {"/proc", "sys", "kernel", "random", "boot_id"};
#define REQUIRE(x) do { if (!(x)) abort(); } while (0)
int __wrap_memcmp(const void *a, const void *b, size_t n) {
  ++comparisons;
  const unsigned char *x = a, *y = b;
  for (size_t i = 0; i < n; ++i) if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
  return 0;
}
int __wrap_openat(int parent, const char *path, int flags, ...) {
  int node = opens++ % 5;
  REQUIRE(opens <= 10 && !strcmp(path, nodes[node]));
  REQUIRE(parent == (node ? 1000 + opens - 1 : AT_FDCWD));
  REQUIRE(flags == (O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK |
                    (node < 4 ? O_DIRECTORY : 0)));
  if (!strcmp(fault, "open")) { errno = EACCES; return -1; }
  if (node == 4) offset = 0;
  return 1000 + opens;
}
int __wrap_fstatfs(int fd, struct statfs *out) {
  REQUIRE(fd == 1000 + opens);
  memset(out, 0, sizeof(*out)); out->f_type = PROC_SUPER_MAGIC;
  if (!strcmp(fault, "origin")) out->f_type = 0;
  return 0;
}
int __wrap_fstat(int fd, struct stat *out) {
  REQUIRE(fd == 1000 + opens);
  memset(out, 0, sizeof(*out));
  out->st_mode = opens % 5 ? S_IFDIR | 0500 : S_IFREG | 0400;
  return 0;
}
long __wrap_syscall(long number, ...) {
  va_list ap; va_start(ap, number);
  int fd = va_arg(ap, int);
  const char *path = va_arg(ap, const char *);
  int flags = va_arg(ap, int);
  unsigned mask = va_arg(ap, unsigned);
  struct statx *out = va_arg(ap, struct statx *);
  va_end(ap);
  REQUIRE(number == SYS_statx && fd == 1000 + opens && !*path);
  REQUIRE(flags == (AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW) && mask == STATX_MNT_ID);
  memset(out, 0, sizeof(*out)); out->stx_mask = STATX_MNT_ID; out->stx_mnt_id = 100;
  if (!strcmp(fault, "mount") && opens > 5) out->stx_mnt_id = 101;
  return 0;
}
ssize_t __wrap_read(int fd, void *out, size_t size) {
  REQUIRE(fd == 1000 + opens && opens % 5 == 0 && size <= 38);
  if (!strcmp(fault, "read-cleanup")) { errno = EIO; return -1; }
  char text[] = "12345678-1234-4abc-8def-123456789abc\n";
  text[0] = candidate == 'A' ? '1' : candidate == 'B' ? '2' : '3';
  if (!strcmp(fault, "disagree") && opens > 5) text[0] = '4';
  size_t n = strlen(text) - (size_t)offset;
  if (n > size) n = size;
  memcpy(out, text + offset, n); offset += (int)n;
  return (ssize_t)n;
}
int __wrap_close(int fd) {
  REQUIRE(fd >= 1001 && fd <= 1010 && ++closes <= opens);
  if ((!strcmp(fault, "read-cleanup") && opens == 5 && fd == 1005) ||
      !strcmp(fault, "close")) { errno = EBADF; return -1; }
  return 0;
}
static void project(struct boot_compare_result r) {
  printf("{\"result\":[%d,%d,%d,%d,%d,%d,%d,%d,%d],"
         "\"comparisons\":%d,\"opens\":%d,\"closes\":%d}\n",
         r.relation, r.reason, r.observation_status, r.observation_stage,
         r.error, r.cleanup_error, r.current_admission, r.prior_admission,
         r.continuity, comparisons, opens, closes);
}
"""

SYNTHETIC = r"""
#include "boot_compare.c"
static struct comparison_snapshot fixture(void) {
  struct comparison_snapshot s = {
    .storage_available = 1, .caller_admitted = 1, .state = PRIOR_ESTABLISHED,
    .anchor_present = 1, .anchor_well_formed = 1,
    .schema_compatible = 1, .contract_compatible = 1,
    .source_compatible = 1, .build_compatible = 1,
    .install_matches = 1, .state_matches = 1, .storage_matches = 1,
    .data_generation_matches = 1, .cluster_slots_match = 1,
    .attempt_matches = 1, .run_matches = 1, .generation_matches = 1,
    .original_prebirth_admitted = 1,
    .lease_uninterrupted = 1, .context_unchanged = 1, .borrow_current = 1,
    .prefix_checked = 1, .tip_unchanged = 1, .commit = COMMIT_COMPLETE,
    .history_complete = 1, .retention_proved = 1,
    .epoch_count = 1, .selected_epoch = 0, .current_epoch = 0
  };
  static const unsigned char text[] = "12345678-1234-4abc-8def-123456789abc";
  REQUIRE(boot_identity_parse(text, sizeof(text)-1, s.epochs[0]) == BOOT_ID_OK);
  memcpy(s.epochs[1], s.epochs[0], BOOT_IDENTITY_BYTES); s.epochs[1][0] = 0x22;
  memcpy(s.epochs[2], s.epochs[0], BOOT_IDENTITY_BYTES); s.epochs[2][0] = 0x32;
  return s;
}
static void remove_gate(struct comparison_snapshot *s, const char *name) {
#define REMOVE(field) if (!strcmp(name, #field)) { s->field = 0; return; }
  REMOVE(storage_available) REMOVE(caller_admitted) REMOVE(anchor_present)
  REMOVE(anchor_well_formed) REMOVE(schema_compatible) REMOVE(contract_compatible)
  REMOVE(source_compatible) REMOVE(build_compatible) REMOVE(install_matches)
  REMOVE(state_matches) REMOVE(storage_matches) REMOVE(data_generation_matches)
  REMOVE(cluster_slots_match) REMOVE(attempt_matches) REMOVE(run_matches)
  REMOVE(generation_matches) REMOVE(original_prebirth_admitted)
  REMOVE(lease_uninterrupted) REMOVE(context_unchanged) REMOVE(borrow_current)
  REMOVE(prefix_checked) REMOVE(tip_unchanged) REMOVE(history_complete)
  REMOVE(retention_proved) REMOVE(epoch_count)
#undef REMOVE
  REQUIRE(!strcmp(name, "none"));
}
static void remove_observation_gate(struct boot_identity_result *r, const char *name) {
  if (!strcmp(name, "knownness")) r->knownness = BOOT_ID_UNKNOWN;
  else if (!strcmp(name, "status")) r->status = BOOT_ID_MALFORMED;
  else if (!strcmp(name, "stage")) r->stage = BOOT_ID_STAGE_PARSE;
  else if (!strcmp(name, "error")) r->error = EIO;
  else if (!strcmp(name, "cleanup")) r->cleanup_error = EBADF;
  else if (!strcmp(name, "method")) r->method = 0;
  else if (!strcmp(name, "context")) r->context = 0;
  else if (!strcmp(name, "failed_sample")) r->failed_sample = 1;
  else if (!strcmp(name, "failed_node")) r->failed_node = 1;
  else if (strcmp(name, "none")) {
    int sample; char field[32];
    REQUIRE(sscanf(name, "%d:%31s", &sample, field) == 2 && sample >= 0 && sample < 2);
    struct boot_identity_sample *s = &r->samples[sample];
    if (!strcmp(field, "origin")) s->origin = BOOT_ID_PROC_UNKNOWN;
    else if (!strcmp(field, "opened")) --s->opened_nodes;
    else if (!strcmp(field, "procfs")) --s->procfs_checks;
    else if (!strcmp(field, "type")) --s->type_checks;
    else if (!strcmp(field, "mount")) --s->mount_checks;
    else if (!strcmp(field, "read_zero")) s->read_calls = 0;
    else if (!strcmp(field, "read_overflow")) s->read_calls = 39;
    else if (!strcmp(field, "bytes")) s->bytes_read = 38;
    else if (!strcmp(field, "leaf")) ++s->leaf_mount_id;
    else if (!strcmp(field, "root")) { ++s->root_mount_id; ++s->leaf_mount_id; }
    else abort();
  }
}
int main(int argc, char **argv) {
  REQUIRE(argc == 6);
  const char *scenario = argv[1]; candidate = argv[2][0]; fault = argv[3];
  struct comparison_snapshot s = fixture();
  remove_gate(&s, argv[4]);
  if (!strcmp(scenario, "invalid-predicate")) s.caller_admitted = -1;

  if (!strcmp(scenario, "repeat") || !strcmp(scenario, "active") ||
      !strcmp(scenario, "collision") || !strcmp(scenario, "duplicate")) {
    s.epoch_count = 3; s.current_epoch = 1;
  }
  if (!strcmp(scenario, "active")) s.selected_epoch = 1;
  if (!strcmp(scenario, "duplicate")) memcpy(s.epochs[2], s.epochs[0], 16);
  if (!strcmp(scenario, "fresh")) { s.state = PRIOR_FRESH; s.anchor_present = 0; }
  if (!strcmp(scenario, "partial")) { s.state = PRIOR_PARTIAL; s.anchor_present = 0; }
  if (!strcmp(scenario, "unproved-state")) s.state = PRIOR_UNPROVED;
  if (!strcmp(scenario, "overflow")) s.epoch_count = BOOT_COMPARE_HISTORY_MAX + 1;
  if (!strcmp(scenario, "bad-selected")) s.selected_epoch = s.epoch_count;
  if (!strcmp(scenario, "bad-current")) s.current_epoch = s.epoch_count;
  if (!strncmp(scenario, "commit-", 7)) s.commit = (enum commit_state)atoi(scenario+7);
  struct boot_identity_result observation = {0};
  int dispatched = s.storage_available || !strcmp(scenario, "storage-after-observation");
  if (dispatched) observation = boot_identity_read();
  remove_observation_gate(&observation, argv[5]);
  comparisons = 0; /* Only comparator identity comparisons, not reader consistency. */
  struct comparison_snapshot before;
  memcpy(&before, &s, sizeof(s));
  struct boot_compare_result r = classify_current(
      !dispatched || !strcmp(scenario, "unobserved") ? NULL : &observation,
      !strcmp(scenario, "no-custody") ? NULL : &s);
  int counted = comparisons;
  REQUIRE(!memcmp(&before, &s, sizeof(s))); /* No learning/writes, even on refusal. */
  erase_private(&observation, sizeof(observation));
  const unsigned char *bytes = (const unsigned char *)&observation;
  for (size_t i = 0; i < sizeof(observation); ++i) REQUIRE(bytes[i] == 0);
  erase_private(&s, sizeof(s)); erase_private(&before, sizeof(before));
  comparisons = counted;
  project(r);
  return 0;
}
"""

PRODUCTION = r"""
#include <stddef.h>
_Static_assert(sizeof(struct boot_compare_result) == 9 * sizeof(int32_t), "closed ABI");
#define FIELD(n, f) _Static_assert(offsetof(struct boot_compare_result, f) == 4*(n), #f)
FIELD(0, relation); FIELD(1, reason); FIELD(2, observation_status);
FIELD(3, observation_stage); FIELD(4, error); FIELD(5, cleanup_error);
FIELD(6, current_admission); FIELD(7, prior_admission); FIELD(8, continuity);
_Static_assert(BOOT_COMPARE_UNOBSERVED == -1, "explicit unobserved");
_Static_assert(BOOT_COMPARE_SAME == 0 && BOOT_COMPARE_NEW == 1 &&
               BOOT_COMPARE_UNKNOWN == 2, "relations");
_Static_assert(BOOT_COMPARE_ADMITTED == 0 && BOOT_COMPARE_UNPROVED == 1 &&
               BOOT_COMPARE_REJECTED == 2 && BOOT_COMPARE_PRIOR_NO_PRIOR_FRESH == 3,
               "admission");
_Static_assert(BOOT_COMPARE_FIRST_CURRENT == 0 && BOOT_COMPARE_REPEAT_CURRENT == 1 &&
               BOOT_COMPARE_CONTINUITY_UNPROVED == 2, "continuity");
int main(int argc, char **argv) {
  REQUIRE(argc == 3); fault = argv[1];
  /* A deliberately invalid nonnull view must never be dereferenced. */
  const struct boot_anchor_view *p = !strcmp(argv[2], "null") ? NULL :
      (const struct boot_anchor_view *)(uintptr_t)1;
  project(boot_compare_current(p));
  return 0;
}
"""


def command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, capture_output=True)


@pytest.fixture(scope="module")
def compiled(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    cc = shutil.which("cc")
    assert cc, "A strict host C compiler is required, not a skipped control"
    directory = tmp_path_factory.mktemp("boot-compare")
    reader = directory / "reader.o"
    command([cc, *STRICT, "-c", str(SOURCE / "boot_identity.c"), "-o", str(reader)])
    outputs = {}
    # Ordinary separate production objects at both inspection and optimized flags.
    for optimization in ("0", "2"):
        obj = directory / f"production-O{optimization}.o"
        command(
            [
                cc,
                *STRICT,
                f"-O{optimization}",
                "-c",
                str(SOURCE / "boot_compare.c"),
                "-o",
                str(obj),
            ]
        )
        outputs[f"object-O{optimization}"] = obj
    for kind, driver in (
        ("synthetic", SYNTHETIC),
        ("production", PRODUCTION),
        ("production-O2", PRODUCTION),
    ):
        path = directory / f"{kind}.c"
        path.write_text(WRAPPER + driver)
        binary = directory / kind
        objects = [str(reader)]
        if kind.startswith("production"):
            objects.append(
                str(outputs["object-O2" if kind.endswith("O2") else "object-O0"])
            )
        command(
            [
                cc,
                *STRICT,
                "-I",
                str(SOURCE),
                str(path),
                *objects,
                *[f"-Wl,--wrap={name}" for name in WRAPS],
                "-o",
                str(binary),
            ]
        )
        outputs[kind] = binary
    outputs["directory"] = directory
    return outputs


def invoke(
    compiled: dict[str, Path],
    *arguments: str,
    production: bool = False,
    optimized: bool = False,
) -> dict:
    binary = compiled[
        "production-O2" if optimized else "production" if production else "synthetic"
    ]
    output = command([str(binary), *arguments]).stdout
    # Private synthetic sentinel and identity-derived encodings must not escape.
    for first in "123":
        text = f"{first}2345678-1234-4abc-8def-123456789abc"
        raw = bytes.fromhex(text.replace("-", ""))
        for secret in (
            text,
            raw.hex(),
            hashlib.sha256(raw).hexdigest(),
            hashlib.sha256(text.encode()).hexdigest(),
        ):
            assert secret not in output
    data = json.loads(output)
    assert set(data) == {"result", "comparisons", "opens", "closes"}
    assert len(data["result"]) == len(FIELDS)
    r = dict(zip(FIELDS, data["result"], strict=True))
    assert all(type(value) is int for value in r.values())
    assert r["relation"] in range(3) and r["reason"] in range(16)
    assert r["observation_status"] in range(-1, 12)
    assert r["observation_stage"] in range(-1, 9)
    assert r["error"] in range(-1, 4096) and r["cleanup_error"] in range(-1, 4096)
    assert r["current_admission"] in range(3) and r["prior_admission"] in range(4)
    assert r["continuity"] in range(3)
    assert (r["relation"] == 2) == (r["reason"] not in (0, 1))
    return {**r, **{key: value for key, value in data.items() if key != "result"}}


def synthetic(
    compiled: dict[str, Path],
    scenario: str = "normal",
    candidate: str = "A",
    fault: str = "none",
    gate: str = "none",
    observation: str = "none",
) -> dict:
    return invoke(compiled, scenario, candidate, fault, gate, observation)


def refused(result: dict, reason: str, *, compares: int = 0) -> None:
    assert result["relation"] == 2
    assert result["reason"] == REASONS.index(reason)
    assert result["continuity"] == 2
    assert result["comparisons"] == compares


@pytest.mark.parametrize(
    "scenario,candidate,relation,continuity",
    [
        ("normal", "A", 0, 1),
        ("normal", "B", 1, 0),
        ("repeat", "B", 1, 1),
        ("active", "B", 0, 1),
    ],
)
def test_synthetic_selected_and_current_roles(
    compiled, scenario, candidate, relation, continuity
):
    r = synthetic(compiled, scenario, candidate)
    assert r["relation"] == relation and r["reason"] == relation
    assert r["continuity"] == continuity
    assert r["current_admission"] == r["prior_admission"] == 0
    assert (
        r["observation_status"]
        == r["observation_stage"]
        == r["error"]
        == r["cleanup_error"]
        == 0
    )
    assert r["opens"] == r["closes"] == 10


@pytest.mark.parametrize(
    "scenario,candidate,compares", [("collision", "C", 4), ("active", "A", 4)]
)
def test_synthetic_distinct_noncurrent_collision(
    compiled, scenario, candidate, compares
):
    r = synthetic(compiled, scenario, candidate)
    refused(r, "HISTORY_COLLISION", compares=compares)
    assert r["prior_admission"] == 0  # Admitted history, refused candidate.


GATES = {
    "CE_LOCKED_OR_STORAGE_UNAVAILABLE": ["storage_available"],
    "CALLER_UNPROVED": ["caller_admitted"],
    "ANCHOR_MISSING": ["anchor_present"],
    "ANCHOR_MALFORMED": ["anchor_well_formed"],
    "SOURCE_INCOMPATIBLE": [
        "schema_compatible",
        "contract_compatible",
        "source_compatible",
        "build_compatible",
    ],
    "BINDING_MISMATCH": [
        "install_matches",
        "state_matches",
        "storage_matches",
        "data_generation_matches",
        "cluster_slots_match",
        "attempt_matches",
        "run_matches",
        "generation_matches",
        "original_prebirth_admitted",
    ],
    "LEASE_LOST": ["lease_uninterrupted", "context_unchanged", "borrow_current"],
    "PREFIX_UNPROVED": ["prefix_checked", "tip_unchanged"],
    "HISTORY_UNPROVED": ["history_complete", "retention_proved", "epoch_count"],
}


@pytest.mark.parametrize(
    "reason,gate", [(r, g) for r, gates in GATES.items() for g in gates]
)
@pytest.mark.parametrize("candidate", ["A", "B"])
def test_synthetic_each_admission_prerequisite(compiled, reason, gate, candidate):
    r = synthetic(compiled, candidate=candidate, gate=gate)
    refused(r, reason)
    if gate == "storage_available":
        assert [r[f] for f in FIELDS[2:6]] == [-1] * 4
    else:
        assert [r[f] for f in FIELDS[2:6]] == [0] * 4


OBSERVATION_GATES = [
    "knownness",
    "status",
    "stage",
    "error",
    "cleanup",
    "method",
    "context",
    "failed_sample",
    "failed_node",
    *[
        f"{i}:{field}"
        for i in range(2)
        for field in (
            "origin",
            "opened",
            "procfs",
            "type",
            "mount",
            "read_zero",
            "read_overflow",
            "bytes",
            "leaf",
            "root",
        )
    ],
]


@pytest.mark.parametrize("gate", OBSERVATION_GATES)
def test_synthetic_r_is_more_than_known_looking_bytes(compiled, gate):
    r = synthetic(compiled, observation=gate)
    refused(r, "OBSERVATION_UNKNOWN")
    assert r["current_admission"] == r["prior_admission"] == 1


@pytest.mark.parametrize(
    "fault,status,stage,error,cleanup",
    [
        ("open", 1, 1, 13, 0),
        ("origin", 4, 2, 0, 0),
        ("mount", 11, 8, 0, 0),
        ("disagree", 11, 8, 0, 0),
        ("read-cleanup", 5, 5, 5, 9),
        ("close", 10, 7, 9, 9),
    ],
)
def test_actual_reader_first_and_cleanup_failure_precede_c_a(
    compiled, fault, status, stage, error, cleanup
):
    for production in (False, True):
        r = (
            invoke(compiled, fault, "invalid", production=True)
            if production
            else synthetic(compiled, fault=fault, gate="caller_admitted")
        )
        # Reader does one consistency memcmp only when both samples finish.
        refused(
            r, "OBSERVATION_UNKNOWN", compares=r["comparisons"] if production else 0
        )
        assert [r[f] for f in FIELDS[2:6]] == [status, stage, error, cleanup]


@pytest.mark.parametrize(
    "state,reason,prior",
    [
        ("fresh", "NO_PRIOR_FRESH", 3),
        ("partial", "FRESHNESS_UNPROVED", 1),
        ("unproved-state", "FRESHNESS_UNPROVED", 2),
        ("unobserved", "OBSERVATION_UNKNOWN", 1),
        ("no-custody", "CALLER_UNPROVED", 1),
        ("overflow", "HISTORY_UNPROVED", 2),
        ("bad-selected", "HISTORY_UNPROVED", 2),
        ("bad-current", "HISTORY_UNPROVED", 2),
    ],
)
def test_freshness_borrow_and_history_refusals(compiled, state, reason, prior):
    r = synthetic(compiled, state)
    refused(r, reason)
    assert r["prior_admission"] == prior
    if state == "unobserved":
        assert [r[f] for f in FIELDS[2:6]] == [-1] * 4


@pytest.mark.parametrize("state", [0, 2, 3, 4, 5, 6, 99])
def test_valid_bytes_cannot_admit_pending_short_sync_or_unknown_commit(compiled, state):
    refused(synthetic(compiled, f"commit-{state}"), "COMMIT_UNCERTAIN")


def test_precedence_and_no_equality_or_learning_after_failure(compiled):
    refused(
        synthetic(compiled, fault="open", gate="storage_available"),
        "CE_LOCKED_OR_STORAGE_UNAVAILABLE",
    )
    refused(synthetic(compiled, "fresh", gate="caller_admitted"), "CALLER_UNPROVED")
    refused(synthetic(compiled, "invalid-predicate"), "CALLER_UNPROVED")
    refused(
        synthetic(compiled, "unproved-state", gate="anchor_present"), "ANCHOR_MISSING"
    )
    observed = synthetic(
        compiled,
        "storage-after-observation",
        fault="read-cleanup",
        gate="storage_available",
    )
    refused(observed, "CE_LOCKED_OR_STORAGE_UNAVAILABLE")
    assert [observed[f] for f in FIELDS[2:6]] == [5, 5, 5, 9]
    refused(
        synthetic(compiled, "commit-2", gate="source_compatible"), "SOURCE_INCOMPATIBLE"
    )
    refused(synthetic(compiled, "overflow", gate="borrow_current"), "LEASE_LOST")
    refused(synthetic(compiled, "commit-4", gate="tip_unchanged"), "PREFIX_UNPROVED")
    refused(
        synthetic(compiled, "collision", "C", gate="history_complete"),
        "HISTORY_UNPROVED",
    )
    refused(
        synthetic(compiled, "active", "B", gate="attempt_matches"), "BINDING_MISMATCH"
    )
    refused(synthetic(compiled, "duplicate"), "HISTORY_UNPROVED", compares=2)


@pytest.mark.parametrize("pointer", ["null", "invalid"])
@pytest.mark.parametrize("optimized", [False, True])
def test_normal_production_object_never_admits_fabricated_view(
    compiled, pointer, optimized
):
    r = invoke(compiled, "none", pointer, production=True, optimized=optimized)
    refused(r, "CALLER_UNPROVED", compares=1)  # Only reader consistency check.
    assert r["current_admission"] == r["prior_admission"] == 1
    assert [r[f] for f in FIELDS[2:6]] == [0] * 4
    assert r["opens"] == r["closes"] == 10


def test_normal_object_symbols_and_public_header_exclude_synthetic_seam(compiled):
    for opt in ("0", "2"):
        obj = compiled[f"object-O{opt}"]
        symbols = command(["nm", "--defined-only", "--extern-only", str(obj)]).stdout
        assert [line.split()[-1] for line in symbols.splitlines()] == [
            "boot_compare_current"
        ]
        undefined = command(["nm", "-u", str(obj)]).stdout
        assert {line.split()[-1] for line in undefined.splitlines()} <= {
            "boot_identity_read",
            "memcmp",
            "__stack_chk_fail",
        }
        contents = obj.read_bytes()
        for secret in (
            b"12345678",
            b"fixture",
            b"__wrap_",
            b"SCHEMII_TEST",
            b"remove_gate",
        ):
            assert secret not in contents
    disassembly = command(["objdump", "-dr", str(compiled["object-O0"])]).stdout
    assert "<classify_current>" in disassembly and "<erase_private>" in disassembly
    header = (SOURCE / "boot_compare.h").read_text()
    assert (
        "struct boot_anchor_view;" in header
        and "struct boot_anchor_view {" not in header
    )
    assert "comparison_snapshot" not in header and "native_observed_equal" not in header
    # External code cannot construct a view or invoke the private classifier.
    cc = shutil.which("cc")
    assert cc
    for i, text in enumerate(
        (
            '#include "boot_compare.h"\nstruct boot_anchor_view fabricated;\n',
            '#include "boot_compare.h"\nint main(void) { classify_current(0, 0); }\n',
        )
    ):
        path = compiled["directory"] / f"forbidden-{i}.c"
        path.write_text(text)
        result = subprocess.run(
            [
                cc,
                *STRICT,
                "-I",
                str(SOURCE),
                "-c",
                str(path),
                "-o",
                str(path.with_suffix(".o")),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert ("storage size" if i == 0 else "implicit declaration") in result.stderr


def test_reason_enum_order_and_closed_scalar_projection(compiled):
    cc = shutil.which("cc")
    assert cc
    path = compiled["directory"] / "enums.c"
    path.write_text(
        '#include "boot_compare.h"\n'
        + "\n".join(
            f'_Static_assert(BOOT_COMPARE_{reason} == {i}, "{reason}");'
            for i, reason in enumerate(REASONS)
        )
    )
    command(
        [
            cc,
            *STRICT,
            "-I",
            str(SOURCE),
            "-c",
            str(path),
            "-o",
            str(path.with_suffix(".o")),
        ]
    )
    # invoke() enforces enum/range/closed-field and sentinel controls on every case.
    assert set(FIELDS) == {
        "relation",
        "reason",
        "observation_status",
        "observation_stage",
        "error",
        "cleanup_error",
        "current_admission",
        "prior_admission",
        "continuity",
    }
