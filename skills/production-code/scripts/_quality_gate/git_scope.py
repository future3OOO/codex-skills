from __future__ import annotations

import os
import subprocess
from pathlib import Path

from hooks.lib.repo_identity import RepoIdentityError, resolve_repo_identity
from hooks.lib.state_store import _active_candidate_tree

from .findings import Numstat


def run_git(repo: Path, args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    # No git child here consumes stdin, and mktree would otherwise block on an
    # inherited open terminal the first time a repository has no HEAD.
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        encoding="utf-8",
        # Path bytes need not be valid UTF-8, and a lossy decode makes the blob
        # unaddressable. surrogateescape round-trips back through the argument
        # encoding, so a path Git reports can always be read again.
        errors="surrogateescape",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, **env} if env else None,
    )


def git_read(repo: Path, args: list[str]) -> tuple[str, str]:
    """Output and the failure reason ("" on success). One place interprets Git
    exit status, so failure-reporting and absence-tolerating callers cannot drift."""
    res = run_git(repo, args)
    if res.returncode != 0:
        return "", res.stderr.strip() or str(res.returncode)
    return res.stdout, ""


def git_text(repo: Path, args: list[str]) -> str:
    # For callers where absence and failure are the same answer.
    return git_read(repo, args)[0]


def git_ok(repo: Path, args: list[str]) -> bool:
    return run_git(repo, args).returncode == 0


def read_git_file(repo: Path, ref: str, rel_path: str) -> str | None:
    if not ref:
        return None
    text, failure = git_read(repo, ["show", f"{ref}:{rel_path}"])
    return None if failure else text


def parse_z_names(raw: str) -> set[str]:
    # -z transports carry literal names: no stripping, or a filename with
    # leading or trailing whitespace would be keyed under a different path.
    return {item for item in raw.split("\0") if item}


def parse_numstat_z(raw: str) -> list[Numstat]:
    records: list[Numstat] = []
    tokens = raw.split("\0")
    index = 0
    while index < len(tokens):
        parts = tokens[index].split("\t", 2)
        if len(parts) < 3:
            index += 1
            continue
        try:
            added: int | None = int(parts[0])
            deleted: int | None = int(parts[1])
        except ValueError:
            # Git writes "-" for a file it treats as binary. Record the absence
            # so the evaluation can report it, rather than inventing counts.
            added = deleted = None
        if parts[2]:
            records.append(Numstat(added=added, deleted=deleted, path=parts[2]))
            index += 1
        elif index + 2 < len(tokens):
            # A rename record carries an empty path field, then the old and new
            # names as their own NUL fields; the counts belong to the new path.
            records.append(Numstat(added=added, deleted=deleted, path=tokens[index + 2]))
            index += 3
        else:
            index += 1
    return records


def _parse_name_status(raw: str) -> tuple[set[str], dict[str, str]]:
    """Changed paths plus the new-to-old rename map from one -z transport.

    A rename record is R<score> followed by the old and new names. The entry,
    hunks, and counts all key on the new path; the old path is where the base
    text lives, which is what keeps a pure rename from reading as new content.
    """
    changed: set[str] = set()
    renamed: dict[str, str] = {}
    tokens = raw.split("\0")
    index = 0
    while index < len(tokens):
        status = tokens[index]
        if not status:
            index += 1
        elif status.startswith("R") and index + 2 < len(tokens):
            renamed[tokens[index + 2]] = tokens[index + 1]
            changed.add(tokens[index + 2])
            index += 3
        elif index + 1 < len(tokens):
            changed.add(tokens[index + 1])
            index += 2
        else:
            index += 1
    return changed, renamed


def _resolve_base(repo: Path, base_ref: str | None) -> tuple[str, str, list[str]]:
    """The one base commit for the comparison: (sha, source, errors)."""
    if base_ref:
        res = run_git(repo, ["rev-parse", "--verify", f"{base_ref}^{{commit}}"])
        if res.returncode != 0:
            return "", "caller", [f"base-ref not found: {base_ref}"]
        # The caller Adapter selects the base; this module captures the commit
        # it was given. Choosing a different one here would drop the very
        # difference the caller asked about.
        return res.stdout.strip(), "caller", []
    head = run_git(repo, ["rev-parse", "--verify", "HEAD^{commit}"])
    if head.returncode == 0:
        return head.stdout.strip(), "HEAD", []
    empty = run_git(repo, ["mktree"])
    if empty.returncode != 0:
        return "", "HEAD", ["cannot resolve an empty base tree"]
    return empty.stdout.strip(), "HEAD", []


