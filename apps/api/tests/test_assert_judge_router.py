from app.main import app
from app.routes import assert_judge


JUDGE_PATH = '/api/assert/runs/{execution_run_id}/conversations/{conversation_id}/judge'


def _route_paths(router) -> set[str]:
    return {route.path for route in router.routes}


def test_assert_judge_is_the_only_assert_runtime_route():
    assert JUDGE_PATH in _route_paths(assert_judge.router)
    assert '/api/assert/runs' not in _route_paths(assert_judge.router)


def test_assert_judge_route_is_always_mounted_on_the_product_app():
    app_paths = _route_paths(app.router)

    assert JUDGE_PATH in app_paths
    assert sum(path == JUDGE_PATH for path in app_paths) == 1

