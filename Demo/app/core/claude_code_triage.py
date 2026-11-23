"""
Findings-triage evaluation: the arbiter's port of the ``findings-triage`` Claude Code skill.

Per task (one :meth:`ClaudeCodeTriage.run_task` call):

    build agent ─ installs deps + prebuilds a private copy of the checkout (registry network only)
    per finding unit, pipelined, all units concurrently (bounded by the agent semaphore):
        Analyst ─ code trace + PoC in its own sandbox
          ├─► Refute verifier   ─ tries to overturn it; re-runs and audits the PoC for rigging
          └─► Severity verifier ─ hunts escalations and mitigations; proves changes with a PoC
        Judge ─ weighs evidence, not votes; re-runs PoCs to settle factual disputes

A "unit" is one finding, or one merged digest of a duplicate group (merged upstream by
:class:`app.core.claude_code_aggregator.ClaudeCodeAggregator`); its verdict is applied to every
member. Every agent is a separate hardened ``claude -p`` run (see claude_code_cli.py) in its own
sandbox (see triage_sandbox.py), and PoCs move between agents only through the system.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.config import config
from app.core.claude_code_cli import AgentResult, ClaudeCodeCLI, normalize_severity
from app.core.triage_prompts import (
    ANALYSIS_SCHEMA,
    BUILD_SCHEMA,
    FINAL_SCHEMA,
    VERIFY_SCHEMA,
    analyst_prompt,
    build_context,
    builder_prompt,
    judge_prompt,
    refute_prompt,
    severity_prompt,
)
from app.core.triage_sandbox import (
    SandboxUnavailable,
    TriageWorkspace,
    check_socket_paths,
    probe_bubblewrap,
    prune_old_runs,
    resolve_work_dir,
)
from app.types import TaskCache

logger = logging.getLogger(__name__)

# The service's own directory (Demo/ locally, /app in the image): agents may never read it.
SERVICE_ROOT = Path(__file__).resolve().parents[2]
# Container credential mounts (the Kubernetes service-account token, Docker secrets). The judge's
# summary is posted as the evaluation comment, so anything an agent can read could reach it.
SYSTEM_SECRET_PATHS = (Path("/var/run/secrets"), Path("/run/secrets"))
MAX_COMMENT_CHARS = 2000
# Wall-clock limits for one finding agent and for the build agent.
AGENT_TIMEOUT_SECONDS = 1800
BUILD_TIMEOUT_SECONDS = 2700
# The only hosts the build agent's sandbox may reach; finding agents get no network. This is a
# sandbox boundary, so it changes through code review, not configuration.
BUILD_ALLOWED_DOMAINS = (
    "github.com",
    "codeload.github.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "binaries.soliditylang.org",
    "crates.io",
    "index.crates.io",
    "static.crates.io",
    "static.rust-lang.org",
)
# Days a run's directory (its PoC evidence) is kept; older ones are deleted when a run starts.
EVIDENCE_RETENTION_DAYS = 30
# Claimed severities that get PoCs under TRIAGE_POC_SCOPE=high_medium.
POC_SEVERITIES = ("High", "Medium")
_POC_LABEL = {
    "CONFIRMED_BY_POC": "confirmed by PoC",
    "REFUTED_BY_POC": "refuted by PoC",
    "PARTIAL": "partially demonstrated by PoC",
    "NO_WORKING_POC": "no working PoC",
}


class TriageInfrastructureError(RuntimeError):
    """The run produced no usable verdict at all (auth/outage/sandbox): do not apply results."""


@dataclass
class TriageUnit:
    """One thing to judge: a finding, or the merged digest of a duplicate group."""

    key: str  # safe identifier, e.g. "u003"
    finding: dict[str, Any]  # untrusted finding JSON shown to the agents
    claimed_severity: str  # for a duplicate group, the highest severity any member claims


@dataclass
class TriageVerdict:
    """The judge's ruling for one unit, mapped onto valid/disputed and the severity scale."""

    is_valid: bool
    severity: str
    comment: str
    error: bool = False
    confidence: str = ""
    final: dict[str, Any] | None = None


