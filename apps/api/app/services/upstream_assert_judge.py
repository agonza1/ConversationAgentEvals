from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

import yaml

from app.integrations.assert_runtime import (
    AssertRuntimeUnavailable,
    cli_executable,
    infer_judge_status,
    installed_version,
    is_not_applicable_dimension,
    is_valid_confidence_label,
    is_valid_event_flag,
    judge_score_contract,
)

from app.services.evaluation_contract import content_hash
from app.services.assert_taxonomy_adapter import build_assert_taxonomy
from app.services.assert_transcript_adapter import build_assert_inference_row, _identifier, _json_text
from app.services.judge_budget import (
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
FINGERPRINT_VERSION = 3
ADAPTER_VERSION = 'cae-assert-evidence-v2'
AGGREGATION_VERSION = 'cae-assert-observability-v2'

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
    expected_input_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Execute ASSERT's existing judge-only pipeline over one completed CAE conversation."""
    if os.getenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
        raise UpstreamAssertJudgeUnavailable(
            'Upstream ASSERT judging is disabled. Set ASSERT_UPSTREAM_JUDGE_ENABLED=1 to enable it.'
        )
    from app.services.llm_providers.chatgpt_plan import ChatGPTPlanError, get_chatgpt_provider
    try:
        selection = get_chatgpt_provider().status()
    except ChatGPTPlanError as exc:
        raise UpstreamAssertJudgeUnavailable(str(exc)) from exc
    configured = selection.get('judge_model') or (os.getenv('ASSERT_JUDGE_MODEL') or DEFAULT_ASSERT_JUDGE_MODEL).strip()
    model = _resolve_model(model_name, configured_model=configured)
    if model.startswith('chatgpt_plan/') and not selection.get('enabled'):
        raise UpstreamAssertJudgeUnavailable(selection['message'])
    max_judge_n = min(3, _positive_int_env('ASSERT_JUDGE_MAX_N', DEFAULT_ASSERT_JUDGE_MAX_N))
    if not 1 <= judge_n <= max_judge_n:
        raise ValueError(f'judge_n must be between 1 and {max_judge_n}.')

    taxonomy = build_assert_taxonomy(scenario_contract=scenario_contract, conversation=conversation)
    inference = build_assert_inference_row(run=run, conversation=conversation)
    configuration = judge_configuration(model, judge_n)
    if model.startswith('chatgpt_plan/') and configuration['transport']['account_binding_sha256'] != selection.get('account_binding_sha256'):
        raise UpstreamAssertJudgeUnavailable('ChatGPT account changed; start a new review.')
    judge_dimensions = configuration['dimensions']
    score_contract = judge_score_contract(judge_dimensions)
    fingerprint = _input_fingerprint(model, judge_n, taxonomy, inference, configuration=configuration)
    if expected_input_fingerprint is not None and fingerprint != expected_input_fingerprint:
        raise UpstreamAssertJudgeUnavailable(
            'Judge configuration or ChatGPT account changed after this request was claimed; start a new review.')

    try:
        executable = cli_executable()
    except AssertRuntimeUnavailable as exc:
        raise UpstreamAssertJudgeUnavailable(str(exc)) from exc
    environment = os.environ.copy()
    if model.startswith('openai/') and not environment.get('OPENAI_API_KEY') and environment.get('LLM_JUDGE_API_KEY'):
        environment['OPENAI_API_KEY'] = environment['LLM_JUDGE_API_KEY']
    _require_provider_credentials(model, environment)
    if model.startswith('chatgpt_plan/'):
        if environment['CAE_CHATGPT_EXPECTED_BINDING'] != configuration['transport']['account_binding_sha256']:
            raise UpstreamAssertJudgeUnavailable('ChatGPT account changed; start a new review.')
        environment['PYTHONPATH'] = str(REPO_ROOT / 'apps' / 'api') + os.pathsep + environment.get('PYTHONPATH', '')
        environment['ASSERT_JUDGE_TIMEOUT_SECONDS'] = str(configuration['timeout_seconds'])

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
                        'model': configuration['model_settings'],
                        'n': judge_n,
                        'inference_set_path': str(inference_path),
                        'taxonomy_path': str(taxonomy_path),
                        'save_dir': str(run_dir),
                        'dimensions': judge_dimensions,
                    }
                },
            }, sort_keys=False), encoding='utf-8')

            command = [
                executable,
                '-m',
                'app.integrations.chatgpt_assert_cli' if model.startswith('chatgpt_plan/') else 'assert_ai.cli',
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
                    timeout=configuration['timeout_seconds'],
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
                score_contract=score_contract,
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
            semantic_pass_eligible = _semantic_pass_eligible(conversation, taxonomy)
            review = _review(score, str(conversation.get('verdict') or ''),
                             allow_semantic_pass=semantic_pass_eligible)
            review['provenance'] = {
                'engine': 'assert',
                'fingerprint_version': FINGERPRINT_VERSION,
                'judge_n': judge_n,
                'configuration': deepcopy(configuration),
                'contract_snapshot_sha256': (scenario_contract or {}).get('_snapshot_sha256'),
                'assert_version': assert_version,
                'judge_status': 'ok',
                'semantic_pass_eligible': semantic_pass_eligible,
                'input_fingerprint': fingerprint,
                'score_sha256': score_sha256,
                'artifacts': deepcopy(artifacts),
                'evidence_level': str(
                    (inference.get('dimensions') or {}).get('evidence_level') or 'black_box'
                ),
                'score_keys': deepcopy(score['score_keys']),
                'not_applicable_score_keys': deepcopy(score['not_applicable_score_keys']),
                'dimension_scales': deepcopy(score.get('dimension_scales') or {}),
                'dimensions': deepcopy(score['verdict']['dimensions']),
                'dimension_justifications': deepcopy(
                    score['verdict'].get('dimension_justifications') or {}
                ),
                'dimension_applicability': deepcopy(
                    score['verdict'].get('dimension_applicability') or {}
                ),
                'node_judgments': deepcopy(score['verdict']['node_judgments']),
                'multi_judge': deepcopy(score.get('multi_judge')),
            }
            response = {
                'status': 'ready',
                'required_plan': 'starter',
                'credits': credits,
                'engine': 'assert',
                'judge_n': judge_n,
                'fingerprint_version': FINGERPRINT_VERSION,
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
    score_contract: dict[str, Any],
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
    if raw_status != 'ok':
        detail = str(score.get('judge_error') or raw_status)
        raise UpstreamAssertJudgeFailed(
            f'ASSERT did not produce a valid judgment for {test_case_id!r}: {detail[:1000]}'
        )

    verdict = score.get('verdict')
    if not isinstance(verdict, dict):
        raise UpstreamAssertJudgeFailed('ASSERT returned a malformed verdict object.')
    dimensions = verdict.get('dimensions')
    if not isinstance(dimensions, dict):
        raise UpstreamAssertJudgeFailed('ASSERT verdict is missing its dimensions object.')

    expected_dimensions = score_contract['score_keys']
    expected_not_applicable_score_keys = score_contract['not_applicable_score_keys']
    expected_dimension_scales = score_contract['dimension_scales']
    if set(dimensions) != set(expected_dimensions):
        mismatched_dimensions = sorted(set(dimensions).symmetric_difference(expected_dimensions))
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict has missing or invalid dimension values: '
            + ', '.join(mismatched_dimensions)
        )
    score_keys = score.get('score_keys')
    if not isinstance(score_keys, list) or not all(isinstance(name, str) for name in score_keys):
        raise UpstreamAssertJudgeFailed('ASSERT 0.3 score is missing its score_keys contract.')
    if score_keys != expected_dimensions:
        raise UpstreamAssertJudgeFailed(
            'ASSERT 0.3 score_keys do not match the configured dimensions: '
            + ', '.join(score_keys)
        )
    not_applicable_score_keys = score.get('not_applicable_score_keys')
    if not isinstance(not_applicable_score_keys, list) or not all(
        isinstance(name, str) for name in not_applicable_score_keys
    ):
        raise UpstreamAssertJudgeFailed(
            'ASSERT 0.3 score has an invalid not_applicable_score_keys contract.'
        )
    if not_applicable_score_keys != expected_not_applicable_score_keys:
        raise UpstreamAssertJudgeFailed(
            'ASSERT 0.3 not_applicable_score_keys do not match the configured dimensions.'
        )
    dimension_scales = score.get('dimension_scales', {})
    if not isinstance(dimension_scales, dict):
        raise UpstreamAssertJudgeFailed('ASSERT 0.3 score has an invalid dimension_scales contract.')
    if dimension_scales != expected_dimension_scales:
        raise UpstreamAssertJudgeFailed(
            'ASSERT 0.3 dimension_scales do not match the configured dimensions.'
        )
    invalid_dimensions = [
        name for name in expected_dimensions
        if not _valid_dimension_value(
            verdict=verdict,
            name=name,
            not_applicable_score_keys=set(expected_not_applicable_score_keys),
            dimension_scales=expected_dimension_scales,
        )
    ]
    if invalid_dimensions:
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict has missing or invalid dimension values: '
            + ', '.join(invalid_dimensions)
        )

    justifications = verdict.get('dimension_justifications')
    if not isinstance(justifications, dict):
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict is missing its dimension_justifications object.'
        )
    if set(justifications) != set(expected_dimensions):
        raise UpstreamAssertJudgeFailed(
            'ASSERT verdict dimension justifications do not match the configured dimensions.'
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
    inferred_status = infer_judge_status(score)
    if inferred_status != 'ok':
        detail = str(score.get('judge_error') or inferred_status)
        raise UpstreamAssertJudgeFailed(
            f'ASSERT did not produce a valid judgment for {test_case_id!r}: {detail[:1000]}'
        )
    return score


def _valid_dimension_value(
    *,
    verdict: dict[str, Any],
    name: str,
    not_applicable_score_keys: set[str],
    dimension_scales: dict[str, Any],
) -> bool:
    dimensions = verdict.get('dimensions')
    if not isinstance(dimensions, dict):
        return False
    value = dimensions.get(name)
    applicability = verdict.get('dimension_applicability')
    if applicability is not None:
        if not isinstance(applicability, dict):
            return False
        if name in applicability:
            applies = applicability[name]
            if not isinstance(applies, bool):
                return False
            if applies is False:
                return (
                    name in not_applicable_score_keys
                    and is_not_applicable_dimension(verdict, name)
                )
    if is_valid_event_flag(value):
        return name not in dimension_scales
    if name in not_applicable_score_keys and is_not_applicable_dimension(verdict, name):
        return True
    scale = dimension_scales.get(name)
    if not isinstance(scale, dict) or scale.get('type') != 'ordinal':
        return False
    allowed = [
        entry.get('value')
        for entry in scale.get('values', [])
        if isinstance(entry, dict) and 'value' in entry
    ]
    if not allowed or isinstance(value, bool):
        return False
    expected_type = str if isinstance(allowed[0], str) else int
    return isinstance(value, expected_type) and value in allowed


def _resolve_model(model_name: str | None, *, configured_model: str | None = None) -> str:
    configured = configured_model if configured_model is not None else _configured_judge_model()
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


def _configured_judge_model() -> str:
    from app.services.llm_providers.chatgpt_plan import ChatGPTPlanError, get_chatgpt_provider
    try:
        selected = get_chatgpt_provider().selected_model()
    except ChatGPTPlanError:
        # Unknown saved billing mode must not silently fall back to API-key usage.
        return 'chatgpt_plan/repair-storage'
    return selected or (os.getenv('ASSERT_JUDGE_MODEL') or DEFAULT_ASSERT_JUDGE_MODEL).strip()


def _require_provider_credentials(model: str, environment: dict[str, str]) -> None:
    """Validate configuration locally; never make a probe or select a fallback model."""
    if model.startswith('chatgpt_plan/'):
        from app.services.llm_providers.chatgpt_plan import ChatGPTPlanError, get_chatgpt_provider
        provider = get_chatgpt_provider()
        try:
            status = provider.status()
        except ChatGPTPlanError as exc:
            raise UpstreamAssertJudgeUnavailable(str(exc)) from exc
        if model == 'chatgpt_plan/select-model':
            raise UpstreamAssertJudgeUnavailable('Select a judge model for this ChatGPT account in Console Settings. No API-key fallback was used.')
        if not status.get('enabled') or not status.get('sharing'):
            raise UpstreamAssertJudgeUnavailable(status['message'])
        environment['CAE_CHATGPT_EXPECTED_BINDING'] = provider.binding()
        return
    if model.startswith('openai/'):
        if not environment.get('OPENAI_API_KEY', '').strip():
            raise UpstreamAssertJudgeUnavailable(
                'ASSERT OpenAI judging requires OPENAI_API_KEY or LLM_JUDGE_API_KEY. '
                'The CAE Codex OAuth session is not forwarded to LiteLLM.')
        return
    # Use the pinned provider adapter's environment rules for non-OpenAI models,
    # including local providers; do not reimplement a second provider registry.
    from litellm import validate_environment
    try:
        result = validate_environment(model=model)
    except Exception as exc:
        raise UpstreamAssertJudgeUnavailable('Cannot validate the configured ASSERT provider environment.') from exc
    if result.get('keys_in_environment') is not True:
        missing = ', '.join(result.get('missing_keys') or [])
        raise UpstreamAssertJudgeUnavailable(
            f'ASSERT provider configuration is incomplete for {model!r}'
            + (f': {missing}.' if missing else '. No supported local credential preflight was found.')
            + ' No silent model or provider fallback is used.')


def assert_judge_readiness() -> dict[str, Any]:
    """Read-only preflight; never invokes a model, OAuth, or reserves credits."""
    enabled = os.getenv('ASSERT_UPSTREAM_JUDGE_ENABLED', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    model = _configured_judge_model()
    blockers: list[str] = []
    if not enabled:
        blockers.append('Set ASSERT_UPSTREAM_JUDGE_ENABLED=1 to enable optional semantic reviews.')
    runtime_ready = credentials_ready = False
    try:
        cli_executable()
        runtime_ready = True
    except AssertRuntimeUnavailable as exc:
        blockers.append(str(exc))
    try:
        _resolve_model(model)
        environment = dict(os.environ)
        if not environment.get('OPENAI_API_KEY') and environment.get('LLM_JUDGE_API_KEY'):
            environment['OPENAI_API_KEY'] = environment['LLM_JUDGE_API_KEY']
        _require_provider_credentials(model, environment)
        credentials_ready = True
    except (ValueError, UpstreamAssertJudgeUnavailable) as exc:
        blockers.append(str(exc))
    spend = _judge_spend_control()
    if not spend['within_budget']:
        blockers.append('The configured daily judge credit budget is exhausted.')
    limit = _positive_int_env('ASSERT_JUDGE_MAX_CONCURRENT', DEFAULT_ASSERT_JUDGE_MAX_CONCURRENT)
    with _ASSERT_JUDGE_SLOT_LOCK:
        available = max(0, limit - _ASSERT_JUDGE_ACTIVE)
    if not available:
        blockers.append('All judge slots are currently busy; retry later.')
    return {'engine': 'assert', 'ready': not blockers, 'enabled': enabled, 'model': model,
            'runtime_ready': runtime_ready, 'credentials_ready': credentials_ready,
            'max_concurrent': limit, 'available_slots': available,
            'concurrency_scope': 'process', 'default_judge_n': 1,
            'within_budget': spend['within_budget'],
            'message': 'ASSERT semantic judging is ready.' if not blockers else ' '.join(blockers)}


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
        'unverified_operational_outcome': {
            'description': 'Is a required external outcome or consequential completion claim not verifiable from the supplied evidence?',
            'rubric': ('true = An external outcome is required or claimed but matching tool/receipt/state evidence is missing or incomplete. '
                       'This means not_observable, not a proven agent failure.\n'
                       'false = No external outcome is required/claimed, or sufficient correlated evidence verifies it.'),
        },
        'required_behavior_failure': {
            'description': 'Did the assistant fail to perform an applicable required behavior?',
            'rubric': (
                'true = An applicable required behavior was omitted, contradicted, abandoned, '
                'with observable evidence of that failure (missing telemetry alone is not failure)\n'
                'false = Every applicable required behavior was completed or safely handed off'
            ),
        },
        'unsupported_operational_claim': {
            'description': 'Did the assistant claim an external or business action occurred without evidence?',
            'rubric': (
                'true = A completion or authorization claim contradicts recorded execution evidence\n'
                'false = No contradiction is established. Missing/partial telemetry is not proof '
                'of a false claim; unverified outcomes are separately marked not_observable.'
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


def _semantic_pass_eligible(conversation: dict[str, Any], taxonomy: dict[str, Any]) -> bool:
    """A complete semantic review may resolve a provisional result, never missing hard evidence."""
    meta = taxonomy.get('meta') or {}
    findings = conversation.get('evaluation_findings') or {}
    if not isinstance(findings, dict):
        return False
    enforcement = findings.get('design_enforcement')
    return bool(meta.get('contract_snapshot_sha256')
        and isinstance(enforcement, dict) and enforcement.get('blocked') is False
        and not enforcement.get('failed')
        and not any(row.get('status') == 'fail' for row in findings.get('behavior_results', []) if isinstance(row, dict)))


def _review(score: dict[str, Any], deterministic_verdict: str, *, allow_semantic_pass: bool = False) -> dict[str, Any]:
    verdict = score['verdict']
    dimensions = verdict['dimensions']
    justifications = verdict['dimension_justifications']
    nodes = verdict['node_judgments']
    flagged = [name for name, value in dimensions.items() if value is True]
    violated = [node for node in nodes if node.get('violated') is True]
    deterministic = deterministic_verdict.strip().lower()
    semantic_clear = all(value is False or is_not_applicable_dimension(verdict, name)
                         for name, value in dimensions.items()) and bool(nodes) and all(
        node.get('violated') is False for node in nodes)
    proposed = (
        'fail' if deterministic in {'fail', 'failed'}
        else 'needs_review' if flagged or violated or (deterministic == 'needs_review' and not (allow_semantic_pass and semantic_clear))
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
        'check_results': [
            {'id': name, 'outcome': ('not_observable' if name == 'unverified_operational_outcome' and value is True
               else 'not_applicable' if is_not_applicable_dimension(verdict, name)
               else 'fail' if value is True else 'pass' if value is False else 'not_observable'),
             'reason': str(justifications.get(name) or ''), 'basis': 'assert_semantic_judge'}
            for name, value in dimensions.items()
        ],
        'agrees': normalized == proposed if normalized else None,
        'rationale': rationale[:4000],
        'next_action': (
            f'Review the evidence for {gaps[0]}.' if gaps
            else 'Confirm the semantic pass proposal; preserve the original automatic result and ASSERT score artifact.'
                 if proposed == 'pass' and deterministic == 'needs_review'
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
    return installed_version()


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def judge_configuration(model: str, judge_n: int) -> dict[str, Any]:
    """All execution-affecting grader settings, excluding secrets and invocation paths."""
    from app.integrations.assert_runtime import BUILT_IN_DIMENSIONS
    # Routing changes must invalidate a saved judge result, but provider URLs
    # can contain basic-auth passwords, API tokens or private gateway names.
    # Only a stable aggregate digest may cross into persisted provenance.
    provider_identity = content_hash({
        key: os.getenv(key, '')
        for key in (
            'OPENAI_API_BASE', 'OPENAI_BASE_URL', 'AZURE_API_BASE',
            'AZURE_API_VERSION', 'ANTHROPIC_API_BASE', 'OLLAMA_API_BASE',
            'GEMINI_API_BASE', 'AWS_REGION_NAME', 'VERTEXAI_PROJECT',
            'VERTEXAI_LOCATION',
        )
    })
    configuration = {'fingerprint_version': FINGERPRINT_VERSION, 'assert_version': _assert_version(),
            'adapter_version': ADAPTER_VERSION, 'aggregation_version': AGGREGATION_VERSION,
            'model': model, 'judge_n': judge_n,
            'model_settings': {'name': model, 'max_tokens': _positive_int_env('ASSERT_JUDGE_MAX_TOKENS', 8000)},
            'timeout_seconds': _positive_int_env('ASSERT_JUDGE_TIMEOUT_SECONDS', DEFAULT_ASSERT_JUDGE_TIMEOUT_SECONDS),
            'dimensions': _judge_dimensions(), 'builtin_dimensions': deepcopy(BUILT_IN_DIMENSIONS),
            'provider_endpoint_identity_sha256': provider_identity}
    if model.startswith('chatgpt_plan/'):
        from app.services.llm_providers.chatgpt_plan import get_chatgpt_provider, RESOURCE
        configuration['model_settings'] = {'name': model}  # Plan API does not accept token caps.
        configuration['transport'] = {'version': 'chatgpt-plan-responses-v1', 'endpoint': RESOURCE + '/responses',
            'stream': True, 'store': False, 'billing': 'chatgpt_plan',
            'account_binding_sha256': get_chatgpt_provider().binding()}
    return configuration


def _input_fingerprint(model: str, judge_n: int, taxonomy: dict[str, Any], inference: dict[str, Any],
                       *, configuration: dict[str, Any] | None = None) -> str:
    return content_hash({'configuration': configuration if configuration is not None else judge_configuration(model, judge_n),
                         'taxonomy': taxonomy, 'inference': inference})


def assert_judge_input_fingerprint(*, run: dict[str, Any], conversation: dict[str, Any],
                                   scenario_contract: dict[str, Any] | None, model: str, judge_n: int) -> str:
    """Recompute saved judging input identity without invoking a judge or loading artifacts."""
    return _input_fingerprint(model, judge_n,
        build_assert_taxonomy(scenario_contract=scenario_contract, conversation=conversation),
        build_assert_inference_row(run=run, conversation=conversation))
