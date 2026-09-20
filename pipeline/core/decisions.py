"""Append-only decision log: who decided what, why, and how.

Every decision in a project -- by a human, by an AI model, or by a plain rule
-- becomes one line in `decisions.jsonl`. A readable `DECISIONS.md` is
regenerated from it. Each entry stores the SHA-256 of the previous entry, so
editing or deleting a line after the fact is detectable (`verify()`).

Actor types:
  human    a named person at a review gate
  ai       a model that produced or judged something (name + model recorded)
  machine  deterministic code (rules, matchers, downloaders)
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GENESIS = "0" * 64


@dataclass(frozen=True)
class Actor:
    type: str                      # human | ai | machine
    name: str
    model: str = ""                # for ai: the model id
    version: str = ""              # for machine: rules/code version

    def as_dict(self) -> dict[str, str]:
        d = {"type": self.type, "name": self.name}
        if self.model:
            d["model"] = self.model
        if self.version:
            d["version"] = self.version
        return d


def human(name: str) -> Actor:
    return Actor("human", (name or "").strip() or "unnamed")


def machine(name: str, version: str = "") -> Actor:
    return Actor("machine", name, version=version)


def ai(name: str, model: str = "") -> Actor:
    return Actor("ai", name, model=model)


def actor_from(obj: Any, default: Actor) -> Actor:
    """Stages may expose an `actor` attribute (Actor or dict) describing themselves."""
    a = getattr(obj, "actor", None)
    if isinstance(a, Actor):
        return a
    if isinstance(a, dict) and a.get("type") and a.get("name"):
        return Actor(a["type"], a["name"], a.get("model", ""), a.get("version", ""))
    return default


def _canonical(entry: dict[str, Any]) -> str:
    return json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class DecisionLog:
    def __init__(self, project_dir: str | Path):
        self.dir = Path(project_dir)
        self.jsonl = self.dir / "decisions.jsonl"
        self.md = self.dir / "DECISIONS.md"

    # ------------------------------------------------------------------ write
    def record(
        self,
        *,
        job_id: str,
        stage: str,
        action: str,
        actor: Actor,
        decision: str = "",
        reason: str = "",
        subject: dict[str, Any] | None = None,
        logic: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        prev_hash, seq = GENESIS, 0
        last = self._last()
        if last:
            prev_hash, seq = last["hash"], last["seq"] + 1
        entry: dict[str, Any] = {
            "seq": seq,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "job_id": job_id,
            "stage": stage,
            "action": action,
            "actor": actor.as_dict(),
            "decision": decision,
            "reason": reason,
            "subject": subject or {},
            "logic": logic or {},
            "inputs": inputs or {},
            "outputs": outputs or {},
            "prev_hash": prev_hash,
        }
        entry["hash"] = hashlib.sha256((prev_hash + _canonical(entry)).encode()).hexdigest()
        with open(self.jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        self.write_markdown()
        return entry

    # ------------------------------------------------------------------- read
    def entries(self) -> list[dict[str, Any]]:
        if not self.jsonl.exists():
            return []
        out = []
        with open(self.jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def _last(self) -> dict[str, Any] | None:
        if not self.jsonl.exists():
            return None
        last = None
        with open(self.jsonl, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        return json.loads(last) if last else None

    def verify(self) -> tuple[bool, int | None]:
        """(True, None) if the chain is intact, else (False, seq of the first bad entry)."""
        prev = GENESIS
        for i, e in enumerate(self.entries()):
            claimed = e.get("hash")
            body = {k: v for k, v in e.items() if k != "hash"}
            ok = (
                e.get("seq") == i
                and e.get("prev_hash") == prev
                and hashlib.sha256((prev + _canonical(body)).encode()).hexdigest() == claimed
            )
            if not ok:
                return False, i
            prev = claimed
        return True, None

    # --------------------------------------------------------------- markdown
    def write_markdown(self) -> None:
        self.md.write_text(render_markdown(self.entries()), encoding="utf-8")


_STAGE_TITLES = {
    "project": "Project", "keywords": "Keywords", "sourcing": "Sourcing", "vetting": "Vetting",
    "assets": "Asset review", "scenes": "Scenes", "render": "Render",
}


def _actor_str(a: dict[str, str]) -> str:
    extra = a.get("model") or a.get("version")
    return f"{a['type']}: {a['name']}" + (f" ({extra})" if extra else "")


def _short(v: Any, n: int = 140) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def render_markdown(entries: list[dict[str, Any]]) -> str:
    lines = ["# Decision log", "",
             "Generated from `decisions.jsonl` (the source of truth; append-only and hash-chained).", ""]
    if not entries:
        return "\n".join(lines + ["_No decisions recorded yet._", ""])
    humans = sum(1 for e in entries if e["actor"]["type"] == "human")
    ais = sum(1 for e in entries if e["actor"]["type"] == "ai")
    machines = sum(1 for e in entries if e["actor"]["type"] == "machine")
    lines += [f"**{len(entries)} entries**: {humans} by humans, {ais} by AI, {machines} by machine rules.", ""]
    current = None
    for e in entries:
        if e["stage"] != current:
            current = e["stage"]
            lines += [f"## {_STAGE_TITLES.get(current, current.title())}", ""]
        head = f"**{e['at']}** · {_actor_str(e['actor'])} · `{e['action']}`"
        if e.get("decision"):
            head += f" → **{e['decision']}**"
        lines.append(f"- {head}")
        subj = e.get("subject") or {}
        if subj:
            lines.append(f"  - about: {_short(subj)}")
        if e.get("reason"):
            lines.append(f"  - why: {_short(e['reason'], 400)}")
        if e.get("logic"):
            lines.append(f"  - how: {_short(e['logic'], 400)}")
        if e.get("outputs"):
            lines.append(f"  - result: {_short(e['outputs'], 300)}")
    lines.append("")
    return "\n".join(lines)
