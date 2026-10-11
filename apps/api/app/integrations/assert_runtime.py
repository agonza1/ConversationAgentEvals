from __future__ import annotations

import importlib.metadata
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assert_ai.config import load_runtime_context, parse_judge_dimensions
from assert_ai.core.judge import (
    BUILT_IN_DIMENSIONS,
    infer_judge_status,
    is_not_applicable_dimension,
    is_valid_confidence_label,
    is_valid_event_flag,
)
from assert_ai.core.transcript import (
    AddMessageEdit,
    Message,
    ToolCallEdit,
    Transcript,
    TranscriptEvent,
    TranscriptMetadata,
)
from assert_ai.library.loader import discover, load_preset


EXPECTED_ASSERT_VERSION = '0.3.0'


class AssertRuntimeUnavailable(RuntimeError):
    pass


def installed_version() -> str:
    try:
        return importlib.metadata.version('assert-ai')
    except importlib.metadata.PackageNotFoundError as exc:
        raise AssertRuntimeUnavailable(
            f'assert-ai=={EXPECTED_ASSERT_VERSION} is required but is not installed.'
        ) from exc


def ensure_expected_version() -> str:
    version = installed_version()
    if version != EXPECTED_ASSERT_VERSION:
        raise AssertRuntimeUnavailable(
            f'CAE requires assert-ai=={EXPECTED_ASSERT_VERSION}; found {version}. '
            'Install the pinned API requirements before starting CAE.'
        )
    return version


def cli_executable() -> str:
    ensure_expected_version()
    return sys.executable


def native_test_set_runtime() -> SimpleNamespace:
    """Expose the pinned native generation stage through CAE's runtime boundary."""
    ensure_expected_version()
    from assert_ai.core.io import normalize_test_case_rows, write_jsonl
    from assert_ai.stages.test_set import run_test_set

    return SimpleNamespace(
        run_test_set=run_test_set,
        normalize_test_case_rows=normalize_test_case_rows,
        write_jsonl=write_jsonl,
    )


def validate_config(config: dict[str, Any], *, config_path: Path) -> None:
    """Validate one CAE-compiled config with the sole supported ASSERT runtime."""
    ensure_expected_version()
    stage_modules = {
        'systematize': SimpleNamespace(SCOPE='suite'),
        'test_set': SimpleNamespace(SCOPE='suite'),
        'inference': SimpleNamespace(SCOPE='run'),
        'judge': SimpleNamespace(SCOPE='run'),
    }
    load_runtime_context(
        deepcopy(config),
        config_path,
        stage_modules=stage_modules,
    )


def judge_score_contract(
    dimensions: dict[str, Any],
    *,
    disabled_dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """Build trusted score metadata from the ASSERT config CAE launches."""
    ensure_expected_version()
    parsed_dimensions = parse_judge_dimensions(
        deepcopy(dimensions),
        field_name='pipeline.judge.dimensions',
    )
    disabled = set(disabled_dimensions or [])
    builtin_names = {str(dimension['name']) for dimension in BUILT_IN_DIMENSIONS}
    unknown_disabled = sorted(disabled - builtin_names)
    if unknown_disabled:
        raise ValueError(
            'Unknown disabled ASSERT judge dimensions: ' + ', '.join(unknown_disabled)
        )
    configured_by_name = {
        str(dimension['name']): deepcopy(dimension)
        for dimension in BUILT_IN_DIMENSIONS
        if dimension['name'] not in disabled
    }
    for dimension in parsed_dimensions:
        configured_by_name[str(dimension['name'])] = dimension
    configured = list(configured_by_name.values())
    return {
        'score_keys': [dimension['name'] for dimension in configured],
        'not_applicable_score_keys': [
            dimension['name']
            for dimension in configured
            if dimension.get('allow_not_applicable') is True
        ],
        'dimension_scales': {
            dimension['name']: deepcopy(dimension['scale'])
            for dimension in configured
            if dimension.get('scale')
        },
    }


def behavior_presets() -> list[dict[str, Any]]:
    """Return ASSERT 0.3 behavior presets without copying the library into CAE."""
    ensure_expected_version()
    return [
        {key: value for key, value in item.items() if key != 'path'}
        for item in discover(kind='behavior')
    ]


def behavior_preset(name: str) -> dict[str, Any]:
    ensure_expected_version()
    return dict(load_preset('behavior', name))


def scenario_presets() -> list[dict[str, Any]]:
    """Application context from ASSERT; these are not executable CAE cases."""
    ensure_expected_version()
    return [{key: value for key, value in item.items() if key != 'path'} for item in discover(kind='scenario')]


def scenario_preset(name: str) -> dict[str, Any]:
    ensure_expected_version()
    return dict(load_preset('scenario', name))


def judge_presets() -> list[dict[str, Any]]:
    """Return ASSERT 0.3 judge presets without copying the library into CAE."""
    ensure_expected_version()
    return [
        {key: value for key, value in item.items() if key != 'path'}
        for item in discover(kind='judge_preset')
    ]


def judge_preset(name: str) -> dict[str, Any]:
    ensure_expected_version()
    return dict(load_preset('judge_preset', name))


__all__ = [
    'AddMessageEdit',
    'AssertRuntimeUnavailable',
    'BUILT_IN_DIMENSIONS',
    'EXPECTED_ASSERT_VERSION',
    'Message',
    'ToolCallEdit',
    'Transcript',
    'TranscriptEvent',
    'TranscriptMetadata',
    'behavior_preset',
    'behavior_presets',
    'cli_executable',
    'ensure_expected_version',
    'infer_judge_status',
    'installed_version',
    'is_not_applicable_dimension',
    'is_valid_confidence_label',
    'is_valid_event_flag',
    'judge_score_contract',
    'judge_preset',
    'judge_presets',
    'native_test_set_runtime',
    'scenario_preset',
    'scenario_presets',
    'validate_config',
]
