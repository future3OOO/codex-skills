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
    MAX_CAPTURE,
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
)
from .workflow_documents import DOCUMENT_SHAPES, load_json
from .workflow_state import (
    NO_INSTANCE_ID,
    TDD_CLOSED,
    WorkflowError,
    _executed_selections,
    annotate_tdd_evidence,
    bound_state,
    commit_tdd,
    evidence_document,
    execution_digest,
    execution_receipt,
    instance_id,
    read_workflow,
    run_recorded_baseline,
    safe_slug,
)

JsonObject = dict[str, object]


def _tdd_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow tdd")
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--phase", choices=("red", "green"))
    mode.add_argument("--not-required", metavar="REASON")
    parser.add_argument("--behavior-id")
    parser.add_argument("--from-evidence", help="reuse an executed evidence-id:run-index")
    parser.add_argument("--test-id", help="the exact unittest test attributed to this item")
    parser.add_argument("--timeout", type=int, default=900)
    return parser


def _map_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow record map", epilog=DOCUMENT_SHAPES["map"])
    parser.add_argument("--repo", "--cwd", dest="repo", default=".")
    parser.add_argument("--slug")
    parser.add_argument("--workflow-id")
    parser.add_argument("--input", required=True)
    parser.add_argument("--check", action="store_true", help="validate without recording")
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


