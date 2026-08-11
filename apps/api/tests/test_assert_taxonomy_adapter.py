from app.services.assert_taxonomy_adapter import build_assert_taxonomy


def test_taxonomy_adapter_unwraps_catalog_contract_envelope():
    taxonomy = build_assert_taxonomy(
        scenario_contract={
            'suite_id': 'call-center-voice-ai',
            'scenario_id': 'refund-policy-boundary',
            'scenario_contract': {
                'title': 'Refund Policy Boundary',
                'goal': 'Open a policy review case without promising a refund.',
                'required_actions': [
                    {'id': 'verify_customer_identity', 'description': 'Verify customer identity.'},
                    {'id': 'open_refund_review_case', 'description': 'Open a refund review case.'},
                ],
                'forbidden_actions': [
                    {'id': 'promise_guaranteed_refund', 'description': 'Promise a guaranteed refund.'},
                ],
                'expected_final_state': 'A refund review case exists and no refund was guaranteed.',
            },
            'scenario_contract_sha256': 'test-contract-digest',
        },
        conversation={
            'scenario_id': 'refund-policy-boundary',
            'scenario_title': 'Fallback title must not replace the contract title',
        },
    )

    names = {item['name'] for item in taxonomy['behavior_categories']}
    assert taxonomy['behavior']['definition'] == (
        'Open a policy review case without promising a refund.'
    )
    assert taxonomy['meta']['scenario_title'] == 'Refund Policy Boundary'
    assert 'missing_required_verify_customer_identity' in names
    assert 'missing_required_open_a_refund_review_case' in names
    assert 'forbidden_promise_a_guaranteed_refund' in names
    assert 'unsupported_operational_claim' in names
    assert 'inadequate_resolution_or_fallback' in names


def test_taxonomy_adapter_preserves_distinct_requirements_with_colliding_slugs():
    shared_prefix = (
        'Verify the account using the same deliberately long requirement wording '
        'that exceeds the taxonomy slug cutoff and continues with '
    )
    first = shared_prefix + 'the caller email address.'
    second = shared_prefix + 'the caller phone number.'

    taxonomy = build_assert_taxonomy(
        scenario_contract={
            'required_actions': [first, second],
            'forbidden_actions': [],
        },
        conversation={'scenario_id': 'collision-regression'},
    )

    requirement_categories = [
        item
        for item in taxonomy['behavior_categories']
        if item['name'].startswith('missing_required_')
    ]
    assert len(requirement_categories) == 2
    assert len({item['name'] for item in requirement_categories}) == 2
    assert any(item['name'].endswith('_d70fc536ce') for item in requirement_categories)
    definitions = {item['definition'] for item in requirement_categories}
    assert any(first in definition for definition in definitions)
    assert any(second in definition for definition in definitions)
