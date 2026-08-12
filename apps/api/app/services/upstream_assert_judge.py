from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

import yaml
from assert_ai.core.judge import (
    BUILT_IN_DIMENSIONS,
    infer_judge_status,
    is_valid_confidence_label,
    is_valid_event_flag,
)

from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_transcript_adapter import build_assert_inference_row, _identifier, _json_text
from app.services.product_service import (
    _judge_spend_control,
    _refund_judge_credits,
    _reserve_judge_credits,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ASSERT_JUDGE_MODEL = 'openai/gpt-4.1-mini'
DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS = 300
DEFAULT_ASSERT_JUDGE_CREDITS = 10
DEFAULT_ASSERT_JUDGE_MAX_CONCURRENT = 2
DEFAULT_ASSERT_JUDGE_MAX_N = 1

_ASSERT_JUDGE_SLOT_LOCK = Lock()
_ASSERT_JUDGE_ACTIVE = 0


class UpstreamAssertJudgeUnavailable(RuntimeError):
    pass


class UpstreamAssertJudgeFailed(RuntimeError):
    pass


class UpstreamAssertJudgeBusy(RuntimeError):
    pass


class UpstreamAssertJudgeBudgetExceeded(RuntimeError):
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
    model = _resolve_model(model_name)
    max_judge_n = min(3, _positive_int_env('ASSERT_JUDGE_MAX_N', DEFAULT_ASSERT_JUDGE_MAX_N))
    if not 1 <= judge_n <= max_judge_n:
        raise ValueError(f'judge_n must be between 1 and {max_judge_n}.')

    taxonomy = build_assert_taxonomy(scenario_contract=scenario_contract, conversation=conversation)
    inference = build_assert_inference_row(run=run, conversation=conversation)
    fingerprint = hashlib.sha256(json.dumps(
        {'model': model, 'n': judge_n, 'taxonomy': taxonomy, 'inference': inference},
        sort_keys=True,
        default=str,
    ).encode()).hexdigest()[:16]

    executable = shutil.which('assert-ai')
    if not executable:
        raise UpstreamAssertJudgeUnavailable(
            'The assert-ai command is unavailable. Install the pinned API requirements first.'
        )
    environment = os.environ.copy()
    if model.startswith('openai/') and not environment.get('OPENAI_API_KEY') and environment.get('LLM_JUDGE_API_KEY'):
        environment['OPENAI_API_KEY'] = environment['LLM_JUDGE_API_KEY']
    _require_provider_credentials(model, environment)

    credits = DEFAULT_ASSERT_JUDGE_CREDITS * judge_n
    with _assert_judge_slot():
        spend_control = _reserve_assert_credits(credits=credits, model=model)
        try:
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
            taxonomy_path.write_text(
                json.dumps(taxonomy, indent=2, sort_keys=True) + '\n',
                encoding='utf-8',
            )
            inference_path.write_text(
                json.dumps(inference, ensure_ascii=False) + '\n',
                encoding='utf-8',
            )
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

            command = [
                executable,
                'run',
                '--config', str(config_path),
                '--force-stage', 'judge',
                '--output', 'json',
            ]
            started = time.perf_counter()
            try:
                completed = subprocess.run(
                    command,
                    cwd=REPO_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=_positive_int_env(
                        'ASSERT_JUDGE_TIMEOUT_SECONDS',
                        DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS,
                    ),
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
            score = _select_valid_score(
                rows,
                test_case_id=str(inference['test_case_id']),
                taxonomy=taxonomy,
            )
            assert_version = _assert_version()
            artifacts = {
                'root': _artifact_path(root),
                'config': _artifact_path(config_path),
                'taxonomy': _artifact_path(taxonomy_path),
                'inference_set': _artifact_path(inference_path),
                'scores': _artifact_path(scores_path),
            }
            score_sha256 = hashlib.sha256(json.dumps(
                score,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()).hexdigest()
            review = _review(score, str(conversation.get('verdict') or ''))
            review['provenance'] = {
                'engine': 'assert',
                'assert_version': assert_version,
                'judge_status': 'ok',
                'input_fingerprint': fingerprint,
                'score_sha256': score_sha256,
                'artifacts': deepcopy(artifacts),
                'dimensions': deepcopy(score['verdict']['dimensions']),
                'node_judgments': deepcopy(score['verdict']['node_judgments']),
            }
            response = {
                'status': 'ready',
                'required_plan': 'starter',
                'credits': credits,
                'engine': 'assert',
                'message': (
                    'Upstream ASSERT semantic judgment completed. '
                    'CAE deterministic evidence remains authoritative.'
                ),
                'evidence_citations': _citations(score),
                'spend_control': spend_control,
                'judge_output': json.dumps(score, ensure_ascii=False, sort_keys=True),
                'judge_result': review,
                'provider': 'assert-ai',
                'model': model,
                'latency_ms': latency_ms,
                'assert_result': score,
                'artifacts': artifacts,
                'assert_version': assert_version,
                'input_fingerprint': fingerprint,
            }
        except Exception:
            try:
                _refund_assert_credits(spend_control, credits=credits)
            except Exception:
                # Preserve the judging failure; spend-ledger repair can be handled
                # separately without misreporting the ASSERT result as successful.
                pass
            raise
    return response


def _select_valid_score(
    rows: list[dict[str, Any]],
    *,
    test_case_id: str,
    taxonomy: dict[str, Any],
) -> dict[str, Any]:
    matches = [row for row in rows if str(row.get('test_case_id') or '') == test_case_id]
    if not matches:
        raise UpstreamAssertJudgeFailed(
            f'ASSERT did not produce a score for requested conversation {test_case_id!r}.'
        )
    if len(matches) != 1:
        raise UpstreamAssertJudgeFailed(
            f'ASSERT produced {len(matches)} scores for requested conversation {test_case_id!r}.'
        )

    score = matches[0]
    raw_status = score.get('judge_status')
    inferred_status = infer_judge_status(score)
    if raw_status != 'ok' or inferred_status != 'ok':
        detail = str(score.get('judge_error') or raw_status or inferred_status)
        raise UpstreamAssertJudgeFailed(
            f'ASSERT did not produce a valid judgment for {test_case_id!r}: {detail[:1000]}'
        )

    verdict = score.get('verdict')
    if not isinstance(verdict, dict):
        raise UpstreamAssertJudgeFailed('ASSERT returned a malformed verdict object.')
    dimensions = verdict.get('dimensions')
    if not isinstance(dimensions, dict):
        raise UpstreamAssertJudgeFailed('ASSERT verdict is missing its dimensions object.')

    expected_dimensions = [
        *(str(item['name']) for item in BUILT_IN_DIMENSIONS),
        *_judge_dimensions().keys(),
    ]
    invalid_dimensions = [
        name for name in expected_dimensions
        if not is_valid_event_flag(dimensions.get(name))
    ]
    if invalid_dimensions:
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict has missing or non-boolean dimensions: '
            + ', '.join(invalid_dimensions)
        )

    justifications = verdict.get('dimension_justifications')
    if not isinstance(justifications, dict):
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict is missing its dimension_justifications object.'
        )
    invalid_justifications = [
        name for name in expected_dimensions
        if not isinstance(justifications.get(name), str)
    ]
    if invalid_justifications:
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict has missing or malformed dimension justifications: '
            + ', '.join(invalid_justifications)
        )

    nodes = verdict.get('node_judgments')
    if not isinstance(nodes, list):
        raise UpstreamAssertJudgeFailed('ASSERT verdict is missing its node_judgments list.')
    expected_nodes = {
        str(category.get('name'))
        for category in taxonomy.get('behavior_categories') or []
        if isinstance(category, dict) and category.get('name')
    }
    observed_nodes: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise UpstreamAssertJudgeFailed(
                f'ASSERT node judgment {index} is not an object.'
            )
        node_name = node.get('node_name')
        if not isinstance(node_name, str) or not node_name:
            raise UpstreamAssertJudgeFailed(
                f'ASSERT node judgment {index} has no valid node_name.'
            )
        if node_name in observed_nodes:
            raise UpstreamAssertJudgeFailed(
                f'ASSERT returned duplicate node judgment {node_name!r}.'
            )
        if expected_nodes and node_name not in expected_nodes:
            raise UpstreamAssertJudgeFailed(
                f'ASSERT returned unexpected node judgment {node_name!r}.'
            )
        if not is_valid_event_flag(node.get('violated')):
            raise UpstreamAssertJudgeFailed(
                f'ASSERT node judgment {node_name!r} has a non-boolean violated flag.'
            )
        if not is_valid_confidence_label(node.get('confidence')):
            raise UpstreamAssertJudgeFailed(
                f'ASSERT node judgment {node_name!r} has an invalid confidence value.'
            )
        if not isinstance(node.get('reasoning'), str):
            raise UpstreamAssertJudgeFailed(
                f'ASSERT node judgment {node_name!r} has no reasoning string.'
            )
        observed_nodes.add(node_name)

    missing_nodes = sorted(expected_nodes - observed_nodes)
    if missing_nodes:
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict omitted taxonomy categories: ' + ', '.join(missing_nodes)
        )
    if not isinstance(verdict.get('narrative'), str):
        raise UpstreamAssertJudgeFailed('ASSERT verdict is missing its narrative string.')
    return score


