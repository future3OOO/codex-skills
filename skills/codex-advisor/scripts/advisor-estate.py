#!/usr/bin/env python3
"""Launch a native provider in the advisor's persistent instruction estate."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[3]


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def user_hooks(document: dict, homes: list[Path], source: Path, target: Path) -> tuple[dict, dict[str, str]]:
    """Remove this estate's commands; preserve unrelated hooks and settings."""
    owned = {str(home / "hooks" / name) for home in homes for name in
             ("code-quality-gate.py", "skill-discipline-rearm.py", "rcf-intake-gate.py")}
    identities = {}
    for event, groups in document.get("hooks", {}).items():
        kept = []
        event_key = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
        for group_index, group in enumerate(groups):
            hooks = []
            for hook_index, hook in enumerate(group.get("hooks", [])):
                tokens = shlex.split(hook.get("command", ""))
                command = tokens[1] if len(tokens) > 1 and Path(tokens[0]).name == "python3" else next(iter(tokens), "")
                if str(Path(command.replace("$HOME", str(Path.home()))).expanduser()) in owned:
                    continue
                identities[f"{source}:{event_key}:{group_index}:{hook_index}"] = f"{target}:{event_key}:{len(kept)}:{len(hooks)}"
                hooks.append(hook)
            group["hooks"] = hooks
            if group["hooks"]:
                kept.append(group)
        document["hooks"][event] = kept
    return document, identities


def prepare(provider: str) -> tuple[Path, Path, dict, dict]:
    native = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()
    default_config = native / "docs/agents/advisor-launch.json"
    if not default_config.is_file():
        default_config = ROOT / "docs/agents/advisor-launch.json"
    config_path = Path(os.environ.get("ADVISOR_LAUNCH_CONFIG", default_config)).expanduser()
    config = read_json(config_path)
    if set(config) != {"rules", "skills", "mcp"}:
        raise ValueError("advisor launch JSON requires exactly rules, skills and mcp")
    for key, values in config.items():
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            raise ValueError(f"advisor {key} must be a list of nonempty strings")
        if len(set(values)) != len(values):
            raise ValueError(f"advisor {key} repeats a selection")
    rules = "\n\n".join((config_path.parent / Path(p).expanduser()).read_text() for p in config["rules"])
    home = Path(os.environ.get("ADVISOR_ESTATE_ROOT", native / "advisor")) / provider
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (home / ("AGENTS.md" if provider == "codex" else "CLAUDE.md")).write_text(rules)
    skills = home / "skills"
    skills.mkdir(exist_ok=True)
    sources = {}
    for name in config["skills"]:
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError(f"invalid advisor skill name: {name}")
        source = next((p for p in (native / "skills" / name, ROOT / "skills" / name) if (p / "SKILL.md").is_file()), None)
        if source is None:
            raise ValueError(f"advisor skill is not installed: {name}")
        sources[name] = source
    for entry in skills.iterdir():
        if entry.is_symlink() or entry.is_file():
            entry.unlink()
        else:
            shutil.rmtree(entry)
    for name, source in sources.items():
        (skills / name).symlink_to(source, target_is_directory=True)
    source_home = native if provider == "codex" else Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
    for name in (("config.toml", "auth.json", ".credentials.json", "sessions", "archived_sessions", "mcp-oauth-locks")
                 if provider == "codex" else (".credentials.json", "projects")):
        target = home / name
        if name in {"sessions", "archived_sessions", "mcp-oauth-locks", "projects"}:
            (source_home / name).mkdir(parents=True, exist_ok=True)
        if (source_home / name).exists() and not target.exists():
            target.symlink_to(source_home / name)
    settings = source_home / ("hooks.json" if provider == "codex" else "settings.json")
    document = read_json(settings) if settings.exists() else {}
    document, identities = user_hooks(document, [native, source_home, Path.home() / ".codex", Path.home() / ".claude"],
                                      settings, home / settings.name)
    trust = {}
    if provider == "codex" and identities and (source_home / "config.toml").exists():
        configured = tomllib.loads((source_home / "config.toml").read_text()).get("hooks", {}).get("state", {})
        # Native trust keys include the file and positions, both changed by projection.
        moved = {target: configured[source] for source, target in identities.items() if source in configured}
        if moved:
            trust = configured | moved
    if provider == "claude":
        plugins = dict(document.get("enabledPlugins", {}))
        for path in (Path.cwd() / ".claude").glob("settings*.json"):
            plugins.update(read_json(path).get("enabledPlugins", {}))
        document["enabledPlugins"] = dict.fromkeys(plugins, False)
        document["skillOverrides"] = {**document.get("skillOverrides", {}), "plugin-authoring": "off",
                                      **dict.fromkeys(config["skills"], "on")}
    (home / settings.name).write_text(json.dumps(document))
    (home / "empty").mkdir(exist_ok=True)
    return home, native, config, trust


