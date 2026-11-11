"""Tests for scripts/check_doc_links.py, run against a real throwaway git repository."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Import the script module from the scripts/ directory (not a package)
_SCRIPT_PATH = Path(__file__).parent.parent.parent / "scripts" / "check_doc_links.py"
_spec = importlib.util.spec_from_file_location("check_doc_links", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod  # dataclasses look their module up while it is executing
_spec.loader.exec_module(_mod)

main = _mod.main

BLOB = "https://github.com/example/project/blob"
TREE = "https://github.com/example/project/tree"
DASH = "\N{EN DASH}"  # a label writes a line range with an en dash, as the walkthrough does


def _git(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    result = subprocess.run(
        ["git", "-C", str(repo), *identity, "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _commit(repo: Path, files: dict[str, str | None]) -> str:
    """Write (or, for None, delete) each file, commit, and return the commit hash."""
    for name, content in files.items():
        path = repo / name
        if content is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    _git(repo, "add", "--all")
    _git(repo, "commit", "--quiet", "--message", "change")
    return _git(repo, "rev-parse", "HEAD")


def _file(*lines: str) -> str:
    """File content with one word per line, so a test can name the line it edits."""
    return "".join(f"{line}\n" for line in lines)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "--quiet", "--initial-branch=main")
    return tmp_path


@pytest.fixture
def first(repo: Path) -> str:
    """The commit the doc is pinned to: a six-line file and a second, one-line file."""
    return _commit(
        repo,
        {
            "src/app.py": _file("one", "two", "three", "four", "five", "six"),
            "src/old.py": _file("legacy"),
        },
    )


def _write_doc(repo: Path, *links: str) -> Path:
    doc = repo / "guide.md"
    doc.write_text("".join(f"Claim {n}. {link}\n" for n, link in enumerate(links, 1)))
    return doc


def test_check_tells_moved_lines_from_changed_lines_and_deleted_files(
    repo: Path, first: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        repo,
        f"[`app.py:2{DASH}3`]({BLOB}/{first}/src/app.py#L2-L3)",
        f"[`app.py:5`]({BLOB}/{first}/src/app.py#L5)",
        f"[`old.py:1`]({BLOB}/{first}/src/old.py#L1)",
        f"[`app.py`]({BLOB}/{first}/src/app.py)",
    )
    # A new first line moves everything down; line five is edited; old.py is deleted.
    _commit(
        repo,
        {
            "src/app.py": _file("zero", "one", "two", "three", "four", "FIVE", "six"),
            "src/old.py": None,
        },
    )

    exit_code = main(["check", str(doc)])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "4 pinned links against HEAD: 1 changed, 1 current, 1 gone, 1 moved" in out
    assert f"{doc}:2: src/app.py#L5 cites code that changed: 1 of 1 cited lines changed" in out
    assert f"{doc}:3: src/old.py#L1 cites a deleted file: the file no longer exists" in out
    assert "src/app.py#L2-L3" not in out  # moved lines still say the same thing


def test_lines_added_inside_a_cited_range_count_as_a_change(
    repo: Path, first: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(repo, f"[`app.py:2{DASH}3`]({BLOB}/{first}/src/app.py#L2-L3)")
    _commit(repo, {"src/app.py": _file("one", "two", "new", "three", "four", "five", "six")})

    main(["check", str(doc)])

    assert "lines were added inside the cited range" in capsys.readouterr().out


def test_check_fails_when_a_link_points_outside_its_pinned_file(
    repo: Path, first: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        repo,
        f"[`app.py:40`]({BLOB}/{first}/src/app.py#L40)",
        f"[`missing.py:1`]({BLOB}/{first}/src/missing.py#L1)",
    )

    exit_code = main(["check", str(doc), "--github"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert (
        f"::error file={doc},line=1::src/app.py#L40 is broken: the pinned file has only 6 lines"
        in out
    )
    assert "src/missing.py#L1 is broken: the file is not in the pinned commit" in out


def test_since_reports_only_the_links_that_drifted_after_that_commit(
    repo: Path, first: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        repo,
        f"[`app.py:2`]({BLOB}/{first}/src/app.py#L2)",
        f"[`app.py:5`]({BLOB}/{first}/src/app.py#L5)",
    )
    already_drifted = _commit(
        repo, {"src/app.py": _file("one", "TWO", "three", "four", "five", "six")}
    )
    _commit(repo, {"src/app.py": _file("one", "TWO", "three", "four", "FIVE", "six")})

    main(["check", str(doc), "--since", already_drifted, "--github"])

    out = capsys.readouterr().out
    assert "2 pinned links against HEAD: 2 changed" in out
    assert f"::warning file={doc},line=2::src/app.py#L5 cites code that changed" in out
    assert "src/app.py#L2 " not in out


def test_repin_moves_unchanged_links_and_leaves_changed_ones_pinned(
    repo: Path, first: str, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _write_doc(
        repo,
        f"[`app.py:2{DASH}3`]({BLOB}/{first}/src/app.py#L2-L3)",
        f"[`the second line`]({BLOB}/{first}/src/app.py#L2)",
        f"[`app.py:5`]({BLOB}/{first}/src/app.py#L5)",
        f"[`app.py`]({BLOB}/{first}/src/app.py)",
        f"[the project at that commit]({TREE}/{first})",
    )
    second = _commit(
        repo, {"src/app.py": _file("zero", "one", "two", "three", "four", "FIVE", "six")}
    )

    exit_code = main(["repin", str(doc), "--to", "main"])

    assert exit_code == 0
    assert doc.read_text().splitlines() == [
        f"Claim 1. [`app.py:3{DASH}4`]({BLOB}/{second}/src/app.py#L3-L4)",
        f"Claim 2. [`the second line`]({BLOB}/{second}/src/app.py#L3)",
        f"Claim 3. [`app.py:5`]({BLOB}/{first}/src/app.py#L5)",
        f"Claim 4. [`app.py`]({BLOB}/{second}/src/app.py)",
        f"Claim 5. [the project at that commit]({TREE}/{second})",
    ]
    out = capsys.readouterr().out
    assert f"moved 3 links to {second[:7]}; 1 left for review" in out
    assert f"{doc}:3: src/app.py#L5 (changed: 1 of 1 cited lines changed)" in out

    # Once re-pinned, only the link that was left behind still reports drift.
    main(["check", str(doc)])
    assert "4 pinned links against HEAD: 1 changed, 3 current" in capsys.readouterr().out


def test_a_pinned_commit_missing_from_the_clone_stops_with_an_explanation(
    repo: Path, first: str
) -> None:
    doc = _write_doc(repo, f"[`app.py:1`]({BLOB}/{'0' * 40}/src/app.py#L1)")

    with pytest.raises(SystemExit, match="is not a commit in this clone"):
        main(["check", str(doc)])
