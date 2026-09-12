"""Behavior-map policy layered onto the existing public workflow CLI."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from . import behavior_map, tdd_surface
from .command_runner import (
    emit_json as _emit_json,
    print_output as _print_output,
    run as _run,
    run_entry as _run_entry,
)
from .repo_identity import RepoIdentity, resolve_repo_identity
from .state_store import (
    _active_candidate_tree,
    atomic_write_json,
    tree_manifest,
    production_changes,
    read_json,
    repo_state_dir,
    utc_timestamp,
)
from .workflow_documents import load_json
from .workflow_state import (
    NO_INSTANCE_ID,
    TDD_CLOSED,
    WorkflowError,
    _executed_selections,
    _head_oid,
    annotate_tdd_evidence,
    bound_state,
    commit_tdd,
    evidence_document,
    instance_id,
    safe_slug,
)

JsonObject = dict[str, object]


def _tdd_parser() -> argparse.ArgumentParser:
    """The sole grammar for mapped and imported-legacy TDD options."""
    parser = argparse.ArgumentParser(
        prog="workflow tdd",
        epilog=(
            "imported pre-map workflows keep the legacy flags: "
            "--behavior, --seam, --expected-failure"
        ),
    )
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phase", choices=("red", "green"))
    mode.add_argument("--not-required", metavar="REASON")
    parser.add_argument("--behavior-id")
    parser.add_argument("--behavior")
    parser.add_argument("--seam", default="")
    parser.add_argument("--expected-failure", default="")
    parser.add_argument("--timeout", type=int, default=900)
    return parser


def _map_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd-map")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--workflow-id", required=True)
    parser.add_argument("--input", required=True)
    return parser


def _evidence_pair(
    identity: RepoIdentity, state: JsonObject
) -> tuple[JsonObject | None, JsonObject | None]:
    """Read preflight only when the current TDD document has no map."""
    tdd_id, preflight_id = state.get("tddEvidence"), state.get("preflightEvidence")
    tdd = evidence_document(identity, tdd_id if isinstance(tdd_id, str) else None)
    preflight = (None if isinstance(tdd, dict) and tdd.get("behaviorMap") is not None
                 else evidence_document(identity, preflight_id if isinstance(preflight_id, str) else None))
    return tdd, preflight


def current_map(
    identity: RepoIdentity, state: JsonObject
) -> tuple[list[JsonObject] | None, JsonObject | None]:
    """The current map and current TDD evidence, falling back to preflight."""
    tdd_document, preflight_document = _evidence_pair(identity, state)
    return behavior_map.recorded_map(tdd_document, preflight_document), tdd_document


def _legacy_green_candidate(
    current: JsonObject | None,
    workflow_id: str,
    args: argparse.Namespace,
) -> bool:
    """Only an imported, already-open legacy RED may finish free-form."""
    if not isinstance(current, dict) or current.get("behaviorMap") is not None:
        return False
    if args.behavior_id is not None or args.not_required is not None:
        return False
    if args.phase != "green":
        return False
    if not all(
        (
            current.get("schemaVersion") == 1,
            current.get("workflowId") == workflow_id,
            current.get("status") == "pending",
            isinstance(current.get("behavior"), str),
            isinstance(current.get("seam"), str),
            isinstance(current.get("command"), str),
        )
    ):
        return False
    runs = current.get("runs")
    return isinstance(runs, list) and any(
        isinstance(run, dict)
        and run.get("phase") == "red"
        and run.get("valid") is True
        for run in runs
    )


def _map_doc(
    *,
    slug: str,
    workflow_id: str,
    items: list[JsonObject],
    status: str,
    kind: str,
    active: str | None = None,
    reassessment_pending: str | None = None,
    reassessment: str | None = None,
    **extra: object,
) -> JsonObject:
    document: JsonObject = {
        "schemaVersion": 2,
        "slug": slug,
        "workflowId": workflow_id,
        "kind": kind,
        "status": status,
        "behaviorMap": items,
        "activeBehaviorId": active,
        "reassessmentPending": reassessment_pending,
        "updatedAt": utc_timestamp(),
        **extra,
    }
    if reassessment is not None:
        document["reassessment"] = reassessment
    return document


def edit_blockers(
    identity: RepoIdentity, state: JsonObject, *, reminders: list[str] | None = None,
) -> list[str]:
    """Ordering advice and, when requested, obligations from the same map read."""
    items, document = current_map(identity, state)
    if items is None:
        return []
    if reminders is not None:
        reminders.append(behavior_map.obligation_digest(items, (document or {}).get("activeBehaviorId")))
    reason = behavior_map.edit_blocker(items)
    return [reason] if reason else []


def completion_blockers(identity: RepoIdentity, state: JsonObject) -> list[str]:
    """Map conditions that forbid workflow completion (diagnostic; complete() re-judges in its transaction)."""
    return behavior_map.closure_blockers(*_evidence_pair(identity, state))


def _not_required(
    args: argparse.Namespace,
    identity: RepoIdentity,
    state: JsonObject,
    items: list[JsonObject] | None,
) -> int:
    reason = args.not_required.strip()
    if not reason:
        raise ValueError("--not-required requires a non-empty reason")
    if args.runner_command:
        raise ValueError("--not-required does not accept a command")
    existing_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    existing = evidence_document(identity, existing_id)
    if items is not None and not behavior_map.all_disposition_only(items):
        raise WorkflowError(
            "--not-required requires every mapped item to be already-satisfied "
            "or omitted by governing evidence"
        )
    runs = existing.get("runs") if isinstance(existing, dict) else None
    if isinstance(runs, list) and any(
        isinstance(run, dict) and run.get("valid") is True for run in runs
    ):
        raise WorkflowError("--not-required cannot replace valid TDD evidence")
    document: JsonObject = (
        _map_doc(
            slug=str(state["slug"]),
            workflow_id=str(state["workflowId"]),
            items=items,
            status="not-required",
            kind="map",
            reassessment=reason,
            reason=reason,
        )
        if items is not None
        else {
            "schemaVersion": 1,
            "slug": str(state["slug"]),
            "workflowId": str(state["workflowId"]),
            "status": "not-required",
            "reason": reason,
            "updatedAt": utc_timestamp(),
        }
    )
    _, evidence_id = commit_tdd(
        identity,
        str(state["slug"]),
        str(state["workflowId"]),
        document,
        "not-required",
        expected_evidence_id=existing_id,
    )
    _emit_json({"summaryId": evidence_id, "status": "not-required"})
    return 0


def _workflow_id_of(state: JsonObject) -> str:
    value = instance_id(state)
    if value is None:
        raise WorkflowError(NO_INSTANCE_ID)
    return str(value)


def _active_candidate(identity: RepoIdentity, value: str) -> tuple[JsonObject, str, str]:
    state = bound_state(identity, safe_slug(value))
    if state.get("revalidation"):
        raise WorkflowError(TDD_CLOSED)
    if state.get("preflight") != "passed" or not state.get("preflightEvidence"):
        raise WorkflowError("tdd requires recorded preflight evidence")
    return state, str(state["slug"]), _workflow_id_of(state)


def _candidate_drift(
    existing: JsonObject,
    contract: dict[str, str],
    surface: JsonObject,
    command_text: str,
) -> tuple[list[JsonObject], str]:
    """Every requested candidate field that differs from the active one."""
    drift = [
        {"field": name, "recorded": existing.get(name), "requested": value}
        for name, value in contract.items()
        if existing.get(name) != value
    ]
    recorded = existing.get("surface")
    if isinstance(recorded, dict):
        return drift + tdd_surface.differences(recorded, surface), ""
    if existing.get("command") == command_text:
        return drift, ""
    drift.append(
        {
            "field": "command",
            "recorded": existing.get("command"),
            "requested": command_text,
        }
    )
    return drift, "\n  this candidate predates normalized surfaces; rerun RED under the new contract"


def _drift_report(drift: list[JsonObject]) -> str:
    return "".join(
        f"\n  {item['field']}: recorded {item['recorded']!r}, requested {item['requested']!r}"
        for item in drift
    )


def _candidate_command(
    runner_command: list[str] | None,
) -> tuple[list[str], str, JsonObject]:
    """Extract one cycle's command, exact text identity, and normalized surface."""
    command = runner_command or []
    if not command:
        raise ValueError("a command is required after --")
    return command, shlex.join(command), tdd_surface.identify(command)


