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
    assert 'missing_required_open_refund_review_case' in names
    assert 'forbidden_promise_a_guaranteed_refund' in names
    assert 'unsupported_operational_claim' in names
    assert 'inadequate_resolution_or_fallback' in names
