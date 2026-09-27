"""The gate's one network Seam: TypeSafe System One (model Jev) judges typed questions about a state. Nothing here
decides; an undelivered request raises `Unjudged` with its reason, never silence. Each answer is kept in a JSON-lines
store by its exact request, and the same request is answered from the store, never sent again."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
_RETRY = {408, 429, *range(500, 600)}  # the TypeSafe SDK RetryPolicy default; 401/403 (a bad key) fail at once
# Sizing: 1.6 request characters per input token (the least measured; 1.9-2.9 on real runs) plus a fixed overhead per
# request, which a short request otherwise out-weighs (357 estimated vs 396 counted without it).
CHARS_PER_TOKEN, REQUEST_TOKENS = 1.6, 64


class Unjudged(Exception):
    """A request TypeSafe did not answer, with the reason."""


def api_key() -> str:
    """TYPESAFE_API_KEY, else ~/.config/typesafe/key; empty when neither is set."""
    key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    path = Path.home() / ".config" / "typesafe" / "key"
    try:
        return key or (path.read_text(encoding="utf-8").strip() if path.is_file() else "")
    except (OSError, UnicodeError):
        return ""


class Session:
    """Requests under one key; `store` (None: keep nothing) holds every answer by its request. An unreadable store is only
    a colder start and an unwritable one only costs the next run its reuse."""

    def __init__(self, key: str, store: Path | None = None) -> None:
        self.key, self.store, self.lock, self.kept = key, store, threading.Lock(), {}
        try:
            self.kept = {row["id"]: row["answers"] for row in map(json.loads, store.read_text(encoding="utf-8").splitlines())} if store and store.is_file() else {}
        except (OSError, UnicodeError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def id(state: dict[str, object], questions: dict[str, object]) -> str:
        return hashlib.sha256(json.dumps([MODEL, state, questions], sort_keys=True).encode()).hexdigest()

    def ask(self, state: dict[str, object], questions: dict[str, object]) -> tuple[dict[str, dict[str, object]], int, bool]:
        """The answers, the input tokens sent, and whether it was sent (False: answered from the store)."""
        ident = self.id(state, questions)
        if ident in self.kept:
            return self.kept[ident], 0, False
        body = json.dumps({"model": MODEL, "state": state, "questions": questions}, sort_keys=True).encode()
        request = urllib.request.Request(URL, data=body, headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    reply = json.load(response)
                answers, tokens = reply["answers"], int(reply["usage"]["input_tokens"])
                if not isinstance(answers, dict) or set(questions) - set(answers):
                    raise Unjudged("TypeSafe answered only part of the questions")
                break
            except urllib.error.HTTPError as error:
                if error.code not in _RETRY or attempt == 3:
                    raise Unjudged(f"TypeSafe rejected the request (HTTP {error.code})") from error
                retry = str(error.headers.get("retry-after") or "")
                time.sleep(min(float(retry) if retry.isdigit() else 2 ** attempt, 10.0))
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise Unjudged(f"TypeSafe unreachable: {error}") from error
        with self.lock:
            self.kept[ident] = answers
            try:
                if self.store:
                    self.store.parent.mkdir(parents=True, exist_ok=True)
                    with self.store.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({"id": ident, "answers": answers}) + "\n")
            except OSError:
                self.store = None
        return answers, tokens, True
