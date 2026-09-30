"""Dependency-free Markdown/link checks; never fetch links or execute report code."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import time
from urllib.parse import unquote, urlsplit

if __package__:
    from .classify_changes import load_classification
else:
    from classify_changes import load_classification


class HTMLLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.anchors: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                self.links.append(value)
            if value is not None and (name == "id" or tag == "a" and name == "name"):
                self.anchors.add(value)


def prose(text: str) -> str:
    """Remove code fences before examining Markdown structure/links."""
    result = []
    fence = ""
    length = 0
    for line in text.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if (
                match
                and match[1][0] == fence
                and len(match[1]) >= length
                and not match[2].strip()
            ):
                fence = ""
            continue
        if match:
            fence, length = match[1][0], len(match[1])
        else:
            result.append(line)
    if fence:
        raise ValueError("Unclosed Markdown code fence")
    return "\n".join(result)


def destination(value: str) -> str:
    value = value.strip()
    if value.startswith("<"):
        end = value.find(">")
        if end < 1:
            raise ValueError("Invalid link destination")
        target, remainder = value[1:end], value[end + 1 :].strip()
    else:
        match = re.match(r"(\S+)(?:\s+(.*))?$", value)
        if match is None:
            raise ValueError("Empty link destination")
        target, remainder = match[1], match[2] or ""
    if remainder and not re.fullmatch(r'"[^"\n]*"|\x27[^\x27\n]*\x27', remainder):
        raise ValueError("Invalid link title")
    return re.sub(r"\\([\\()])", r"\1", target)


def markdown_links(text: str) -> tuple[list[str], set[str]]:
    heading_content = prose(text)
    # Code labels inside links remain valid; literal code examples are not links.
    content = re.sub(r"(`+)([^`]|(?!\1)`)*?\1", "", heading_content)
    references = {}
    for match in re.finditer(r"^ {0,3}\[([^]\n]+)\]:\s*(.+)$", content, re.MULTILINE):
        label = " ".join(match[1].lower().split())
        if label in references:
            raise ValueError("Duplicate link reference")
        references[label] = destination(match[2])
    content = re.sub(r"^ {0,3}\[[^]\n]+\]:.*$", "", content, flags=re.MULTILINE)
    links = list(references.values())
    starts = list(re.finditer(r"(?<!\\)!?\[[^]\n]*\]\(", content))
    for match in starts:
        start = index = match.end()
        depth = 1
        while index < len(content) and depth:
            if content[index] == "\\":
                index += 2
                continue
            if content[index] == "(":
                depth += 1
            elif content[index] == ")":
                depth -= 1
            index += 1
        if depth:
            raise ValueError("Unclosed Markdown link")
        links.append(destination(content[start : index - 1]))
    for match in re.finditer(r"(?<!\\)!?\[([^]\n]+)\]\[([^]\n]*)\]", content):
        label = " ".join((match[2] or match[1]).lower().split())
        if label not in references:
            raise ValueError("Missing Markdown link reference")
    html = HTMLLinks()
    html.feed(content)
    links.extend(html.links)
    for match in re.finditer(r"<(https?://[^\s<>]+|mailto:[^\s<>]+)>", content):
        links.append(match[1])
    anchors = set(html.anchors)
    counts: dict[str, int] = {}
    headings = re.findall(
        r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?$", heading_content, re.MULTILINE
    )
    headings += re.findall(
        r"^([^\n]+)\n {0,3}(?:=+|-+)\s*$", heading_content, re.MULTILINE
    )
    for heading in headings:
        heading = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug + (f"-{count}" if count else ""))
    return links, anchors


def validate_file(root: Path, relative: str) -> int:
    root = root.resolve()
    path = root / relative
    if (
        not path.resolve().is_relative_to(root)
        or path.is_symlink()
        or not path.is_file()
    ):
        raise ValueError("Report is not an owned regular file")
    text = path.read_text(encoding="utf-8")
    if not text.strip() or "\0" in text or not re.match(r"\A\s*#\s+\S", text):
        raise ValueError("Report needs a Markdown title and nonempty UTF-8 content")
    links, own_anchors = markdown_links(text)
    for link in links:
        parsed = urlsplit(link)
        if parsed.scheme:
            if parsed.scheme not in {"https", "http", "mailto"}:
                raise ValueError("Unsupported link scheme")
            if parsed.scheme != "mailto" and not parsed.netloc:
                raise ValueError("Invalid external link")
            continue
        if parsed.netloc or parsed.query or not parsed.path and not parsed.fragment:
            raise ValueError("Invalid local link")
        destination_path = unquote(parsed.path)
        if destination_path.startswith("/") or "\0" in destination_path:
            raise ValueError("Invalid local link path")
        target = path.parent / destination_path if destination_path else path
        if not target.resolve().is_relative_to(root) or not (
            target.is_file() or target.is_dir()
        ):
            raise ValueError("Broken local link")
        if parsed.fragment:
            if target.is_dir():
                raise ValueError("Directory link cannot carry a file heading")
            anchor = unquote(parsed.fragment)
            if target.suffix.lower() == ".md":
                anchors = (
                    own_anchors
                    if target == path
                    else markdown_links(target.read_text(encoding="utf-8"))[1]
                )
                if anchor not in anchors:
                    raise ValueError("Broken Markdown heading link")
            else:
                lines = re.fullmatch(r"L([1-9]\d*)(?:-L([1-9]\d*))?", anchor)
                if lines and max(int(value) for value in lines.groups() if value) > len(
                    target.read_bytes().splitlines()
                ):
                    raise ValueError("Broken source line link")
    return len(links)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    result = {
        "schema": 1,
        "outcome": "failure",
        "files": 0,
        "links": 0,
        "execution_ms": 0,
    }
    try:
        classification = load_classification(args.classification)
        if not classification["valid"]:
            raise ValueError("Invalid Git comparison")
        for path in classification["markdown"]:
            result["links"] += validate_file(Path.cwd(), path)
            result["files"] += 1
        result["outcome"] = "success"
    except (OSError, ValueError, UnicodeError):
        print("Report validation failed; inspect Markdown structure and local links.")
    result["execution_ms"] = round((time.monotonic() - started) * 1000, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        f"Report validation: {result['outcome']}; files={result['files']}; links={result['links']}"
    )
    return 0 if result["outcome"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