_BASELINE_STAMP = behavior_map.BASELINE_STAMP


def _pass_proof(
    surface: JsonObject, output: str, *, baseline: bool, exit_code: int,
) -> tuple[dict[str, object] | None, str, bool]:
    """The final result positively identifies no execution, not an unknown failure.

    A pass is the surface passing, not the command exiting 0: a runner's report of
    an executed passing test; a non-runner exit 0 closes its own RED, never a baseline."""
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest"}:
        if baseline:
            return None, (
                "a baseline needs the runner's own report of an executed passing "
                "test; a non-runner operation exiting 0 is not one"
            ), False
        return {"quality": "operation-succeeded", "runner": str(runner)}, "", False
    output = tdd_surface.ANSI_ESCAPE.sub("", output)
    if runner == "unittest":
        # unittest exits 0 with skipped and expected-failure tests inside its
        # Ran count; only its own result line says how many did not genuinely
        # pass. That line is the first OK line after the last Ran line: test
        # output either precedes Ran (unbuffered) or flushes after the runner
        # has finished (buffered), never between the two runner writes.
        runs = list(tdd_surface.UNITTEST_RAN.finditer(output))
        executed = int(runs[-1].group(1)) if runs else 0
        result = re.search(r"(?m)^OK(?: \((.*)\))?$", output[runs[-1].end():]) if runs else None
        skipped = re.fullmatch(r"skipped=(\d+)", result.group(1) or "") if result else None
        nonexecuting = bool(result and (
            (executed == 0 and not result.group(1))
            or (skipped and int(skipped.group(1)) == executed)
        ))
        executed -= sum(
            int(count)
            for count in re.findall(r"(?:skipped|expected failures)=(\d+)", result.group(1) or "")
        ) if result else 0
    else:
        # Only the terminal summary line describes the run; text a test prints
        # under -s, or a run that exits before the summary, counts nothing.
        summaries = tdd_surface.PYTEST_SUMMARY.findall(output)
        passed = re.search(r"(?<!\d)(\d+) passed\b", summaries[-1]) if summaries else None
        executed = int(passed.group(1)) if passed else 0
        nonexecuting = bool(summaries and re.fullmatch(
            r"no tests ran|\d+ (?:skipped|deselected|warnings?)(?:, \d+ (?:skipped|deselected|warnings?))*",
            summaries[-1],
        ) and (exit_code == 5 or (
            exit_code == 0 and re.search(r"[1-9]\d* (?:skipped|deselected)\b", summaries[-1])
        )))
    if executed < 1:
        return None, f"{runner} did not report an executed passing test", nonexecuting
    return {"quality": "baseline-passed", "runner": runner, "testsExecuted": executed}, "", False


