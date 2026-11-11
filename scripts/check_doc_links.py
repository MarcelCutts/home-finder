"""Check and re-pin the commit-pinned code links in a Markdown doc.

A pinned link names a commit, a file and usually a line range:

    https://github.com/<owner>/<repo>/blob/<40-hex commit>/<path>#L<start>-L<end>

Such a link never breaks, but the code it cites moves on. ``check`` reports the
links whose cited lines have since changed; that is advice, and never fails. It
exits 1 only for a link that is wrong at its own pinned commit. ``repin`` moves
every link whose cited lines are unchanged to a newer commit, and leaves the
rest pinned where they were for someone to re-read against the sentence that
cites them.

Standard library only: CI runs this with the runner's own Python, before any
project dependency is installed.

Run: python scripts/check_doc_links.py check docs/ingest-pipeline.md
     python scripts/check_doc_links.py check docs/ingest-pipeline.md --since origin/main
     python scripts/check_doc_links.py repin docs/ingest-pipeline.md --to origin/main
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# current: same lines, same place.  moved: same lines, new line numbers.
# changed: a cited line was edited or removed, or lines were added inside the range.
# gone: the file no longer exists.  broken: the link is wrong at its own pinned commit.
Status = Literal["current", "moved", "changed", "gone", "broken"]
DRIFTED: frozenset[Status] = frozenset({"changed", "gone"})
_PROBLEM: dict[Status, str] = {
    "changed": "cites code that changed",
    "gone": "cites a deleted file",
    "broken": "is broken",
}

_LINK = re.compile(
    r"(?:\[`(?P<label>[^`\]]+)`\]\()?"  # the code-span label, when the link has one
    r"(?P<prefix>https://github\.com/[\w.-]+/[\w.-]+/blob/)"
    r"(?P<commit>[0-9a-f]{40})/(?P<path>[^\s#)\"]+)"
    r"(?:#L(?P<start>\d+)(?:-L(?P<end>\d+))?)?"
)
# A tree link names the whole repository at a commit, so it has no lines to compare.
_TREE_LINK = re.compile(r"(https://github\.com/[\w.-]+/[\w.-]+/tree/)[0-9a-f]{40}")

LineRange = tuple[int, int]  # first and last line, 1-based and inclusive


@dataclass(frozen=True)
class Link:
    doc_line: int
    commit: str
    path: str
    lines: LineRange | None  # None when the link cites the whole file

    @property
    def target(self) -> str:
        return self.path if self.lines is None else f"{self.path}{_anchor(self.lines)}"


@dataclass(frozen=True)
class Finding:
    status: Status
    lines: LineRange | None = None  # where the cited lines sit at the compared commit
    reason: str = ""


def _anchor(lines: LineRange) -> str:
    start, end = lines
    return f"#L{start}" if start == end else f"#L{start}-L{end}"


def _label_suffix(lines: LineRange) -> str:
    start, end = lines
    return f":{start}" if start == end else f":{start}\N{EN DASH}{end}"


def _link(match: re.Match[str], text: str) -> Link:
    lines = None
    if match["start"]:
        start = int(match["start"])
        lines = (start, int(match["end"]) if match["end"] else start)
    doc_line = text.count("\n", 0, match.start()) + 1
    return Link(doc_line, match["commit"], match["path"], lines)


def find_links(text: str) -> list[Link]:
    return [_link(match, text) for match in _LINK.finditer(text)]


class Repository:
    """Read-only access to file contents at a commit, with caching."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._commits: dict[str, str] = {}
        self._files: dict[tuple[str, str], list[str] | None] = {}
        self._line_maps: dict[tuple[str, str, str], dict[int, int]] = {}

    def _git(self, *args: str) -> str | None:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], capture_output=True, text=True, check=False
        )
        return result.stdout if result.returncode == 0 else None

    def resolve(self, ref: str) -> str:
        if ref not in self._commits:
            commit = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            if commit is None:
                raise SystemExit(
                    f"{ref} is not a commit in this clone. A shallow clone lacks older "
                    "commits: fetch the full history (git fetch --unshallow)."
                )
            self._commits[ref] = commit.strip()
        return self._commits[ref]

    def lines(self, commit: str, path: str) -> list[str] | None:
        key = (commit, path)
        if key not in self._files:
            content = self._git("show", f"{commit}:{path}")
            self._files[key] = None if content is None else content.splitlines()
        return self._files[key]

    def line_map(self, old_commit: str, new_commit: str, path: str) -> dict[int, int]:
        """Map each 1-based line that is unchanged between the commits to its new number.

        The alignment is difflib's, not git's, so the two can pair repeated lines
        differently. A mapped line always has identical text on both sides.
        """
        key = (old_commit, new_commit, path)
        if key not in self._line_maps:
            old = self.lines(old_commit, path) or []
            new = self.lines(new_commit, path) or []
            mapping: dict[int, int] = {}
            matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
            for block in matcher.get_matching_blocks():
                for offset in range(block.size):
                    mapping[block.a + offset + 1] = block.b + offset + 1
            self._line_maps[key] = mapping
        return self._line_maps[key]


