#!/usr/bin/env python3
"""Canonical repository identity, owned in exactly one file.

The byte contract is exact: resolve the Git top-level, canonicalise it through
``realpath -e``, then feed the canonical root to the POSIX checksum utility
without a trailing newline. All state writers and readers import this module;
none may derive a repository key locally.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class RepoIdentityError(RuntimeError):
    """Base error for repository identity failures."""


class NotGitRepository(RepoIdentityError):
    """Raised when the supplied path is not inside a Git worktree."""


class CanonicalRoot(str):
    """JSON-safe root string with convenient ``Path`` joining."""

    def __truediv__(self, other: str | os.PathLike[str]) -> Path:
        return Path(self) / other


@dataclass(frozen=True)
class RepoIdentity:
    root: CanonicalRoot
    key: str

    def as_dict(self) -> dict[str, str]:
        return {"root": str(self.root), "key": self.key}


def _probe(path: str | os.PathLike[str]) -> str:
    candidate = Path(path).expanduser()
    if candidate.is_file():
        candidate = candidate.parent
    return os.fspath(candidate)


def resolve_repo_identity(path: str | os.PathLike[str]) -> RepoIdentity:
    """Return the canonical worktree root and its newline-free POSIX key."""
    probe = _probe(path)
    try:
        top_result = subprocess.run(
            ["git", "-C", probe, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RepoIdentityError(f"cannot execute git for {probe!r}: {exc}") from exc
    if top_result.returncode != 0:
        raise NotGitRepository(f"not inside a Git worktree: {probe!r}")
    top = top_result.stdout.rstrip("\r\n")
    if not top:
        raise RepoIdentityError(f"git returned an empty top-level for {probe!r}")
    try:
        root = subprocess.run(
            ["realpath", "-e", top],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.rstrip("\r\n")
        if not root:
            raise RepoIdentityError(f"realpath returned an empty root for {top!r}")
        checksum_output = subprocess.run(
            ["cksum"],
            input=root.encode("utf-8"),
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.decode("ascii", errors="strict")
        key = checksum_output.split()[0]
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeError, IndexError) as exc:
        raise RepoIdentityError(f"cannot derive canonical repository identity for {top!r}: {exc}") from exc
    return RepoIdentity(CanonicalRoot(root), key)



def try_resolve_repo_identity(path: str | os.PathLike[str]) -> RepoIdentity | None:
    try:
        return resolve_repo_identity(path)
    except RepoIdentityError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", "--cwd", dest="path", default=os.getcwd())
    parser.add_argument("--field", choices=("root", "key"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        identity = resolve_repo_identity(args.path)
    except RepoIdentityError as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2
    if args.json:
        print(json.dumps(identity.as_dict(), sort_keys=True))
    elif args.field:
        print(getattr(identity, args.field))
    else:
        print(f"{identity.key}\t{identity.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