def _tree_binding(identity: RepoIdentity, state: JsonObject) -> dict[str, object]:
    """The tree a RED-phase run is launched on: production paths changed since the
    pass began (tracked or untracked) and the commits on either side. Order of
    proof is recorded here as evidence; nothing refuses on it."""
    start = state.get("passStartOid")
    return {
        "productionChanged": production_changes(identity, start if isinstance(start, str) and start else "HEAD"),
        "passStartOid": start,
        "headOid": _head_oid(identity),
    }


def _run_tdd(values: list[str]) -> int:
    """Run the one mapped-or-imported-legacy candidate-cycle lifecycle."""
    dash = values.index("--") if "--" in values else None
    recorder_region = values if dash is None else values[:dash]
    runner_region = [] if dash is None else values[dash + 1 :]
    args = _tdd_parser().parse_args(recorder_region)
    if args.phase in {"red", "green"} and not runner_region:
        raise ValueError(
            "a runner command is required after -- ; place recorder flags "
            "before the sentinel and the command after it"
        )
    args.runner_command = runner_region

    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    items, current = current_map(identity, state)
    legacy = items is None or _legacy_green_candidate(current, workflow_id, args)
    if legacy and args.behavior_id is not None:
        raise WorkflowError(
            "imported legacy TDD uses --behavior/--seam, not --behavior-id"
        )
    if not legacy and (args.behavior is not None or args.seam or args.expected_failure):
        raise WorkflowError(
            "mapped TDD uses --behavior-id; legacy --behavior/--seam flags cannot "
            "satisfy a recorded Behavior Map"
        )
    if not legacy and args.behavior_id is None and args.not_required is None:
        raise WorkflowError("recorded Behavior Map requires --behavior-id or --not-required")
    if args.not_required is not None:
        return _not_required(args, identity, state, items)

    phase = str(args.phase)
    reassessment = False
    evidence_id = (
        state.get("tddEvidence")
        if isinstance(state.get("tddEvidence"), str)
        else None
    )
    if legacy:
        behavior = str(args.behavior or "").strip()
        seam = str(args.seam or "").strip()
        if not behavior:
            raise ValueError("--behavior is required for RED/GREEN")
        if not seam:
            raise ValueError("--seam is required: name the real production Interface")
        expected = str(args.expected_failure or "").strip()
        contract: dict[str, str] = {"slug": slug, "behavior": behavior, "seam": seam}
        candidate = current
        active = None
    else:
        if not args.behavior_id:
            raise ValueError("--behavior-id is required for mapped RED/GREEN")
        mapped = behavior_map.item(items, args.behavior_id)
        status = str(mapped["status"])
        reassessment = mapped.get("revalidationRequired") is True
        if phase == "red" and status not in {"pending", "red"}:
            raise WorkflowError(
                f"behavior {args.behavior_id} is {status}; add a new map item for a new defect"
            )
        # A finished cycle for another item is history, not a candidate: the next
        # item's RED opens its own cycle without an intervening map update.
        candidate = (
            current
            if isinstance(current, dict) and current.get("kind") == "cycle"
            and (current.get("activeBehaviorId") is not None or current.get("behaviorId") == args.behavior_id)
            else None
        )
        active = candidate.get("activeBehaviorId") if isinstance(candidate, dict) else None
        if phase == "green" and status != "red" and not (status == "green" and reassessment):
            raise WorkflowError(
                f"behavior {args.behavior_id} has no valid mapped RED; a contract item is "
                "proved only by GREEN through its own RED"
            )
        expected = str(mapped["redFailure"])
        contract = {
            "slug": slug,
            "behaviorId": args.behavior_id,
            "behavior": str(mapped["behavior"]),
            "seam": str(mapped["seam"]),
        }

    command, command_text, surface = _candidate_command(args.runner_command)
    if not legacy:
        refusal = tdd_surface.repository_resolution(surface, identity.root)
        if refusal is not None:
            raise WorkflowError("mapped proof surfaces must resolve inside the repository: " + refusal)
    same_instance = (
        isinstance(candidate, dict) and candidate.get("workflowId") == workflow_id
    )
    drift, guidance = (
        _candidate_drift(candidate, contract, surface, command_text)
        if same_instance
        else ([], "")
    )
    # Only an open cycle (status red) binds the item to its surface; before that a
    # differing command is the corrected attempt, accumulating beside refused ones.
    if not legacy and same_instance and status != "red":
        drift, guidance = [], ""
    matches = same_instance and not drift
    # RED sweep: every pending item records its own RED beside an open one.
    sweep = (
        not legacy and phase == "red" and status == "pending" and active is not None
        and active != args.behavior_id
    )
    # Every already-RED item binds both repeated RED and GREEN to its own
    # producer command, even beside another item's open cycle.
    recorded_red = None if legacy else mapped.get("redCommand")
    own_red = (
        not legacy and (status == "red" or (status == "green" and reassessment))
        and isinstance(recorded_red, str)
        and not tdd_surface.differences(tdd_surface.identify(shlex.split(recorded_red)), surface)
    )
    if not legacy and (status == "red" and recorded_red is not None or status == "green" and reassessment) and not own_red:
        prefix = "candidate does not match the active mapped cycle; " if phase == "red" else ""
        raise WorkflowError(f"{prefix}{phase.upper()} must run the item's recorded RED surface: {recorded_red}")
    if sweep or (own_red and active != args.behavior_id):
        candidate, same_instance, drift, matches, guidance = None, False, [], False, ""
    completed_cycle = (
        legacy
        and same_instance
        and candidate.get("status") in {"passed", "not-required"}
    )
    if legacy:
        if same_instance and not matches and (phase == "green" or not completed_cycle):
            raise WorkflowError(
                "candidate does not match the active cycle; finish or regress the current "
                "candidate first" + _drift_report(drift) + guidance
            )
    else:
        if same_instance and drift:
            raise WorkflowError(
                "candidate does not match the active mapped cycle; finish it first"
                + _drift_report(drift)
                + guidance
            )
        if active is not None and not matches and not (sweep or own_red):
            raise WorkflowError("finish the active mapped cycle before selecting another item")

    env = None
    if surface.get("runner") == "pytest":
        # The recorded command is the executed surface: pytest's environment
        # and configuration addopts channels could append --pyargs or targets
        # the repository-resolution check never saw. Later override-ini
        # assignments win, so the neutralizer goes after the caller's options,
        # before any -- positional region.
        env = {**os.environ, "PYTEST_ADDOPTS": ""}
        sentinel = command.index("--") if "--" in command else len(command)
        command = [*command[:sentinel], "--override-ini=addopts=", *command[sentinel:]]
    # Measured before the command runs: the binding describes the tree the RED
    # was launched on, whatever the command rewrites or commits before returning.
    binding = _tree_binding(identity, state) if phase == "red" else {}
    tree_before = tree_manifest(identity) if reassessment else None
    if reassessment:
        binding["candidateTree"] = _active_candidate_tree(identity)
    try:
        raw, exit_code, timed_out = _run(command, identity, args.timeout, env=env)
    except OSError as exc:
        # Never started: retained under the shell's not-found status with the OS error.
        raw, exit_code, timed_out = str(exc).encode(), 127, False
    output = raw.decode("utf-8", errors="replace")
    prior_runs = (
        candidate.get("runs")
        if matches and isinstance(candidate.get("runs"), list)
        else []
    )
    prior_red = own_red or any(
        isinstance(run, dict)
        and run.get("phase") == "red"
        and run.get("valid") is True
        for run in prior_runs
    )
    proof: dict[str, object] | None = None
    proof_error = ""
    red_ok = False
    baseline = False
    nonexecuting = False
    if phase == "red" and not timed_out and exit_code != 0:
        if legacy:
            red_ok = bool(expected) and expected in output
        else:
            proof, proof_error = tdd_surface.evaluate_red(surface, output, expected)
            red_ok = proof is not None
    elif phase == "red" and not legacy and not timed_out and status == "pending":
        # Producer-backed baseline: a pending surface passing is already
        # satisfied, opens nothing, counts no cycle. A dirty tree does not refuse
        # it; the run entry records what had changed, and the reviews weigh it.
        proof, proof_error, nonexecuting = _pass_proof(surface, output, baseline=True, exit_code=exit_code)
        baseline = proof is not None
    elif phase == "green" and not legacy and not timed_out and (
        exit_code == 0 or (surface.get("runner") == "pytest" and exit_code == 5)
    ):
        # A GREEN is the surface passing, not the command exiting 0: a skipped or
        # incomplete run reports no passing test and proves nothing.
        proof, proof_error, nonexecuting = _pass_proof(surface, output, baseline=False, exit_code=exit_code)
    valid = (
        red_ok
        if phase == "red"
        else not timed_out and exit_code == 0 and prior_red and (legacy or proof is not None)
    )

    if proof is not None and binding.get("productionChanged"):
        proof = {**proof, "productionChanged": binding["productionChanged"]}
    fields: dict[str, object] = {
        "phase": phase,
        "command": command_text,
        "valid": valid,
        **binding,
    }
    if legacy:
        fields["expectedFailure"] = expected or None
    else:
        fields["behaviorId"] = args.behavior_id
        fields["expectedFailure"] = expected if phase == "red" else None
        if proof is not None:
            fields["passProof" if phase == "green" else "redProof"] = proof
        elif proof_error:
            fields["passProofFailure" if phase == "green" else "redProofFailure"] = proof_error
    run = _run_entry(raw, exit_code, timed_out, **fields)

    document: JsonObject | None = None
    opens_cycle = False
    action: str | None = "in-progress"
    if legacy:
        preserved = phase == "red" and matches and not valid and completed_cycle
        new_cycle = (
            phase == "red"
            and valid
            and not matches
            and (not same_instance or completed_cycle)
        )
        recorded = not preserved and (matches or new_cycle)
        if recorded:
            regression = phase == "green" and matches and not valid
            reopen = regression or (
                phase == "red" and valid and (new_cycle or completed_cycle)
            )
            action = (
                "passed"
                if phase == "green" and valid
                else "reopen"
                if reopen
                else "in-progress"
            )
            opens_cycle = new_cycle
            document = {
                "schemaVersion": 1,
                "slug": slug,
                "workflowId": workflow_id,
                "status": "passed" if phase == "green" and valid else "pending",
                "behavior": contract["behavior"],
                "seam": contract["seam"],
                "command": command_text,
                "surface": surface,
                "runs": [*prior_runs, run] if matches else [run],
                "updatedAt": utc_timestamp(),
            }
    else:
        # Every mapped run is retained, a refused attempt with its reason.
        updated = behavior_map.clone(items)
        updated_item = behavior_map.item(updated, args.behavior_id)
        doc_kind = "cycle"
        if baseline:
            updated_item["status"] = "already-satisfied"
            updated_item["evidence"] = _BASELINE_STAMP + command_text
            updated_item["baselineProof"] = proof
            updated_item.pop("revalidationRequired", None)
            next_active = None
            reassessment_pending = None
            doc_kind = "map"
        elif phase == "red" and valid:
            updated_item["status"] = "red"
            updated_item["redCommand"] = command_text
            # Lateness is sticky: a rerun on a cleaner tree keeps every path an
            # earlier RED for this item recorded.
            previous = mapped.get("redProof") if isinstance(mapped.get("redProof"), dict) else {}
            changed = sorted({*previous.get("productionChanged", []), *proof.get("productionChanged", [])})
            updated_item["redProof"] = {**proof, "productionChanged": changed} if changed else proof
            next_active = args.behavior_id
            reassessment_pending = None
            action = "in-progress" if status == "red" else "reopen"
            opens_cycle = status != "red"
        elif phase == "green" and valid:
            updated_item["status"] = "green"
            updated_item["proofCommand"] = command_text
            updated_item.pop("revalidationRequired", None)
            next_active = None
            reassessment_pending = None
        else:
            # A refused attempt is evidence, not progress: annotated without a
            # transition (action None). A failed GREEN is a regression.
            next_active = args.behavior_id if status == "red" else None
            reassessment_pending = None
            action = "reopen" if phase == "green" else None
        pending = behavior_map.unresolved(updated)
        if baseline or (phase == "green" and valid):
            action = "in-progress" if pending else "passed"
        if reassessment and (baseline or (
            phase == "green" and status == "green" and (valid or nonexecuting)
        )):
            # No execution leaves the obligation unresolved, not contradicted.
            # A regression or ambiguous failure keeps ordinary invalidation.
            action = ("passed" if (valid or baseline) and not pending
                      and state.get("tdd") not in {"passed", "not-required"} else None)
        if isinstance(current, dict) and (
            action is None or (active is not None and active != args.behavior_id and not opens_cycle)
        ):
            # Evidence beside A's RED must not replace A's binding, including
            # baselines, unsuccessful attempts and another item's recheck.
            document = {**current, "behaviorMap": updated, "status": "pending" if pending else "passed",
                        "runs": [*current.get("runs", []), run], "updatedAt": utc_timestamp()}
        else:
            document = _map_doc(
                slug=slug,
                workflow_id=workflow_id,
                items=updated,
                status="pending" if pending else "passed",
                kind=doc_kind,
                active=next_active,
                reassessment_pending=reassessment_pending,
                behaviorId=args.behavior_id,
                behavior=contract["behavior"],
                seam=contract["seam"],
                command=command_text,
                surface=surface,
                runs=[*prior_runs, run] if matches else [run],
            )
    if document is not None:
        _, evidence_id = commit_tdd(
            identity, slug, workflow_id, document, action,
            expected_evidence_id=evidence_id, opens_cycle=opens_cycle, tree_before=tree_before,
        )
    if run.get("bindingError"):
        valid, baseline, proof_error = False, False, str(run["bindingError"])

    _print_output(raw)
    payload: JsonObject = {
        "summaryId": evidence_id,
        "phase": phase,
        "valid": valid,
        "exitCode": exit_code,
    }
    if not legacy:
        payload["behaviorId"] = args.behavior_id
    if baseline:
        payload["status"] = "already-satisfied"
    _emit_json(payload)
    if valid or baseline:
        return 0
    if legacy:
        print(
            "RED must fail for the expected reason."
            if phase == "red"
            else "GREEN must pass after a valid RED for the same command, behavior, and Seam.",
            file=sys.stderr,
        )
    elif phase == "red":
        reason = proof_error or (
            "the command timed out" if timed_out else "the command exited 0 and opened no RED"
        )
        print(
            "RED must fail for the expected reason after reaching the mapped Seam. "
            + reason,
            file=sys.stderr,
        )
    else:
        print(
            "GREEN must pass after a valid RED for the same mapped behavior and surface: "
            "a runner-backed pass reports an executed passing test, and a non-runner "
            "operation exits 0."
            + (f" {proof_error}" if proof_error else ""),
            file=sys.stderr,
        )
    return 2


