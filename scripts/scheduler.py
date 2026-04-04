"""Scheduler — persistent scheduled and recurring jobs for Octobots.

Jobs execute by sending taskbox messages to workers, sending prompts
directly to tmux panes, running shell commands, or invoking Claude Code agents.

Adapted from Octo's heartbeat/cron system, simplified for supervisor use.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict, replace as dataclasses_replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Interval / time helpers ──────────────────────────────────────────


def parse_interval(spec: str) -> timedelta:
    """Parse interval spec like '30s', '5m', '2h', '1d' into timedelta."""
    match = re.match(r"^(\d+)\s*(s|m|h|d)", spec.strip(), re.I)
    if not match:
        raise ValueError(f"Invalid interval: {spec!r}")
    value = int(match.group(1))
    unit = match.group(2).lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return timedelta(seconds=value * multipliers[unit])


def parse_at_time(spec: str) -> datetime:
    """Parse an 'at' spec into a UTC datetime.

    Supports:
      - Relative: "in 2h", "in 30m", "2h", "30m"
      - Time-of-day: "15:00", "08:30" (next occurrence, local)
      - ISO datetime: "2024-02-11T15:00:00Z"
    """
    s = spec.strip().lower()

    # Relative: "in 2h", "in 30m", "2h", "30m"
    rel = re.match(r"^(?:in\s+)?(\d+)\s*(s|m|h|d)", s, re.I)
    if rel:
        delta = parse_interval(rel.group(1) + rel.group(2))
        return datetime.now(timezone.utc) + delta

    # Time-of-day: "15:00", "08:30"
    tod = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if tod:
        h, m = int(tod.group(1)), int(tod.group(2))
        now = datetime.now()
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target.astimezone(timezone.utc)

    # ISO datetime
    try:
        dt = datetime.fromisoformat(s.rstrip("z"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    raise ValueError(f"Cannot parse time spec: {spec!r}")


def next_cron_run(cron_expr: str, after: datetime) -> datetime:
    """Calculate next run time for a 5-field cron expression.

    Fields: minute hour day_of_month month day_of_week
    Supports: *, ranges (1-5), lists (1,3,5), steps (*/5)
    Day of week: 0=Mon ... 6=Sun (also MON-SUN)
    """
    DOW_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

    def _parse_field(field_str: str, min_val: int, max_val: int) -> set[int]:
        result: set[int] = set()
        for part in field_str.split(","):
            part = part.strip().lower()
            for name, num in DOW_MAP.items():
                part = part.replace(name, str(num))

            if "/" in part:
                base, step_str = part.split("/", 1)
                step = int(step_str)
                if base == "*":
                    start = min_val
                elif "-" in base:
                    start = int(base.split("-")[0])
                else:
                    start = int(base)
                result.update(range(start, max_val + 1, step))
            elif "-" in part:
                lo, hi = part.split("-", 1)
                result.update(range(int(lo), int(hi) + 1))
            elif part == "*":
                result.update(range(min_val, max_val + 1))
            else:
                result.add(int(part))
        return result

    fields = cron_expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got {len(fields)}: {cron_expr!r}")

    minutes = _parse_field(fields[0], 0, 59)
    hours = _parse_field(fields[1], 0, 23)
    doms = _parse_field(fields[2], 1, 31)
    months = _parse_field(fields[3], 1, 12)
    dows = _parse_field(fields[4], 0, 6)

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    max_candidate = after + timedelta(days=730)

    while candidate < max_candidate:
        if (candidate.month in months
                and candidate.day in doms
                and candidate.weekday() in dows
                and candidate.hour in hours
                and candidate.minute in minutes):
            return candidate
        candidate += timedelta(minutes=1)

    raise ValueError(f"No matching time found for cron expression: {cron_expr!r}")


def format_interval(td: timedelta) -> str:
    """Format a timedelta as a human-readable interval."""
    total = int(td.total_seconds())
    if total >= 86400 and total % 86400 == 0:
        return f"{total // 86400}d"
    if total >= 3600 and total % 3600 == 0:
        return f"{total // 3600}h"
    if total >= 60 and total % 60 == 0:
        return f"{total // 60}m"
    return f"{total}s"


# ── Job data structures ──────────────────────────────────────────────


class JobType(str, Enum):
    AT = "at"
    EVERY = "every"
    CRON = "cron"


class JobAction(str, Enum):
    SEND = "send"       # Send taskbox message to a worker
    PROMPT = "prompt"   # Send keys directly to worker's tmux pane
    RUN = "run"         # Execute a shell command
    AGENT = "agent"     # Invoke a Claude Code agent


@dataclass
class ScheduledJob:
    id: str
    type: JobType
    spec: str
    action: JobAction
    target: str             # Role name (for send/prompt) or shell command (for run)
    content: str            # Message content (for send/prompt), empty for run
    created_at: str = ""
    next_run: str = ""
    last_run: str | None = None
    paused: bool = False
    run_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["action"] = self.action.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> ScheduledJob:
        data = dict(data)
        data["type"] = JobType(data.get("type", "at"))
        data["action"] = JobAction(data.get("action", "send"))
        return cls(**data)


# ── Job persistence ──────────────────────────────────────────────────


class JobStore:
    """Read/write scheduled jobs from a JSON file."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> list[ScheduledJob]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [ScheduledJob.from_dict(d) for d in data]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def save(self, jobs: list[ScheduledJob]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([j.to_dict() for j in jobs], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def add(self, job: ScheduledJob) -> None:
        jobs = self.load()
        jobs.append(job)
        self.save(jobs)

    def remove(self, job_id: str) -> bool:
        jobs = self.load()
        before = len(jobs)
        jobs = [j for j in jobs if j.id != job_id]
        if len(jobs) < before:
            self.save(jobs)
            return True
        return False

    def update(self, job: ScheduledJob) -> None:
        jobs = self.load()
        for i, j in enumerate(jobs):
            if j.id == job.id:
                jobs[i] = job
                break
        self.save(jobs)

    def toggle_pause(self, job_id: str) -> bool | None:
        """Toggle paused state. Returns new paused value, or None if not found."""
        jobs = self.load()
        for j in jobs:
            if j.id == job_id:
                j.paused = not j.paused
                self.save(jobs)
                return j.paused
        return None


