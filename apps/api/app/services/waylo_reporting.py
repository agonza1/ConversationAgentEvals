"""Read-only reporting of typed Waylo observations, never a grading verdict.

Observer and worker clocks describe different boundaries. Only the local
queue-drain -> received speech observations enter the response-latency summary.
"""
from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any


OBSERVER_METRIC = 'cae_livekit_queue_end_to_received_speech'
OBSERVER_CLOCK = 'cae_monotonic_since_transport_creation'


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _valid_ms(event: dict) -> bool:
    value = event.get('value_ms')
    return event.get('unit') == 'ms' and type(value) in {int, float} and isfinite(value) and value >= 0


def report_profile(profile: dict) -> dict:
    """Project one authoritative profile; do not merge repeated capture copies."""
    events = {event['event_id']: event for event in profile.get('voice_events', [])
              if isinstance(event, dict) and isinstance(event.get('event_id'), str)}
    observer, worker, interruptions = [], [], []
    for event in events.values():
        if event.get('event_type') == 'latency.observation' and _valid_ms(event):
            if event.get('source') == 'cae_livekit_observer' and event.get('clock_origin') == OBSERVER_CLOCK:
                observer.append({**deepcopy(event), 'kind': OBSERVER_METRIC, 'response_metric': OBSERVER_METRIC,
                    'latency_ms': event['value_ms'], 'participant': 'target', 'direction': 'target_to_tester',
                    'label': 'Local caller queue drained → received target speech',
                    'scoreable': False})
            elif event.get('source') == 'waylo.agent.turn_metrics':
                worker.append(deepcopy(event))
        elif (event.get('event_type') == 'playback.interruption_observed'
                and event.get('source') == 'waylo.agent.turn_metrics' and type(event.get('interrupted')) is bool):
            interruptions.append(deepcopy(event))
    scheduled = sum(event.get('event_type') == 'tester.barge_in_scheduled' for event in events.values())
    interrupted = sum(event['interrupted'] for event in interruptions)
    context = _record(profile.get('context'))
    return {'latency_marks': observer, 'evidence_diagnostics': {
        **deepcopy(_record(context.get('diagnostics'))),
        'response_latency': {'status': 'observed' if observer else 'unknown',
            'observer_count': len(observer), 'observer_definition': 'Local caller queue drained → first received remote frame above RMS 150.',
            'clock_origin': OBSERVER_CLOCK, 'unit': 'ms', 'scoreable': False,
            'worker_observations': worker,
            'worker_definition': 'Worker-reported LiveKit turn e2e_latency; not tester-observed latency. Do not add or average it with the observer series.'},
        'interruption': {'status': 'observed' if interruptions else 'unknown',
            'reported_true': interrupted, 'reported_false': len(interruptions) - interrupted,
            'observations': interruptions, 'controlled_attempts': scheduled,
            'handling_status': 'needs_review' if scheduled else 'not_tested', 'scoreable': False,
            'scope': 'Native flags report interrupted agent speech; they do not prove detection, cause, or correct barge-in handling.'},
    }}


