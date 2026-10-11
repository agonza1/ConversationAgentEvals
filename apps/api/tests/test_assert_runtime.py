from pathlib import Path
import sys

import pytest

from app.integrations import assert_runtime


def test_assert_runtime_is_pinned_to_the_only_supported_version():
    assert assert_runtime.EXPECTED_ASSERT_VERSION == '0.3.0'
    assert assert_runtime.ensure_expected_version() == '0.3.0'


def test_assert_runtime_rejects_any_other_installed_version(monkeypatch):
    monkeypatch.setattr(assert_runtime.importlib.metadata, 'version', lambda package: '0.2.0')

    with pytest.raises(assert_runtime.AssertRuntimeUnavailable, match='requires assert-ai==0.3.0'):
        assert_runtime.ensure_expected_version()


def test_assert_cli_uses_the_same_interpreter_as_the_validated_package():
    assert assert_runtime.cli_executable() == sys.executable


def test_native_test_set_runtime_rejects_an_unpinned_package(monkeypatch):
    monkeypatch.setattr(assert_runtime.importlib.metadata, 'version', lambda package: '0.2.0')

    with pytest.raises(assert_runtime.AssertRuntimeUnavailable, match='requires assert-ai==0.3.0'):
        assert_runtime.native_test_set_runtime()


def test_judge_score_contract_is_derived_from_the_configured_dimensions():
    contract = assert_runtime.judge_score_contract({
        'resolution_quality': {
            'description': 'How completely was the request resolved?',
            'rubric': 'low = unresolved; high = resolved',
            'allow_not_applicable': True,
            'scale': {
                'type': 'ordinal',
                'values': {'low': 'Unresolved', 'high': 'Resolved'},
            },
        },
    })

    assert contract['score_keys'] == [
        'policy_violation',
        'overrefusal',
        'resolution_quality',
    ]
    assert contract['not_applicable_score_keys'] == ['resolution_quality']
    assert contract['dimension_scales'] == {
        'resolution_quality': {
            'type': 'ordinal',
            'values': [
                {'value': 'low', 'label': 'Unresolved'},
                {'value': 'high', 'label': 'Resolved'},
            ],
        },
    }


def test_assert_ai_imports_are_centralized_in_the_runtime_boundary():
    app_root = Path(__file__).resolve().parents[1] / 'app'
    integration = app_root / 'integrations' / 'assert_runtime.py'
    offenders = []
    for path in app_root.rglob('*.py'):
        if path == integration:
            continue
        source = path.read_text(encoding='utf-8')
        if 'from assert_ai' in source or 'import assert_ai' in source:
            offenders.append(str(path.relative_to(app_root)))

    assert offenders == []
