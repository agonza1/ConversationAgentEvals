"""Finite, side-effect-free checks over recorded evidence. Never execute authored code."""
from __future__ import annotations

from app.services.assert_trace import parse_action_trace
from app.services.structured_checks import _evaluate_check, _event_type_names_from_action_trace, _normalize_event_name, structured_outcome

EVIDENCE_ALIASES = {
    'transcript': 'transcript', 'conversation transcript': 'transcript',
    'action_trace': 'action_trace', 'action trace': 'action_trace', 'tool trace': 'action_trace',
    'final_state': 'final_state', 'final state': 'final_state', 'vcon': 'vcon',
    'final_state_or_action_trace': 'final_state_or_action_trace',
}
CHECKS = {'transcript_present', 'action_trace_present', 'final_state_present', 'final_state_complete'}
SUCCESS = {'success', 'succeeded', 'completed', 'complete', 'ok', 'true'}
FAILURE = {'fail', 'failed', 'failure', 'error', 'errored', 'false', 'rejected', 'cancelled', 'canceled', 'timeout', 'timed_out'}


def check_expression(label: str) -> tuple[str, str] | None:
    """Only explicit tokens carry executable meaning; free-form guidance is unsupported."""
    label = label.strip()
    if label in CHECKS:
        return label, ''
    name, separator, parameter = label.partition(':')
    if separator and name in {'tool_succeeded', 'transcript_contains'} and parameter.strip():
        return name, parameter.strip()
    return None


def evidence_kind(label: str) -> str | None:
    return EVIDENCE_ALIASES.get(label.strip().lower())


def _citation(source: str, path: str, text: str) -> dict:
    return {'source': source, 'path': path, 'text': text[:500]}


