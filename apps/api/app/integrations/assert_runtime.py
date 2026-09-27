from __future__ import annotations

import importlib.metadata
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assert_ai.config import load_runtime_context
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
    executable = shutil.which('assert-ai')
    if not executable:
        raise AssertRuntimeUnavailable(
            f'The assert-ai {EXPECTED_ASSERT_VERSION} command is unavailable.'
        )
    return executable


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
    'judge_preset',
    'judge_presets',
    'validate_config',
]
