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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.config import config
from app.core.claude_code_cli import ClaudeCodeCLI, normalize_severity
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
    probe_bubblewrap,
    resolve_work_dir,
)
from app.types import TaskCache

logger = logging.getLogger(__name__)

# The service's own directory (Demo/ locally, /app in the image): agents may never read it.
SERVICE_ROOT = Path(__file__).resolve().parents[2]
MAX_COMMENT_CHARS = 2000
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
    finding: Dict[str, Any]  # untrusted finding JSON shown to the agents
    claimed_severity: str


@dataclass
class TriageVerdict:
    is_valid: bool
    severity: str
    comment: str
    error: bool = False
    confidence: str = ""
    final: Optional[Dict[str, Any]] = None


@dataclass
class _Run:
    ws: TriageWorkspace
    poc_enabled: bool
    sem: asyncio.Semaphore
    context: str = ""
    cost_usd: float = 0.0
    agents: int = 0
    failures: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ClaudeCodeTriage:
    """Runs the findings-triage pipeline for one task's findings."""

    def __init__(self, cli: Optional[ClaudeCodeCLI] = None, settings=config) -> None:
        self.cli = cli or ClaudeCodeCLI()
        self.cfg = settings
        # One limit for the whole service, shared by concurrent runs (e.g. several TESTTASK
        # submissions processed in the background at once).
        self._sem: Optional[asyncio.Semaphore] = None

    # --- preflight -----------------------------------------------------------------------
    def secret_paths(self) -> List[Path]:
        """
        Regions no agent may read: the service user's HOME, the service dir, task data, and the
        service's working directory (where a .env may live). The filesystem root is never one:
        denying it would deny everything.
        """
        candidates = [Path.home(), SERVICE_ROOT, Path(self.cfg.data_dir).resolve(), Path.cwd()]
        return [p for p in candidates if p.resolve() != Path(p.anchor)]

    def preflight(self) -> bool:
        """
        Decide whether agents may run PoCs. Returns ``True`` when the command sandbox works.

        Raises :class:`SandboxUnavailable` when PoCs are required but the sandbox cannot run,
        and ``ValueError`` when the work directory is misconfigured. Call it before touching
        any finding so a broken host leaves the task unprocessed (and retryable).
        """
        resolve_work_dir(self.cfg.triage_work_dir, self.secret_paths())
        if not shutil.which(self.cli.command):
            raise SandboxUnavailable(f"Claude Code CLI '{self.cli.command}' not found on PATH")
        mode = self.cfg.triage_poc_mode
        if mode == "off":
            return False
        reason = probe_bubblewrap()
        if reason is None:
            return True
        if mode == "required":
            raise SandboxUnavailable(reason)
        logger.error("PoC sandbox unavailable (%s); triage continues with static analysis only", reason)
        return False

    # --- entry point -----------------------------------------------------------------------
    async def run_task(
        self, task_cache: TaskCache, units: Sequence[TriageUnit], *, poc_enabled: bool
    ) -> Dict[str, TriageVerdict]:
        """Triage every unit of one task; returns verdicts keyed by ``unit.key``."""
        if not units:
            return {}
        repo = Path(task_cache.repoPath or "")
        if not task_cache.repoPath or not repo.is_dir():
            raise TriageInfrastructureError(f"repository checkout unavailable ({task_cache.repoPath!r})")
        work_dir = resolve_work_dir(self.cfg.triage_work_dir, self.secret_paths())
        ws = TriageWorkspace(
            work_dir,
            secret_paths=self.secret_paths(),
            extra_read_paths=self.cfg.triage_extra_read,
            keep_sandboxes=self.cfg.triage_keep_sandboxes,
        )
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.cfg.triage_max_parallel_agents)
        run = _Run(ws=ws, poc_enabled=poc_enabled, sem=self._sem)
        logger.info("triage run %s: %d unit(s), PoCs %s", ws.root.name, len(units), "on" if poc_enabled else "off")
        try:
            await asyncio.to_thread(ws.init_base, repo)
            task = self._task_json(task_cache)
            build_report = await self._build(run, task) if poc_enabled else None
            await asyncio.to_thread(ws.seal_base)
            run.context = build_context(task, build_report, poc_enabled=poc_enabled)

            outcomes = await asyncio.gather(*(self._triage_unit(run, u) for u in units), return_exceptions=True)
            verdicts: Dict[str, TriageVerdict] = {}
            for unit, outcome in zip(units, outcomes):
                if isinstance(outcome, BaseException):
                    logger.error("triage of %s crashed: %r", unit.key, outcome)
                    outcome = self._error_verdict(unit, f"internal error: {type(outcome).__name__}")
                verdicts[unit.key] = outcome
        finally:
            await asyncio.to_thread(ws.cleanup)
        logger.info(
            "triage run %s finished: %d agents (%d failed), total cost $%.2f; evidence in %s",
            ws.root.name, run.agents, run.failures, run.cost_usd, ws.evidence_dir,
        )
        if all(v.error for v in verdicts.values()):
            raise TriageInfrastructureError("every finding failed to evaluate; see the claude CLI errors above")
        return verdicts

    # --- build agent -----------------------------------------------------------------------
    async def _build(self, run: _Run, task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sb = run.ws.base
        async with run.sem:
            res = await self.cli.run_agent(
                builder_prompt(task),
                cwd=sb.repo, home=sb.home, tmpdir=sb.tmp,
                tools=["Read", "Grep", "Glob", "Edit", "Write", "Bash"],
                settings=run.ws.settings_for(sb, allow_bash=True, allowed_domains=self.cfg.triage_build_domains),
                json_schema=BUILD_SCHEMA,
                timeout_s=self.cfg.triage_build_timeout_seconds,
                max_budget_usd=self.cfg.triage_agent_max_budget_usd or None,
                label=f"{run.ws.root.name}/build",
            )
        await self._account(run, res)
        if not res.ok:
            logger.error("build agent failed (%s); agents will work without a prebuilt project", res.error)
            return {"build_succeeded": False, "projects": [], "toolchain": "", "repo_map": "",
                    "problems": "the preparation agent failed; no build report"}
        report = res.structured or {}
        logger.info("build agent: succeeded=%s projects=%s", report.get("build_succeeded"),
                    [p.get("path") for p in report.get("projects", [])])
        return report

    # --- per-unit pipeline -----------------------------------------------------------------
    async def _triage_unit(self, run: _Run, unit: TriageUnit) -> TriageVerdict:
        ctx, finding = run.context, unit.finding

        analysis = await self._role(run, unit, "analyst", lambda note: analyst_prompt(ctx, finding),
                                    ANALYSIS_SCHEMA, staged=[], retries=1)
        if analysis is None:
            return self._error_verdict(unit, "the analyst agent failed")
        logger.info("[%s] analyst: %s / %s (PoC %s)", unit.key, analysis.get("verdict"),
                    analysis.get("recommended_severity"), (analysis.get("poc") or {}).get("result"))

        verifiers = list(await asyncio.gather(
            self._role(run, unit, "refute", lambda note: refute_prompt(ctx, finding, analysis, note),
                       VERIFY_SCHEMA, staged=["analyst"], retries=0),
            self._role(run, unit, "severity", lambda note: severity_prompt(ctx, finding, analysis, note),
                       VERIFY_SCHEMA, staged=["analyst"], retries=0),
        ))
        unanimous = all(v and v.get("agrees_with_verdict") and v.get("agrees_with_severity") for v in verifiers)

        final = await self._role(run, unit, "judge",
                                 lambda note: judge_prompt(ctx, finding, analysis, verifiers, note),
                                 FINAL_SCHEMA, staged=["analyst", "refute", "severity"], retries=1)
        if final is None:
            # No verdict without the independent judge: falling back to the analyst would let a
            # run that hijacked the analyst and made the judge fail decide the outcome.
            return self._error_verdict(unit, "the judge agent failed")
        logger.info("[%s] FINAL: %s / %s (%s)", unit.key, final.get("final_verdict"), final.get("final_severity"),
                    "unanimous verifiers" if unanimous else "contested")
        return self._to_verdict(unit, final)

    async def _role(
        self,
        run: _Run,
        unit: TriageUnit,
        role: str,
        make_prompt: Callable[[str], str],
        schema: Dict[str, Any],
        *,
        staged: Sequence[str],
        retries: int,
    ) -> Optional[Dict[str, Any]]:
        """Run one role in a fresh sandbox (with retries); collect the PoCs it created."""
        ws = run.ws
        bash = run.poc_enabled
        tools = ["Read", "Grep", "Glob"] + (["Edit", "Write", "Bash"] if bash else [])
        for attempt in range(retries + 1):
            label = f"{unit.key}-{role}" + (f"-retry{attempt}" if attempt else "")
            # The semaphore covers sandbox creation too, so disk use is bounded by it.
            async with run.sem:
                sb = await asyncio.to_thread(ws.make_sandbox, label)
                try:
                    note = await asyncio.to_thread(ws.stage_into, sb, unit.key, staged)
                    res = await self.cli.run_agent(
                        make_prompt(note),
                        cwd=sb.repo, home=sb.home, tmpdir=sb.tmp,
                        tools=tools,
                        settings=ws.settings_for(sb, allow_bash=bash, allowed_domains=[]),
                        json_schema=schema,
                        timeout_s=self.cfg.triage_agent_timeout_seconds,
                        max_budget_usd=self.cfg.triage_agent_max_budget_usd or None,
                        label=f"{ws.root.name}/{label}",
                    )
                    await asyncio.to_thread(ws.finish, sb)
                    if bash:
                        changes = await asyncio.to_thread(ws.changes, sb)
                        await asyncio.to_thread(ws.collect_evidence, sb, unit.key, role, changes)
                finally:
                    await asyncio.to_thread(ws.discard, sb)
            await self._account(run, res)
            if res.ok and res.structured:
                return res.structured
            logger.warning("[%s] %s attempt %d failed: %s", unit.key, role, attempt + 1, res.error)
        return None

    async def _account(self, run: _Run, res) -> None:
        async with run.lock:
            run.agents += 1
            run.cost_usd += res.cost_usd
            if not res.ok:
                run.failures += 1

    # --- mapping ---------------------------------------------------------------------------
    def _to_verdict(self, unit: TriageUnit, final: Dict[str, Any]) -> TriageVerdict:
        """
        Map the judge's ruling onto the database's valid/disputed + High/Medium/Low/Info.

        VALID / PARTIALLY_VALID count at the rated severity (Critical is stored as High);
        INVALID is disputed; INFORMATIONAL follows ``triage_informational_policy``.
        """
        verdict = str(final.get("final_verdict", "")).upper()
        if verdict in ("VALID", "PARTIALLY_VALID"):
            is_valid = True
            severity = normalize_severity(str(final.get("final_severity", "")), "Info")
        elif verdict == "INFORMATIONAL":
            policy = self.cfg.triage_informational_policy
            is_valid = policy == "valid" or (policy == "actionable" and final.get("actionable") is True)
            severity = "Info"
        else:  # INVALID (or anything unexpected): not counted
            is_valid = False
            severity = "Info"
        confidence = str(final.get("confidence", ""))
        poc = _POC_LABEL.get(str(final.get("poc_status", "")), "no working PoC")
        prefix = {"PARTIALLY_VALID": "Partially valid: ", "INFORMATIONAL": "Informational: "}.get(verdict, "")
        comment = f"{prefix}{final.get('one_line', '')} {final.get('summary', '')}".strip()
        suffix = f" [{poc}; confidence: {confidence or 'n/a'}]"
        if len(comment) + len(suffix) > MAX_COMMENT_CHARS:
            comment = comment[: MAX_COMMENT_CHARS - len(suffix) - 3].rstrip() + "..."
        return TriageVerdict(is_valid=is_valid, severity=severity, comment=comment + suffix,
                             confidence=confidence, final=final)

    @staticmethod
    def _error_verdict(unit: TriageUnit, reason: str) -> TriageVerdict:
        # Fail closed (the finding is not approved), but say plainly that no judgment on the
        # merits was made so operators can find and re-run these.
        return TriageVerdict(
            is_valid=False,
            severity=unit.claimed_severity,
            comment=f"EVALUATION_ERROR: {reason}; no judgment on the merits was made (abstained).",
            error=True,
        )

    @staticmethod
    def _task_json(task_cache: TaskCache) -> Dict[str, Any]:
        return {
            "title": task_cache.title or "(no title)",
            "description": task_cache.description or "(no description)",
            "in_scope_files": list(task_cache.selectedFiles or []) or "WHOLE REPOSITORY CODE",
            "in_scope_docs": list(task_cache.selectedDocs or []) or "WHOLE REPOSITORY DOCS",
            "qa_responses": [{"question": qa.question, "answer": qa.answer} for qa in (task_cache.qaResponses or [])],
            "additional_docs": task_cache.additionalDocs or "",
            "additional_links": list(task_cache.additionalLinks or []),
        }