@dataclass
class _Run:
    ws: TriageWorkspace
    sem: asyncio.Semaphore
    poc_units: frozenset[str] = frozenset()  # keys of the units that get PoCs (and Bash)
    contexts: dict[bool, str] = field(default_factory=dict)  # by "gets PoCs"
    cost_usd: float = 0.0
    unknown_cost: int = 0  # runs that reported no cost (killed at the timeout, or no result)
    agents: int = 0
    failures: int = 0


class ClaudeCodeTriage:
    """Runs the findings-triage pipeline for one task's findings."""

    def __init__(self, cli: ClaudeCodeCLI | None = None, settings=config) -> None:
        self.cli = cli or ClaudeCodeCLI()
        self.cfg = settings
        # One limit for the whole service, shared by concurrent runs (e.g. a manual trigger
        # while a scheduled run is in progress).
        self._sem: asyncio.Semaphore | None = None
        # Run directories in use by this process, which evidence pruning must not touch.
        self._active_runs: set[Path] = set()

    @property
    def semaphore(self) -> asyncio.Semaphore:
        """
        The service-wide limit on concurrent Claude Code runs, created on first use (inside the
        event loop). The aggregator's merges take it too, so they count toward the same limit.
        """
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.cfg.triage_max_parallel_agents)
        return self._sem

    # --- preflight -----------------------------------------------------------------------
    def secret_paths(self) -> list[Path]:
        """
        Regions no agent may read: the service user's HOME, the service dir, task data, the
        service's working directory (where a .env may live) and the container credential mounts.
        The filesystem root is never one: denying it would deny everything.
        """
        candidates = [
            Path.home(),
            SERVICE_ROOT,
            Path(self.cfg.data_dir).resolve(),
            Path.cwd(),
            *SYSTEM_SECRET_PATHS,
        ]
        return [p for p in candidates if p.resolve() != Path(p.anchor)]

    def preflight(self) -> None:
        """
        Check that this host can run the triage, PoC sandbox included.

        Raises :class:`SandboxUnavailable` when the CLI or the command sandbox cannot run, and
        ``ValueError`` when the API key or the work directory is misconfigured (including a work
        directory too long for the sandbox's sockets). Call it before touching any finding so a
        broken host leaves the task unprocessed (and retryable). The sandbox is required even if
        no unit of a task gets PoCs, so a host's fitness does not depend on the findings.
        """
        if not (self.cli.api_key or "").strip():
            # The settings reject an empty CLAUDE_API_KEY; a blank one would only fail in every
            # agent, after the repository download and the Gemini call.
            raise ValueError("CLAUDE_API_KEY is blank")
        work_dir = resolve_work_dir(self.cfg.triage_work_dir, self.secret_paths())
        if not shutil.which(self.cli.command):
            raise SandboxUnavailable(f"Claude Code CLI '{self.cli.command}' not found on PATH")
        reason = probe_bubblewrap()
        if reason is not None:
            raise SandboxUnavailable(reason)
        check_socket_paths(work_dir)

    def gets_poc(self, unit: TriageUnit) -> bool:
        """Whether a unit's agents write and run PoCs, per TRIAGE_POC_SCOPE."""
        if self.cfg.triage_poc_scope == "all":
            return True
        # An unrecognised claim gets the full investigation rather than less.
        return normalize_severity(unit.claimed_severity, "High") in POC_SEVERITIES

    # --- entry point -----------------------------------------------------------------------
    async def run_task(
        self, task_cache: TaskCache, units: Sequence[TriageUnit]
    ) -> dict[str, TriageVerdict]:
        """
        Triage every unit of one task; returns verdicts keyed by ``unit.key``.

        Units that get PoCs (see :meth:`gets_poc`) have Bash in the sandbox; the build agent
        prepares the checkout only when at least one does.
        """
        if not units:
            return {}
        repo = Path(task_cache.repoPath or "")
        if not task_cache.repoPath or not await asyncio.to_thread(repo.is_dir):
            raise TriageInfrastructureError(
                f"repository checkout unavailable ({task_cache.repoPath!r})"
            )
        work_dir = resolve_work_dir(self.cfg.triage_work_dir, self.secret_paths())
        try:
            await asyncio.to_thread(
                prune_old_runs,
                work_dir,
                EVIDENCE_RETENTION_DAYS,
                set(self._active_runs),
            )
        except OSError as exc:  # housekeeping only: never fail a run over it
            logger.warning("could not prune old triage runs in %s: %s", work_dir, exc)
        ws = TriageWorkspace(
            work_dir,
            secret_paths=self.secret_paths(),
            keep_sandboxes=self.cfg.debug,
        )
        self._active_runs.add(ws.root)
        run = _Run(
            ws=ws,
            sem=self.semaphore,
            poc_units=frozenset(u.key for u in units if self.gets_poc(u)),
        )
        logger.info(
            "triage run %s: %d unit(s), %d with PoCs",
            ws.root.name,
            len(units),
            len(run.poc_units),
        )
        try:
            await asyncio.to_thread(ws.init_base, repo)
            task = self._task_json(task_cache)
            build_report = await self._build(run, task) if run.poc_units else None
            restored = await asyncio.to_thread(ws.seal_base)
            run.contexts = {
                poc: build_context(task, build_report, poc_enabled=poc, restored=restored)
                for poc in (True, False)
            }

            outcomes = await asyncio.gather(
                *(self._triage_unit(run, u) for u in units), return_exceptions=True
            )
            verdicts: dict[str, TriageVerdict] = {}
            for unit, outcome in zip(units, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    logger.error("triage of %s crashed: %r", unit.key, outcome)
                    outcome = self._error_verdict(unit, f"internal error: {type(outcome).__name__}")
                verdicts[unit.key] = outcome
        finally:
            try:
                await asyncio.to_thread(ws.cleanup)
            finally:
                self._active_runs.discard(ws.root)
        cap = self.cfg.triage_agent_max_budget_usd
        unknown = (
            f" plus {run.unknown_cost} run(s) of unknown cost (killed or without a result"
            + (f", at most ${cap:g} each)" if cap else ")")
            if run.unknown_cost
            else ""
        )
        logger.info(
            "triage run %s finished: %d agents (%d failed), total cost $%.2f%s; evidence in %s",
            ws.root.name,
            run.agents,
            run.failures,
            run.cost_usd,
            unknown,
            ws.evidence_dir,
        )
        if all(v.error for v in verdicts.values()):
            raise TriageInfrastructureError(
                "every finding failed to evaluate; see the claude CLI errors above"
            )
        return verdicts

    # --- build agent -----------------------------------------------------------------------
    async def _build(self, run: _Run, task: dict[str, Any]) -> dict[str, Any] | None:
        sb = run.ws.base
        async with run.sem:
            res = await self.cli.run_agent(
                builder_prompt(task),
                cwd=sb.repo,
                home=sb.home,
                tmpdir=sb.tmp,
                tools=["Read", "Grep", "Glob", "Edit", "Write", "Bash"],
                settings=run.ws.settings_for(
                    sb, allow_bash=True, allowed_domains=BUILD_ALLOWED_DOMAINS
                ),
                json_schema=BUILD_SCHEMA,
                timeout_s=BUILD_TIMEOUT_SECONDS,
                max_budget_usd=self.cfg.triage_agent_max_budget_usd or None,
                label=f"{run.ws.root.name}/build",
            )
        self._account(run, res)
        if not res.ok:
            logger.error(
                "build agent failed (%s); agents will work without a prebuilt project", res.error
            )
            return {
                "build_succeeded": False,
                "projects": [],
                "toolchain": "",
                "repo_map": "",
                "problems": "the preparation agent failed; no build report",
            }
        report = res.structured or {}
        logger.info(
            "build agent: succeeded=%s projects=%s",
            report.get("build_succeeded"),
            [p.get("path") for p in report.get("projects", [])],
        )
        return report

    # --- per-unit pipeline -----------------------------------------------------------------
    async def _triage_unit(self, run: _Run, unit: TriageUnit) -> TriageVerdict:
        ctx, finding = run.contexts[unit.key in run.poc_units], unit.finding

        analysis = await self._role(
            run,
            unit,
            "analyst",
            lambda _note: analyst_prompt(ctx, finding),  # the analyst gets no earlier evidence
            ANALYSIS_SCHEMA,
            staged=[],
            retries=1,
        )
        if analysis is None:
            return self._error_verdict(unit, "the analyst agent failed")
        logger.info(
            "[%s] analyst: %s / %s (PoC %s)",
            unit.key,
            analysis.get("verdict"),
            analysis.get("recommended_severity"),
            (analysis.get("poc") or {}).get("result"),
        )

        # return_exceptions: a crashed verifier must neither leave the other one running
        # unawaited nor deprive the unit of its judge, who checks a missing angle itself.
        outcomes = await asyncio.gather(
            self._role(
                run,
                unit,
                "refute",
                lambda note: refute_prompt(ctx, finding, analysis, note),
                VERIFY_SCHEMA,
                staged=["analyst"],
                retries=0,
            ),
            self._role(
                run,
                unit,
                "severity",
                lambda note: severity_prompt(ctx, finding, analysis, note),
                VERIFY_SCHEMA,
                staged=["analyst"],
                retries=0,
            ),
            return_exceptions=True,
        )
        verifiers: list[dict[str, Any] | None] = []
        for name, outcome in zip(("refute", "severity"), outcomes, strict=True):
            if isinstance(outcome, Exception):
                logger.error("[%s] %s verifier crashed: %r", unit.key, name, outcome)
                outcome = None
            elif isinstance(outcome, BaseException):
                raise outcome  # cancellation: stop the unit
            verifiers.append(outcome)
        unanimous = all(
            v and v.get("agrees_with_verdict") and v.get("agrees_with_severity") for v in verifiers
        )

        final = await self._role(
            run,
            unit,
            "judge",
            lambda note: judge_prompt(ctx, finding, analysis, verifiers, note),
            FINAL_SCHEMA,
            staged=["analyst", "refute", "severity"],
            retries=1,
        )
        if final is None:
            # No verdict without the independent judge: falling back to the analyst would let a
            # run that hijacked the analyst and made the judge fail decide the outcome.
            return self._error_verdict(unit, "the judge agent failed")
        logger.info(
            "[%s] FINAL: %s / %s (%s)",
            unit.key,
            final.get("final_verdict"),
            final.get("final_severity"),
            "unanimous verifiers" if unanimous else "contested",
        )
        return self._to_verdict(final)

    async def _role(
        self,
        run: _Run,
        unit: TriageUnit,
        role: str,
        make_prompt: Callable[[str], str],
        schema: dict[str, Any],
        *,
        staged: Sequence[str],
        retries: int,
    ) -> dict[str, Any] | None:
        """Run one role in a fresh sandbox (with retries); collect the PoCs it created."""
        bash = unit.key in run.poc_units
        tools = ["Read", "Grep", "Glob"] + (["Edit", "Write", "Bash"] if bash else [])
        for attempt in range(retries + 1):
            label = f"{unit.key}-{role}" + (f"-retry{attempt}" if attempt else "")
            res = await self._attempt(
                run, unit, role, label, make_prompt, schema, staged=staged, tools=tools, bash=bash
            )
            if res is None:
                error = "its sandbox could not be prepared"
            else:
                self._account(run, res)
                if res.ok and res.structured:
                    return res.structured
                error = res.error
            logger.warning("[%s] %s attempt %d failed: %s", unit.key, role, attempt + 1, error)
        return None

    async def _attempt(
        self,
        run: _Run,
        unit: TriageUnit,
        role: str,
        label: str,
        make_prompt: Callable[[str], str],
        schema: dict[str, Any],
        *,
        staged: Sequence[str],
        tools: list[str],
        bash: bool,
    ) -> AgentResult | None:
        """
        One attempt of a role in a fresh sandbox; None when no agent ran.

        A disk error preparing the sandbox or saving the evidence fails this attempt only, so
        the role's retry still runs. An agent that already ran was paid for, so its result is
        returned, as failed: the PoCs it cites did not reach the evidence, and later agents
        could not re-run them.
        """
        ws = run.ws
        res: AgentResult | None = None
        try:
            # The semaphore covers sandbox creation too, so disk use is bounded by it.
            async with run.sem:
                sb = await asyncio.to_thread(ws.make_sandbox, label)
                try:
                    note = await asyncio.to_thread(ws.stage_into, sb, unit.key, staged)
                    res = await self.cli.run_agent(
                        make_prompt(note),
                        cwd=sb.repo,
                        home=sb.home,
                        tmpdir=sb.tmp,
                        tools=tools,
                        settings=ws.settings_for(sb, allow_bash=bash, allowed_domains=[]),
                        json_schema=schema,
                        timeout_s=AGENT_TIMEOUT_SECONDS,
                        max_budget_usd=self.cfg.triage_agent_max_budget_usd or None,
                        label=f"{ws.root.name}/{label}",
                    )
                    await asyncio.to_thread(ws.finish, sb)
                    if bash:
                        changes = await asyncio.to_thread(ws.changes, sb)
                        await asyncio.to_thread(ws.collect_evidence, sb, unit.key, role, changes)
                finally:
                    await asyncio.to_thread(ws.discard, sb)
        except (OSError, subprocess.CalledProcessError) as exc:
            logger.warning("[%s] sandbox error: %r", label, exc)
            return None if res is None else replace(res, ok=False, error=f"sandbox error: {exc!r}")
        return res

    @staticmethod
    def _account(run: _Run, res: AgentResult) -> None:
        # Synchronous: the counters only change on the event loop, never across an await.
        run.agents += 1
        if res.cost_usd is None:
            run.unknown_cost += 1
        else:
            run.cost_usd += res.cost_usd
        if not res.ok:
            run.failures += 1

    # --- mapping ---------------------------------------------------------------------------
    def _to_verdict(self, final: dict[str, Any]) -> TriageVerdict:
        """
        Map the judge's ruling onto the database's valid/disputed + High/Medium/Low/Info.

        VALID / PARTIALLY_VALID count at the rated severity (Critical is stored as High);
        INVALID is disputed; INFORMATIONAL (true, but no security impact) counts as Info only
        when the judge says a competent developer would change code because of it. The schema
        lets the verdict and the severity disagree, so a ruling rated Invalid is disputed
        whatever its verdict, and a VALID / PARTIALLY_VALID one rated Informational follows the
        INFORMATIONAL rule: the actionable check cannot be bypassed.
        """
        verdict = str(final.get("final_verdict", "")).upper()
        rated = str(final.get("final_severity", "")).upper()
        if rated == "INVALID":
            verdict = "INVALID"
        elif rated == "INFORMATIONAL" and verdict in ("VALID", "PARTIALLY_VALID"):
            verdict = "INFORMATIONAL"
        if verdict in ("VALID", "PARTIALLY_VALID"):
            is_valid = True
            severity = normalize_severity(str(final.get("final_severity", "")), "Info")
        elif verdict == "INFORMATIONAL":
            is_valid = final.get("actionable") is True
            severity = "Info"
        else:  # INVALID (or anything unexpected): not counted
            is_valid = False
            severity = "Info"
        confidence = str(final.get("confidence", ""))
        poc = _POC_LABEL.get(str(final.get("poc_status", "")), "no working PoC")
        prefix = {"PARTIALLY_VALID": "Partially valid: ", "INFORMATIONAL": "Informational: "}.get(
            verdict, ""
        )
        comment = f"{prefix}{final.get('one_line', '')} {final.get('summary', '')}".strip()
        suffix = f" [{poc}; confidence: {confidence or 'n/a'}]"
        if len(comment) + len(suffix) > MAX_COMMENT_CHARS:
            comment = comment[: MAX_COMMENT_CHARS - len(suffix) - 3].rstrip() + "..."
        return TriageVerdict(
            is_valid=is_valid,
            severity=severity,
            comment=comment + suffix,
            confidence=confidence,
            final=final,
        )

    @staticmethod
    def _error_verdict(unit: TriageUnit, reason: str) -> TriageVerdict:
        # A unit with this verdict gets no verdict at all: evaluate_groups returns its findings
        # as failed, and the run then posts nothing. is_valid=False and the comment only keep the
        # value safe and explicit should it ever be applied.
        return TriageVerdict(
            is_valid=False,
            severity=unit.claimed_severity,
            comment=f"EVALUATION_ERROR: {reason}; no judgment on the merits was made (abstained).",
            error=True,
        )

    @staticmethod
    def _task_json(task_cache: TaskCache) -> dict[str, Any]:
        return {
            "title": task_cache.title or "(no title)",
            "description": task_cache.description or "(no description)",
            "in_scope_files": list(task_cache.selectedFiles or []) or "WHOLE REPOSITORY CODE",
            "in_scope_docs": list(task_cache.selectedDocs or []) or "WHOLE REPOSITORY DOCS",
            "qa_responses": [
                {"question": qa.question, "answer": qa.answer}
                for qa in (task_cache.qaResponses or [])
            ],
            "additional_docs": task_cache.additionalDocs or "",
            "additional_links": list(task_cache.additionalLinks or []),
        }
