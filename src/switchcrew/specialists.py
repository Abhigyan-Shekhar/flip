"""Useful, bounded specialist analyses from operator-selected local snapshots.

These executors return artifacts for human review. They never send messages,
change calendars, moderate users, run code, or write into the source repository.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

SPECIALIST_ROLES = frozenset(
    {
        "community",
        "documentation",
        "knowledge",
        "onboarding",
        "moderation",
        "security",
        "scheduling",
        "analytics",
        "incident",
    }
)
MAX_SOURCE_BYTES = 65536
MAX_TOTAL_BYTES = 262144
MAX_RECORDS = 500
SENSITIVE = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:api[_ -]?key|password|secret|token)[\"']?\s*[:=]\s*[\"']?\S+"
    r"|(?:sk-|ghp_|github_pat_)[A-Za-z0-9_.-]{12,}|-----BEGIN .*PRIVATE KEY-----)"
)
STOP_WORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "what",
        "which",
        "how",
        "are",
        "this",
        "that",
        "from",
        "find",
        "return",
        "source",
        "sources",
        "evidence",
        "official",
        "about",
        "explain",
        "please",
    }
)


def tokens(value):
    return set(re.findall(r"\b[a-z0-9_]{3,}\b", value.lower())) - STOP_WORDS


def safe_text(value, limit=1000):
    text = str(value)
    return ("[REDACTED: sensitive content]" if SENSITIVE.search(text) else text)[:limit]


def _private_path(relative):
    parts = [part.lower() for part in relative.parts]
    return any(
        part in {".git", ".ssh", ".aws", ".env", ".switch", "credentials", "secrets"}
        or part.startswith(".env.")
        or part.endswith((".pem", ".key", ".p12"))
        for part in parts
    )


def _snapshot(config):
    root = Path(config["root"]).resolve(strict=True)
    paths = config.get("sources", [])
    if not root.is_dir() or not isinstance(paths, list) or not 1 <= len(paths) <= 20:
        raise ValueError("Configure an artifact root and between one and twenty source paths")
    documents, seen, total = [], set(), 0
    for entry in paths:
        if not isinstance(entry, str):
            raise ValueError("Sources must be relative paths")
        relative = Path(entry)
        if relative.is_absolute() or ".." in relative.parts or _private_path(relative):
            raise ValueError("Source must be a public artifact inside the configured root")
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or _private_path(path.relative_to(root)):
            raise ValueError("Source escaped the configured artifact root")
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file() or path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError("Source is not a bounded regular file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_SOURCE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_SOURCE_BYTES or total > MAX_TOTAL_BYTES or b"\0" in raw:
            raise ValueError("Source size/type limit exceeded")
        text = raw.decode("utf-8")
        redactions = [
            index for index, line in enumerate(text.splitlines(), 1) if SENSITIVE.search(line)
        ]
        clean = "\n".join(safe_text(line, MAX_SOURCE_BYTES) for line in text.splitlines())
        documents.append(
            {
                "id": f"S{len(documents) + 1}",
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "text": clean,
                "redacted_lines": redactions,
            }
        )
    return documents


def _evidence(documents, question, criteria):
    terms = tokens(question + " " + " ".join(criteria))
    snippets = []
    seen = set()
    for source in documents:
        for number, line in enumerate(source["text"].splitlines(), 1):
            line = re.sub(r"\s+", " ", line).strip()
            normalized = line.casefold()
            if not line or line.startswith("```") or normalized in seen:
                continue
            overlap = len(terms & tokens(line))
            if overlap:
                snippets.append(
                    {
                        "source": source["id"],
                        "line": number,
                        "excerpt": line[:600],
                        "score": overlap,
                    }
                )
                seen.add(normalized)
    return sorted(snippets, key=lambda item: -item["score"])[:12]


def _records(documents, key):
    records = []
    for source in documents:
        data = json.loads(source["text"])
        if not isinstance(data, dict) or not isinstance(data.get(key), list):
            raise ValueError(f"A JSON object containing {key} is required")
        for record in data[key]:
            if not isinstance(record, dict):
                raise ValueError("Snapshot records must be objects")
            records.append({**record, "_source": source["id"]})
            if len(records) > MAX_RECORDS:
                raise ValueError("Snapshot record limit exceeded")
    return records


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("ISO datetime string required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return result


def _window(record):
    start, end = _instant(record["start"]), _instant(record["end"])
    if end <= start:
        raise ValueError("Window end must follow start")
    return start, end


class SpecialistExecutor:
    def __init__(self, role, config):
        if role not in SPECIALIST_ROLES:
            raise ValueError("Unknown artifact specialist")
        # Runtime configuration is trusted operator state, never handoff JSON.
        self.role, self.config = role, dict(config)

    async def __call__(self, state):
        try:
            documents = _snapshot(self.config)
            criteria = state["acceptance_criteria"]
            evidence = _evidence(documents, state["question"], criteria)
            content, details = self._work(documents, evidence, state)
            provenance = [
                {key: source[key] for key in ("id", "path", "sha256", "redacted_lines")}
                for source in documents
            ]
            return {
                "status": "completed",
                "role": self.role,
                "action_performed": False,
                "artifact": {"format": "markdown", "content": content[:24000]},
                "sources": provenance,
                "evidence": evidence,
                "acceptance_assessment": [
                    {"criterion": safe_text(c, 2000), "status": "human_review_required"}
                    for c in criteria
                ],
                "limitations": "Analysis/draft from configured snapshots only; no external action "
                "was taken. Snapshot completeness and acceptance need human review.",
                **details,
            }
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return {
                "status": "blocked_configuration",
                "role": self.role,
                "reason": "valid_bounded_operator_snapshots_required",
                "action_performed": False,
            }

    def _work(self, documents, evidence, state):
        def render(rows):
            return (
                "\n".join(f"- {row['excerpt']} [{row['source']}:L{row['line']}]" for row in rows)
                or "- No relevant source excerpt found; request clarification."
            )

        role = self.role
        if role == "documentation":
            content = (
                f"# Documentation draft\n\n## Request\n{safe_text(state['question'], 2000)}\n\n"
            )
            content += "## Source-backed notes\n" + render(evidence)
            content += "\n\n## Acceptance review\n" + "\n".join(
                f"- [ ] {safe_text(c, 2000)}" for c in state["acceptance_criteria"]
            )
            return content, {"draft_kind": "documentation"}
        if role == "community":
            content = "# Support reply draft\n\nBased on the configured project references:\n"
            content += render(evidence)
            content += (
                "\n\nPlease confirm the affected version and reproduction steps before sending."
            )
            return content, {
                "draft_kind": "support_reply",
                "needs_clarification": not bool(evidence),
            }
        if role == "knowledge":
            return "# Curated knowledge notes\n\n" + render(evidence), {
                "entries": [
                    {"statement": e["excerpt"], "source": e["source"], "line": e["line"]}
                    for e in evidence
                ],
                "draft_kind": "knowledge_notes",
            }
        if role == "onboarding":
            rows = [
                row
                for row in evidence
                if not row["excerpt"].startswith("#")
                and re.search(
                    r"\b(install|setup|configure|run|start|copy|register|verify|test)\b",
                    row["excerpt"],
                    re.I,
                )
            ]
            rows.sort(key=lambda row: (int(row["source"][1:]), row["line"]))
            content = "# Onboarding checklist draft\n\n" + "\n".join(
                f"- [ ] {row['excerpt']} [{row['source']}:L{row['line']}]" for row in rows
            )
            if not rows:
                content += (
                    "No matching setup steps found; ask the operator for an onboarding guide."
                )
            return content, {"steps": rows, "draft_kind": "onboarding_checklist"}
        if role == "security":
            findings = []
            for source in documents:
                for line in source["redacted_lines"]:
                    findings.append(
                        {
                            "source": source["id"],
                            "line": line,
                            "rule": "possible_secret",
                            "severity": "high",
                        }
                    )
                for number, line in enumerate(source["text"].splitlines(), 1):
                    if re.search(r"\b(eval|exec)\s*\(|shell\s*=\s*True|verify\s*=\s*False", line):
                        findings.append(
                            {
                                "source": source["id"],
                                "line": number,
                                "rule": "unsafe_execution_or_tls_pattern",
                                "severity": "review",
                            }
                        )
            return "# Security review\n\n" + "\n".join(
                f"- {f['rule']} ({f['severity']}) [{f['source']}:L{f['line']}]" for f in findings
            ) + "\n\nPattern scan only; absence of findings does not establish security.", {
                "findings": findings[:100],
                "scan_kind": "static_pattern_review",
            }
        if role == "analytics":
            records = _records(documents, "records")
            counts = Counter(safe_text(r.get("status", "unknown"), 100) for r in records)
            durations = []
            for record in records:
                if "duration_seconds" in record:
                    value = record["duration_seconds"]
                    if isinstance(value, bool) or not isinstance(value, (int, float)):
                        raise ValueError("Numeric duration required")
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("Nonnegative finite duration required")
                    durations.append(value)
            metrics = {
                "record_count": len(records),
                "status_counts": dict(counts),
                "duration_sample_count": len(durations),
                "mean_duration_seconds": sum(durations) / len(durations) if durations else None,
            }
            return "# Snapshot metrics\n\n```json\n" + json.dumps(metrics, indent=2) + "\n```", {
                "metrics": metrics,
            }
        if role == "scheduling":
            events = _records(documents, "events")
            candidate = self.config["candidate"]
            start, end = _window(candidate)
            conflicts = []
            for event in events:
                other_start, other_end = _window(event)
                if start < other_end and other_start < end:
                    conflicts.append(
                        {
                            "source": event["_source"],
                            "title": safe_text(event.get("title", "Untitled"), 200),
                            "start": event["start"],
                            "end": event["end"],
                        }
                    )
            proposal = {
                "title": safe_text(candidate.get("title", "Proposed meeting"), 200),
                "start": candidate["start"],
                "end": candidate["end"],
                "conflicts": conflicts,
                "available_in_snapshot": not conflicts,
            }
            return "# Scheduling proposal\n\n```json\n" + json.dumps(
                proposal, indent=2
            ) + "\n```", {
                "proposal": proposal,
            }
        if role == "moderation":
            messages = _records(documents, "messages")
            rules = self.config.get("rules", [])
            if (
                not isinstance(rules, list)
                or not 1 <= len(rules) <= 50
                or any(not isinstance(rule, dict) for rule in rules)
            ):
                raise ValueError("Operator moderation rules required")
            reviews = []
            for message in messages:
                text = str(message.get("text", ""))
                for rule in rules:
                    terms = rule.get("terms", [])
                    if (
                        not isinstance(terms, list)
                        or not 1 <= len(terms) <= 30
                        or not all(
                            isinstance(term, str) and 0 < len(term.strip()) <= 200 for term in terms
                        )
                    ):
                        raise ValueError("Nonblank moderation terms required")
                    if any(
                        re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I)
                        for term in terms
                    ):
                        reviews.append(
                            {
                                "message_id": safe_text(message["id"], 100),
                                "rule_id": safe_text(rule["id"], 100),
                                "source": message["_source"],
                                "recommendation": "human_review",
                            }
                        )
            return "# Moderation review queue\n\n" + "\n".join(
                f"- Message {r['message_id']}: rule {r['rule_id']} [{r['source']}]" for r in reviews
            ), {"reviews": reviews[:100], "draft_kind": "moderation_queue"}
        if role == "incident":
            events = _records(documents, "events")
            timeline = sorted(events, key=lambda record: _instant(record["timestamp"]))
            failures = [
                r
                for r in timeline
                if str(r.get("severity", "")).lower() in {"error", "critical", "fatal"}
            ]
            lines = [
                f"- {r['timestamp']} {safe_text(r.get('severity', 'info'), 30)}: "
                f"{safe_text(r.get('message', ''), 400)} [{r['_source']}]"
                for r in timeline
            ]
            content = "# Incident triage draft\n\n## Timeline\n" + "\n".join(lines)
            content += (
                "\n\n## Suggested next checks\n- Correlate errors with deployment/restart times."
            )
            content += "\n- Verify current health before applying any mitigation."
            return content, {
                "event_count": len(events),
                "failure_count": len(failures),
                "root_cause": "unconfirmed",
            }
        raise ValueError("Unsupported specialist")


def specialist_executor(role, config):
    """Return an async graph executor from explicit operator-owned configuration."""
    return SpecialistExecutor(role, config)
