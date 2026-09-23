"""Bounded current-pass evidence for the advisor's immutable tree comparison."""
from __future__ import annotations

import subprocess


def _git(root: str, *args: str | bytes) -> bytes:
    return subprocess.run(["git", "-C", root, *args], check=True, stdout=subprocess.PIPE).stdout


def current_pass_evidence(root: str, base_tree: str, candidate_tree: str) -> bytes:
    """Send ordinary change hunks and line counts for deleted files."""
    diff = _git(root, "diff", "--no-ext-diff", "--binary", "--unified=3",
                "--diff-filter=ACMRTUXB", base_tree, candidate_tree)
    deleted = _git(root, "diff", "--no-ext-diff", "--numstat", "-z", "--diff-filter=D",
                   base_tree, candidate_tree)
    for entry in deleted.split(b"\0"):
        if not entry:
            continue
        _, removed, path = entry.split(b"\t", 2)
        path = path.replace(b"\\", b"\\\\").replace(b"\n", b"\\n").replace(b"\r", b"\\r").replace(b"\t", b"\\t")
        count = removed if removed.isdigit() else b"binary"
        diff += b"diff --git a/" + path + b" b/" + path + b"\n"
        diff += b"deleted file: " + path + b" (" + count + b" lines)\n"
    return diff
