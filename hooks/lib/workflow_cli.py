"""One public command-line Interface for repository workflow operations."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from ._workflow_db import CHECK_ONLY, LedgerError, _canonical, history
from .behavior_map import interpretation_pending
from .command_runner import emit_json as _emit_json, print_output as _print_output, run as _run, run_entry as _run_entry
from .repo_identity import RepoIdentity, RepoIdentityError, resolve_repo_identity, try_resolve_repo_identity
from .state_prune import prune
from .state_store import _active_candidate_tree, repo_state_dir, state_root, tree_manifest, utc_timestamp
from .workflow_documents import (
    DOCUMENT_SHAPE_TABLE,
    advisor_envelope,
    design_declaration,
    load_json,
    preflight_document,
    review_summary,
    validate_gate_result,
)
from .workflow_state import (
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
    execution_receipt,
    pause,
    public_status,
    read_workflow,
    record_advisor_result,
    set_phase,
    summary,
)

ROOT = Path(__file__).resolve().parents[2]
ITEM_SHAPE = ('{"id":"BM_X","kind":"contract|preservation","behavior":"...","seam":"...","expected":"...",'
              '"redFailure":"MARKER","status":"pending|already-satisfied|omitted",'
              '"sourceRefs":[{"type":"finding","evidenceId":"<intake>","id":"SPEC-1"}]}')
RECORD_SHAPES = {
    "preflight": f'{{"authoritativeContract":"text","behaviorMap":[{ITEM_SHAPE}]}}',
    "review": ('intake {"findings":[{"id":"R-1","claim":"...","material":true,"kind":"behavioral|nonbehavioral",'
               '"priorFinding":{"evidenceId":"...","id":"..."}}],"implementationContextId":"optional"} or '
               'disposition {"intakeEvidenceId":"...","dispositions":[{"finding_id":"R-1","status":"fixed|'
               'rejected-with-evidence|report-only|accepted-follow-up","reason":"...","evidenceRefs":["E:0"]}]}'),
    "advisor-result": ('the advisor envelope {"schemaVersion":1,"findings":[{"id":"SPEC-1","claim":"...",'
                       '"material":true,"kind":"behavioral|nonbehavioral"}],"verdict":"completed|commit-ready|'
                       'fix-before-commit|context-mismatch"}, or --verdict unavailable --reason TEXT'),
    "advisor-disposition": ("--finding F --fixed|--rejected|--report-only|--follow-up REF --evidence-ref E:i "
                            "[--behavior-id BM] [--reason TEXT]; or --stage S --findings none; or --input "
                            '{"intakeEvidenceId":"...","context":{...},"dispositions":[...]}, each disposition:\n'
                            + DOCUMENT_SHAPE_TABLE),
    "tdd-map": (f'{{"sourceBehaviorId":"BM_GREEN","items":[{ITEM_SHAPE}],"dispositions":['
                '{"id":"BM_X","sourceRefs":[...]} | {"id":"BM_X","status":"omitted|superseded|withdrawn",'
                '"supersededBy":"BM_Y"} | {"id":"BM_X","revalidate":true,"evidence":"why"}],"reassessment":"optional"}'),
}
DISPOSITION_FLAGS = {"fixed": "fixed", "rejected": "rejected-with-evidence", "report_only": "report-only"}


def _repo(command: argparse.ArgumentParser, *, instance: bool = False) -> argparse.ArgumentParser:
    command.add_argument("--repo", "--cwd", dest="repo", default=".")
    if instance:
        command.add_argument("--slug")
        command.add_argument("--workflow-id")
    return command


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="workflow", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    command = _repo(commands.add_parser("begin", help="start and activate a workflow pass"))
    command.add_argument("--slug", required=True)
    source = command.add_mutually_exclusive_group()
    source.add_argument("--intent", default="")
    source.add_argument("--intent-file")

    command = _repo(commands.add_parser("status"))
    command.add_argument("--fields", help="comma-separated fields; default is full status")
    _repo(commands.add_parser("summary"))
    command = _repo(commands.add_parser("paths", help="print the resolved workflow-state paths; "
                                        "with --workflow-id also the governing-design path"))
    command.add_argument("--workflow-id")
    command = _repo(commands.add_parser("history", help="read ordered accepted events"))
    command.add_argument("--workflow-id")
    command = _repo(commands.add_parser("evidence", help="evidence metadata; --full for the document"))
    command.add_argument("--evidence-id", required=True)
    command.add_argument("--full", action="store_true")

    command = _repo(commands.add_parser("set-phase", help="record code-review not-required"), instance=True)
    command.add_argument("--phase", required=True)
    command.add_argument("--status", required=True)
    command.add_argument("--findings")
    command = _repo(commands.add_parser("pause", help="record an honest wait"), instance=True)
    command.add_argument("--reason", required=True)
    command = _repo(commands.add_parser("checkpoint", help="query advisor readiness without mutation"))
    command.add_argument("--phase", required=True)
    command.add_argument("--reconsult", action="store_true", help="user-authorized repeat preflight consultation")
    command.add_argument("--channel-dir", help="write the advisor evidence channels here and list them in order")
    _repo(commands.add_parser("complete", help="complete a ready workflow"), instance=True)
    commands.add_parser("tdd", help="run and record one real RED/GREEN candidate")

    command = _repo(commands.add_parser("verify", help="execute and record typed verification"), instance=True)
    command.add_argument("--kind", choices=("generic", "quality-gate"), default="generic")
    command.add_argument("--base-ref")
    command.add_argument("--replaces", help="failed generic evidence-id:zero-based-run-index")
    command.add_argument("--reason")
    command.add_argument("--timeout", type=int, default=900)
    command.add_argument("--observed", action="store_true", help="run a command transparently, keeping its receipt")
    command.add_argument("--from-evidence", help="bind an executed receipt evidence-id:run-index as verification")
    command.add_argument("runner_command", nargs=argparse.REMAINDER)

    record = commands.add_parser("record", help="validate and record one document; --check records nothing")
    kinds = record.add_subparsers(dest="kind", required=True)
    for kind, shape in RECORD_SHAPES.items():
        command = _repo(kinds.add_parser(kind, epilog=f"accepted shape: {shape}",
                                         formatter_class=argparse.RawDescriptionHelpFormatter), instance=True)
        command.add_argument("--check", action="store_true", help="validate against the ledger without recording")
        command.add_argument("--input", help="document path, or - for stdin")
        if kind == "review":
            command.add_argument("--review-context-id")
        elif kind == "advisor-result":
            command.add_argument("--stage", required=True)
            command.add_argument("--source", default="codex-advisor")
            command.add_argument("--verdict")
            command.add_argument("--reason")
            command.add_argument("--design-declaration")
            command.add_argument("--expected-candidate-tree")
        elif kind == "advisor-disposition":
            command.add_argument("--stage")
            command.add_argument("--findings", choices=("none", "addressed"))
            command.add_argument("--finding")
            status = command.add_mutually_exclusive_group()
            for flag in DISPOSITION_FLAGS:
                status.add_argument(f"--{flag.replace('_', '-')}", action="store_true")
            status.add_argument("--follow-up", metavar="REFERENCE")
            command.add_argument("--evidence-ref", action="append", default=[])
            command.add_argument("--behavior-id")
            command.add_argument("--reason")

    command = commands.add_parser("prune", help="report or apply workflow-state retirement")
    command.add_argument("--apply", action="store_true")
    return result


def _intent(args: argparse.Namespace) -> str:
    """The task text exactly as the caller sent it: raw UTF-8, no newline translation;
    U+0000 is refused because the consult payload cannot carry it."""
    if args.intent_file is not None:
        text = Path(args.intent_file).read_bytes().decode("utf-8")
    else:
        text = sys.stdin.buffer.read().decode("utf-8") if args.intent == "-" else args.intent
    if "\0" in text:
        raise ValueError("intent text cannot contain U+0000; the consult payload cannot carry it")
    return text


def _receipt(state: dict[str, object], identity: RepoIdentity) -> dict[str, object]:
    """A mutation's whole answer: which pass, where it stands, what comes next
    (derived against the current tree, so a stale binding is never advertised)."""
    return public_status(state, identity, fields={"workflowId", "slug", "phase", "nextAction"}, recovery=True)


def _command(values: list[str]) -> list[str]:
    command = values[1:] if values and values[0] == "--" else values
    if not command:
        raise ValueError("a command is required after --")
    return command


def _observed(command: list[str]) -> int:
    """Run a hook-rewritten test command as asked; at the root of a checkout with an
    open workflow, also keep its receipt. Output and exit code are the command's."""
    identity = try_resolve_repo_identity(os.getcwd())
    state = None
    if identity is not None and str(Path.cwd().resolve()) == identity.root:
        try:
            state = read_workflow(identity)
        except LedgerError as exc:  # an unreadable ledger never stops the command
            print(f"workflow receipt not recorded: {exc}", file=sys.stderr)
    if state is None or state.get("phase") == "complete" and not state.get("revalidation"):
        return subprocess.run(command, check=False).returncode
    binding_error = None
    try:
        tree_before: dict[str, str] | None = tree_manifest(identity)
    except RuntimeError as exc:
        tree_before, binding_error = None, str(exc)
    # In the caller's process group, streaming as it runs: a kill of the tool call
    # stops the command, and the output arrives when it would have directly.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    chunks: list[bytes] = []
    for chunk in iter(lambda: process.stdout.read1(65536), b""):
        chunks.append(chunk)
        sys.stdout.buffer.write(chunk)
        sys.stdout.flush()
    raw, exit_code, timed_out = b"".join(chunks), process.wait(), False
    run = _run_entry(raw, exit_code, timed_out, kind="observed", command=shlex.join(command),
                     valid=binding_error is None and exit_code == 0, outputBytes=len(raw),
                     **({"bindingError": binding_error} if binding_error else {}))
    try:
        _, evidence_id, recorded = commit_verification(identity, state["slug"], state["workflowId"], run,
                                                       tree_before=tree_before)
        print(f"workflow receipt {evidence_id}:{recorded['runIndex']}", file=sys.stderr)
    except (LedgerError, ValueError, OSError) as exc:
        print(f"workflow receipt not recorded: {exc}", file=sys.stderr)
    return exit_code