def observed_coverage(*, tools: list[dict], voice: list[dict], snapshots: list[dict],
                      diagnostics: dict, media_dialogs: list[dict] | None, expectations: dict) -> dict:
    """Coverage describes captured/applicable checks, not a four-C score.

    Presence of an empty backend snapshot is evidence of a read, not business
    success. Native interruption=false is an observation, not an exercised test.
    """
    observed = {key: [] for key in ('conversation_experience', 'speech_boundary', 'agent_execution', 'business_outcome')}
    if media_dialogs or any(event.get('event_type') in {'target.audio.received', 'tester.audio.sent'} for event in voice):
        observed['conversation_experience'].append('audio')
    if any(event.get('event_type') == 'latency.observation' and _valid_ms(event)
           and event.get('source') == 'cae_livekit_observer' and event.get('clock_origin') == OBSERVER_CLOCK for event in voice):
        observed['conversation_experience'].append('observer_response_latency')
    if any(event.get('event_type') == 'latency.observation' and event.get('source') == 'waylo.agent.turn_metrics' for event in voice):
        observed['conversation_experience'].append('native_worker_latency')
    if any(event.get('event_type') == 'playback.interruption_observed' and type(event.get('interrupted')) is bool for event in voice):
        observed['conversation_experience'].append('native_interruption_flags')
    if any(event.get('event_type') == 'tester.barge_in_scheduled' for event in voice):
        observed['conversation_experience'].append('controlled_barge_in_attempt')
    sent = [event for event in voice if event.get('event_type') == 'tester.audio.sent']
    if any(isinstance(event.get('reference_text'), str) and event['reference_text'].strip() for event in sent):
        observed['speech_boundary'].append('tester_reference')
    if any(event.get('audio_sha256') and type(event.get('accepted_samples')) is int and event['accepted_samples'] > 0 for event in sent):
        observed['speech_boundary'].append('actual_sent_audio')
    if any(type(event.get('accepted_samples')) is int and type(event.get('intended_samples')) is int for event in sent):
        observed['speech_boundary'].append('local_send_observation')
    comparisons = _record(diagnostics.get('speech_boundary')).get('comparisons') or []
    if comparisons:
        observed['speech_boundary'].append('target_asr_final_provisional_alignment')
    if any(row.get('critical_entities') for row in comparisons):
        observed['speech_boundary'].append('critical_entity_comparison')
    if tools:
        observed['agent_execution'].append('tool_invocations')
    if tools and all(event.get('arguments_captured') is True for event in tools):
        observed['agent_execution'].append('tool_arguments')
    results = [event for event in tools if event.get('event_type') == 'tool_call.result']
    if results and all(event.get('result_captured') is True for event in results):
        observed['agent_execution'].append('tool_results')
    if tools and all(event.get('turn_id') or event.get('turn_index') is not None for event in tools):
        observed['agent_execution'].append('turn_associations')
    if any(event.get('policy_decision') for event in tools):
        observed['agent_execution'].append('policy_decisions')
    if any(event.get('retry_of') for event in tools):
        observed['agent_execution'].append('reported_retries')
    phases = {snapshot.get('phase') for snapshot in snapshots}
    for phase in ('before', 'after', 'final'):
        if phase in phases:
            observed['business_outcome'].append(f'request_list_{phase}_readback')
    if _record(diagnostics.get('business_outcome')).get('status') in {'observed_agreement', 'observed_mismatch'}:
        observed['business_outcome'].append('expected_request_list_comparison')
    if any(event.get('name') == 'search_documents' and event.get('status') == 'success'
           and event.get('result_captured') is True for event in results):
        observed['business_outcome'].append('catalog_lookup_results')
    requirements = {
        'conversation_experience': ['audio', 'observer_response_latency', 'native_interruption_flags'],
        'speech_boundary': ['tester_reference', 'actual_sent_audio', 'local_send_observation', 'target_asr_final_provisional_alignment'],
        'agent_execution': ['tool_invocations', 'tool_arguments', 'tool_results', 'turn_associations', 'policy_decisions'],
        'business_outcome': ['request_list_before_readback', 'request_list_after_readback', 'request_list_final_readback'],
    }
    if expectations.get('expected_entities_by_turn'):
        requirements['speech_boundary'].append('critical_entity_comparison')
    if 'expected_requests' in expectations:
        requirements['business_outcome'].append('expected_request_list_comparison')
    limits = {
        'conversation_experience': ['target_playback_ack', 'transport_quality', 'silence_and_continuity_assessment', 'verified_interruption_handling'],
        'speech_boundary': ['peer_delivery_ack', 'independent_audio_asr_alignment', 'asr_partial_timestamps'],
        'agent_execution': ['complete_execution_and_retry_coverage'],
        'business_outcome': ['full_backend_state', 'spoken_confirmation_alignment', 'durable_canonical_product_association'],
    }
    if 'expected_requests' not in expectations:
        limits['business_outcome'].append('controlled_expected_request_list')
    return {area: {'status': 'partial' if fields else 'unknown', 'evaluation_status': 'needs_review',
        'observed': fields, 'missing': [*filter(lambda field: field not in fields, requirements[area]), *limits[area]],
        'scoreable': False} for area, fields in observed.items()}


def present_run(run: dict[str, Any]) -> dict[str, Any]:
    """Enrich a COPY for current and historical runs without rewriting evidence.

    In particular this never changes an accepted grade, saved vCon, capture
    revision, review freshness, or inference file.
    """
    if run.get('executor_id') != 'waylo_livekit':
        return run
    from app.services.vcon_evidence import decode_evidence
    from app.services.execution_metrics import build_metrics_and_timeline
    for row in run.get('conversations', []):
        try:
            profile = decode_evidence(row.get('ietf_vcon_export') or {})
        except (ValueError, TypeError, KeyError):
            continue
        if not isinstance(profile, dict):
            continue
        report = report_profile(profile)
        row.update(report)
        context = _record(profile.get('context'))
        row['evidence_coverage'] = observed_coverage(tools=profile.get('tool_events') or [],
            voice=profile.get('voice_events') or [], snapshots=profile.get('state_snapshots') or [],
            diagnostics=_record(context.get('diagnostics')), media_dialogs=None,
            expectations=_record(context.get('test_expectations')))
        metrics, _ = build_metrics_and_timeline(turns=row.get('turns') or [], latency_marks=report['latency_marks'],
            verdict=row.get('verdict'), score=row.get('score'))
        if row.get('metrics_summary'):
            row['metrics_summary']['latency'] = metrics.latency.model_dump()
        else:
            row['metrics_summary'] = metrics.model_dump()
    return run
