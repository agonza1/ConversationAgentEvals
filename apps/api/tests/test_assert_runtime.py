from pathlib import Path

import pytest

from app.integrations import assert_runtime


def test_assert_runtime_is_pinned_to_the_only_supported_version():
    assert assert_runtime.EXPECTED_ASSERT_VERSION == '0.3.0'
    assert assert_runtime.ensure_expected_version() == '0.3.0'


def test_assert_runtime_rejects_any_other_installed_version(monkeypatch):
    monkeypatch.setattr(assert_runtime.importlib.metadata, 'version', lambda package: '0.2.0')

    with pytest.raises(assert_runtime.AssertRuntimeUnavailable, match='requires assert-ai==0.3.0'):
        assert_runtime.ensure_expected_version()


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