def main() -> int:
    provider, *arguments = sys.argv[1:]
    if provider not in {"codex", "claude"}:
        raise ValueError("advisor provider must be codex or claude")
    home, native, config, trust = prepare(provider)
    if arguments == ["--prepare"]:
        return 0
    environment = os.environ | {"ADVISOR_ACTIVE": "1", "CODEX_ADVISOR_ACTIVE": "1",
                               "CODEX_WORKFLOW_STATE_ROOT": os.environ.get("CODEX_WORKFLOW_STATE_ROOT", str(native / "state"))}
    if provider == "codex":
        result = subprocess.run(["codex", "mcp", "list", "--json"], capture_output=True, text=True, check=True, timeout=20)
        servers = json.loads(result.stdout)
        if not isinstance(servers, list) or any(not isinstance(s, dict) or not isinstance(s.get("name"), str) for s in servers):
            raise ValueError("native Codex returned an invalid MCP catalog")
        names = {s["name"] for s in servers}
        if missing := set(config["mcp"]) - names:
            raise ValueError(f"advisor MCPs are not configured: {sorted(missing)}")
        flags = ["-c", "features.plugins=false", "-c", "features.apps=false", "-c", "features.memories=false",
                 "-c", "features.goals=false", "-c", "features.image_generation=false", "-c", "features.view_image=false",
                 "-c", "features.sleep_tool=false", "-c", "tools.experimental_request_user_input={enabled=false}",
                 "-c", "include_collaboration_mode_instructions=false",
                 # features.multi_agent does not remove the lead's multi-agent briefing or tools; these settings do.
                 "-c", 'features.multi_agent_v2={disable_native_tools=true,root_agent_usage_hint_text="",multi_agent_mode_hint_text=""}',
                 "-c", "skills.bundled.enabled=false", "-c", "sqlite_home=" + json.dumps(str(native))]
        flags += ["-c", "skills.config=[" + ",".join("{name=" + json.dumps(name) + ",enabled=true}"
                                                    for name in config["skills"]) + "]"]
        if trust:
            entries = [json.dumps(key) + "={" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in state.items()) + "}"
                       for key, state in trust.items()]
            flags += ["-c", "hooks.state={" + ",".join(entries) + "}"]
        flags += ["-c", "mcp_servers={" + ",".join(json.dumps(name) + "={enabled=" + str(name in config["mcp"]).lower() + "}"
                                                 for name in sorted(names)) + "}"]
        environment["CODEX_HOME"] = str(home)
        command = ["codex", *flags, *arguments]
    else:
        global_config = (Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / ".claude.json"
                         if "CLAUDE_CONFIG_DIR" in os.environ else Path.home() / ".claude.json")
        document = read_json(global_config) if global_config.exists() else {}
        servers = dict(document.get("mcpServers", {}))
        project = Path.cwd() / ".mcp.json"
        if project.exists():
            servers.update(read_json(project).get("mcpServers", {}))
        servers.update(document.get("projects", {}).get(str(Path.cwd()), {}).get("mcpServers", {}))
        if missing := set(config["mcp"]) - servers.keys():
            raise ValueError(f"advisor MCPs are not configured: {sorted(missing)}")
        mcp = home / ".claude.json"
        mcp.write_text(json.dumps({"mcpServers": {name: servers[name] for name in config["mcp"]}}))
        mcp.chmod(0o600)
        environment["CLAUDE_CONFIG_DIR"] = str(home)
        environment["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"] = "1"
        command = ["claude", *arguments, "--settings", str(home / "settings.json"),
                   "--strict-mcp-config", "--mcp-config", str(mcp)]
    # Full filesystem access; only other automatic skill roots are hidden.
    binding = ["bwrap", "--die-with-parent", "--dev-bind", "/", "/", "--tmpfs", str(home / "skills")]
    for selected in (home / "skills").iterdir():
        binding += ["--ro-bind", str(selected.resolve()), str(selected)]
    roots = {Path.home() / ".agents/skills", Path("/etc/codex/skills"), Path.cwd() / ".codex/skills"}
    roots.update(parent / relative for parent in (Path.cwd(), *Path.cwd().parents)
                 for relative in (".agents/skills", ".claude/skills", ".claude/commands"))
    for path in sorted(roots):
        if path.is_dir() and path != home / "skills":
            binding += ["--ro-bind", str(home / "empty"), str(path)]
    os.execvpe(binding[0], [*binding, "--", *command], environment)


if __name__ == "__main__":
    raise SystemExit(main())
