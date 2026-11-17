"""The current-pass diff the advisor wrapper sends.

git's ordinary diff between the two immutable trees, except that a deleted file
arrives as its header and line count: its body is the base tree's own content.
On #96 whole-definition test context and deleted bodies made this channel
1,067,228 bytes, beyond the codex transport's 1,048,576-character input.
"""
from __future__ import annotations

import subprocess


def _git(root: str, *args: str | bytes) -> bytes:
    return subprocess.run(["git", "-C", root, *args], check=True, stdout=subprocess.PIPE).stdout


def current_pass_evidence(root: str, base_tree: str, candidate_tree: str, *, since: str | None = None) -> bytes:
    """The base_tree -> candidate_tree diff with every deleted file reduced to its header. Given `since`,
    the tree the pass's last final judged, it is the whole pass's numstat, then only the diff since."""
    if since:
        delta = current_pass_evidence(root, since, candidate_tree)
        return _git(root, "diff", "--numstat", base_tree, candidate_tree) + (
            delta or b"no change since the judged tree\n")
    deleted = [record.split(b"\t", 2) for record in _git(  # git quotes unusual paths, so none spans lines
        root, "diff", "--numstat", "--diff-filter=D", base_tree, candidate_tree).splitlines()]
    diff = _git(root, "diff", "--no-ext-diff", "--binary", "--diff-filter=d", base_tree, candidate_tree)
    return diff + b"".join(b"diff --git a/%s b/%s\ndeleted file: %s\n" % (
        path, path, b"binary" if removed == b"-" else removed + b" lines") for _, removed, path in deleted)
