from __future__ import annotations

import os
import re
from collections import namedtuple
from pathlib import Path


EXCLUDE_DIRS = {
    ".cache", ".git", ".hg", ".mypy_cache", ".next", ".pytest_cache", ".ruff_cache", ".svn", ".tox", ".venv",
    "build", "coverage", "dist", "node_modules", "target", "vendor", "venv",
}

VENDORED_DIRS = {"node_modules", "vendor"}

ROLE_PRODUCTION = "production"
ROLE_TEST = "test"
ROLE_TEST_SUPPORT = "test-support"
ROLE_GENERATED = "generated"
ROLE_DOCS = "docs"
ROLE_VENDORED = "vendored"
ROLE_UNKNOWN = "unknown"

DOC_EXTENSIONS = {".adoc", ".md", ".markdown", ".rst", ".txt"}

# Dependency pins are configuration, not prose, under every spelling the
# convention uses: requirements.txt, requirements-dev.txt, dev-requirements.txt,
# requirements.dev.txt, and the directory-split requirements/dev.txt.
MANIFEST_STEMS = ("requirements", "constraints")

# Machine-written source is not human-authored, so it is excluded from the
# human-authored totals rather than folded into the test-support bucket.
GENERATED_MARKERS = ("/generated/",)

# Material that supports tests without being test code itself.
SUPPORT_MARKERS = ("/__fixtures__/", "/__mocks__/", "/__snapshots__/", "/fixture/", "/fixtures/", "/snapshots/")


_LEGACY_TEST_MARKERS = SUPPORT_MARKERS + GENERATED_MARKERS + ("/__tests__/", "/test/", "/tests/")

# One resolved classification, computed once and stored on the snapshot. A
# namedtuple, not a dataclass: workflow state loads this file standalone via
# spec_from_file_location without registering it in sys.modules, and dataclass
# creation resolves annotations through sys.modules on Python 3.12.
PathClass = namedtuple(
    "PathClass",
    ["role", "language", "human_authored", "source", "test_like_compat", "exclusion_reason"],
)


def normalize_path(value: str) -> str:
    return value.strip().replace(os.sep, "/")


def is_binary_path(path: str) -> bool:
    return Path(path).name.lower() in {"pnpm-lock.yaml", "package-lock.json", "go.sum", "bun.lockb"} or Path(path).suffix.lower() in {
        ".avif", ".bin", ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".lock",
        ".map", ".pdf", ".png", ".snap", ".svg", ".webp", ".zip",
    }


def is_excluded_path(path: str) -> bool:
    parts = [part.lower() for part in normalize_path(path).split("/") if part]
    if any(part in EXCLUDE_DIRS for part in parts):
        return True
    lowered = f"/{normalize_path(path).lower()}"
    return any(
        marker in lowered
        for marker in ("/.codex/quality/logs/", "/.codex/quality/logs/", "/logs/")
    )


def is_test_like_path(path: str) -> bool:
    lowered = f"/{normalize_path(path).lower()}"
    if any(marker in lowered for marker in _LEGACY_TEST_MARKERS):
        return True
    name = Path(path).name.lower()
    # pytest discovers test_*.py and *_test.py with no tests/ directory in the
    # path, and reads conftest.py wherever it sits, so name alone decides those;
    # .test./.spec. cover the JS convention.
    if re.fullmatch(r"(?:test_.+|.+_test|conftest)\.py", name):
        return True
    return bool(re.search(r"\.(?:test|spec)\.", name)) or name.endswith(".schema.json")



def is_test_path(path: str) -> bool:
    normalized = normalize_path(path)
    entry = Path(normalized)
    name = entry.name.casefold()
    words = {word.casefold() for word in re.split(
        r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])",
        str(entry.parent) if entry.suffix.casefold() in DOC_EXTENSIONS else normalized)}
    return (name == "conftest.py" or bool(words & {"test", "tests"})
            or bool({part.casefold() for part in entry.parts[:-1]} & {"spec", "specs"})
            or ".spec." in name or ".test." in name
            or any(marker in "/" + normalized.casefold() for marker in SUPPORT_MARKERS))


def is_source_path(path: str) -> bool:
    return bool(path) and not is_excluded_path(path) and not is_binary_path(path) and (Path(path).suffix.lower() not in DOC_EXTENSIONS or is_dependency_manifest(path))


def classify_path(path: str) -> PathClass:
    """The single classification every quality-gate consumer reads.

    The filename rule governs test capture and quality roles; the legacy hint
    keeps its existing meaning.
    """
    test_like = is_test_path(path)
    compat = is_test_like_path(path)
    lowered = f"/{normalize_path(path).lower()}"
    if is_source_path(path):
        # The stored language enum reserves real parser names for source
        # entries; every non-source classification is "other".
        language = language_for_path(path)
        if any(marker in lowered for marker in GENERATED_MARKERS):
            return PathClass(ROLE_GENERATED, language, False, True, compat, "generated path")
        if not test_like:
            return PathClass(ROLE_PRODUCTION, language, True, True, compat, None)
        support = any(marker in lowered for marker in SUPPORT_MARKERS)
        return PathClass(ROLE_TEST_SUPPORT if support else ROLE_TEST, language, True, True, compat, None)
    if not path:
        return PathClass(ROLE_UNKNOWN, "other", False, False, compat, "empty path")
    if is_excluded_path(path):
        parts = {part for part in lowered.split("/") if part}
        role = ROLE_VENDORED if parts & VENDORED_DIRS else ROLE_UNKNOWN
        return PathClass(role, "other", False, False, compat, "excluded directory")
    if is_binary_path(path):
        return PathClass(ROLE_UNKNOWN, "other", False, False, compat, "binary extension")
    if is_dependency_manifest(path):
        return PathClass(ROLE_PRODUCTION, "other", True, False, compat, None)
    role = ROLE_DOCS if Path(path).suffix.lower() in DOC_EXTENSIONS else ROLE_UNKNOWN
    return PathClass(role, "other", False, False, compat, "non-source extension")


def is_dependency_manifest(path: str) -> bool:
    """Whether the path pins dependencies, under any spelling of the convention.

    The literal name decides, not the name stripping would produce: Git keeps a
    pathname's whitespace, so `requirements.txt ` is a different file.
    """
    literal = Path(path)
    stem = literal.name.lower().removesuffix(".txt")
    if stem == literal.name.lower():
        return False
    return literal.parent.name.lower() in MANIFEST_STEMS or any(
        part in MANIFEST_STEMS for part in re.split(r"[-.]", stem)
    )


def language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
        return "javascript"
    return {
        ".go": "go",
        ".rs": "rust",
        ".sh": "shell",
        ".php": "php",
        ".rb": "ruby",
    }.get(suffix, suffix.lstrip(".") or "source")


def is_temp_artifact(path: str) -> bool:
    lowered = normalize_path(path).lower()
    if not lowered:
        return False
    return bool(re.search(r"(^|/)(?:\.tmp|tmp|temp)(/|$)", lowered)) or lowered.endswith(
        (".bak", ".orig", ".rej", ".tmp")
    )
