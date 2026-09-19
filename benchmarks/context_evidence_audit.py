#!/usr/bin/env python3
"""Audit labeled capture events. Candidates are NOT measured avoidable retrieval.

Input JSON: {"provenance": {"traceSha256": "...", "labeler": "..."}, "events": [
  {"id": "...", "path": "...", "operation": "sed", "requestedScope": {"start": 1, "end": 40},
   "sourceVersion": null, "resultRef": "actual-trace-location", "output": null}]}
Use exact returned output and observed versions; missing evidence remains null.
Unassignable output uses path=null, attribution="unbound" and is excluded from
file-repeat metrics. This preserves the complete capture without guessing paths.
The tool never infers model retention, delivery, necessity, or token savings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MAX_BYTES = 32 * 1024 * 1024


def audit(document: dict) -> dict:
    if not isinstance(document, dict) or not isinstance(document.get("events"), list):
        raise ValueError("expected labeled events, not aggregate counts")
    provenance = document.get("provenance")
    if (not isinstance(provenance, dict) or not isinstance(provenance.get("traceSha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", provenance["traceSha256"])
            or not isinstance(provenance.get("labeler"), str) or not provenance["labeler"]):
        raise ValueError("retain a trace digest and labeling provenance")
    ids, paths, requests, outputs = set(), set(), set(), set()
    counts = dict(events=0, repeatedPath=0, sameRequestedScope=0,
                  sameVersionOutputCandidates=0, unknownSourceVersion=0, missingOutput=0, unboundPath=0)
    observations = []
    for event in document["events"]:
        if (not isinstance(event, dict) or not all(isinstance(event.get(key), str) and event[key]
                for key in ("id", "operation", "resultRef"))
                or "requestedScope" not in event or "sourceVersion" not in event or "output" not in event):
            raise ValueError("each event needs identity, request scope and actual result reference; use null for unknowns")
        if ("path" not in event or (event["path"] is None and event.get("attribution") != "unbound")
                or (event["path"] is not None and (not isinstance(event["path"], str) or not event["path"]))):
            raise ValueError("path must identify an observed source, or be null with attribution=unbound")
        bound = event["path"] is not None and event.get("attribution") != "unbound"
        if event["id"] in ids:
            raise ValueError("duplicate event id")
        ids.add(event["id"])
        version, output = event["sourceVersion"], event["output"]
        if ((version is not None and not isinstance(version, str))
                or (output is not None and not isinstance(output, str))):
            raise ValueError("sourceVersion/output must be strings or null")
        request = (event["path"], event["operation"], json.dumps(event["requestedScope"], sort_keys=True))
        same_scope = bound and event["requestedScope"] is not None and request in requests
        fingerprint = (event["path"], version, hashlib.sha256(output.encode()).hexdigest()) if bound and output is not None and version else None
        same_output = fingerprint is not None and fingerprint in outputs
        counts["events"] += 1
        counts["repeatedPath"] += bound and event["path"] in paths
        counts["unboundPath"] += not bound
        counts["sameRequestedScope"] += same_scope
        counts["sameVersionOutputCandidates"] += same_output
        counts["unknownSourceVersion"] += not bool(version)
        counts["missingOutput"] += output is None
        observations.append({"id": event["id"], "resultRef": event["resultRef"],
                             "sameRequestCandidate": same_scope, "sameOutputCandidate": same_output})
        if bound:
            paths.add(event["path"])
            requests.add(request)
        if fingerprint:
            outputs.add(fingerprint)
    return {"provenance": provenance, "counts": counts, "observations": observations,
            "avoidableRetrieval": None, "tokenSavings": None,
            "qualification": "Input labels require audit. Different scopes can overlap; identical requests may be necessary. "
                             "No availability, final delivery, semantic sufficiency or savings are inferred."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args()
    try:
        with args.capture.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("capture exceeds audit input bound; split at explicit capture boundaries")
        print(json.dumps(audit(json.loads(raw)), ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, f"capture audit unavailable: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