def _map_doc(
    *,
    slug: str,
    workflow_id: str,
    items: list[JsonObject],
    status: str,
    kind: str,
    active: str | None = None,
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
            "--not-required requires every mapped item to be already-satisfied by an "
            "executed baseline or omitted by governing evidence; unresolved: "
            + ", ".join(behavior_map.unresolved(items))
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
        }
    )
    _, evidence_id = commit_tdd(
        identity,
        str(state["slug"]),
        str(state["workflowId"]),
        document,
        "not-required",
        expected_evidence_id=existing_id,
        expected_preflight_id=str(state["preflightEvidence"]),
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
    an executed passing test; a non-runner exit 0 closes its own RED, and baselines a
    pending item the same way, recording what it observed — reach stays unresolved
    for review to establish, exactly as a non-runner RED records it."""
    runner = surface.get("runner")
    if runner not in {"unittest", "pytest"}:
        if baseline:
            lines = [
                line
                for line in tdd_surface.ANSI_ESCAPE.sub("", output).splitlines()
                if line.strip()
            ]
            observed = tdd_surface._final_diagnostic(lines)[:1000]
            if not observed:
                return None, (
                    "a baseline is the surface passing, not the command exiting 0: "
                    "the operation emitted nothing to observe"
                ), False
            return {
                "quality": "operation-succeeded",
                "runner": str(runner),
                "observation": [observed],
                "site": shlex.join(str(token) for token in surface.get("arguments") or []),
            }, "", False
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
    """Production paths changed since the pass began, before this RED runs."""
    start = state.get("passStartOid")
    return {
        "productionChanged": production_changes(identity, start if isinstance(start, str) and start else "HEAD"),
    }


def _baseline_refusal(binding: dict[str, object], kind: object) -> str:
    """Why a passing RED-phase run cannot baseline a pending contract item: production
    already changed in this pass, so the pass describes the candidate, not the
    baseline (issue #54). A preservation item's candidate observation is its
    evidence and is recorded late instead."""
    changed = [str(path) for path in binding.get("productionChanged") or []]
    if kind != "contract" or not changed:
        return ""
    return ("a pending contract item cannot be baselined after production changed in this pass ("
            + ", ".join(changed) + "): the candidate-only pass is retained and the item stays pending; "
            "prove it through its own RED at the real Seam, observe it on the pass-start tree, "
            "or narrow the obligation with governing evidence")


def _input_admission(mapped: JsonObject, surface: JsonObject, root: Path, source_tree: dict[str, str],
                     test_id: str | None = None, repo_root: Path | None = None) -> tuple[JsonObject, str]:
    evidence = tdd_surface.input_evidence(surface, root, mapped["boundaryInputs"], test_id)
    if repo_root is not None and root != repo_root:
        prefix = root.relative_to(repo_root)
        evidence["sources"] = {str(prefix / path): digest for path, digest in evidence["sources"].items()}
    if evidence["sources"].keys() - source_tree.keys():
        evidence.update(represented=[], missing=[], unresolved=mapped["boundaryInputs"],
                        limits=[*evidence["limits"], "selected source lacks execution-tree binding"])
    return evidence, f"missing discriminating input(s) {evidence['missing']!r} in {evidence['sources'] or surface}" if evidence["missing"] else ""


def _run_tdd(values: list[str]) -> int:
    """Run one mapped candidate-cycle lifecycle."""
    dash = values.index("--") if "--" in values else None
    recorder_region = values if dash is None else values[:dash]
    runner_region = [] if dash is None else values[dash + 1 :]
    args = _tdd_parser().parse_args(recorder_region)
    if args.from_evidence and (runner_region or not args.test_id):
        raise ValueError("--from-evidence requires --test-id and takes no command")
    if args.test_id and not args.from_evidence:
        raise ValueError("--test-id belongs to --from-evidence")
    if args.phase in {"red", "green"} and not runner_region and not args.from_evidence:
        raise ValueError(
            "a runner command is required after -- ; place recorder flags "
            "before the sentinel and the command after it"
        )
    args.runner_command = runner_region

    identity = resolve_repo_identity(args.repo)
    state, slug, workflow_id = _active_candidate(identity, args.slug)
    receipt = None
    receipt_tree = None
    if args.from_evidence:
        receipt, receipt_tree = execution_receipt(identity, state, args.from_evidence)
        if "outputTail" not in receipt:
            raise WorkflowError("test attribution requires the original execution report")
        args.runner_command = shlex.split(str(receipt["command"]))
    execution_root = Path(identity.root) / str(receipt.get("runCwd", ".")) if receipt else Path(identity.root)
    if not execution_root.resolve().is_relative_to(Path(identity.root).resolve()):
        raise WorkflowError("execution working directory is outside the repository")
    items, current = current_map(identity, state)
    if items is None:
        raise WorkflowError("TDD requires a recorded Behavior Map")
    if args.behavior_id is None and args.not_required is None:
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
    if not args.behavior_id:
        raise ValueError("--behavior-id is required for mapped RED/GREEN")
    mapped = behavior_map.item(items, args.behavior_id)
    status = str(mapped["status"])
    reassessment = mapped.get("revalidationRequired") is True or (phase == "green" and status == "green")
    if phase == "red" and status not in {"pending", "red"}:
        raise WorkflowError(f"behavior {args.behavior_id} is {status}; add a new map item for a new defect")
    candidate = (
        current if isinstance(current, dict) and current.get("kind") == "cycle"
        and (current.get("activeBehaviorId") is not None or current.get("behaviorId") == args.behavior_id)
        else None
    )
    active = candidate.get("activeBehaviorId") if isinstance(candidate, dict) else None
    if phase == "green" and status != "red" and not (status == "green" and reassessment):
        raise WorkflowError(f"behavior {args.behavior_id} has no valid mapped RED")
    expected = str(mapped["redFailure"])
    contract = {"slug": slug, "behaviorId": args.behavior_id,
                "behavior": str(mapped["behavior"]), "seam": str(mapped["seam"])}

    command, command_text, surface = _candidate_command(args.runner_command)
    refusal = tdd_surface.repository_resolution(surface, execution_root)
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
    if same_instance and status != "red":
        drift, guidance = [], ""
    matches = same_instance and not drift
    # RED sweep: every pending item records its own RED beside an open one.
    sweep = (
        phase == "red" and status == "pending" and active is not None
        and active != args.behavior_id
    )
    # Every already-RED item binds both repeated RED and GREEN to its own
    # producer command, even beside another item's open cycle.
    recorded_red = mapped.get("redCommand")
    red_proof = mapped.get("redProof")
    if (status in {"red", "green"} and isinstance(red_proof, dict)
            and red_proof.get("runCwd") != (receipt.get("runCwd") if receipt else None)):
        raise WorkflowError("GREEN/RED must use the recorded RED working directory")
    if receipt is not None and status in {"red", "green"}:
        test_id = (mapped.get("redProof") or {}).get("testId")
        if test_id != args.test_id:
            raise WorkflowError("reused GREEN/RED must name the item's recorded test identity")
    own_red = (
        (status == "red" or (status == "green" and reassessment))
        and isinstance(recorded_red, str)
        and not tdd_surface.differences(tdd_surface.identify(shlex.split(recorded_red)), surface)
    )
    if (status == "red" and recorded_red is not None or status == "green" and reassessment) and not own_red:
        prefix = "candidate does not match the active mapped cycle; " if phase == "red" else ""
        raise WorkflowError(f"{prefix}{phase.upper()} must run the item's recorded RED surface: {recorded_red}")
    if sweep or (own_red and active != args.behavior_id):
        candidate, same_instance, drift, matches, guidance = None, False, [], False, ""
    if same_instance and drift:
        raise WorkflowError("candidate does not match the active mapped cycle; finish it first"
                            + _drift_report(drift) + guidance)
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
    tree_before = receipt_tree if receipt is not None else tree_manifest(identity)
    binding["candidateTree"] = _active_candidate_tree(identity)
    if receipt is not None:
        raw = str(receipt["outputTail"]).encode()
        exit_code, timed_out = int(receipt["exitCode"]), False
    else:
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
    if receipt is not None:
        outcome, proof, proof_error = tdd_surface.attributed_result(
            surface, receipt, args.test_id, expected, execution_root)
        red_ok = phase == "red" and outcome == "failed"
        baseline = phase == "red" and status == "pending" and outcome == "passed"
        exit_code = 0 if outcome == "passed" else int(receipt["exitCode"]) or 1
        nonexecuting = outcome == "skipped"
    elif phase == "red" and not timed_out and exit_code != 0:
        proof, proof_error = tdd_surface.evaluate_red(surface, output, expected, Path(identity.root))
        red_ok = proof is not None
    elif phase == "red" and not timed_out and status == "pending":
        # Producer-backed baseline: a pending surface passing is already
        # satisfied, opens nothing, counts no cycle, and describes the baseline
        # only while this pass has not changed production code.
        proof, proof_error, nonexecuting = _pass_proof(surface, output, baseline=True, exit_code=exit_code)
        baseline = proof is not None
    elif phase == "green" and not timed_out and (
        exit_code == 0 or (surface.get("runner") == "pytest" and exit_code == 5)
    ):
        # A GREEN is the surface passing, not the command exiting 0: a skipped or
        # incomplete run reports no passing test and proves nothing.
        proof, proof_error, nonexecuting = _pass_proof(surface, output, baseline=False, exit_code=exit_code)
    if receipt is not None and proof is not None:
        proof = {
            **proof,
            "sourceReference": args.from_evidence,
            "sourceExecution": execution_digest(receipt) or args.from_evidence,
        }
    if baseline and (refusal := _baseline_refusal(binding, mapped.get("kind"))):
        proof, proof_error, baseline = None, refusal, False
    input_check = None
    input_error = ""
    if proof is not None and (baseline or phase == "green") and mapped.get("boundaryInputs"):
        input_check, input_error = _input_admission(mapped, surface, execution_root, tree_before,
                                                    args.test_id, Path(identity.root))
        if input_error:
            proof, proof_error, baseline = None, input_error, False
        else:
            proof = {**proof, "inputEvidence": input_check}
    if baseline and receipt is not None:
        # The stored execution already settled another item: its run recorded a
        # baseline for that item's own id. Re-attributing the same observed
        # outcome here would settle a second item from one observation.
        owner = receipt.get("behaviorId")
        if (isinstance(owner, str) and owner and owner != args.behavior_id
                and run_recorded_baseline(receipt)):
            proof, proof_error, baseline = None, (
                f"the stored execution already settled {owner}: one observed outcome "
                "cannot baseline two items"
            ), False
    if baseline and (owner := behavior_map.inherited_baseline(items, args.behavior_id, proof)):
        # One observed outcome settles one item, the baseline mirror of the RED
        # rule above: the same observation at the same site cannot satisfy a
        # second pending item; a different site carries its own observation.
        detail = (
            f"the observed outcome {proof['observation']!r} at {proof.get('site')!r}"
            if isinstance(proof.get("observation"), list)
            else f"the stored execution {proof.get('sourceReference')!r} test {proof.get('testId')!r}"
        )
        proof, proof_error, baseline = None, (
            f"{detail} already settled {owner}: one observed outcome cannot baseline two items"
        ), False
    if red_ok and (owner := behavior_map.inherited_red(items, args.behavior_id, proof)):
        # The same observation cannot open RED for two items: this obligation's
        # test stopped where another item's already did and observed nothing of
        # its own; the first RED stays the initial slice.
        proof, proof_error, red_ok = None, (
            f"the observed failure {proof['observation']!r} at {proof.get('site')!r} is the RED "
            f"already recorded for {owner}; an independent guarantee cannot inherit it - drive "
            "this item through the real Interface once it exists and assert its own promised outcome"), False
    valid = (
        red_ok
        if phase == "red"
        else not timed_out and exit_code == 0 and prior_red and proof is not None
    )

    if proof is not None and binding.get("productionChanged"):
        proof = {**proof, "productionChanged": binding["productionChanged"]}
    fields: dict[str, object] = {
        "phase": phase,
        "command": command_text,
        "valid": valid,
        **({"candidateTree": binding["candidateTree"]} if "candidateTree" in binding else {}),
    }
    fields["behaviorId"] = args.behavior_id
    if proof is not None:
        fields["passProof" if phase == "green" else "redProof"] = proof
    elif proof_error:
        fields["passProofFailure" if phase == "green" else "redProofFailure"] = proof_error
    run = _run_entry(raw, exit_code, timed_out,
                     capture_limit=MAX_CAPTURE if surface.get("runner") == "unittest" else 1024,
                     outputBytes=len(raw), **fields)
    if receipt is not None:
        run.pop("outputTail")
        run.update(sourceReference=args.from_evidence, testId=args.test_id)

    document: JsonObject | None = None
    opens_cycle = False
    action: str | None = "in-progress"
    # Every mapped run is retained, a refused attempt with its reason.
    updated = behavior_map.clone(items)
    updated_item = behavior_map.item(updated, args.behavior_id)
    if baseline or (phase == "green" and valid):
        updated_item["proofBinding"] = {"candidateTree": binding["candidateTree"],
                                         "command": command_text, "testId": args.test_id,
                                         **({"runCwd": receipt["runCwd"]} if receipt and receipt.get("runCwd") else {})}
    doc_kind = "cycle"
    if baseline:
        updated_item["status"] = "already-satisfied"
        updated_item["evidence"] = _BASELINE_STAMP + command_text
        updated_item["baselineProof"] = proof
        updated_item.pop("revalidationRequired", None)
        next_active = None
        doc_kind = "map"
    elif phase == "red" and valid:
        updated_item["status"] = "red"
        updated_item["redCommand"] = command_text
        # Lateness is sticky: a rerun on a cleaner tree keeps every path an
        # earlier RED for this item recorded.
        previous = mapped.get("redProof") if isinstance(mapped.get("redProof"), dict) else {}
        changed = sorted({*previous.get("productionChanged", []), *proof.get("productionChanged", [])})
        updated_item["redProof"] = {
            **proof,
            **({"testId": previous["testId"]} if previous.get("testId") and not proof.get("testId") else {}),
            **({"productionChanged": changed} if changed else {}),
            **({"runCwd": receipt["runCwd"]} if receipt and receipt.get("runCwd") else {}),
        }
        next_active = args.behavior_id
        action = "reopen" if reassessment else "in-progress"
        opens_cycle = status != "red"
    elif phase == "green" and valid:
        updated_item["status"] = "green"
        updated_item["proofCommand"] = command_text
        updated_item.pop("revalidationRequired", None)
        next_active = None
    else:
        # A refused attempt is evidence, not progress: annotated without a
        # transition (action None). A failed GREEN is a regression.
        if phase == "green" and status == "green" and not nonexecuting:
            updated_item["status"] = "red"
        next_active = args.behavior_id if status == "red" else None
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
                    "runs": [*current.get("runs", []), run]}
    else:
        document = _map_doc(
            slug=slug,
            workflow_id=workflow_id,
            items=updated,
            status="pending" if pending else "passed",
            kind=doc_kind,
            active=next_active,
            reassessment=(current or {}).get("reassessment"),
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
            expected_evidence_id=evidence_id,
            expected_preflight_id=str(state["preflightEvidence"]),
            opens_cycle=opens_cycle, tree_before=tree_before,
            review_changed=opens_cycle,
        )
    if run.get("bindingError"):
        valid, baseline, proof_error = False, False, str(run["bindingError"])

    if receipt is None:
        _print_output(raw)
    payload: JsonObject = {
        "summaryId": evidence_id,
        "phase": phase,
        "valid": valid,
        "exitCode": exit_code,
        "runIndex": len(document.get("runs", [])) - 1 if document else None,
    }
    payload["behaviorId"] = args.behavior_id
    if input_check is not None:
        payload["inputEvidence"] = input_check
    if baseline:
        payload["status"] = "already-satisfied"
    _emit_json(payload)
    if valid or baseline:
        return 0
    if input_error and proof_error == input_error:
        print(
            input_error + ". Select actual proof supplying these inputs; reuse an applicable "
            "verification receipt with --from-evidence and --test-id. Passing preservation "
            "proof can establish a baseline without a failing RED.",
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
    """Cache the canonical result and return a notice for the hook's epoch filter."""
    paths = {name: unowned[name] for name in sorted(unowned)}
    canonical = {"workflowId": workflow_id, "gap": gap, "paths": paths}
    try:
        store = repo_state_dir(identity) / _ADVISORY_FILE
        prior = read_json(store)
        if prior != canonical:
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
    state = bound_state(identity, safe_slug(args.slug)) if args.slug else read_workflow(identity)
    if state is None:
        raise WorkflowError("no active workflow")
    args.workflow_id = args.workflow_id or state["workflowId"]
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
    errors = []
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append("TDD map update has unknown fields: " + ", ".join(unknown))
    reassessment = value.get("reassessment")
    if reassessment is not None and (not isinstance(reassessment, str) or not reassessment.strip()):
        errors.append("TDD map update reassessment must be non-empty when supplied")
    additions = value.get("items", [])
    if not isinstance(additions, list):
        errors.append("TDD map update items must be an array")
        additions = []
    dispositions = value.get("dispositions", [])
    if not isinstance(dispositions, list):
        errors.append("TDD map update dispositions must be an array")
        dispositions = []
    if reassessment is None and (additions or not dispositions or any(
        not isinstance(entry, dict) or set(entry) - {"id", "sourceRefs"}
        for entry in dispositions
    )):
        errors.append("TDD map update requires a non-empty reassessment")
    source = value.get("sourceBehaviorId")
    if source is not None:
        try:
            if behavior_map.item(items, str(source)).get("status") not in behavior_map.PROOF_STATUSES:
                errors.append("sourceBehaviorId must name a GREEN item")
        except ValueError:
            errors.append("sourceBehaviorId must name a GREEN item")

    updated = behavior_map.clone(items)
    if dispositions:
        try:
            behavior_map.apply_dispositions(updated, dispositions, settled_findings=settled_findings)
        except ValueError as exc:
            errors.append(str(exc))
    try:
        added_items = behavior_map.added_items(additions, updated) if additions else []
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        raise ValueError("; ".join(errors))
    updated.extend(added_items)
    input_checks = {}
    candidate_tree = None
    source_tree = None
    for previous, entry in zip(items, updated):
        if json.dumps(entry.get("boundaryInputs"), sort_keys=True) == json.dumps(previous.get("boundaryInputs"), sort_keys=True):
            continue
        proof_binding = entry.get("proofBinding")
        if isinstance(proof_binding, dict) and proof_binding.get("candidateTree") == (candidate_tree := candidate_tree or _active_candidate_tree(identity)):
            source_tree = tree_manifest(identity) if source_tree is None else source_tree
            check, error = _input_admission(entry, tdd_surface.identify(shlex.split(proof_binding["command"])),
                                           Path(identity.root) / str(proof_binding.get("runCwd", ".")),
                                           source_tree, proof_binding.get("testId"), Path(identity.root))
            input_checks[entry["id"]] = check
            if not error and not check["unresolved"]:
                continue
        else:
            input_checks[entry["id"]] = {"limits": ["no current execution binding for input reassessment"]}
        if entry.get("status") in {"green", "already-satisfied"}:
            behavior_map.apply_dispositions(updated, [{"id": entry["id"], "revalidate": True,
                                                      "evidence": "interpretation input proof requires reassessment"}])
    # Supersession is judged over the merged map, so a replacement added in
    # this same update is legal and a broken graph refuses before any commit.
    unresolved = behavior_map.unresolved(updated)
    status = "pending" if unresolved else "passed"
    evidence_id = current_evidence_id = state.get("tddEvidence")
    reassessed = frozenset(str(entry["id"]).strip() for entry in [*added_items, *dispositions])
    if source is not None:
        reassessed |= {str(source)}
    if args.check:
        _emit_json({"valid": True, "status": status})
        return 0
    if reassessed or json.dumps(updated, sort_keys=True) != json.dumps(items, sort_keys=True):
        document = {**(current or _map_doc(
            slug=str(state["slug"]), workflow_id=str(state["workflowId"]),
            items=items, status=status, kind="map",
        )), "behaviorMap": updated, "status": status, "dispositions": dispositions,
            }
        if reassessment is None:
            document.pop("reassessment", None)
        else:
            document["reassessment"] = reassessment.strip()
        if input_checks:
            document["inputEvidence"] = input_checks
        active = document.get("activeBehaviorId")
        if active is not None and behavior_map.item(updated, str(active))["status"] == "pending":
            document.update(kind="map", activeBehaviorId=None)
            for field in ("behaviorId", "behavior", "seam", "command", "surface", "runs"):
                document.pop(field, None)
        # Flagged reassessment is not a new cycle or a reason to replay a
        # finished downstream chain. Actual new/settled obligations still move
        # the normal lifecycle; source edits retain their existing invalidation.
        before, after = (
            {str(entry["id"]) for entry in entries
             if entry.get("status") in {"pending", "red"} and not entry.get("revalidationRequired")}
            for entries in (items, updated)
        )
        review_changed = before != after or any(entry.get("status") == "superseded" for entry in dispositions)
        interpretation_progress = any(
            set(entry) & {"boundaryInputs", "interpretations", "interpretation", "authority"}
            for entry in dispositions
        ) and set(behavior_map.unresolved(items)) != set(unresolved)
        if before == after and not review_changed and not interpretation_progress:
            _, evidence_id = annotate_tdd_evidence(
                identity, str(state["slug"]), str(state["workflowId"]), document,
                expected_evidence_id=current_evidence_id,
                expected_preflight_id=str(state["preflightEvidence"]), reassessed=reassessed,
            )
        else:
            action = "in-progress" if unresolved else "passed"
            _, evidence_id = commit_tdd(
                identity, str(state["slug"]), str(state["workflowId"]), document, action,
                expected_evidence_id=current_evidence_id,
                expected_preflight_id=str(state["preflightEvidence"]),
                review_changed=review_changed, reassessed=reassessed,
            )
    _emit_json(
        {
            "summaryId": evidence_id,
            "status": status,
            "pending": unresolved,
            "added": [entry["id"] for entry in added_items],
            **({"inputEvidence": input_checks} if input_checks else {}),
        }
    )
    return 0


def run_tdd(values: list[str]) -> int:
    """Public entry for the workflow CLI's mapped TDD verb."""
    return _run_tdd(values)


def run_map_update(values: list[str]) -> int:
    """Public entry for the workflow CLI's Behavior Map update verb."""
    return _map_update(values)
