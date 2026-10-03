"""Cheap report checks catch actual broken Markdown and local destinations."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.ci.validate_reports import markdown_links, validate_file
from scripts.ci.test_selection import NATIVE_MARKDOWN, NATIVE_SKILL


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def reports(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "# Product\n\n## Saved `state`\n\n## Saved `state`\n"
    )
    (tmp_path / "source.py").write_text("first\nsecond\n")
    report = tmp_path / "docs/report.md"
    report.write_text(
        "# Results\n\n[Product](../README.md#saved-state)\n[Line](../source.py#L1-L2)\n"
    )
    return tmp_path, report


def test_real_local_heading_duplicate_heading_source_lines_and_external_links(reports):
    root, report = reports
    report.write_text(
        report.read_text()
        + "[Duplicate](../README.md#saved-state-1)\n[GitHub](https://github.com/example/report)\n"
    )
    assert validate_file(root, "docs/report.md") == 4


@pytest.mark.parametrize(
    "body,error",
    [
        ("[Missing](absent.md)", "Broken local"),
        ("[Missing heading](../README.md#unknown)", "heading"),
        ("[Missing line](../source.py#L99)", "source line"),
        ("[Escape](../../outside.md)", "Broken local"),
        ("[Absolute](/etc/passwd)", "local link path"),
        ("[Encoded](%2fetc%2fpasswd)", "local link path"),
        ("[Unclosed](../README.md", "Unclosed Markdown link"),
        ("```python\nunsafe()", "code fence"),
        ("[Reference][missing]", "reference"),
        ("[Execute](javascript:alert(1))", "scheme"),
        ("[Malformed](https:broken)", "external link"),
        ("[Local query](../README.md?raw=1)", "local link"),
        ("[Empty]()", "destination"),
    ],
)
def test_broken_links_or_markdown_structure_fail_cheap_validation(reports, body, error):
    root, report = reports
    report.write_text("# Results\n\n" + body + "\n")
    with pytest.raises(ValueError, match=error):
        validate_file(root, "docs/report.md")


def test_fenced_and_inline_code_examples_are_not_interpreted_as_links(reports):
    root, report = reports
    report.write_text(
        "# Results\n\n```md\n[broken](absent.md)\n```\n`[broken](absent.md)`\n"
    )
    assert validate_file(root, "docs/report.md") == 0


def test_reference_links_images_html_and_titles_are_checked(reports):
    root, report = reports
    report.write_text(
        '# Results\n\n[Product][home]\n![Image](../source.py "Owned file")\n<a href="../README.md#product">Home</a>\n[home]: ../README.md\n'
    )
    assert validate_file(root, "docs/report.md") == 3
    report.write_text(
        report.read_text().replace('href="../README.md#product"', 'href="absent.md"')
    )
    with pytest.raises(ValueError, match="Broken local"):
        validate_file(root, "docs/report.md")


def test_angle_destination_supports_spaces_and_balanced_parentheses(reports):
    root, report = reports
    (root / "docs/owned file.md").write_text("# Owned file\n")
    (root / "docs/owned(file).md").write_text("# Owned file\n")
    report.write_text(
        "# Results\n\n[Spaces](<owned file.md>)\n[Parentheses](owned(file).md)\n"
    )
    assert validate_file(root, "docs/report.md") == 2


@pytest.mark.parametrize(
    "link,expected",
    [
        ("[![Status](../present.svg)](missing.md)", ["../present.svg", "missing.md"]),
        (r"[Array \[index\]](missing.md)", ["missing.md"]),
    ],
)
def test_nested_images_and_escaped_label_brackets_check_outer_destination(
    reports, link, expected
):
    root, report = reports
    (root / "present.svg").write_text("<svg></svg>\n")
    report.write_text("# Results\n\n" + link + "\n")
    assert markdown_links(report.read_text())[0] == expected
    with pytest.raises(ValueError, match="Broken local"):
        validate_file(root, "docs/report.md")
    report.write_text(report.read_text().replace("missing.md", "../README.md"))
    assert validate_file(root, "docs/report.md") == len(expected)


def test_empty_invalid_title_nul_and_symlink_reports_fail(reports):
    root, report = reports
    for text in ("", "Untitled prose\n", "# Results\0\n"):
        report.write_text(text)
        with pytest.raises(ValueError, match="title"):
            validate_file(root, "docs/report.md")
    report.unlink()
    report.symlink_to("../README.md")
    with pytest.raises(ValueError, match="regular file"):
        validate_file(root, "docs/report.md")


def test_symlink_destination_cannot_escape_repository(reports, tmp_path):
    root, report = reports
    outside = root.parent / (root.name + "-outside.md")
    outside.write_text("# Unrelated user file\n")
    (root / "docs/outside.md").symlink_to(outside)
    report.write_text("# Results\n\n[Outside](outside.md)\n")
    with pytest.raises(ValueError, match="Broken local"):
        validate_file(root, "docs/report.md")


def test_directory_links_are_valid_without_file_heading(reports):
    root, report = reports
    (root / "archive").mkdir()
    report.write_text("# Results\n\n[Archive](../archive/)\n")
    assert validate_file(root, "docs/report.md") == 1
    report.write_text("# Results\n\n[Archive](../archive/#title)\n")
    with pytest.raises(ValueError, match="Directory"):
        validate_file(root, "docs/report.md")


def test_standard_library_cli_records_failure_and_duration_without_application_deps(
    reports,
):
    root, report = reports
    classification = root / "classification.json"
    classification.write_text(
        json.dumps(
            {
                "schema": 2,
                "lane": "source",
                "profile": "full",
                "valid": True,
                "reason": "full-validation",
                "base": "a" * 40,
                "head": "b" * 40,
                "comparison_base": "a" * 40,
                "markdown": ["docs/report.md"],
            }
        )
    )
    output = root / "validation.json"
    args = [
        sys.executable,
        "-S",
        str(ROOT / "scripts/ci/validate_reports.py"),
        "--classification",
        str(classification),
        "--output",
        str(output),
    ]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=10)
    assert (
        result.returncode == 0
        and json.loads(output.read_text())["outcome"] == "success"
    )
    report.write_text("# Broken report\n\n[Link](missing.md)\n")
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, timeout=10)
    receipt = json.loads(output.read_text())
    assert result.returncode == 1 and receipt["outcome"] == "failure"
    assert receipt["execution_ms"] >= 0


def test_heading_slug_uses_visible_link_and_code_label():
    _, anchors = markdown_links("# [Saved](README.md) `state`\n")
    assert anchors == {"saved-state"}


SKILL_METADATA = (
    "---\nname: stock-t3-agents\ndescription: Native workflow instructions.\n---\n"
)


@pytest.fixture
def native_documents(tmp_path):
    skill = tmp_path / NATIVE_SKILL
    skill.parent.mkdir(parents=True)
    skill.write_text(
        SKILL_METADATA + "\nRead [the runbook](../../../testing/agents/README.md).\n"
    )
    runbook = tmp_path / "testing/agents/README.md"
    runbook.parent.mkdir(parents=True)
    runbook.write_text(
        "# Native agents\n\nRead [the skill](../../.agents/skills/stock-t3-agents/SKILL.md).\n"
    )
    return tmp_path, skill


def test_actual_native_companions_pass_existing_link_and_fence_validation():
    assert NATIVE_MARKDOWN == {NATIVE_SKILL, "testing/agents/README.md"}
    for path in sorted(NATIVE_MARKDOWN):
        assert validate_file(ROOT, path) > 0


@pytest.mark.parametrize(
    "text",
    [
        "# Generic heading\n\nBody.\n",
        SKILL_METADATA.replace("stock-t3-agents", "other-skill") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "   ") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "null") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "true") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "false") + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "Nested: value")
        + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "Comment # not a field")
        + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "Tabbed\tvalue")
        + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "|\n  Block metadata")
        + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", '"Quoted metadata"')
        + "Body.\n",
        SKILL_METADATA.replace("Native workflow instructions.", "x" * 1025) + "Body.\n",
        SKILL_METADATA.replace("---\nname:", "---\nunknown: value\nname:") + "Body.\n",
        SKILL_METADATA.replace("description:", "name: stock-t3-agents\ndescription:")
        + "Body.\n",
        SKILL_METADATA.replace("description:", "description: Duplicate\ndescription:")
        + "Body.\n",
        SKILL_METADATA.removesuffix("---\n") + "Body.\n",
        SKILL_METADATA,
        SKILL_METADATA + "\n \n",
    ],
)
def test_exact_native_skill_requires_closed_metadata_and_body(native_documents, text):
    root, skill = native_documents
    skill.write_text(text)
    with pytest.raises(ValueError, match="native skill metadata"):
        validate_file(root, NATIVE_SKILL)


@pytest.mark.parametrize(
    "body,error",
    [
        ("[Broken](absent.md)\n", "Broken local"),
        ("```python\nunclosed()\n", "code fence"),
    ],
)
def test_skill_metadata_does_not_bypass_body_validation(native_documents, body, error):
    root, skill = native_documents
    skill.write_text(SKILL_METADATA + "\n" + body)
    with pytest.raises(ValueError, match=error):
        validate_file(root, NATIVE_SKILL)


def test_skill_metadata_is_not_a_title_exception_for_unrelated_documents(
    native_documents,
):
    root, skill = native_documents
    unrelated = root / "unrelated.md"
    unrelated.write_text(skill.read_text())
    with pytest.raises(ValueError, match="Markdown title"):
        validate_file(root, "unrelated.md")


@pytest.mark.parametrize(
    "documents",
    [(NATIVE_SKILL,), ("testing/agents/README.md",), tuple(sorted(NATIVE_MARKDOWN))],
)
def test_closed_local_native_document_cli_validates_real_content(
    native_documents, documents
):
    root, _ = native_documents
    output = root / "validation.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/validate_reports.py"),
            "--native-documents",
            *documents,
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert json.loads(output.read_text())["files"] == len(documents)


@pytest.mark.parametrize(
    "arguments,code",
    [
        (["--native-documents", "unrelated.md"], 2),
        (["--native-documents", "../testing/agents/README.md"], 2),
        (["--native-documents"], 2),
        (
            ["--native-documents", NATIVE_SKILL, "--classification", "descriptor.json"],
            2,
        ),
        (["--native-documents", NATIVE_SKILL, NATIVE_SKILL], 1),
    ],
)
def test_local_native_document_cli_rejects_unknown_or_ambiguous_inputs(
    native_documents, arguments, code
):
    root, _ = native_documents
    output = root / "validation.json"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(ROOT / "scripts/ci/validate_reports.py"),
            *arguments,
            "--output",
            str(output),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == code
    if code == 1:
        assert json.loads(output.read_text())["outcome"] == "failure"
    else:
        assert not output.exists()
