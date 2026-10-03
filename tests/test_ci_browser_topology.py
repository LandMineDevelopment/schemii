"""Raw browser indices stay bounded; acceptance proves the profile topology."""

import json
from pathlib import Path
import subprocess

import pytest

from scripts.ci.summary import summarize, valid


ROOT = Path(__file__).resolve().parents[1]
PROJECTS = ("desktop-chromium", "android-chromium")
META = {
    "schema": 1,
    "source_sha": "a" * 40,
    "run_id": 1,
    "run_attempt": 1,
    "lane": "browser",
    "project": PROJECTS[0],
    "shard": 1,
}


def receipt(meta):
    return [
        {**meta, "kind": "start", "planned": 1},
        {**meta, "kind": "plan", "test_id": "b" * 64},
        {
            **meta,
            "kind": "attempt",
            "test_id": "b" * 64,
            "source_id": "c" * 64,
            "source_line": 1,
            "attempt": 0,
            "outcome": "passed",
            "skip": "none",
            "setup_ms": 2,
            "execution_ms": 3,
            "teardown_ms": 1,
        },
        {**meta, "kind": "end", "outcome": "passed", "wall_ms": 7},
    ]


@pytest.mark.parametrize("project", PROJECTS)
@pytest.mark.parametrize("shard", range(7))
def test_browser_raw_receipt_supports_six_indices_and_existing_unsharded_zero(
    project, shard
):
    evidence = receipt({**META, "project": project, "shard": shard})
    assert all(valid(record) for record in evidence)
    result = summarize(evidence)
    assert result["project"] == project and result["shard"] == shard
    assert result["complete"] and result["collected"] == 1


@pytest.mark.parametrize("project", PROJECTS)
@pytest.mark.parametrize("shard", [-1, 7, 1.5, "6", True, None])
def test_browser_raw_receipt_rejects_unknown_or_coerced_indices(project, shard):
    evidence = receipt({**META, "project": project, "shard": shard})
    assert all(not valid(record) for record in evidence)
    with pytest.raises(ValueError, match="schema validation"):
        summarize(evidence)


@pytest.mark.parametrize("lane", ["node", "python", "postgres"])
def test_nonbrowser_raw_receipts_keep_none_project_and_zero_shard(lane):
    meta = {**META, "lane": lane, "project": "none", "shard": 0}
    assert summarize(receipt(meta))["complete"]
    for changed in [{"project": PROJECTS[0]}, {"shard": 6}]:
        assert all(not valid(record) for record in receipt({**meta, **changed}))


def test_node_writer_and_python_validator_agree_on_all_browser_indices():
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            "import {metadata} from './scripts/ci/timing.mjs';"
            "for (const project of ['desktop-chromium','android-chromium'])"
            "for (let shard=0;shard<=6;shard++)"
            "console.log(JSON.stringify(metadata('browser',project,shard)));",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    identities = [json.loads(line) for line in result.stdout.splitlines()]
    assert {(meta["project"], meta["shard"]) for meta in identities} == {
        (project, shard) for project in PROJECTS for shard in range(7)
    }
    assert len(identities) == 14
    for meta in identities:
        assert summarize(receipt(meta))["complete"]


def test_raw_receipt_cannot_mix_sixth_shard_into_another_lane():
    evidence = receipt({**META, "shard": 6})
    evidence[-1]["shard"] = 3
    with pytest.raises(ValueError, match="Mixed timing lane identities"):
        summarize(evidence)
