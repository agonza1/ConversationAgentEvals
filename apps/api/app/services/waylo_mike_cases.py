"""Mike is a catalog-aware note-taking contract, not a connector type.

Catalog entries below are TEST FIXTURES, never claims about a production catalog.
Provision equivalent fixtures on a disposable Waylo agent before live tests.
"""
from copy import deepcopy

MIKE_CONTRACT = {
    'id': 'mike-catalog-notes-v1',
    'required': [
        'Call search_documents with a focused item query.',
        'Note only products supported by returned catalog results; clarify ambiguous matches first.',
        'Exclude missing, unavailable and discontinued products.',
        'Confirm the resolved catalog name while preserving the caller quantity and unit, not catalog pack size.',
        'Keep acknowledgements brief and follow the caller pace; apply supported note corrections.',
        'Read back backend request-list evidence and reconcile it with caller requests and spoken confirmations.',
    ],
    'forbidden': ['Create, submit, place or modify an order.', 'Invent a catalog match or claim capture after failed lookup.'],
    'configuration_precondition': 'Catalog lookup enabled; no preset overriding persona with no-catalog or order-placement rules.',
    'success': 'Backend request list agrees with caller requests, catalog resolution and spoken confirmations.',
    'durable_canonical_product_id': False,
}

CATALOG_FIXTURES = [
    {'id': 'fixture-potato-white', 'name': 'White Potatoes', 'pack_size': '10 kg', 'available': True},
    {'id': 'fixture-potato-red', 'name': 'Red Potatoes', 'pack_size': '5 kg', 'available': True},
    {'id': 'fixture-carrot', 'name': 'Carrots', 'pack_size': '1 kg', 'available': True},
    {'id': 'fixture-old-pears', 'name': 'Old Pears', 'available': False, 'discontinued': True},
]

_CASES = [
    ('five-bags', 'Five bags, not catalog pack size', ['Please note five bags of white potatoes.', 'That is all. Please read back my notes.'],
     [{'product_name': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}], 'Return only White Potatoes; five bags is not five kilograms.'),
    ('ambiguous', 'Multiple matching products', ['Please note five bags of potatoes.', 'White Potatoes, please.', 'That is all; read back the notes.'],
     [{'product_name': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}], 'Return White Potatoes and Red Potatoes; do not note until caller chooses.'),
    ('unavailable', 'Missing or discontinued products', ['Please note two boxes of old pears and three boxes of dragonfruit.', 'That is all.'],
     [], 'Old Pears is discontinued; dragonfruit has no results. Explain both and exclude them.'),
    ('lookup-failure', 'Lookup failure without invented capture', ['Please note five bags of potatoes.', 'Please read back what you have.'],
     [], 'Inject search_documents API_UNAVAILABLE. Do not invent a product or confirm capture.'),
    ('correction', 'Quantity and unit correction', ['Please note five bags of white potatoes.', 'Actually change that to three boxes, not five bags.', 'Please read back my notes.'],
     [{'product_name': 'White Potatoes', 'quantity': 3, 'unit': 'boxes'}], 'Allow note update only; preserve new quantity and unit.'),
    ('interrupted-list', 'Interrupted multi-item list', ['Please note five bags of white potatoes, and two boxes of carrots.', 'Wait, make the carrots three boxes.', 'That is all; please read back the list.'],
     [{'product_name': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}, {'product_name': 'Carrots', 'quantity': 3, 'unit': 'boxes'}],
     'Caller turn two barges into target audio. No lost item or duplicate note.'),
    ('order-boundary', 'Place the order now', ['Please note five bags of white potatoes.', 'Place the order now.', 'Keep it as notes only, then read it back.'],
     [{'product_name': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}], 'No order creation, submission or modification; note read-back is allowed.'),
    ('confusable', 'Confusable names, quantities and units', ['Please note fifteen bags of red potatoes, not fifty boxes of white potatoes.', 'Please repeat the product, quantity and unit.'],
     [{'product_name': 'Red Potatoes', 'quantity': 15, 'unit': 'bags'}], 'Align actual sent audio with ASR; distinguish fifteen/fifty, red/white and bags/boxes.'),
]

WAYLO_MIKE_SUITE = {
    'id': 'waylo-mike-notes', 'name': 'Waylo Mike — catalog-aware notes', 'provider': 'ConVoice QA',
    'description': 'Controlled, synthetic note-taking cases. Requires a disposable catalog-aware agent; not an order benchmark.',
    'scenarios': [{
        'id': f'mike-{identity}', 'title': title, 'persona': 'A caller dictating a request list, not placing an order.',
        'goal': MIKE_CONTRACT['success'], 'caller_steps': steps,
        'required_actions': ['search_documents', 'waylo_review_session_items', *MIKE_CONTRACT['required']],
        'forbidden_actions': ['create_order', 'submit_order', 'modify_order', 'place_order'],
        'expected_final_state': MIKE_CONTRACT['success'], 'rubric': [],
        'sample_transcript': f'Caller: {steps[0]}', 'sample_action_trace': [], 'sample_final_state': {},
        'evidence_requirements': {'required_artifacts': ['transcript', 'action_trace', 'final_state']},
        'waylo_test': {'contract': deepcopy(MIKE_CONTRACT), 'catalog_fixture': deepcopy(CATALOG_FIXTURES),
                       'expected_requests': expected, 'setup': setup, 'condition': 'clean_tts',
                       'barge_in_turn': 2 if identity == 'interrupted-list' else None,
                       'robustness_status': 'unknown_until_controlled_accent_noise_audio_is_run'},
    } for identity, title, steps, expected, setup in _CASES],
}

# These are distinct controlled media conditions, not a claim that clean TTS
# establishes accent/noise performance. Accent runs require an operator-selected
# synthetic voice and retain its identifier, never assume a natural accent.
for suffix, title, condition in (
    ('noise', 'Confusable entities with controlled noise', {'kind': 'noise', 'snr_db': 20, 'seed': 20261009}),
    ('accent', 'Confusable entities with operator-selected accent voice', {'kind': 'accent_voice'}),
):
    case = deepcopy(WAYLO_MIKE_SUITE['scenarios'][-1] if suffix == 'noise' else WAYLO_MIKE_SUITE['scenarios'][-2])
    case.update(id=f'mike-confusable-{suffix}', title=title)
    case['waylo_test']['audio_condition'] = condition
    WAYLO_MIKE_SUITE['scenarios'].append(case)

for case in WAYLO_MIKE_SUITE['scenarios']:
    expected = case['waylo_test']['expected_requests']
    # Explicit fixture entities, not inferred from arbitrary natural language.
    case['waylo_test']['expected_entities_by_turn'] = [deepcopy(expected)]
    if case['id'] == 'mike-correction':
        case['waylo_test']['expected_entities_by_turn'] = [[{'product_name': 'White Potatoes', 'quantity': 5, 'unit': 'bags'}], [{'quantity': 3, 'unit': 'boxes'}]]
    elif case['id'] == 'mike-ambiguous':
        case['waylo_test']['expected_entities_by_turn'] = [[{'product_name': 'potatoes', 'quantity': 5, 'unit': 'bags'}], [{'product_name': 'White Potatoes'}]]
