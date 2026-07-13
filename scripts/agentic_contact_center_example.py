from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / 'apps' / 'api'))

from app.services.agentic_contact_center_example import (
    build_assert_run_request,
    build_benchmark_run_request,
    normalize_acc_run,
)


DEFAULT_SCENARIO = PROJECT_ROOT / 'docs' / 'examples' / 'agentic-contact-center-cancellation-rescue.json'
DEFAULT_INPUT_FIXTURE = PROJECT_ROOT / 'docs' / 'examples' / 'agentic-contact-center-run-fixture.json'


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scenario = json.loads(args.scenario.read_text())
    timestamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')
    output_dir = args.output_root / f"acc-example-{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        if args.input is not None:
            raw_response = json.loads(args.input.read_text())
            if not isinstance(raw_response, dict):
                raise RuntimeError(f'Input fixture must contain a JSON object: {args.input}')
            target_source = 'offline_fixture'
            target_endpoint = None
        else:
            target_endpoint = _join_url(args.acc_url, scenario['target']['run_endpoint'])
            target_payload = {
                'openclawSessionLabel': f"conversation-agent-evals/{scenario['scenario_id']}/{timestamp}",
            }
            raw_response = _json_request('POST', target_endpoint, target_payload, timeout=args.timeout)
            target_source = 'running_acc_http_target'

        _write_json(output_dir / 'acc-raw-response.json', raw_response)

        normalized = normalize_acc_run(raw_response, scenario=scenario)
        normalized['provenance'] = {
            **normalized.get('provenance', {}),
            'target_source': target_source,
            'input_fixture': str(args.input) if args.input is not None else None,
            'resolved_target_endpoint': target_endpoint,
        }
        call_id = normalized.get('call_id')
        if args.input is None and call_id and not isinstance(raw_response.get('proof'), dict):
            proof_template = scenario['target'].get('proof_endpoint_template')
            if isinstance(proof_template, str) and proof_template:
                proof_endpoint = _join_url(args.acc_url, proof_template.format(call_id=call_id))
                try:
                    proof = _json_request('GET', proof_endpoint, timeout=args.timeout)
                    raw_response = {**raw_response, 'proof': proof}
                    _write_json(output_dir / 'acc-raw-response.json', raw_response)
                    normalized = normalize_acc_run(raw_response, scenario=scenario)
                    normalized['provenance'] = {
                        **normalized.get('provenance', {}),
                        'target_source': target_source,
                        'input_fixture': None,
                        'resolved_target_endpoint': target_endpoint,
                        'resolved_proof_endpoint': proof_endpoint,
                    }
                except RuntimeError as exc:
                    normalized.setdefault('runtime_caveats', []).append(
                        f'ACC proof follow-up request failed: {exc}'
                    )

        _write_json(output_dir / 'normalized-evidence.json', normalized)

        benchmark_request = build_benchmark_run_request(
            normalized,
            scenario=scenario,
            user_id=args.user_id,
            project_id=args.project_id,
        ).model_dump(mode='json', exclude_none=True)
        _write_json(output_dir / 'benchmark-run-request.json', benchmark_request)

        assert_request = build_assert_run_request(
            normalized,
            scenario=scenario,
            assert_sidecar_url=args.assert_sidecar_url,
            user_id=args.user_id,
            project_id=args.project_id,
        ).model_dump(mode='json', exclude_none=True)
        _write_json(output_dir / 'assert-run-request.json', assert_request)

        evaluation_response = None
        if not args.skip_submit:
            evaluation_endpoint = _join_url(args.conversation_agent_evals_url, '/api/assert/runs')
            evaluation_response = _json_request(
                'POST',
                evaluation_endpoint,
                assert_request,
                timeout=args.timeout,
            )
            _write_json(output_dir / 'assert-ingestion-response.json', evaluation_response)

        summary = {
            'ok': True,
            'scenario_id': scenario['scenario_id'],
            'execution_mode': normalized['execution_mode'],
            'target_source': target_source,
            'input_fixture': str(args.input) if args.input is not None else None,
            'call_id': normalized.get('call_id'),
            'outcome': normalized.get('outcome'),
            'transcript_turns': len(normalized.get('conversation', {}).get('dialog', [])),
            'action_events': len(normalized.get('action_trace', [])),
            'latency_marks': len(normalized.get('latency_evidence', {}).get('marks', [])),
            'submitted': not args.skip_submit,
            'platform_run_id': evaluation_response.get('platform_run_id') if isinstance(evaluation_response, dict) else None,
            'output_dir': str(output_dir),
            'limitations': normalized.get('runtime_caveats', []),
            'result_label': (
                'assert_sidecar_ingestion_validation'
                if evaluation_response is not None
                else 'target_evidence_collection_only'
            ),
        }
        _write_json(output_dir / 'summary.json', summary)
        print(json.dumps(summary, indent=2))
        return 0
    except Exception as exc:
        failure = {
            'ok': False,
            'scenario_id': scenario.get('scenario_id'),
            'error': str(exc),
            'output_dir': str(output_dir),
        }
        _write_json(output_dir / 'failure.json', failure)
        print(json.dumps(failure, indent=2), file=sys.stderr)
        return 1


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Run the optional ConversationAgentEvals example against ACC or a checked-in offline ACC fixture.'
    )
    parser.add_argument(
        '--scenario',
        type=Path,
        default=DEFAULT_SCENARIO,
        help='Machine-readable example scenario contract.',
    )
    parser.add_argument(
        '--input',
        type=Path,
        default=None,
        help=(
            'Read a saved ACC response instead of calling ACC. '
            f'Use {DEFAULT_INPUT_FIXTURE.relative_to(PROJECT_ROOT)} for the checked-in standalone fixture.'
        ),
    )
    parser.add_argument(
        '--acc-url',
        default=os.getenv('ACC_BASE_URL', 'http://127.0.0.1:8026'),
        help='Optional running Agentic Contact Center base URL.',
    )
    parser.add_argument(
        '--conversation-agent-evals-url',
        default=os.getenv('CONVERSATION_AGENT_EVALS_BASE_URL', 'http://127.0.0.1:8025'),
        help='Running ConversationAgentEvals API base URL when submitting the wrapper request.',
    )
    parser.add_argument(
        '--assert-sidecar-url',
        default=os.getenv('ASSERT_SIDECAR_BASE_URL', 'http://127.0.0.1:8091'),
        help='ASSERT invocation target recorded in the canonical request.',
    )
    parser.add_argument(
        '--output-root',
        type=Path,
        default=PROJECT_ROOT / 'artifacts' / 'agentic-contact-center-example',
        help='Directory where raw and normalized example artifacts are written.',
    )
    parser.add_argument('--timeout', type=float, default=30.0)
    parser.add_argument('--user-id', default='acc-example-user')
    parser.add_argument('--project-id', default='agentic-contact-center')
    parser.add_argument(
        '--skip-submit',
        action='store_true',
        help='Normalize evidence and write requests without calling the ConversationAgentEvals API.',
    )
    return parser.parse_args(argv)


def _json_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            'accept': 'application/json',
            'content-type': 'application/json',
            'user-agent': 'conversation-agent-evals-acc-example/1',
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode('utf-8')
            decoded = json.loads(raw) if raw else {}
            if not isinstance(decoded, dict):
                raise RuntimeError(f'{method} {url} returned a non-object JSON response')
            return decoded
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')
        raise RuntimeError(f'{method} {url} returned HTTP {exc.code}: {detail}') from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f'{method} {url} failed: {exc.reason}') from exc


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'{json.dumps(payload, indent=2, sort_keys=True)}\n')


if __name__ == '__main__':
    raise SystemExit(main())
