"""One public command-line Interface for repository workflow operations."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from ._workflow_db import LedgerError, history
from . import tdd_surface
from .behavior_map import interpretation_pending
from .command_runner import MAX_CAPTURE, emit_json as _emit_json, print_output as _print_output, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity
from .state_prune import prune
from .state_store import _active_candidate_tree, repo_state_dir, state_root, tree_manifest
from .workflow_documents import (
    DOCUMENT_SHAPES,
    DOCUMENT_SHAPE_TABLE,
    advisor_disposition_document,
    advisor_envelope,
    design_absence,
    design_declaration,
    review_summary,
    validated_document,
    validate_gate_result,
)
from .workflow_state import (
    NO_INSTANCE_ID,
    WorkflowError,
    advisor_disposition,
    begin,
    bound_state,
    checkpoint,
    commit_evidence_phase,
    commit_review,
    commit_verification,
    complete,
    evidence_document,
    evidence_record,
    instance_id,
    pause,
    public_status,
    read_workflow,
    record_advisor_result,
    safe_slug,
    set_phase,
    summary,
)

ROOT = Path(__file__).resolve().parents[2]
LEAD_PHASES = {"code-review"}
PRODUCER_OWNED = {
    "repo-context-forge": "repo-context-forge is producer-owned; run the Repo Context Forge bootstrap",
    "preflight": "preflight is recorder-owned; use workflow record preflight",
    "verification": "verification is runner-owned; use workflow verify",
}


def _repo(command: argparse.ArgumentParser) -> None:
    command.add_argument("--repo", "--cwd", dest="repo", default=".")


def _instance(command: argparse.ArgumentParser, *, required: bool = True) -> None:
    command.add_argument("--slug", required=required)
    command.add_argument("--workflow-id", required=required)


def _instance_command(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    *, required: bool = True,
) -> argparse.ArgumentParser:
    command = commands.add_parser(name, help=help_text)
    _repo(command)
    _instance(command, required=required)
    return command


def _document_command(command: argparse.ArgumentParser) -> argparse.ArgumentParser:
    command.add_argument("--input", required=True)
    return command


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="workflow", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    command = commands.add_parser("begin", help="start and activate a workflow pass")
    _repo(command)
    command.add_argument("--slug", required=True)
    # Multi-KB request text does not survive being a shell argument, which is why
    # callers reach for a summary. The group refuses both at once rather than
    # inventing precedence between two sources of the same field.
    source = command.add_mutually_exclusive_group()
    source.add_argument("--intent", default="")
    source.add_argument("--intent-file")

    for name in ("status", "summary"):
        command = commands.add_parser(name)
        _repo(command)
        if name == "status":
            command.add_argument("--fields", help="comma-separated fields; default is full status")

    command = commands.add_parser(
        "paths",
        help="print the resolved workflow-state paths for this repository; "
             "with --workflow-id also prints the governing-design path",
    )
    _repo(command)
    command.add_argument("--workflow-id")

    command = commands.add_parser("history", help="read ordered accepted events")
    _repo(command)
    command.add_argument("--workflow-id")

    command = commands.add_parser("evidence", help="read a logical evidence record")
    _repo(command)
    command.add_argument("--evidence-id", required=True)
    command.add_argument("--full", action="store_true", help="include the recorded document")

    command = commands.add_parser("set-phase", help="record a lead-owned phase")
    _repo(command)
    command.add_argument("--phase", required=True)
    command.add_argument("--status", required=True)
    command.add_argument("--findings")
    command.add_argument("--slug")
    command.add_argument("--workflow-id")

    command = _instance_command(commands, "pause", "record an instance-bound honest wait")
    command.add_argument("--reason", required=True)

    command = commands.add_parser("checkpoint", help="query advisor readiness without mutation")
    _repo(command)
    command.add_argument("--phase", required=True)
    command.add_argument("--reconsult", action="store_true", help="user-authorized repeat preflight consultation")
    command.add_argument("--channel-dir", type=Path, help="write ordered advisor channels here")

    command = commands.add_parser("complete", help="complete a ready workflow")
    _repo(command)
    command.add_argument("--slug")
    command.add_argument("--workflow-id")

    record = commands.add_parser("record", help="validate and record a workflow document",
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=DOCUMENT_SHAPE_TABLE)
    kinds = record.add_subparsers(dest="record_kind", required=True)
    for name in ("preflight", "review", "advisor-result", "advisor-disposition", "map"):
        command = _instance_command(kinds, name, f"record {name}", required=False)
        command.epilog = DOCUMENT_SHAPES[name]
        command.formatter_class = argparse.RawDescriptionHelpFormatter
        command.add_argument("--check", action="store_true", help="validate without recording")
        if name in {"preflight", "review", "map"}:
            _document_command(command)
        else:
            command.add_argument("--input")
        if name == "review":
            command.add_argument("--review-context-id")
        elif name == "advisor-result":
            command.add_argument("--stage", required=True)
            command.add_argument("--source", required=True)
            command.add_argument("--verdict", choices=("unavailable",))
            command.add_argument("--reason")
            command.add_argument("--design-declaration")
            command.add_argument("--expected-candidate-tree")
        elif name == "advisor-disposition":
            command.add_argument("--stage")
            command.add_argument("--findings")
            command.add_argument("--finding")
            command.add_argument("--fixed", action="store_true")
            command.add_argument("--evidence-ref")
            command.add_argument("--behavior-id")

    # The TDD flag surface stays with its implementation.
    commands.add_parser("tdd", help="run and record one real RED/GREEN candidate")

    command = commands.add_parser("verify", help="execute and record typed verification")
    _repo(command)
    command.add_argument("--slug", required=True)
    command.add_argument("--kind", choices=("generic", "quality-gate"), default="generic")
    command.add_argument("--base-ref")
    command.add_argument("--replaces", help="failed generic evidence-id:zero-based-run-index")
    command.add_argument("--reason")
    command.add_argument("--timeout", type=int, default=900)
    command.add_argument("--observed", action="store_true", help="record an intercepted shell test and return its exit code")
    command.add_argument("--run-cwd", help="original working directory for an observed command")
    command.add_argument("runner_command", nargs=argparse.REMAINDER)

    command = commands.add_parser("prune", help="report or apply workflow-state retirement")
    command.add_argument("--apply", action="store_true")

    return result





def _intent(args: argparse.Namespace) -> str:
    """The task text exactly as the caller sent it: no stripping, no truncation.

    Both file and stdin intake decode raw bytes as UTF-8 rather than reading text:
    the locale's codec would make the record depend on the environment that started
    the pass, and text mode would translate CRLF and lone CR to LF, so a request
    pasted from a Windows editor would be recorded as something it never said.
    U+0000 is refused rather than recorded: the advisor payload carries this text
    through a shell variable, which cannot hold that character, so accepting it
    would promise a custody the rest of the chain silently breaks.
    """
    if args.intent_file is not None:
        text = Path(args.intent_file).read_bytes().decode("utf-8")
    else:
        text = sys.stdin.buffer.read().decode("utf-8") if args.intent == "-" else args.intent
    if "\0" in text:
        raise ValueError("intent text cannot contain U+0000; the consult payload cannot carry it")
    return text


def _emit_mutation(
    identity: RepoIdentity, operation: Callable[[str], dict[str, object]], *,
    evidence_path: tuple[str, str] | None = None,
) -> None:
    """Return the new state handle without echoing the ledger back to the agent."""
    candidate = _active_candidate_tree(identity)
    state = operation(candidate)
    recovery = public_status(state, identity, fields={"nextAction", "verification", "bindingError"},
                             candidate_tree=candidate, recovery=True)
    phase = str(state.get("phase", ""))
    status = state.get(phase)
    if not isinstance(status, str):
        review = state.get({"advisor-preflight": "advisorPreflight",
                            "final-review": "finalReview",
                            "code-review": "codeReview"}.get(phase, ""))
        status = review.get("status") if isinstance(review, dict) else phase
    receipt: dict[str, object] = {
        "workflowId": state.get("workflowId"), "phase": phase,
        "status": status, "nextAction": recovery.get("nextAction"),
    }
    if "bindingError" in recovery:
        receipt.update(verification=recovery.get("verification"), bindingError=recovery["bindingError"])
    if evidence_path:
        block = state.get(evidence_path[0])
        handle = block.get(evidence_path[1]) if isinstance(block, dict) else None
        if isinstance(handle, str):
            receipt["evidenceId"] = handle
    _emit_json(receipt)


def _state(identity: RepoIdentity) -> dict[str, object]:
    value = read_workflow(identity)
    if value is None:
        raise WorkflowError("no active workflow")
    return value


def _workflow_id(state: dict[str, object]) -> str:
    value = instance_id(state)
    if value is None:
        raise WorkflowError(NO_INSTANCE_ID)
    return value


def _verify(args: argparse.Namespace, identity: RepoIdentity) -> int:
    if args.run_cwd and not args.observed:
        raise ValueError("--run-cwd belongs to --observed")
    if args.run_cwd and resolve_repo_identity(args.run_cwd).root != identity.root:
        raise ValueError("observed command working directory belongs to another repository")
    if bool(args.replaces) != bool(args.reason and args.reason.strip()):
        raise ValueError("--replaces and a non-empty --reason are required together")
    if args.replaces and args.kind != "generic":
        raise ValueError("only generic verification can replace a failed invocation")
    state = bound_state(identity, safe_slug(args.slug))
    slug = str(state["slug"])
    workflow_id = _workflow_id(state)

    # The tree this run's result will describe. The recorder compares it with
    # the tree at commit; a run that could not sample it is recorded invalid.
    binding_error: str | None = None
    tree_before: dict[str, str] | None = None
    graph_context_path: str | None = None
    try:
        tree_before = tree_manifest(identity)
    except RuntimeError as exc:
        binding_error = str(exc)

    if args.kind == "quality-gate":
        if not args.base_ref:
            raise ValueError("quality-gate verification requires --base-ref")
        if args.runner_command:
            raise ValueError("quality-gate verification runs the bundled gate and accepts no command")
        command = [
            sys.executable,
            str(ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"),
            "check",
            "--repo",
            str(identity.root),
            "--base-ref",
            args.base_ref,
            "--json",
        ]
        # The pass's recorded Repo Context Forge evidence, handed to the gate
        # unchanged when it carries the producer's snapshot-bound gate context.
        # The gate's own binding check adjudicates match, stale, or absent; a
        # document without that context simply attaches nothing, and the gate
        # names the absence.
        recorded = state.get("repoContextForgeEvidence")
        graph_document = evidence_document(identity, recorded if isinstance(recorded, str) else None)
        graph_context = (
            graph_document.get("gateContext")
            if isinstance(graph_document, dict) and graph_document.get("workflowId") == workflow_id
            else None
        )
        if isinstance(graph_context, dict):
            handle = tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", prefix="quality-gate-graph-", suffix=".json", delete=False,
            )
            with handle:
                json.dump(graph_context, handle)
            graph_context_path = handle.name
            command += ["--gitnexus-context-json", graph_context_path]
    else:
        if args.base_ref:
            raise ValueError("--base-ref belongs to --kind quality-gate")
        command = args.runner_command[1:] if args.runner_command and args.runner_command[0] == "--" else args.runner_command
        if not command:
            raise ValueError("a command is required after --")

    try:
        raw, exit_code, timed_out = _run(command, identity, args.timeout, cwd=args.run_cwd)
    finally:
        if graph_context_path is not None:
            os.unlink(graph_context_path)
    valid = binding_error is None and not timed_out and exit_code == 0
    gate: dict[str, object] | None = None
    if args.kind == "quality-gate":
        try:
            gate = validate_gate_result(json.loads(raw.decode("utf-8")))
            valid = valid and gate.get("ok") is True
            errors = gate.get("errors")
            capture = next(
                (
                    error for error in (errors if isinstance(errors, list) else ())
                    if isinstance(error, str) and error.startswith("candidate capture ")
                ),
                None,
            )
            if binding_error is None and capture is not None:
                # Drift and outright capture failure are one condition here: the gate
                # never held a tree still, so nothing it reports binds to one. Only the
                # drift shape has a settled name; the rest carry the gate's own words
                # rather than being attributed to a cause the runner cannot know.
                binding_error = (
                    "reviewable tree changed during the quality-gate run"
                    if capture.startswith("candidate capture drift:")
                    else f"the quality gate could not capture the reviewable tree: {capture}"
                )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            valid = False
            binding_error = str(exc)

    observed_shell = args.observed and len(command) == 3 and command[:2] == ["bash", "-lc"]
    run = _run_entry(
        raw, exit_code, timed_out,
        capture_limit=MAX_CAPTURE if observed_shell or tdd_surface.identify(command)["runner"] == "unittest" else 1024,
        kind=args.kind, command=command[2] if observed_shell else shlex.join(command),
        valid=valid, outputBytes=len(raw),
    )
    if args.run_cwd:
        relative = Path(args.run_cwd).resolve().relative_to(Path(identity.root).resolve())
        if relative != Path("."):
            run["runCwd"] = relative.as_posix()
    if args.replaces:
        run.update(replaces=args.replaces, replacementReason=args.reason.strip())
    if args.kind == "quality-gate":
        run["bindingError"] = binding_error
    elif binding_error is not None:
        run["bindingError"] = binding_error
    state, evidence_id, recorded = commit_verification(identity, slug, workflow_id, run, tree_before=tree_before)

    _print_output(raw)
    _emit_json({
        "evidenceId": evidence_id,
        "exitCode": exit_code,
        "kind": args.kind,
        "verification": state["verification"],
        "valid": recorded["valid"],
        "runIndex": recorded["runIndex"],
        "workflowId": workflow_id,
        "treeManifestId": recorded.get("treeManifestId"),
    })
    if recorded["valid"] is not True:
        reason = recorded.get("bindingError") or "verification command failed"
        print(f"{reason}; verification stays pending until its rerun is green", file=sys.stderr)
        if args.observed:
            return exit_code if exit_code >= 0 else 128 - exit_code
        return 2
    return 0


def _record_phase(args: argparse.Namespace, identity: RepoIdentity, phase: str, key: str, value: object) -> int:
    slug = safe_slug(args.slug)
    document = {
        "schemaVersion": 1,
        "slug": slug,
        "workflowId": args.workflow_id,
        key: value,
    }
    pending = phase == "preflight" and any(
        interpretation_pending(item)
        for item in value.get("behaviorMap", [])
    )
    status = "pending" if pending else "passed"
    _, evidence_id = commit_evidence_phase(identity, slug, args.workflow_id, phase, document, status=status)
    recorded = {"evidenceId": evidence_id, "status": status}
    _emit_json(recorded)
    return 2 if pending else 0


def _direct_disposition(args: argparse.Namespace, identity: RepoIdentity) -> tuple[str, dict[str, object]]:
    if not args.finding or not args.fixed or not args.evidence_ref or args.findings not in (None, "addressed"):
        raise ValueError("direct fixed disposition requires --finding, --fixed, and --evidence-ref")
    state = read_workflow(identity)
    if state is None:
        raise WorkflowError("no active workflow")
    matches = [item for item in state.get("findingStates", [])
               if item.get("findingId") == args.finding and item.get("status") == "pending"
               and (args.stage is None or item.get("stage") == args.stage)]
    if len(matches) != 1:
        raise WorkflowError("direct disposition requires one pending finding identity")
    finding = matches[0]
    if finding.get("kind") == "behavioral" and not args.behavior_id:
        raise ValueError("behavioral fixed requires --behavior-id")
    stage = str(finding["stage"])
    return stage, {
        "schemaVersion": 1, "slug": args.slug, "workflowId": args.workflow_id,
        "stage": stage, "context": None, "intakeEvidenceId": finding["intakeEvidenceId"],
        "dispositions": [{"finding_id": args.finding, "status": "fixed",
                          "reason": f"Executed receipt {args.evidence_ref}; mapped attack {args.behavior_id or 'none'}",
                          "evidenceRefs": [args.evidence_ref],
                          **({"behaviorId": args.behavior_id} if args.behavior_id else {})}],
    }


def _advisor_result_input(args: argparse.Namespace) -> tuple[dict[str, object] | None, str]:
    if args.source != "codex-advisor":
        raise ValueError(f"unsupported reviewer source: {args.source}")
    if args.input is not None:
        if args.verdict is not None or args.reason is not None:
            raise ValueError("advisor-result --input derives verdict and findings")
        return advisor_envelope(args.input, slug=safe_slug(args.slug), workflow_id=args.workflow_id,
                                stage=args.stage, producer=args.source)
    if args.verdict != "unavailable":
        raise ValueError("advisor success requires --input; --verdict is only for unavailable preflight")
    if args.stage != "preflight" or not args.reason or not args.reason.strip():
        raise ValueError("preflight unavailable requires --reason")
    return None, "unavailable"


def _advisor_disposition_input(args: argparse.Namespace, identity: RepoIdentity) -> tuple[str, str, dict[str, object] | None]:
    if args.input is not None and (args.finding or args.fixed or args.evidence_ref or args.behavior_id):
        raise ValueError("--input and direct finding flags are separate disposition forms")
    if args.input is not None and args.stage is None:
        raise ValueError("document disposition requires --stage")
    if args.findings == "none" and (args.input is not None or args.finding):
        raise ValueError("--input records an addressed disposition; findings none carries no document")
    findings = args.findings or ("addressed" if args.input is not None else None)
    document = advisor_disposition_document(
        args.input, slug=safe_slug(args.slug), workflow_id=args.workflow_id, stage=args.stage,
    ) if args.input else None
    stage = args.stage
    if args.finding:
        stage, document = _direct_disposition(args, identity)
        findings = "addressed"
    if stage not in {"preflight", "final"} or findings not in {"none", "addressed"} or (findings == "addressed" and document is None):
        raise ValueError("disposition requires --stage and --findings, or direct fixed finding flags")
    return stage, findings, document


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "prune":
        _emit_json(prune(apply=args.apply))
        return 0

    identity = resolve_repo_identity(args.repo)
    if args.command == "record":
        if not args.slug or not args.workflow_id or args.check:
            active = read_workflow(identity)
            if active is None:
                raise WorkflowError("no active workflow")
            args.slug = args.slug or active["slug"]
            args.workflow_id = args.workflow_id or active["workflowId"]
            if safe_slug(args.slug) != active["slug"] or args.workflow_id != active["workflowId"]:
                raise WorkflowError("record targets a different workflow instance")
        if args.check:
            slug = safe_slug(args.slug)
            if args.record_kind == "preflight":
                validated_document(args.input)
            elif args.record_kind == "review":
                review_summary(args.input, slug=slug, workflow_id=args.workflow_id,
                               review_context_id=args.review_context_id)
            elif args.record_kind == "advisor-result":
                _advisor_result_input(args)
                if args.design_declaration is not None:
                    design_declaration(args.design_declaration)
            elif args.record_kind == "advisor-disposition":
                _advisor_disposition_input(args, identity)
            _emit_json({"valid": True})
            return 0
    if args.command == "begin":
        intent = _intent(args)
        state = begin(identity, args.slug, intent)
        # The caller just supplied the intent; the receipt names the pass, never echoes it.
        _emit_json(public_status(state, fields={"schemaVersion", "workflowId", "slug", "activeCandidateTree", "phase", "nextAction"}))
    elif args.command == "status":
        fields = set(args.fields.split(",")) if args.fields else None
        _emit_json(public_status(_state(identity), identity, fields=fields))
    elif args.command == "paths":
        directory = repo_state_dir(identity)
        out: dict[str, object] = {
            "stateRoot": str(state_root()),
            "repoKey": identity.key,
            "repoStateDir": str(directory),
        }
        if args.workflow_id:
            out["designPath"] = str(directory / "designs" / f"{args.workflow_id}.md")
        _emit_json(out)
    elif args.command == "summary":
        print(summary(identity))
    elif args.command == "history":
        _emit_json(history(identity, args.workflow_id))
    elif args.command == "evidence":
        value = evidence_record(identity, args.evidence_id, full=args.full)
        if value is None:
            raise WorkflowError("evidence not found")
        _emit_json(value)
    elif args.command == "set-phase":
        phase = args.phase
        if phase in PRODUCER_OWNED:
            raise ValueError(PRODUCER_OWNED[phase])
        if phase not in LEAD_PHASES:
            raise ValueError("set-phase is lead-owned only for code-review not-required")
        if phase == "code-review" and (args.status != "not-required" or args.findings != "none"):
            raise ValueError("code-review passed is recorder-owned; lead-owned set-phase permits only not-required with findings none")
        _emit_mutation(identity, lambda candidate: set_phase(
            identity,
            phase,
            args.status,
            findings=args.findings,
            slug=args.slug,
            workflow_id=args.workflow_id,
            expected_candidate_tree=candidate,
        ))
    elif args.command == "record" and args.record_kind == "advisor-result":
        intake, verdict = _advisor_result_input(args)
        def record(candidate: str) -> dict[str, object]:
            expected = args.expected_candidate_tree
            if expected is not None and expected != candidate:
                raise WorkflowError("active candidate changed after the advisor checkpoint")
            return record_advisor_result(
                identity,
                args.slug,
                args.workflow_id,
                args.stage,
                args.source,
                verdict,
                reason=args.reason,
                design=(design_declaration(args.design_declaration) if args.design_declaration
                        else design_absence("no governing design supplied")),
                intake=intake,
                expected_candidate_tree=expected or candidate,
            )

        _emit_mutation(identity, record, evidence_path=(
            "advisorPreflight" if args.stage == "preflight" else "finalReview", "intakeEvidence",
        ))
    elif args.command == "record" and args.record_kind == "advisor-disposition":
        stage, findings, document = _advisor_disposition_input(args, identity)
        _emit_mutation(identity, lambda candidate: advisor_disposition(
            identity,
            args.slug,
            args.workflow_id,
            stage,
            findings,
            document=document,
            expected_candidate_tree=candidate,
        ), evidence_path=(
            "advisorPreflight" if stage == "preflight" else "finalReview", "dispositionEvidence",
        ) if findings == "addressed" else None)
    elif args.command == "pause":
        _emit_mutation(identity, lambda candidate: pause(
            identity, args.slug, args.workflow_id, args.reason,
            expected_candidate_tree=candidate,
        ))
    elif args.command == "checkpoint":
        _emit_json(checkpoint(identity, args.phase, reconsult=args.reconsult,
                              channel_dir=args.channel_dir))
    elif args.command == "complete":
        _emit_mutation(identity, lambda candidate: complete(
            identity, slug=args.slug, workflow_id=args.workflow_id,
            expected_candidate_tree=candidate,
        ))
    elif args.command == "record" and args.record_kind == "preflight":
        return _record_phase(args, identity, "preflight", "document", validated_document(args.input))
    elif args.command == "verify":
        return _verify(args, identity)
    elif args.command == "record" and args.record_kind == "review":
        slug = safe_slug(args.slug)
        if _workflow_id(bound_state(identity, slug)) != args.workflow_id:
            raise WorkflowError("--workflow-id does not match the active workflow instance")
        document, status, findings = review_summary(
            args.input,
            slug=slug,
            workflow_id=args.workflow_id,
            review_context_id=args.review_context_id,
        )
        state, evidence_id = commit_review(identity, slug, args.workflow_id, document, status, findings)
        _emit_json({"summaryId": evidence_id, "status": state["codeReview"]["status"]})
    else:
        raise ValueError(f"unsupported workflow command: {args.command}")
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        # TDD parses its own flags before this module's stricter parser.
        if values and values[0] == "tdd":
            from .tdd_workflow import run_tdd
            return run_tdd(values[1:])
        if values[:2] == ["record", "map"]:
            from .tdd_workflow import run_map_update
            return run_map_update(values[2:])
        return _dispatch(parser().parse_args(values))
    except (RepoIdentityError, LedgerError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
