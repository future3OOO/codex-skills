#!/usr/bin/env python3
"""Run the canonical Repo Context Forge bootstrap and advance workflow state."""

from __future__ import annotations

import fcntl
import filecmp
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from hooks.lib.repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity  # noqa: E402
from hooks.lib.state_store import _active_candidate_tree  # noqa: E402
from hooks.lib.workflow_documents import graph_evidence_document  # noqa: E402
from hooks.lib.workflow_state import (  # noqa: E402
    NO_INSTANCE_ID,
    WorkflowError,
    commit_evidence_phase,
    instance_id,
    read_workflow,
    record_base_oid,
    record_pass_start_snapshot,
    safe_slug,
)

SOURCE_ROOT = Path("/home/prop_/.local/share/repo-context-forge/current")
BOOTSTRAP = SOURCE_ROOT / "scripts" / "codex_context_bootstrap.py"
INTAKE_LOCK = Path.home() / ".cache" / "repo-context-forge" / "intake.lock"


def _extract_option(argv: list[str], name: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == name and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(name + "="):
            return arg.split("=", 1)[1]
    return None


def _remove_option(argv: list[str], name: str) -> tuple[list[str], str | None]:
    output: list[str] = []
    value: str | None = None
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == name:
            if index + 1 >= len(argv):
                raise ValueError(f"{name} requires a value")
            value = argv[index + 1]
            index += 2
            continue
        if arg.startswith(name + "="):
            value = arg.split("=", 1)[1]
            index += 1
            continue
        output.append(arg)
        index += 1
    return output, value


def _record_pass_base(identity: RepoIdentity, slug: str, workflow_id: str, packet: Path) -> None:
    """Record the packet's already-resolved base as the pass's immutable OID.

    The producer owns base resolution, and its no-base sentinel is the head
    ref itself: with no resolvable base it substitutes the head ref for the
    base, so a packet whose base_ref equals head_ref carries no base and
    nothing is recorded — the gate keeps reporting the honest base-binding
    gap. With a real base, `git.merge_base` is its resolved fork-point commit.
    The first recorded OID survives reruns; a rerun that resolves a different
    commit is reported, never silently absorbed.
    """
    payload = json.loads(packet.read_text(encoding="utf-8"))
    target = payload.get("target_state")
    git_facts = payload.get("git")
    base_ref = target.get("base_ref") if isinstance(target, dict) else None
    head_ref = target.get("head_ref") if isinstance(target, dict) else None
    merge_base = git_facts.get("merge_base") if isinstance(git_facts, dict) else None
    if not base_ref or base_ref == head_ref or not isinstance(merge_base, str) or not merge_base:
        return
    recorded = record_base_oid(identity, slug, workflow_id, merge_base).get("baseOid")
    if recorded != merge_base:
        sys.stderr.write(
            f"note: pass base already recorded as {recorded}; this bootstrap resolved "
            f"{merge_base}; keeping the immutable recorded base\n"
        )


def _pass_start_snapshot(packet: Path) -> tuple[dict[str, str] | None, str]:
    """The identity of the index this intake analysed, or None when it is not complete.

    The advisory reaches this pass's index through the GitNexus selector and
    diffs the current candidate against the tree that index was built from. That
    tree is the index's own `indexedTree`, written by the producer that built it:
    the packet's `indexed_candidate_tree` is the analysed checkout's HEAD tree,
    which is a different object and not the baseline `detect-changes` reports
    using. Anything short of the whole identity records nothing, because a
    consumer cannot tell a missing field from an absent baseline.
    """
    payload = json.loads(packet.read_text(encoding="utf-8"))
    gitnexus = payload.get("gitnexus")
    target = payload.get("target_state")
    gitnexus = gitnexus if isinstance(gitnexus, dict) else {}
    target = target if isinstance(target, dict) else {}
    index_path = str(gitnexus.get("index_path") or "")
    if not index_path:
        return None, "the packet names no index directory for this analysis"
    try:
        meta = json.loads((Path(index_path) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the index metadata at {index_path} could not be read: {exc}"
    snapshot = {
        "indexRepo": str(gitnexus.get("repo") or ""),
        "indexPath": index_path,
        "analysisRepo": str(target.get("analysis_repo") or gitnexus.get("expected_repo_path") or ""),
        "sourceCommit": str(meta.get("lastCommit") or ""),
        "indexedTree": str(meta.get("indexedTree") or ""),
        "recordedAt": str(meta.get("indexedAt") or ""),
    }
    missing = sorted(name for name, value in snapshot.items() if not value.strip())
    if missing:
        return None, f"the index at {index_path} records no {', '.join(missing)}"
    return snapshot, ""


def _record_pass_start(identity: RepoIdentity, slug: str, workflow_id: str, packet: Path) -> None:
    """Record the pass-start index identity, first run wins, differing reruns reported."""
    snapshot, gap = _pass_start_snapshot(packet)
    if snapshot is None:
        record_pass_start_snapshot(identity, slug, workflow_id, gap=gap)
        return
    recorded = record_pass_start_snapshot(identity, slug, workflow_id, snapshot).get("passStartSnapshot")
    if recorded != snapshot:
        sys.stderr.write(
            f"note: pass-start snapshot already recorded as {recorded}; this intake resolved "
            f"{snapshot}; keeping the immutable recorded snapshot\n"
        )


def _run_producer(args: list[str]) -> int:
    """One producer at a time. GitNexus rewrites its global registry without an
    atomic replace, so two analyses finishing together can tear it and every
    later intake then fails. The lock covers the producer alone; packet assembly
    and evidence recording stay concurrent. Upstream fix: future3OOO/GitNexus#25."""
    INTAKE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(INTAKE_LOCK, "a+", encoding="utf-8") as lock:
        # The producer retains the same lock if this adapter is terminated.
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = subprocess.run(
            [sys.executable, str(BOOTSTRAP), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(lock.fileno(),),
            check=False,
        )
    sys.stdout.buffer.write(result.stdout)
    sys.stderr.buffer.write(result.stderr)
    return result.returncode


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> tuple[str, str]:
    """Output and the failure reason ("" on success) of one git command."""
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="surrogateescape",
        env={**os.environ, **env} if env else None,
        check=False,
    )
    if result.returncode != 0:
        return "", result.stderr.strip() or str(result.returncode)
    return result.stdout, ""


SSH_ORIGIN = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>.+)$")
GH_TIMEOUT_SECONDS = 15


def github_slug(origin: str) -> str | None:
    """The `owner/repo` a GitHub origin's host names, never a path segment's."""
    origin = origin.strip()
    if not origin:
        return None
    parsed = urlsplit(origin)
    host, path = parsed.hostname, parsed.path
    if not host:
        ssh = SSH_ORIGIN.match(origin)
        if not ssh:
            return None
        host, path = ssh.group("host").lower(), ssh.group("path")
    owner, _, repo = path.strip("/").removesuffix(".git").rpartition("/")
    if host != "github.com" or not owner or not repo or "/" in owner:
        return None
    return f"{owner}/{repo}"


def _branch_ref(root: Path, name: str, remotes: tuple[str, ...]) -> str | None:
    """The named branch as a full ref, preferring `remotes` in order; a bare name would read through `refs/tags` first."""
    candidates = [f"refs/remotes/{remote}/{name}" for remote in remotes]
    candidates.append(f"refs/heads/{name}")
    for candidate in candidates:
        _, missing = _git(root, "rev-parse", "--verify", "-q", candidate)
        if not missing:
            return candidate
    return None


def _names_base(argv: list[str]) -> bool:
    """Whether the caller already gave a base, under any spelling the producer takes.

    argparse accepts an unambiguous prefix, so `--ba` is as explicit as `--base`
    and appending a second one would silently win over it.
    """
    return any(
        "--base".startswith(token.split("=", 1)[0]) and len(token.split("=", 1)[0]) >= 4
        for token in argv
    )


def _pr_base_ref(root: Path, env: dict[str, str] | None = None) -> str | None:
    """The branch this checkout's PR merges into: a live PR in the origin fork, else the `gh-merge-base` config in the producer's upstream-first order."""
    branch, failure = _git(root, "branch", "--show-current")
    branch = branch.strip()
    if failure or not branch:
        return None
    environment = {**os.environ, **(env or {})}
    origin, _ = _git(root, "remote", "get-url", "origin")
    slug = github_slug(origin)
    if slug and shutil.which("gh", path=environment.get("PATH")):
        # The one remote call the adapter makes, and the bootstrap is an
        # edit-time gate: an expiry is just another lookup that cannot answer.
        try:
            answer = subprocess.run(
                ["gh", "pr", "view", branch, "--json", "baseRefName", "--jq", ".baseRefName"],
                cwd=root, env={**environment, "GH_REPO": slug}, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, encoding="utf-8",
                check=False, timeout=GH_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            answer = None
        name = answer.stdout.strip() if answer is not None and answer.returncode == 0 else ""
        # A base this checkout never fetched resolves to nothing; the config is
        # still a signal, so a named-but-absent branch falls through rather than
        # ending the search.
        resolved = _branch_ref(root, name, ("origin",)) if name else None
        if resolved:
            return resolved
    configured, _ = _git(root, "config", "--get", f"branch.{branch}.gh-merge-base")
    name = configured.strip()
    if not name:
        return None
    return _branch_ref(root, name, ("upstream", "origin"))


def _worktree_snapshot(root: Path) -> tuple[str, str]:
    """The canonical candidate tree and its measured failure, if any."""
    try:
        return _active_candidate_tree(resolve_repo_identity(root)), ""
    except (OSError, RepoIdentityError) as exc:
        reason = str(exc)
        prefix = "candidate capture failed at "
        return "", reason[len(prefix):] if reason.startswith(prefix) else reason


def _same_content(source: Path, target: Path) -> bool:
    if source.is_symlink() or target.is_symlink():
        return (
            source.is_symlink() and target.is_symlink()
            and os.readlink(source) == os.readlink(target)
        )
    return filecmp.cmp(source, target, shallow=False)


def _overlay_mismatch(root: Path, analysis_repo: Path, head_sha: str, tree: str) -> str:
    """Per-path proof that the analysis worktree materialized the snapshot.

    Every path differing between the analyzed checkout's head and the snapshot
    tree must hold the snapshot's exact content in the analysis worktree, and a
    deleted path must be absent there; unchanged paths already match through the
    shared head commit. Returns the first measured mismatch, "" when none.
    """
    if not analysis_repo.is_dir():
        return f"the analysis worktree is gone: {analysis_repo}"
    listed, failure = _git(root, "diff-tree", "-r", "-z", "--name-only", head_sha, tree)
    if failure:
        return f"cannot diff the analyzed head against the snapshot: {failure}"
    for rel_path in (path for path in listed.split("\0") if path):
        source, target = root / rel_path, analysis_repo / rel_path
        try:
            if not os.path.lexists(source):
                if os.path.lexists(target):
                    return f"{rel_path}: deleted in the snapshot but present in the analysis worktree"
            elif not os.path.lexists(target) or not _same_content(source, target):
                return f"{rel_path}: analysis worktree content does not match the snapshot"
        except OSError as exc:
            return f"{rel_path}: snapshot fidelity could not be measured: {exc}"
    return ""


def _snapshot_binding(
    root: Path,
    packet_path: Path,
    tree_before: str,
) -> tuple[dict[str, str] | None, str]:
    """The measured claim "this analysis covered snapshot tree T", or its gap.

    The binding is asserted only from measurements taken here: the source tree
    held still across the producer run, the packet's base ref resolves, and the
    analysis worktree demonstrably materialized the snapshot. Anything less
    records the named gap instead — the gate then reports the honest absence.
    """
    tree_after, failure = _worktree_snapshot(root)
    if failure:
        return None, f"snapshot capture failed after the producer run: {failure}"
    if tree_after != tree_before:
        return None, (
            "the worktree changed during the producer run "
            f"({tree_before[:12]} then {tree_after[:12]})"
        )
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"the machine packet could not be read: {exc}"
    target = packet.get("target_state") if isinstance(packet, dict) else None
    target = target if isinstance(target, dict) else {}
    head_sha = str(target.get("head_sha") or "")
    analysis_repo = str(target.get("analysis_repo") or "")
    base_ref = str(target.get("base_ref") or "")
    if not head_sha or not analysis_repo:
        return None, "the packet names no analyzed head or analysis worktree"
    if not base_ref:
        return None, "the packet names no base ref to declare the evidence against"
    base_sha, failure = _git(root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    if failure:
        return None, f"the packet base ref does not resolve to a commit: {base_ref}"
    mismatch = _overlay_mismatch(root, Path(analysis_repo), head_sha, tree_before)
    if mismatch:
        return None, mismatch
    return {"base": base_sha.strip(), "candidate": tree_before}, ""


def main(argv: list[str]) -> int:
    if "-h" in argv or "--help" in argv:
        print(
            "Repo Context Forge bootstrap wrapper. Wrapper options (consumed here, not by the producer):\n"
            "  --workflow-slug <slug>   record the packet on this active governed workflow\n"
            "  --revalidate             fast post-edit refresh: local mode, no SoulForge map rebuild;\n"
            "                           requires --workflow-slug\n"
            "Every other option is passed to the producer; its help follows.\n"
        )
    revalidate = "--revalidate" in argv
    if revalidate:
        # Fast post-intake revalidation (issue #182): the typed gate needs a
        # candidate-bound graph projection, not another SoulForge map build.
        # These wrapper-owned refusals run before the producer-existence
        # check, so they hold in every environment — CI without the producer
        # installed included — while non-revalidate invocations keep the
        # established producer-first ordering.
        try:
            probe_args, probe_slug = _remove_option(list(argv), "--workflow-slug")
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 2
        if not probe_slug:
            sys.stderr.write(
                "error: --revalidate refuses without --workflow-slug: fast revalidation "
                "re-records graph evidence on an active governed workflow\n"
            )
            return 2
        # Every occurrence, not just the first, and every argparse-recognized
        # abbreviation ("--mo", "--mod" resolve unambiguously to --mode; "--m"
        # is ambiguous and the producer refuses it itself): the producer honors
        # the last mode occurrence, so any admitted non-local one defeats the
        # forced-local invariant.
        def mode_option(token: str) -> bool:
            head = token.split("=", 1)[0]
            return "--mode".startswith(head) and len(head) >= 4
        modes = [probe_args[i + 1] for i, arg in enumerate(probe_args)
                 if mode_option(arg) and "=" not in arg and i + 1 < len(probe_args)]
        modes += [arg.split("=", 1)[1] for arg in probe_args
                  if mode_option(arg) and "=" in arg]
        if any(mode != "local" for mode in modes):
            sys.stderr.write(
                "error: --revalidate analyzes the dirty candidate in local mode; "
                f"refusing modes {sorted(set(modes))!r}\n"
            )
            return 2
    if not BOOTSTRAP.exists():
        sys.stderr.write(
            f"<blocker>repo-context-forge source bootstrap not found at {BOOTSTRAP}</blocker>\n"
        )
        return 2
    try:
        args, workflow_slug = _remove_option(list(argv), "--workflow-slug")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    if revalidate:
        # Slug and modes were validated above; the map phase is the producer's
        # only wrapper-skippable heavy phase (measured ~23s of ~60s), while
        # target selection and summaries cost ~1s and stay, so the evidence
        # remains honestly produced for the current dirty candidate.
        args = [arg for arg in args if arg != "--revalidate"]
        # Always trailing: the producer honors the last occurrence, so this
        # wins over any earlier spelling the validation above admitted.
        args += ["--mode", "local"]
        args, _ = _remove_option(args, "--map-build")
        args += ["--map-build", "never"]
    elif workflow_slug:
        # The advisor checkpoint binds its projection to the candidate tree. On a
        # dirty checkout the producer's own choice (pr, once the branch is past
        # its base) binds HEAD instead and every consult refuses; measured on a
        # real pass as one 48s rerun per consult. Local mode analyzes the
        # candidate, and the trailing occurrence wins over any caller-passed mode.
        root = Path(_extract_option(args, "--repo") or os.getcwd())
        head_tree, failure = _git(root, "rev-parse", "HEAD^{tree}")
        candidate, snapshot_failure = _worktree_snapshot(root)
        if not failure and not snapshot_failure and candidate != head_tree.strip():
            sys.stderr.write("note: dirty governed checkout; analyzing the candidate in local mode\n")
            args += ["--mode", "local"]
    if workflow_slug and not _names_base(args):
        # The recorded base names what the PR merges into, on the first run and
        # on every revalidation, so the packet surface, the snapshot-binding
        # base, and baseOid all come from one ref. A caller's --base still wins.
        base = _pr_base_ref(Path(_extract_option(args, "--repo") or os.getcwd()))
        if base:
            args += ["--base", base]
    if "--enforce-intake" not in args:
        args.append("--enforce-intake")
    if not workflow_slug:
        return _run_producer(args)
    if str(SOURCE_ROOT) not in sys.path:
        sys.path.insert(0, str(SOURCE_ROOT))
    from repo_context_forge import canonical_repo_identity

    try:
        slug = safe_slug(workflow_slug)
        identity = resolve_repo_identity(_extract_option(args, "--repo") or os.getcwd())
        state = read_workflow(identity)
        if state is None or state.get("slug") != slug:
            raise WorkflowError("Repo Context Forge slug does not match the active workflow")
        captured_workflow_id = instance_id(state)
        if captured_workflow_id is None:
            raise WorkflowError(NO_INSTANCE_ID)
    except (WorkflowError, RepoIdentityError, ValueError) as exc:
        sys.stderr.write(f"<blocker>cannot bind Repo Context Forge to the active workflow: {exc}</blocker>\n")
        return 2
    if state.get("passStartSnapshot"):
        # The pass already indexed a checkout and recorded it as its baseline;
        # the producer's candidate slot keeps this intake off that one.
        args.append("--candidate-slot")
    # The machine packet is asked of the same packet-generation pass that renders the
    # prompt, into a private directory this process owns: one graph execution, and
    # nothing written to the user's checkout or the state root.
    with tempfile.TemporaryDirectory(prefix="repo-context-forge-packet-") as scratch:
        packet = Path(scratch) / "packet.json"
        # The snapshot the producer is about to overlay, captured before it runs:
        # binding evidence to a tree measured afterwards could name content the
        # analysis never saw.
        tree_before, capture_failure = _worktree_snapshot(Path(identity.root))
        code = _run_producer([*args, "--packet-json-out", str(packet)])
        if code != 0:
            return code
        if capture_failure:
            snapshot, snapshot_gap = None, f"snapshot capture failed: {capture_failure}"
        else:
            snapshot, snapshot_gap = _snapshot_binding(Path(identity.root), packet, tree_before)
        try:
            commit_evidence_phase(
                identity,
                slug,
                captured_workflow_id,
                "repo-context-forge",
                graph_evidence_document(
                    str(packet),
                    slug=slug,
                    workflow_id=captured_workflow_id,
                    source_root=str(identity.root),
                    canonical_source_repo=canonical_repo_identity(Path(identity.root)),
                    snapshot=snapshot,
                    snapshot_gap=snapshot_gap or None,
                ),
            )
            _record_pass_base(identity, slug, captured_workflow_id, packet)
            if not revalidate:
                # Revalidation analyses the dirty candidate, a different graph
                # the typed gate consumes; the advisory's baseline stays the
                # index this pass started against.
                _record_pass_start(identity, slug, captured_workflow_id, packet)
        except (WorkflowError, RepoIdentityError, ValueError) as exc:
            sys.stderr.write(
                f"<blocker>cannot record Repo Context Forge graph evidence: {exc}; "
                "rerun the bootstrap</blocker>\n"
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
