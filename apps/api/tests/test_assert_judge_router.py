from app.config import local_assert_sidecar_enabled
from app.main import app
from app.routes import assert_sidecar


JUDGE_PATH = '/api/assert/runs/{execution_run_id}/conversations/{conversation_id}/judge'
LOCAL_CREATE_PATH = '/api/assert/runs'


def _route_paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_assert_judge_router_is_separate_from_local_sidecar_lifecycle():
    assert JUDGE_PATH in _route_paths(assert_sidecar.judge_router)
    assert JUDGE_PATH not in _route_paths(assert_sidecar.router)
    assert LOCAL_CREATE_PATH in _route_paths(assert_sidecar.router)
    assert LOCAL_CREATE_PATH not in _route_paths(assert_sidecar.judge_router)


def test_assert_judge_route_is_always_mounted_on_the_product_app():
    app_paths = _route_paths(app.router)

    assert JUDGE_PATH in app_paths
    assert sum(path == JUDGE_PATH for path in app_paths) == 1


def test_product_judge_remains_mounted_when_local_sidecar_policy_is_disabled(
    monkeypatch,
):
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.delenv('K_SERVICE', raising=False)
    assert local_assert_sidecar_enabled() is False
    assert JUDGE_PATH in _route_paths(app.router)

    monkeypatch.setenv('K_SERVICE', 'cae-production')
    assert local_assert_sidecar_enabled() is False
    assert JUDGE_PATH in _route_paths(app.router)