def compare(link: Link, repo: Repository, commit: str) -> Finding:
    """Say what happened to a link's cited lines between its pinned commit and ``commit``."""
    # A pinned commit missing from the clone is not a broken link: stop, don't misreport.
    repo.resolve(link.commit)
    pinned = repo.lines(link.commit, link.path)
    if pinned is None:
        return Finding("broken", reason="the file is not in the pinned commit")
    if link.lines is not None and not 1 <= link.lines[0] <= link.lines[1] <= len(pinned):
        return Finding("broken", reason=f"the pinned file has only {len(pinned)} lines")
    if repo.lines(commit, link.path) is None:
        return Finding("gone", reason="the file no longer exists")
    if link.lines is None:
        return Finding("current")
    start, end = link.lines
    mapping = repo.line_map(link.commit, commit, link.path)
    lost = [number for number in range(start, end + 1) if number not in mapping]
    if lost:
        cited = end - start + 1
        return Finding("changed", reason=f"{len(lost)} of {cited} cited lines changed")
    new_start, new_end = mapping[start], mapping[end]
    if new_end - new_start != end - start:
        return Finding("changed", reason="lines were added inside the cited range")
    return Finding("current" if new_start == start else "moved", lines=(new_start, new_end))


def check(doc: Path, repo: Repository, against: str, since: str | None, github: bool) -> int:
    """Print the links that drifted; return 1 only when a link is broken."""
    commit = repo.resolve(against)
    baseline = repo.resolve(since) if since else None
    tally: Counter[Status] = Counter()
    problems: list[tuple[Link, Finding]] = []
    for link in find_links(doc.read_text()):
        finding = compare(link, repo, commit)
        tally[finding.status] += 1
        drifted_since_baseline = finding.status in DRIFTED and (
            baseline is None or compare(link, repo, baseline).status not in DRIFTED
        )
        if finding.status == "broken" or drifted_since_baseline:
            problems.append((link, finding))

    counts = ", ".join(f"{count} {status}" for status, count in sorted(tally.items()))
    print(f"{doc}: {sum(tally.values())} pinned links against {against}: {counts or 'none'}")
    for link, finding in problems:
        message = f"{link.target} {_PROBLEM[finding.status]}: {finding.reason}"
        if github:
            level = "error" if finding.status == "broken" else "warning"
            print(f"::{level} file={doc},line={link.doc_line}::{message}")
        else:
            print(f"{doc}:{link.doc_line}: {message}")
    return 1 if tally["broken"] else 0


def repin(doc: Path, repo: Repository, to: str) -> int:
    """Move every link whose cited lines are unchanged to ``to``; list the rest."""
    commit = repo.resolve(to)
    left: list[tuple[Link, Finding]] = []
    repinned = 0
    text = doc.read_text()

    def rewrite(match: re.Match[str]) -> str:
        nonlocal repinned
        link = _link(match, text)
        finding = compare(link, repo, commit)
        if finding.status not in ("current", "moved"):
            left.append((link, finding))
            return match.group(0)
        if link.commit != commit or finding.status == "moved":
            repinned += 1
        label, anchor = match["label"], ""
        if link.lines is not None and finding.lines is not None:
            anchor = _anchor(finding.lines)
            # A label that ends with the cited lines follows them; any other label is kept.
            old_suffix = _label_suffix(link.lines)
            if label and label.endswith(old_suffix):
                label = label.removesuffix(old_suffix) + _label_suffix(finding.lines)
        opening = f"[`{label}`](" if label else ""
        return f"{opening}{match['prefix']}{commit}/{link.path}{anchor}"

    rewritten = _LINK.sub(rewrite, text)
    rewritten = _TREE_LINK.sub(lambda match: match.group(1) + commit, rewritten)
    doc.write_text(rewritten)
    print(f"{doc}: moved {repinned} links to {commit[:7]}; {len(left)} left for review")
    for link, finding in left:
        print(f"{doc}:{link.doc_line}: {link.target} ({finding.status}: {finding.reason})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    check_parser = commands.add_parser("check", help="report links whose cited code changed")
    check_parser.add_argument("docs", nargs="+", type=Path)
    check_parser.add_argument("--against", default="HEAD", help="commit to compare with")
    check_parser.add_argument(
        "--since", help="only report links that were still unchanged at this commit"
    )
    check_parser.add_argument(
        "--github", action="store_true", help="print GitHub Actions annotations"
    )
    repin_parser = commands.add_parser("repin", help="move unchanged links to a newer commit")
    repin_parser.add_argument("docs", nargs="+", type=Path)
    repin_parser.add_argument("--to", required=True, help="commit to pin to, such as origin/main")
    args = parser.parse_args(argv)

    exit_code = 0
    for doc in args.docs:
        root = subprocess.run(
            ["git", "-C", str(doc.resolve().parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        repo = Repository(Path(root))
        if args.command == "check":
            exit_code |= check(doc, repo, args.against, args.since, args.github)
        else:
            exit_code |= repin(doc, repo, args.to)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