def _verify(args: argparse.Namespace, identity: RepoIdentity) -> int:
    if bool(args.replaces) != bool(args.reason and args.reason.strip()):
        raise ValueError("--replaces and a non-empty --reason are required together")
    if args.replaces and args.kind != "generic":
        raise ValueError("only generic verification can replace a failed invocation")
    state = bound_state(identity, args.slug, args.workflow_id)
    slug, workflow_id = str(state["slug"]), str(state["workflowId"])
    if args.from_evidence:
        if args.runner_command or args.kind != "generic":
            raise ValueError("--from-evidence binds a recorded receipt and takes no command")
        receipt, manifest = execution_receipt(identity, state, args.from_evidence)
        run = {key: receipt[key] for key in ("command", "exitCode", "timedOut", "outputBytes") if key in receipt}
        run.update(kind="generic", valid=receipt.get("exitCode") == 0, sourceReference=args.from_evidence,
                   at=utc_timestamp(), **({"replaces": args.replaces, "replacementReason": args.reason.strip()}
                                          if args.replaces else {}))
        state, evidence_id, recorded = commit_verification(identity, slug, workflow_id, run, tree_before=manifest)
        _emit_json({"evidenceId": evidence_id, "runIndex": recorded["runIndex"], "valid": recorded["valid"],
                    "verification": state["verification"]})
        return 0 if recorded["valid"] is True else 2

    # The tree this run's result will describe. The recorder compares it with
    # the tree at commit; a run that could not sample it is recorded invalid.
    binding_error: str | None = None
    tree_before: dict[str, str] | None = None
    graph_evidence_id: str | None = None
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
        command = [sys.executable, str(ROOT / "skills" / "production-code" / "scripts" / "code_quality_gate.py"),
                   "check", "--repo", str(identity.root), "--base-ref", args.base_ref, "--json"]
        # The pass's recorded Repo Context Forge evidence, handed to the gate
        # unchanged when it carries the producer's snapshot-bound gate context;
        # the gate's own binding check adjudicates match, stale, or absent.
        recorded = state.get("repoContextForgeEvidence")
        graph_document = evidence_document(identity, recorded if isinstance(recorded, str) else None)
        graph_context = (
            graph_document.get("gateContext")
            if isinstance(graph_document, dict) and graph_document.get("workflowId") == workflow_id
            else None
        )
        if isinstance(graph_context, dict):
            graph_evidence_id = str(recorded)
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
        command = _command(args.runner_command)

    try:
        raw, exit_code, timed_out = _run(command, identity, args.timeout)
    finally:
        if graph_context_path is not None:
            os.unlink(graph_context_path)
    valid = binding_error is None and not timed_out and exit_code == 0
    gate: dict[str, object] | None = None
    shown = raw
    if args.kind == "quality-gate":
        try:
            gate = validate_gate_result(json.loads(raw.decode("utf-8")))
            # The lead reads the gate's whole report; the ledger keeps its verdict.
            raw = (json.dumps(gate, sort_keys=True) + "\n").encode()
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
                # never held a tree still, so nothing it reports binds to one.
                binding_error = (
                    "reviewable tree changed during the quality-gate run"
                    if capture.startswith("candidate capture drift:")
                    else f"the quality gate could not capture the reviewable tree: {capture}"
                )
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            valid = False
            binding_error = str(exc)

    run = _run_entry(
        raw, exit_code, timed_out,
        kind=args.kind, command=shlex.join(command), valid=valid, outputBytes=len(raw),
    )
    if args.replaces:
        run.update(replaces=args.replaces, replacementReason=args.reason.strip())
    if args.kind == "quality-gate":
        run.update(baseRef=args.base_ref, gate=gate, bindingError=binding_error, graphEvidenceId=graph_evidence_id)
    elif binding_error is not None:
        run["bindingError"] = binding_error
    state, evidence_id, recorded = commit_verification(identity, slug, workflow_id, run, tree_before=tree_before)

    _print_output(shown)
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
        return 2
    return 0