def evaluate_design(scenario: dict, payload: dict, transcript: str) -> dict:
    events = list(parse_action_trace(payload.get('action_trace')))
    final = payload.get('final_state')
    presence = {
        'transcript': bool(transcript.strip()), 'action_trace': bool(events),
        'final_state': isinstance(final, dict) and bool(final),
        'vcon': isinstance(payload.get('vcon'), dict) and bool(payload['vcon']),
    }
    presence['final_state_or_action_trace'] = presence['final_state'] or presence['action_trace']

    def presence_citations(kind):
        if kind == 'transcript' and presence[kind]:
            return [_citation('transcript', 'lines[0]', transcript.splitlines()[0])]
        if kind == 'final_state_or_action_trace':
            kind = 'final_state' if presence['final_state'] else 'action_trace'
        return [_citation(kind, '$', f'{kind} artifact supplied')] if presence.get(kind) else []

    requirements = []
    evidence_requirements = scenario.get('evidence_requirements', [])
    if isinstance(evidence_requirements, dict):
        evidence_requirements = evidence_requirements.get('required_artifacts', [])
    for index, label in enumerate(evidence_requirements):
        kind = evidence_kind(label)
        supplied = kind is not None and presence[kind]
        requirements.append({'id': f'evidence-{index}', 'label': label, 'supported': kind is not None,
            'status': 'pass' if supplied else 'insufficient_evidence',
            'reason': 'Required artifact supplied (presence check only).' if supplied else
                      'Unsupported evidence requirement; replace it with a documented token.' if kind is None else 'Required artifact is missing or empty.',
            'citations': presence_citations(kind) if supplied else []})

    checks = []
    for check in scenario.get('deterministic_checks', []):
        expression = check_expression(check.get('label', ''))
        result = {'id': check['id'], 'label': check.get('label') or check['id'], 'severity': check.get('severity', 'error'),
                  'supported': expression is not None, 'status': 'insufficient_evidence',
                  'reason': 'Unsupported check; use a documented check expression.', 'citations': []}
        if check.get('applicable') is False:
            result.update(status='pass', outcome='not_applicable', reason='The approved contract marks this check inapplicable.')
            checks.append(result)
            continue
        if check.get('kind') in {'event_any', 'event_order', 'final_state_equals', 'final_state_in', 'conditional_event'}:
            passed, observed = _evaluate_check(check, event_names=_event_type_names_from_action_trace(payload.get('action_trace')),
                                                final_state=final if isinstance(final, dict) else {})
            outcome = structured_outcome(check, passed, observed)
            result.update(supported=True, outcome=outcome,
                          status='pass' if outcome in {'pass', 'not_applicable'} else 'fail' if outcome == 'fail' else 'insufficient_evidence',
                          reason=('No triggering event in the recorded trace; unrecorded activity is not evaluated.' if outcome == 'not_applicable'
                                  else 'Recorded evidence satisfies the explicit check.' if passed
                                  else 'Recorded evidence contradicts the explicit check.' if outcome == 'fail'
                                  else 'The required observation is missing; execution cannot be verified.'),
                          citations=[_citation('final_state' if str(check['kind']).startswith('final_state') else 'action_trace',
                                                str(check.get('path') or '$'), str(observed))], basis='executable_evidence_check')
            checks.append(result)
            continue
        if expression:
            name, parameter = expression
            if name.endswith('_present'):
                kind = name.removesuffix('_present')
                result.update(status='pass' if presence[kind] else 'insufficient_evidence',
                              reason='Artifact supplied.' if presence[kind] else 'Artifact is missing or empty.',
                              citations=presence_citations(kind))
            elif name == 'final_state_complete':
                value = final.get('complete') if isinstance(final, dict) else None
                result.update(status='pass' if value is True else 'fail' if value is False else 'insufficient_evidence',
                              reason='Final state explicitly reports complete=true.' if value is True else
                                     'Final state explicitly reports complete=false.' if value is False else 'No boolean final_state.complete was recorded.',
                              citations=[_citation('final_state', '$.complete', str(value))] if isinstance(value, bool) else [])
            elif name == 'transcript_contains':
                matching = [(i, line) for i, line in enumerate(transcript.splitlines()) if parameter.casefold() in line.casefold()]
                result.update(status='pass' if matching else 'fail' if presence['transcript'] else 'insufficient_evidence',
                              reason='Literal text found (not semantic verification).' if matching else
                                     'Literal text absent from supplied transcript.' if presence['transcript'] else 'Transcript missing.',
                              citations=[_citation('transcript', f'lines[{i}]', line) for i, line in matching[:3]] or
                                        ([_citation('transcript', '$', transcript)] if presence['transcript'] else []))
            elif name == 'tool_succeeded':
                matches = [(i, event) for i, event in enumerate(events) if event.name == parameter]
                succeeded = any(str(event.status).strip().lower() in SUCCESS for _, event in matches)
                failed = any(str(event.status).strip().lower() in FAILURE for _, event in matches)
                cited_matches = [(i, event) for i, event in matches
                    if str(event.status).strip().lower() in (SUCCESS if succeeded else FAILURE)] if succeeded or failed else matches
                result.update(status='pass' if succeeded else 'fail' if failed else 'insufficient_evidence',
                              reason='Named tool has a successful invocation.' if succeeded else 'Named tool has an explicit failure.' if failed else 'No terminal invocation of the named tool was recorded.',
                              citations=[_citation('action_trace', f'events[{i}]', f'{event.name}: {event.status}') for i, event in cited_matches[:3]])
        result['outcome'] = 'not_observable' if result['status'] == 'insufficient_evidence' else result['status']
        result['basis'] = 'literal_text_only' if expression and expression[0] == 'transcript_contains' else 'executable_evidence_check'
        checks.append(result)

    event_names = _event_type_names_from_action_trace(payload.get('action_trace'))
    for forbidden in scenario.get('forbidden_event_types', []):
        if _normalize_event_name(forbidden) in event_names:
            checks.append({'id': 'forbidden-event:' + forbidden, 'label': forbidden, 'supported': True,
                           'status': 'fail', 'outcome': 'fail', 'basis': 'executable_evidence_check',
                           'reason': 'An explicitly forbidden event was recorded.',
                           'citations': [_citation('action_trace', '$', forbidden)]})
    behaviors = []
    focus = scenario.get('target_behavior_id')
    for rule in scenario.get('behaviors', []):
        if rule['id'] != focus:
            continue
        matching = []
        for i, event in enumerate(events):
            raw = event.raw if isinstance(event.raw, dict) else {}
            if (raw.get('behavior_id') == focus if 'behavior_id' in raw else event.name in {focus, f'{rule["label"]} [{focus}]'}):
                matching.append((i, event))
        # Seeing a forbidden action is a policy violation even without a successful
        # tool result; this is distinct from the tool_succeeded predicate above.
        observed = any(str(event.status).strip().lower() in SUCCESS | {'observed'} for _, event in matching)
        violation = rule['kind'] == 'forbidden' and observed
        behaviors.append({'id': focus, 'label': rule['label'], 'kind': rule['kind'],
            'status': 'fail' if violation else 'insufficient_evidence',
            'reason': 'An ID-linked forbidden action was observed.' if violation else
                      'An ID-linked required action was observed; semantic policy review is still required.' if observed else
                      'No conclusive ID-linked evidence; absence does not prove compliance.',
            'basis': 'structured_action_evidence_not_semantic',
            'citations': [_citation('action_trace', f'events[{i}]', f'{event.name}: {event.status}') for i, event in matching[:3]]})
    for item in requirements:
        item['outcome'] = 'not_observable' if item['status'] == 'insufficient_evidence' else item['status']
    for item in behaviors:
        item['outcome'] = 'not_observable' if item['status'] == 'insufficient_evidence' else item['status']
    blocking = [item for item in [*requirements, *checks] if item['status'] != 'pass']
    failed = any(item['status'] == 'fail' for item in checks)
    return {'behavior_results': behaviors, 'programmatic_check_results': checks,
            'evidence_requirement_results': requirements,
            'design_enforcement': {'blocked': bool(blocking), 'failed': failed, 'blocking_ids': [item['id'] for item in blocking]}}