def _resolve_model(model_name: str | None) -> str:
    configured = (os.getenv('ASSERT_JUDGE_MODEL') or DEFAULT_ASSERT_JUDGE_MODEL).strip()
    model = (model_name or configured).strip()
    if not model:
        raise ValueError('ASSERT judge model cannot be empty.')
    allowed_raw = os.getenv('ASSERT_JUDGE_ALLOWED_MODELS', '').strip()
    allowed = {
        item.strip()
        for item in allowed_raw.split(',')
        if item.strip()
    } if allowed_raw else {configured}
    allowed.add(configured)
    if model not in allowed:
        raise ValueError(
            f'ASSERT judge model {model!r} is not allowed. '
            'Configure ASSERT_JUDGE_ALLOWED_MODELS to permit it.'
        )
    return model


def _require_provider_credentials(model: str, environment: dict[str, str]) -> None:
    if model.startswith('openai/') and not environment.get('OPENAI_API_KEY'):
        raise UpstreamAssertJudgeUnavailable(
            'ASSERT OpenAI judging requires OPENAI_API_KEY or LLM_JUDGE_API_KEY. '
            'The CAE Codex OAuth session is not forwarded to LiteLLM.'
        )


@contextmanager
def _assert_judge_slot() -> Iterator[None]:
    global _ASSERT_JUDGE_ACTIVE
    max_concurrent = _positive_int_env(
        'ASSERT_JUDGE_MAX_CONCURRENT',
        DEFAULT_ASSERT_JUDGE_MAX_CONCURRENT,
    )
    with _ASSERT_JUDGE_SLOT_LOCK:
        if _ASSERT_JUDGE_ACTIVE >= max_concurrent:
            raise UpstreamAssertJudgeBusy(
                f'ASSERT judge concurrency limit reached ({max_concurrent}).'
            )
        _ASSERT_JUDGE_ACTIVE += 1
    try:
        yield
    finally:
        with _ASSERT_JUDGE_SLOT_LOCK:
            _ASSERT_JUDGE_ACTIVE = max(_ASSERT_JUDGE_ACTIVE - 1, 0)