def _document(args: argparse.Namespace, label: str) -> dict[str, object]:
    if not args.input:
        raise ValueError(f"record {args.kind} requires --input <path|->")
    return load_json(args.input, label=label)


def _record(args: argparse.Namespace, identity: RepoIdentity) -> int:
    """One ingest seam; --check runs the whole recording and rolls it back."""
    CHECK_ONLY.set(args.check)
    state = bound_state(identity, args.slug, args.workflow_id)
    slug, workflow_id = str(state["slug"]), str(state["workflowId"])
    checked = {"checked": True} if args.check else {}
    if args.kind == "preflight":
        if not args.input:
            raise ValueError("record preflight requires --input <path|->")
        document = preflight_document(args.input)
        pending = any(interpretation_pending(item) for item in document["behaviorMap"])
        status = "pending" if pending else "passed"
        _, evidence_id = commit_evidence_phase(identity, slug, workflow_id, "preflight", {
            "schemaVersion": 1, "slug": slug, "workflowId": workflow_id, "document": document,
            "recordedAt": utc_timestamp()}, status=status)
        _emit_json({"evidenceId": evidence_id, "status": status, **checked})
        return 2 if pending else 0
    if args.kind == "review":
        if not args.input:
            raise ValueError("record review requires --input <path|->")
        document, status, findings = review_summary(
            args.input, slug=slug, workflow_id=workflow_id, review_context_id=args.review_context_id)
        state, evidence_id = commit_review(identity, slug, workflow_id, document, status, findings)
        _emit_json({"summaryId": evidence_id, "status": state["codeReview"]["status"], **checked})
        return 0
    if args.kind == "tdd-map":
        from .tdd_workflow import map_update
        _emit_json({**map_update(identity, state, _document(args, "TDD map update")), **checked})
        return 0
    candidate = _active_candidate_tree(identity)
    if args.kind == "advisor-result":
        if args.input == "-" and args.design_declaration == "-":
            raise ValueError("only one of --input and --design-declaration can read stdin")
        intake, verdict = None, args.verdict
        if args.input is not None:
            if args.verdict is not None or args.reason is not None:
                raise ValueError("record advisor-result --input derives the verdict; --verdict/--reason are for unavailable")
            intake, verdict = advisor_envelope(args.input, slug=slug, workflow_id=workflow_id,
                                               stage=args.stage, producer=args.source)
        elif verdict is None:
            raise ValueError("record advisor-result requires --input or --verdict unavailable --reason")
        expected = args.expected_candidate_tree
        if expected is not None and expected != candidate:
            raise WorkflowError("active candidate changed after the advisor checkpoint")
        state = record_advisor_result(
            identity, slug, workflow_id, args.stage, args.source, verdict, reason=args.reason,
            design=design_declaration(args.design_declaration) if args.design_declaration else None,
            intake=intake, expected_candidate_tree=expected or candidate)
    else:
        flag = None
        if args.finding is not None:
            status = next((DISPOSITION_FLAGS[name] for name in DISPOSITION_FLAGS if getattr(args, name)),
                          "accepted-follow-up" if args.follow_up else None)
            if status is None or not args.evidence_ref or args.input is not None:
                raise ValueError("--finding needs one of --fixed/--rejected/--report-only/--follow-up, "
                                 "at least one --evidence-ref, and no --input")
            if status != "fixed" and not (args.reason and args.reason.strip()):
                raise ValueError(f"{status} needs --reason with the measured judgment")
            flag = {"finding": args.finding, "status": status, "evidenceRefs": args.evidence_ref,
                    "reason": (args.reason or "").strip() or None, "reference": args.follow_up,
                    "behaviorId": args.behavior_id}
        elif args.behavior_id or args.evidence_ref:
            raise ValueError("--behavior-id and --evidence-ref belong to --finding")
        document = None if flag is not None or args.input is None else load_json(args.input, label="disposition")
        findings = args.findings or ("none" if flag is None and document is None else "addressed")
        state = advisor_disposition(identity, slug, workflow_id, args.stage, findings,
                                    document=document, flag=flag, expected_candidate_tree=candidate)
    _emit_json({**_receipt(state, identity), **checked})
    return 0


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "prune":
        _emit_json(prune(apply=args.apply))
        return 0
    if args.command == "verify" and args.observed:
        defaults = {"repo": ".", "slug": None, "workflow_id": None, "kind": "generic", "base_ref": None,
                    "replaces": None, "reason": None, "timeout": 900, "from_evidence": None}
        if given := [name for name, value in defaults.items() if getattr(args, name) != value]:
            raise ValueError("--observed takes only a command; drop " + ", ".join(f"--{n.replace('_', '-')}" for n in given))
        return _observed(_command(args.runner_command))

    identity = resolve_repo_identity(args.repo)
    if args.command == "begin":
        _emit_json(public_status(begin(identity, args.slug, _intent(args)), fields={
            "schemaVersion", "workflowId", "slug", "activeCandidateTree", "phase", "nextAction"}))
    elif args.command == "status":
        state = read_workflow(identity)
        if state is None:
            raise WorkflowError("no active workflow")
        _emit_json(public_status(state, identity, fields=set(args.fields.split(",")) if args.fields else None))
    elif args.command == "paths":
        directory = repo_state_dir(identity)
        out: dict[str, object] = {"stateRoot": str(state_root()), "repoKey": identity.key, "repoStateDir": str(directory)}
        if args.workflow_id:
            out["designPath"] = str(directory / "designs" / f"{args.workflow_id}.md")
        _emit_json(out)
    elif args.command == "summary":
        print(summary(identity))
    elif args.command == "history":
        _emit_json(history(identity, args.workflow_id))
    elif args.command == "evidence":
        value = evidence_record(identity, args.evidence_id)
        if value is None:
            raise WorkflowError("evidence not found")
        document = value.pop("document")
        _emit_json({**value, "document": document} if args.full else {
            **value, "bytes": len(_canonical(document).encode()), "fields": sorted(document)})
    elif args.command == "set-phase":
        if (args.phase, args.status, args.findings) != ("code-review", "not-required", "none"):
            raise ValueError("set-phase records only --phase code-review --status not-required --findings none; "
                             "producers record every other step")
        _emit_json(_receipt(set_phase(identity, "code-review", "not-required", findings="none", slug=args.slug,
                                      workflow_id=args.workflow_id,
                                      expected_candidate_tree=_active_candidate_tree(identity)), identity))
    elif args.command == "pause":
        _emit_json(_receipt(pause(identity, args.slug, args.workflow_id, args.reason,
                                  expected_candidate_tree=_active_candidate_tree(identity)), identity))
    elif args.command == "checkpoint":
        _emit_json(checkpoint(identity, args.phase, reconsult=args.reconsult, channel_dir=args.channel_dir))
    elif args.command == "complete":
        _emit_json(_receipt(complete(identity, slug=args.slug, workflow_id=args.workflow_id,
                                     expected_candidate_tree=_active_candidate_tree(identity)), identity))
    elif args.command == "verify":
        return _verify(args, identity)
    elif args.command == "record":
        return _record(args, identity)
    return 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    try:
        # The TDD verb's parsing travels with its implementation.
        if values and values[0] == "tdd":
            from .tdd_workflow import run_tdd
            return run_tdd(values[1:])
        return _dispatch(parser().parse_args(values))
    except (RepoIdentityError, LedgerError, WorkflowError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