_ADVISORY_FILE = "map-advisory.json"
_ADVISORY_TIMEOUT = 10
# Git permits control bytes in a path and the graph can surface one verbatim, so
# escape them before the path reaches the one-line notice.
_ADVISORY_CONTROL_ESCAPES = {c: f"\\x{c:02x}" for c in range(0x20)} | {0x7f: "\\x7f"}


def map_advisory(identity: RepoIdentity, state: JsonObject) -> str | None:
    """After a successful production edit, name the impacted tests the map does
    not own, or a short gap when that cannot be decided against this pass's
    index. Advisory only: it returns at most one notice line for the caller to
    deliver and writes one disposable file, and never raises into the edit it
    follows."""
    workflow_id = str(state.get("workflowId") or "")
    try:
        snapshot = state.get("passStartSnapshot")
        if not isinstance(snapshot, dict) or not snapshot:
            return _advisory_publish(identity, workflow_id, "the pass-start index identity was not recorded", {})
        root = Path(identity.root)
        impacted, gap = _impacted_tests(snapshot, root)
        owned = _owned_scopes(identity, state, root)
        unowned: dict[str, int] = {}
        for entry in impacted:
            path = str(entry.get("filePath") or "").replace("\\", "/")
            node = (str(entry.get("id") or "").split(":", 2)[2:] or [""])[0]
            if path and not _is_owned(path, node, owned):
                unowned[path] = unowned.get(path, 0) + 1
        return _advisory_publish(identity, workflow_id, gap, unowned)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, json.JSONDecodeError, subprocess.SubprocessError):
        return _advisory_publish(identity, workflow_id, "the advisory could not complete", {})