def _reserve_assert_credits(*, credits: int, model: str) -> dict[str, Any]:
    spend_control = _judge_spend_control()
    reserved, spend_control = _reserve_judge_credits(spend_control, credits=credits)
    if not reserved:
        raise UpstreamAssertJudgeBudgetExceeded(
            'LLM judge daily credit budget is exhausted. '
            'Increase LLM_JUDGE_DAILY_CREDIT_LIMIT or wait for the next budget window.'
        )
    return {
        **spend_control,
        'estimated_credits': credits,
        'provider': 'assert-ai',
        'provider_configured': True,
        'model': model,
    }


def _refund_assert_credits(spend_control: dict[str, Any], *, credits: int) -> None:
    _refund_judge_credits(spend_control, credits=credits)


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
    verdict = score['verdict']
    dimensions = verdict['dimensions']
    justifications = verdict['dimension_justifications']
    nodes = verdict['node_judgments']
    flagged = [name for name, value in dimensions.items() if value is True]
    violated = [node for node in nodes if node.get('violated') is True]
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
        if str(justifications[name]).strip()
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
        if node.get('violated') is False
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
    verdict = score['verdict']
    value = verdict.get('citations') or verdict.get('evidence_citations') or verdict.get('highlights')
    if isinstance(value, str) and value.strip():
        return [value.strip()[:500]]
    if isinstance(value, list):
        return [_json_text(item, limit=500) for item in value[:6]]
    nodes = verdict['node_judgments']
    return [
        f"{node.get('node_name')}: {node.get('reasoning') or 'violation observed'}"[:500]
        for node in nodes
        if node.get('violated') is True
    ][:6]


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise UpstreamAssertJudgeFailed(
                f'ASSERT wrote invalid JSON in scores.jsonl line {line_number}: {exc.msg}'
            ) from exc
        if not isinstance(value, dict):
            raise UpstreamAssertJudgeFailed(
                f'ASSERT wrote a non-object score in scores.jsonl line {line_number}.'
            )
        rows.append(value)
    return rows


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
