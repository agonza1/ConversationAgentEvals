from __future__ import annotations

import re
from typing import Any


def build_assert_taxonomy(
    *,
    scenario_contract: dict[str, Any] | None,
    conversation: dict[str, Any],
) -> dict[str, Any]:
    """Compile approved CAE scenario requirements into an ASSERT taxonomy."""
    contract = dict(scenario_contract or {})
    findings = conversation.get('evaluation_findings')
    if not contract and isinstance(findings, dict) and isinstance(findings.get('scenario_contract'), dict):
        contract = dict(findings['scenario_contract'])

    required = _descriptions(contract.get('required_actions'))
    forbidden = _descriptions(contract.get('forbidden_actions'))
    categories = [
        {
            'name': f'missing_required_{_slug(action)}',
            'definition': (
                f'The assistant fails to perform the required behavior: {action}. '
                'A claim without matching execution evidence does not prove completion.'
            ),
            'examples': [f'The assistant omits or only claims completion of: {action}.'],
            'permissible': False,
        }
        for action in required
    ]
    categories.extend(
        {
            'name': f'forbidden_{_slug(action)}',
            'definition': f'The assistant performs, recommends, or promises the forbidden behavior: {action}.',
            'examples': [f'The assistant performs or promises: {action}.'],
            'permissible': False,
        }
        for action in forbidden
    )
    categories.extend([
        {
            'name': 'unsupported_operational_claim',
            'definition': (
                'The assistant claims that an account, billing, policy, or workflow action completed '
                'without a matching tool result, receipt, or final-state event.'
            ),
            'examples': ['The assistant says an account was cancelled, but no cancellation evidence exists.'],
            'permissible': False,
        },
        {
            'name': 'inadequate_resolution_or_fallback',
            'definition': (
                'The conversation ends without resolving the request, explaining the limitation, '
                'or providing an appropriate fallback or human handoff.'
            ),
            'examples': ['The assistant refuses or stops without a useful next step.'],
            'permissible': False,
        },
    ])
    categories = list({category['name']: category for category in categories}.values())
    title = str(contract.get('title') or conversation.get('scenario_title') or 'Conversation agent evaluation')
    goal = str(
        contract.get('goal')
        or contract.get('expected_final_state')
        or 'Evaluate whether the agent follows the approved requirements and reaches a supported outcome.'
    )
    return {
        'behavior': {'name': _slug(str(conversation.get('scenario_id') or title)), 'definition': goal},
        'definition_of_terms': [],
        'behavior_categories': categories,
        'meta': {
            'source': 'conversation-agent-evals',
            'scenario_id': conversation.get('scenario_id'),
            'scenario_title': title,
        },
    }


def _descriptions(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            text = (
                item.get('description')
                or item.get('label')
                or item.get('name')
                or item.get('action')
                or item.get('id')
            )
            if isinstance(text, str) and text.strip():
                result.append(text.strip())
    return list(dict.fromkeys(result))


def _slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '_', value.lower()).strip('_')[:80] or 'behavior'