def _impacted_tests(snapshot: JsonObject, root: Path) -> tuple[list[JsonObject], str | None]:
    """The impacted tests the pass-start index attributes to the current candidate,
    with a gap reason when the analysis is not a complete diff against that index."""
    binary = shutil.which("gitnexus")
    if binary is None:
        return [], "the graph tool is unavailable"
    try:
        proc = subprocess.run(
            [binary, "detect-changes", "--repo", str(snapshot["indexRepo"]), "--worktree", str(root)],
            capture_output=True, text=True, check=False, timeout=_ADVISORY_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return [], "the graph diff did not finish in time"
    if proc.returncode != 0:
        return [], "the pass-start index could not be diffed"
    data = json.loads(proc.stdout)
    impacted = data.get("impacted_tests") or []
    analysis = data.get("analysis") or {}
    baseline = analysis.get("baseline") or {}
    if baseline.get("tree") != snapshot.get("indexedTree") or baseline.get("source_commit") != snapshot.get("sourceCommit"):
        return impacted, "the graph baseline is not this pass's index"
    status = analysis.get("status")
    return impacted, None if status == "complete" else f"the graph analysis is {status}"


def _owned_scopes(identity: RepoIdentity, state: JsonObject, root: Path) -> list[tuple[str, str, bool]]:
    """Every (path, node-prefix, is-directory) the current map's recorded proofs
    selected. An unresolved selection contributes nothing, so it owns nothing."""
    selections = _executed_selections(identity, state) or {}
    scopes: list[tuple[str, str, bool]] = []
    for phases in selections.values():
        if not isinstance(phases, dict):
            continue
        for selection in phases.values():
            if not isinstance(selection, dict):
                continue
            targets = selection.get("targets")
            if not isinstance(targets, list):
                continue
            # A directory owns its subtree only for a recursive selection: a
            # pytest path or a unittest `discover`. A plain unittest package
            # load is non-recursive, so it owns only the tests it names.
            surface = tdd_surface.identify(shlex.split(str(selection.get("command") or "")))
            recursive = surface.get("runner") == "pytest" or bool(selection.get("discover"))
            for target in targets:
                scope = _target_scope(str(target), root, recursive)
                if scope is not None:
                    scopes.append(scope)
    return scopes


def _target_scope(target: str, root: Path, recursive: bool) -> tuple[str, str, bool] | None:
    """Resolve a recorded selection target to (path, node-prefix, is-directory)
    under root, or None when it names nothing there or is a non-recursive
    directory. A pytest target is a path with an optional ``::`` node; a unittest
    target is a dotted path whose file prefix is found on disk and its remainder
    the node."""
    if "::" in target or "/" in target or target.endswith(".py") or target in (".", ".."):
        head, _, node = target.partition("::")
        resolved = root / head.rstrip("/")
        is_dir = resolved.is_dir()
        if is_dir and not recursive:
            return None
        # Normalise to the producer's root-relative filePath shape, so a
        # recorded "./tests/x.py" or "." matches "tests/x.py"; "" owns the tree.
        relative = _relative(resolved, root)
        path = "" if relative == "." else relative
        return path, node.replace("::", "."), is_dir
    parts = target.split(".")
    for i in range(len(parts), 0, -1):
        base = root.joinpath(*parts[:i])
        file = base.with_suffix(".py")
        if file.is_file():
            return _relative(file, root), ".".join(parts[i:]), False
        if base.is_dir():
            return (_relative(base, root), "", True) if recursive else None
    return None


def _relative(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _is_owned(path: str, node: str, scopes: list[tuple[str, str, bool]]) -> bool:
    """Whether one impacted test (path, node) falls inside any selected scope: a
    directory owns its subtree, a file with an empty node-prefix owns the file,
    and a node-prefix owns itself and its descendants."""
    for scope_path, node_prefix, is_dir in scopes:
        if is_dir:
            if scope_path == "" or path == scope_path or path.startswith(scope_path.rstrip("/") + "/"):
                return True
        elif path == scope_path and (
            node_prefix == "" or node == node_prefix or node.startswith(node_prefix + ".")
        ):
            return True
    return False


def _advisory_publish(
    identity: RepoIdentity, workflow_id: str, gap: str | None, unowned: dict[str, int]
) -> str | None:
    """Write the canonical result and return one notice line only when it changed
    and has something to report; an identical result is silent (returns None) and
    writes nothing."""
    paths = {name: unowned[name] for name in sorted(unowned)}
    canonical = {"workflowId": workflow_id, "gap": gap, "paths": paths}
    try:
        store = repo_state_dir(identity) / _ADVISORY_FILE
        prior = read_json(store)
        if prior == canonical:
            return None
        atomic_write_json(store, canonical)
    except OSError:
        # The disposable dedup cache could not be resolved or written. Report
        # that as a gap through the one-line notice rather than failing the edit
        # or retrying the same writer, so the edit's outcome and state are
        # untouched.
        return "map advisory: gap, the advisory cache could not be written"
    if not gap and not paths:
        return None
    sort = sorted(paths)
    shown = ", ".join(p.translate(_ADVISORY_CONTROL_ESCAPES) for p in sort[:10])
    remaining = len(sort) - 10
    if paths:
        total = sum(paths.values())
        report = f"{total} impacted tests not owned by the map: {shown}"
        if remaining > 0:
            report += f" (and {remaining} more)"
    else:
        report = ""
    if gap:
        report = f"gap, {gap}" + (f"; {report}" if report else "")
    return f"map advisory: {report}"


def _map_update(values: list[str]) -> int:
    args = _map_parser().parse_args(values)
    identity = resolve_repo_identity(args.repo)
    state = bound_state(identity, safe_slug(args.slug))
    if instance_id(state) != args.workflow_id:
        raise WorkflowError("--workflow-id does not match the active workflow instance")
    current, preflight_document = _evidence_pair(identity, state)
    items = behavior_map.recorded_map(current, preflight_document)
    if items is None:
        raise WorkflowError("tdd-map requires a recorded preflight Behavior Map")
    settled_findings = frozenset(
        (str(entry.get("intakeEvidenceId")), str(entry.get("findingId")))
        for entry in state.get("findingStates") or []
        if isinstance(entry, dict)
        and entry.get("status") in {"rejected-with-evidence", "report-only"}
    )

    value = load_json(args.input, label="TDD map update")
    allowed = {"sourceBehaviorId", "reassessment", "items", "dispositions"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError("TDD map update has unknown fields: " + ", ".join(unknown))
    reassessment = value.get("reassessment")
    if not isinstance(reassessment, str) or not reassessment.strip():
        raise ValueError("TDD map update requires a non-empty reassessment")
    additions = value.get("items", [])
    if not isinstance(additions, list):
        raise ValueError("TDD map update items must be an array")
    dispositions = value.get("dispositions", [])
    if not isinstance(dispositions, list):
        raise ValueError("TDD map update dispositions must be an array")
    source = value.get("sourceBehaviorId")
    if source is not None and behavior_map.item(items, str(source)).get("status") not in behavior_map.PROOF_STATUSES:
        raise ValueError("sourceBehaviorId must name a GREEN item")

    updated = behavior_map.clone(items)
    if dispositions:
        behavior_map.apply_dispositions(updated, dispositions, settled_findings=settled_findings)
    added_items: list[JsonObject] = []
    if additions:
        added_items = behavior_map.added_items(additions, updated)
        updated.extend(added_items)
    # Supersession is judged over the merged map, so a replacement added in
    # this same update is legal and a broken graph refuses before any commit.
    unresolved = behavior_map.unresolved(updated)
    status = "pending" if unresolved else "passed"
    current_evidence_id = state.get("tddEvidence")
    evidence_id = current_evidence_id
    if updated != items:
        document = {**(current or _map_doc(
            slug=str(state["slug"]), workflow_id=str(state["workflowId"]),
            items=items, status=status, kind="map",
        )), "behaviorMap": updated, "status": status, "reassessment": reassessment.strip(),
            "sourceBehaviorId": source, "updatedAt": utc_timestamp()}
        # Flagged reassessment is not a new cycle or a reason to replay a
        # finished downstream chain. Actual new/settled obligations still move
        # the normal lifecycle; source edits retain their existing invalidation.
        before, after = (
            {str(entry["id"]) for entry in entries
             if entry.get("status") in {"pending", "red"} and not entry.get("revalidationRequired")}
            for entries in (items, updated)
        )
        if before == after:
            _, evidence_id = annotate_tdd_evidence(
                identity, str(state["slug"]), str(state["workflowId"]), document,
                expected_evidence_id=current_evidence_id,
            )
        else:
            action = ("reopen" if unresolved and state.get("tdd") in {"passed", "not-required"}
                      else "in-progress" if unresolved else "passed")
            _, evidence_id = commit_tdd(
                identity, str(state["slug"]), str(state["workflowId"]), document, action,
                expected_evidence_id=current_evidence_id,
            )
    _emit_json(
        {
            "summaryId": evidence_id,
            "status": status,
            "pending": unresolved,
            "added": [entry["id"] for entry in added_items],
        }
    )
    return 0


def run_tdd(values: list[str]) -> int:
    """Public entry for the workflow CLI's mapped-or-legacy TDD verb."""
    return _run_tdd(values)


def run_map_update(values: list[str]) -> int:
    """Public entry for the workflow CLI's Behavior Map update verb."""
    return _map_update(values)
