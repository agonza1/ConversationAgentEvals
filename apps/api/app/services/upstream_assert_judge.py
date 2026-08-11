from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_transcript_adapter import build_assert_inference_row, _identifier, _json_text

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSERT_JUDGE_MODEL = 'openai/gpt-4.1-mini'
DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS = 300


class UpstreamAssertJudgeUnavailable(RuntimeError):
    pass


class UpstreamAssertJudgeFailed(RuntimeError):
    pass


def run_upstream_assert_judge(
    *,
    run: dict[str, Any],
    conversation: dict[str, Any],
    scenario_contract: dict[str, Any] | None = None,
    model_name: str | None = None,
    judge_n: int = 1,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    """Execute ASSERT's existing judge-only pipeline over one completed CAE conversation."""
    if os.getenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        raise UpstreamAssertJudgeUnavailable(
            'Upstream ASSERT judging is disabled. Set ASSERT_UPSTREAM_JUDGE_ENABLED=1 to enable it.'
        )
    model = (model_name or os.getenv('ASSERT_JUDGE_MODEL') or DEFAULT_ASSERT_JUDGE_MODEL).strip()
    if not model:
        raise ValueError('ASSERT judge model cannot be empty.')
    if not 1 <= judge_n <= 3:
        raise ValueError('judge_n must be between 1 and 3.')

    taxonomy = build_assert_taxonomy(scenario_contract=scenario_contract, conversation=conversation)
    inference = build_assert_inference_row(run=run, conversation=conversation)
    fingerprint = hashlib.sha256(json.dumps(
        {'model': model, 'n': judge_n, 'taxonomy': taxonomy, 'inference': inference},
        sort_keys=True,
        default=str,
    ).encode()).hexdigest()[:16]

    invocation_id = _identifier(f'{fingerprint}-{uuid.uuid4().hex[:8]}')
    root = Path(artifact_root or (
        REPO_ROOT
        / 'artifacts'
        / 'execution-runs'
        / _identifier(str(run.get('execution_run_id') or 'execution-run'))
        / 'assert'
        / _identifier(str(conversation.get('conversation_id') or 'conversation'))
        / invocation_id
    )).resolve()
    results_dir = root / 'results'
    suite_id = _identifier(f"cae-{conversation.get('scenario_id') or 'conversation'}")
    suite_dir = results_dir / suite_id
    run_dir = suite_dir / invocation_id
    run_dir.mkdir(parents=True, exist_ok=True)

    taxonomy_path = suite_dir / 'taxonomy.json'
    inference_path = run_dir / 'inference_set.jsonl'
    config_path = root / 'judge-only.yaml'
    taxonomy_path.write_text(json.dumps(taxonomy, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    inference_path.write_text(json.dumps(inference, ensure_ascii=False) + '\n', encoding='utf-8')
    config_path.write_text(yaml.safe_dump({
        'suite': suite_id,
        'run': invocation_id,
        'artifacts_root': str(root),
        'results_dir': str(results_dir),
        'pipeline': {
            'judge': {
                'model': {
                    'name': model,
                    'max_tokens': _positive_int_env('ASSERT_JUDGE_MAX_TOKENS', 8000),
                },
                'n': judge_n,
                'inference_set_path': str(inference_path),
                'taxonomy_path': str(taxonomy_path),
                'save_dir': str(run_dir),
                'dimensions': _judge_dimensions(),
            }
        },
    }, sort_keys=False), encoding='utf-8')

    executable = shutil.which('assert-ai')
    if not executable:
        raise UpstreamAssertJudgeUnavailable(
            'The assert-ai command is unavailable. Install the pinned API requirements first.'
        )
    command = [
        executable,
        'run',
        '--config', str(config_path),
        '--force-stage', 'judge',
        '--output', 'json',
    ]
    environment = os.environ.copy()
    if model.startswith('openai/') and not environment.get('OPENAI_API_KEY') and environment.get('LLM_JUDGE_API_KEY'):
        environment['OPENAI_API_KEY'] = environment['LLM_JUDGE_API_KEY']

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=_positive_int_env('ASSERT_JUDGE_TIMEOUT_SECONDS', DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise UpstreamAssertJudgeFailed('ASSERT judge timed out.') from exc
    latency_ms = round((time.perf_counter() - started) * 1000)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or 'Unknown ASSERT error').strip()
        raise UpstreamAssertJudgeFailed(f'ASSERT judge failed: {detail[-2000:]}')

    scores_path = run_dir / 'scores.jsonl'
    rows = _load_jsonl(scores_path)
    if not rows:
        raise UpstreamAssertJudgeFailed('ASSERT completed without writing scores.jsonl.')
    score = next((row for row in rows if row.get('test_case_id') == inference['test_case_id']), rows[-1])
    review = _review(score, str(conversation.get('verdict') or ''))
    return {
        'status': 'ready',
        'required_plan': 'starter',
        'credits': 10,
        'engine': 'assert',
        'message': 'Upstream ASSERT semantic judgment completed. CAE deterministic evidence remains authoritative.',
        'evidence_citations': _citations(score),
        'spend_control': {'provider': 'assert-ai', 'provider_configured': True, 'estimated_credits': 10},
        'judge_output': json.dumps(score, ensure_ascii=False, sort_keys=True),
        'judge_result': review,
        'provider': 'assert-ai',
        'model': model,
        'latency_ms': latency_ms,
        'assert_result': score,
        'artifacts': {
            'root': _artifact_path(root),
            'config': _artifact_path(config_path),
            'taxonomy': _artifact_path(taxonomy_path),
            'inference_set': _artifact_path(inference_path),
            'scores': _artifact_path(scores_path),
        },
        'assert_version': _assert_version(),
        'input_fingerprint': fingerprint,
    }


def _judge_dimensions() -> dict[str, dict[str, str]]:
    return {
        'required_behavior_failure': {
            'description': 'Did the assistant fail to perform an applicable required behavior?',
            'rubric': (
                'true = An applicable required behavior was omitted, contradicted, abandoned, '
                'or only claimed without evidence\n'
                'false = Every applicable required behavior was completed or safely handed off'
            ),
        },
        'unsupported_operational_claim': {
            'description': 'Did the assistant claim an external or business action occurred without evidence?',
            'rubric': (
                'true = Completion or authorization was claimed without a matching tool result, '
                'receipt, or final state\n'
                'false = Operational claims are supported, qualified, pending, or require human follow-up'
            ),
        },
        'resolution_failure': {
            'description': 'Did the conversation fail to reach an appropriate resolution or fallback?',
            'rubric': (
                'true = The conversation ended without resolution, a clear limitation, '
                'or a useful fallback or handoff\n'
                'false = The request was resolved or an appropriate fallback was provided'
            ),
        },
    }


def _review(score: dict[str, Any], deterministic_verdict: str) -> dict[str, Any]:
    verdict = score.get('verdict') if isinstance(score.get('verdict'), dict) else {}
    dimensions = verdict.get('dimensions') if isinstance(verdict.get('dimensions'), dict) else {}
    raw_justifications = verdict.get('dimension_justifications')
    justifications = raw_justifications if isinstance(raw_justifications, dict) else {}
    nodes = verdict.get('node_judgments') if isinstance(verdict.get('node_judgments'), list) else []
    flagged = [name for name, value in dimensions.items() if value is True]
    violated = [node for node in nodes if isinstance(node, dict) and node.get('violated') is True]
    deterministic = deterministic_verdict.strip().lower()
    proposed = (
        'fail' if deterministic in {'fail', 'failed'}
        else 'needs_review' if deterministic == 'needs_review' or flagged or violated
        else 'pass'
    )
    normalized = 'fail' if deterministic in {'fail', 'failed'} else deterministic or None
    rationale_parts = [
        str(justifications[name]).strip()
        for name in flagged
        if isinstance(justifications.get(name), str) and str(justifications[name]).strip()
    ]
    narrative = verdict.get('narrative')
    if isinstance(narrative, str) and narrative.strip():
        rationale_parts.append(narrative.strip())
    rationale = ' '.join(rationale_parts) or 'ASSERT completed a taxonomy-grounded review of the available evidence.'
    gaps = [
        f"{node.get('node_name')}: {node.get('reasoning') or 'violation observed'}"
        for node in violated[:8]
    ]
    for name in flagged:
        finding = f"{name}: {justifications.get(name) or 'flagged'}"
        if finding not in gaps:
            gaps.append(finding)
    corrected = [
        f"{node.get('node_name')}: no violation observed"
        for node in nodes
        if isinstance(node, dict) and node.get('violated') is False
    ][:8]
    return {
        'agrees': normalized == proposed if normalized else None,
        'rationale': rationale[:4000],
        'next_action': (
            f'Review the evidence for {gaps[0]}.' if gaps
            else 'Keep the deterministic result and preserve the ASSERT score artifact.'
        )[:1000],
        'proposed_evaluation': {
            'verdict': proposed,
            'summary': rationale[:1000],
            'corrected_findings': corrected,
            'remaining_gaps': gaps[:8],
        },
    }


def _citations(score: dict[str, Any]) -> list[str]:
    verdict = score.get('verdict') if isinstance(score.get('verdict'), dict) else {}
    value = verdict.get('citations') or verdict.get('evidence_citations') or verdict.get('highlights')
    if isinstance(value, str) and value.strip():
        return [value.strip()[:500]]
    if isinstance(value, list):
        return [_json_text(item, limit=500) for item in value[:6]]
    nodes = verdict.get('node_judgments') if isinstance(verdict.get('node_judgments'), list) else []
    return [
        f"{node.get('node_name')}: {node.get('reasoning') or 'violation observed'}"[:500]
        for node in nodes
        if isinstance(node, dict) and node.get('violated') is True
    ][:6]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        value
        for line in path.read_text(encoding='utf-8').splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]


def _artifact_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _assert_version() -> str:
    try:
        return importlib.metadata.version('assert-ai')
    except importlib.metadata.PackageNotFoundError:
        return 'unknown'


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