def _capture_worktree(repo: Path) -> tuple[str, list[str]]:
    """Capture the worktree as one tree OID, or report why it was unstable."""
    try:
        return _active_candidate_tree(resolve_repo_identity(repo)), []
    except (OSError, RepoIdentityError) as exc:
        return "", [str(exc)]


def _diff_scope(repo: Path, base: str, tree: str) -> tuple[set[str], dict[str, str], str, list[Numstat], list[str]]:
    """The evaluated diff, plus the reads that failed to produce it.

    The diff belongs to the gate, not to repository configuration: an external
    driver or textconv filter can empty the textual patch while --name-status
    and --numstat still succeed. A non-zero exit becomes a recorded gap rather
    than an empty string that reads as "no change". Rename detection and its
    search budget are pinned with -M -l1000 so evaluation does not depend on
    repository diff configuration; past the budget Git degrades to
    delete-plus-add deterministically.
    """
    diff = ["diff", "-M", "-l1000", "--no-ext-diff", "--no-textconv", base, tree]
    errors: list[str] = []

    def read(args: list[str], transport: str) -> str:
        text, failure = git_read(repo, [*diff, *args])
        if failure:
            errors.append(f"diff {transport} read failed: {failure}")
        return text

    changed, renamed = _parse_name_status(read(["--name-status", "-z"], "name-status"))
    raw_diff = read(["--unified=0", "--no-color"], "unified")
    numstats = parse_numstat_z(read(["--numstat", "-z"], "numstat"))
    return changed, renamed, raw_diff, numstats, errors


def collect_scope(repo: Path, base_ref: str | None, *, staged_only: bool = False) -> dict[str, object]:
    """One collection flow: resolve the base, capture the candidate tree, diff
    the captured pair. `staged_only` selects the capture strategy — the exact
    Git index instead of a worktree snapshot — not a second collection path."""
    source = "index" if staged_only else "worktree-snapshot"
    unresolved = f"index-tree:{base_ref}...unresolved" if staged_only else f"commit-range:{base_ref}...worktree"
    if staged_only and not base_ref:
        return _scope("", "caller", "index-tree:unresolved", ["staged-only mode requires --base-ref"], candidate_source=source)
    base, base_source, errors = _resolve_base(repo, base_ref)
    if errors:
        return _scope("", base_source, unresolved, errors, candidate_source=source)
    if staged_only:
        # Capture the tree, then diff the captured tree, not the live index:
        # `--cached` re-reads whatever is staged now, so a concurrent stage could
        # be evaluated as if it were the candidate that gets authorised.
        tree = git_text(repo, ["write-tree"]).strip()
        capture_errors = [] if tree else ["git write-tree failed"]
    else:
        tree, capture_errors = _capture_worktree(repo)
    if capture_errors:
        scope_name = unresolved if staged_only else f"commit-range:{base}...worktree"
        return _scope(base, base_source, scope_name, capture_errors, candidate_source=source)
    changed, renamed, raw_diff, numstats, errors = _diff_scope(repo, base, tree)
    untracked: set[str] = set()
    if not staged_only:
        listed, failure = git_read(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
        if failure:
            # Failed discovery is not an absence of untracked files.
            errors.append(f"untracked discovery failed: {failure}")
        untracked = parse_z_names(listed) & changed
    scope_name = f"index-tree:{base[:12]}...{tree[:12]}" if staged_only else f"commit-range:{base[:12]}...worktree-snapshot"
    return _scope(
        base,
        base_source,
        scope_name,
        errors,
        changed_files=changed,
        renamed=renamed,
        untracked=untracked,
        raw_diff=raw_diff,
        numstats=numstats,
        candidate_source=source,
        candidate_tree=tree,
    )


def _scope(
    base_commit: str,
    base_source: str,
    changed_scope: str,
    errors: list[str],
    *,
    changed_files: set[str] | None = None,
    renamed: dict[str, str] | None = None,
    untracked: set[str] | None = None,
    raw_diff: str = "",
    numstats: list[Numstat] | None = None,
    candidate_source: str = "worktree-snapshot",
    candidate_tree: str = "",
) -> dict[str, object]:
    return {
        "base_commit": base_commit,
        "base_source": base_source,
        "changed_scope": changed_scope,
        "changed_files": changed_files or set(),
        "renamed": renamed or {},
        "untracked": untracked or set(),
        "raw_diff": raw_diff,
        "numstats": numstats or [],
        "errors": errors,
        "candidate_source": candidate_source,
        "candidate_tree": candidate_tree,
    }