# ── Scheduler ────────────────────────────────────────────────────────


def resolve_agent(agent_name: str, octobots_dir: Path, runtime_dir: Path) -> Path | None:
    """Find an agent directory by name.

    Resolution order:
      1. .octobots/agents/<name>/AGENT.md  (project override)
      2. octobots/shared/agents/<name>/AGENT.md  (framework shared)
    """
    for base in [runtime_dir / "agents", octobots_dir / "shared" / "agents"]:
        agent_dir = base / agent_name
        if (agent_dir / "AGENT.md").is_file():
            return agent_dir
    return None


class Scheduler:
    """Checks for due jobs and executes them. Called from the supervisor poll loop.

    Synchronous execution via taskbox, tmux, shell commands, or Claude Code agents.
    """

    def __init__(
        self,
        store: JobStore,
        taskbox: Any,           # Taskbox instance from supervisor
        tmux: Any,              # TmuxManager instance from supervisor
        relay_script: Path,     # Path to relay.py
        octobots_dir: Path | None = None,   # Path to octobots framework dir
        runtime_dir: Path | None = None,    # Path to .octobots runtime dir
        on_event: Any = None,   # Optional callback: (job, result) -> None
    ):
        self.store = store
        self.taskbox = taskbox
        self.tmux = tmux
        self.relay_script = relay_script
        self.octobots_dir = octobots_dir or relay_script.parent.parent.parent
        self.runtime_dir = runtime_dir or Path.cwd() / ".octobots"
        self.on_event = on_event

    def check(self) -> list[tuple[ScheduledJob, str]]:
        """Check for due jobs, execute them, and return list of (job, result)."""
        jobs = self.store.load()
        now = datetime.now(timezone.utc)
        results: list[tuple[ScheduledJob, str]] = []

        for job in jobs:
            if job.paused:
                continue

            try:
                next_run = datetime.fromisoformat(job.next_run)
                if next_run.tzinfo is None:
                    next_run = next_run.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            if next_run <= now:
                result = self._execute(job)
                results.append((job, result))

                updated = self._advance(job)
                if updated is None:
                    self.store.remove(job.id)
                else:
                    self.store.update(updated)

                if self.on_event:
                    self.on_event(job, result)

        return results

    def _interpolate(self, content: str, job: ScheduledJob) -> str:
        """Resolve {variable} placeholders in scheduled prompt content at execution time.

        Variables:
            {time}     — 24h local time, e.g. "14:35"
            {date}     — ISO 8601 date, e.g. "2026-04-04"
            {datetime} — local date + time, e.g. "2026-04-04 14:35"
            {weekday}  — full weekday name, e.g. "Friday"
            {week}     — ISO week number, e.g. "14"
            {month}    — full month name, e.g. "April"
            {role}     — target role receiving the prompt
            {schedule} — the job's spec (interval or cron expression)

        Unknown {placeholders} are left unchanged.
        """
        now = datetime.now()
        return (
            content
            .replace("{time}",     now.strftime("%H:%M"))
            .replace("{date}",     now.strftime("%Y-%m-%d"))
            .replace("{datetime}", now.strftime("%Y-%m-%d %H:%M"))
            .replace("{weekday}",  now.strftime("%A"))
            .replace("{week}",     now.strftime("%W"))
            .replace("{month}",    now.strftime("%B"))
            .replace("{role}",     job.target)
            .replace("{schedule}", job.spec)
        )

    def _execute(self, job: ScheduledJob) -> str:
        """Execute a single job. Returns a result string."""
        # Resolve template variables in content before dispatch
        job = dataclasses_replace(job, content=self._interpolate(job.content, job))
        try:
            if job.action == JobAction.SEND:
                return self._exec_send(job)
            elif job.action == JobAction.PROMPT:
                return self._exec_prompt(job)
            elif job.action == JobAction.RUN:
                return self._exec_run(job)
            elif job.action == JobAction.AGENT:
                return self._exec_agent(job)
            else:
                return f"Unknown action: {job.action}"
        except Exception as e:
            return f"Error: {e}"

    def _exec_send(self, job: ScheduledJob) -> str:
        """Send a taskbox message to a worker."""
        try:
            r = subprocess.run(
                [
                    "python3", str(self.relay_script),
                    "send", "--from", "scheduler", "--to", job.target,
                    job.content,
                ],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                return f"Sent to {job.target}"
            return f"Send failed: {r.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return "Send timed out"

    def _exec_prompt(self, job: ScheduledJob) -> str:
        """Send keys directly to a worker's tmux pane."""
        pane = self.tmux.panes.get(job.target)
        if not pane:
            return f"Unknown role: {job.target}"
        if self.tmux.send_keys(pane, job.content, confirm_paste=True):
            return f"Prompted {job.target}"
        return f"Failed to prompt {job.target}"

    def _exec_run(self, job: ScheduledJob) -> str:
        """Run a shell command."""
        try:
            r = subprocess.run(
                job.target,
                shell=True,
                capture_output=True, text=True,
                timeout=60,
            )
            output = r.stdout.strip() or r.stderr.strip()
            status = "ok" if r.returncode == 0 else f"exit {r.returncode}"
            return f"[{status}] {output[:200]}" if output else f"[{status}]"
        except subprocess.TimeoutExpired:
            return "Command timed out (60s)"

    def _exec_agent(self, job: ScheduledJob) -> str:
        """Invoke a Claude Code agent as a subprocess.

        target = agent name (e.g. "taskbox-listener")
        content = prompt to send to the agent
        """
        agent_dir = resolve_agent(job.target, self.octobots_dir, self.runtime_dir)
        if not agent_dir:
            return f"Agent not found: {job.target}"

        cmd = [
            "claude", "-p", job.content,
            "--add-dir", str(agent_dir),
            "--dangerously-skip-permissions",
        ]

        try:
            r = subprocess.run(
                cmd,
                capture_output=True, text=True,
                timeout=300,  # 5 min timeout for agent runs
                cwd=str(Path.cwd()),
            )
            output = r.stdout.strip() or r.stderr.strip()
            status = "ok" if r.returncode == 0 else f"exit {r.returncode}"
            return f"[{status}] {output[:300]}" if output else f"[{status}]"
        except subprocess.TimeoutExpired:
            return "Agent timed out (5m)"

    def _advance(self, job: ScheduledJob) -> ScheduledJob | None:
        """Calculate next run time. Returns None for completed one-shots."""
        now = datetime.now(timezone.utc)
        job.last_run = now.isoformat()
        job.run_count += 1

        if job.type == JobType.AT:
            return None
        elif job.type == JobType.EVERY:
            delta = parse_interval(job.spec)
            job.next_run = (now + delta).isoformat()
        elif job.type == JobType.CRON:
            job.next_run = next_cron_run(job.spec, now).isoformat()

        return job

    def create_job(
        self,
        job_type: str,
        spec: str,
        action: str,
        target: str,
        content: str = "",
    ) -> ScheduledJob:
        """Create and store a new scheduled job."""
        jtype = JobType(job_type)
        jaction = JobAction(action)
        now = datetime.now(timezone.utc)

        if jtype == JobType.AT:
            next_run = parse_at_time(spec)
        elif jtype == JobType.EVERY:
            delta = parse_interval(spec)
            next_run = now + delta
        elif jtype == JobType.CRON:
            next_run = next_cron_run(spec, now)
        else:
            raise ValueError(f"Unknown job type: {job_type}")

        job = ScheduledJob(
            id=uuid.uuid4().hex[:8],
            type=jtype,
            spec=spec,
            action=jaction,
            target=target,
            content=content,
            created_at=now.isoformat(),
            next_run=next_run.isoformat(),
        )

        self.store.add(job)
        return job
