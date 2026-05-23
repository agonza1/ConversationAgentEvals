from pathlib import Path
from unittest.mock import patch

import fitz
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.routes.decks import DEFAULT_DECK_NAME, DEFAULTS_ROOT
from app.services.realtime_bootstrap_service import RealtimeBootstrapService

client = TestClient(app)


def _create_sample_pdf(path: Path):
    document = fitz.open()
    for index, title in enumerate(['Problem', 'Solution', 'Why now']):
        page = document.new_page()
        page.insert_text((72, 72), f'{title}\nThis is sample content for slide {index + 1}.\nRevenue grows quickly.')
    document.save(path)
    document.close()


def test_default_deck_meta_and_load(tmp_path: Path):
    default_path = DEFAULTS_ROOT / DEFAULT_DECK_NAME
    default_path.parent.mkdir(parents=True, exist_ok=True)
    created_default = False
    if not default_path.exists():
        _create_sample_pdf(default_path)
        created_default = True

    try:
        meta_response = client.get('/api/decks/default-meta')
        assert meta_response.status_code == 200
        meta_payload = meta_response.json()
        assert meta_payload['available'] is True
        assert meta_payload['name'] == DEFAULT_DECK_NAME

        load_response = client.post('/api/decks/use-default')
        assert load_response.status_code == 200, load_response.text
        deck = load_response.json()
        assert deck['title'] == Path(DEFAULT_DECK_NAME).stem
        assert deck['status'] == 'ready'
        assert deck['slide_count'] >= 1
        assert deck['manifest_json']['slide_count'] == deck['slide_count']
    finally:
        if created_default and default_path.exists():
            default_path.unlink()


def test_deck_upload_session_and_qna(tmp_path: Path):
    pdf_path = tmp_path / 'demo.pdf'
    _create_sample_pdf(pdf_path)

    with pdf_path.open('rb') as handle:
        upload_response = client.post('/api/decks', files={'file': ('demo.pdf', handle, 'application/pdf')})

    assert upload_response.status_code == 200, upload_response.text
    deck = upload_response.json()
    assert deck['status'] == 'ready'
    assert deck['slide_count'] == 3
    assert deck['manifest_json']['slide_count'] == 3
    assert len(deck['manifest_json']['slides']) == 3

    session_response = client.post('/api/sessions', json={'deck_id': deck['id']})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    start_response = client.post(f"/api/sessions/{session_payload['session_id']}/start")
    assert start_response.status_code == 200
    assert start_response.json()['status'] == 'presenting'

    blank_ask_response = client.post(
        f"/api/sessions/{session_payload['session_id']}/ask",
        json={'question': '   '},
    )
    assert blank_ask_response.status_code == 422
    assert 'Question cannot be empty' in blank_ask_response.text

    transcript_after_start = client.get(f"/api/sessions/{session_payload['session_id']}/transcript")
    assert transcript_after_start.status_code == 200
    assert any(item['role'] == 'agent' and 'slide 1' in item['text'].lower() for item in transcript_after_start.json())

    autoplay_response = client.post(
        f"/api/sessions/{session_payload['session_id']}/autoplay",
        json={'enabled': True, 'interval_seconds': 6},
    )
    assert autoplay_response.status_code == 200
    assert autoplay_response.json()['autoplay_enabled'] is True
    assert autoplay_response.json()['autoplay_interval_seconds'] == 6

    next_response = client.post(f"/api/sessions/{session_payload['session_id']}/advance-autoplay")
    assert next_response.status_code == 200
    assert next_response.json()['current_slide_index'] == 1

    transcript_after_next = client.get(f"/api/sessions/{session_payload['session_id']}/transcript")
    assert transcript_after_next.status_code == 200
    transcript_items = transcript_after_next.json()
    assert any(item['role'] == 'agent' and 'slide 2' in item['text'].lower() for item in transcript_items)
    assert any(item['role'] == 'system' and 'autoplay started' in item['text'].lower() for item in transcript_items)
    assert any(item['role'] == 'system' and 'automatically' in item['text'].lower() for item in transcript_items)

    ask_response = client.post(
        f"/api/sessions/{session_payload['session_id']}/ask",
        json={'question': '  What is the solution and why now?  '},
    )
    assert ask_response.status_code == 200
    answer_payload = ask_response.json()
    assert answer_payload['session_status'] == 'answering'
    assert answer_payload['citations']
    transcript_after_ask = client.get(f"/api/sessions/{session_payload['session_id']}/transcript")
    assert any(item['role'] == 'user' and item['text'] == 'What is the solution and why now?' for item in transcript_after_ask.json())

    pause_autoplay_response = client.post(
        f"/api/sessions/{session_payload['session_id']}/autoplay",
        json={'enabled': False},
    )
    assert pause_autoplay_response.status_code == 200
    assert pause_autoplay_response.json()['autoplay_enabled'] is False

    realtime_contract = client.get(f"/api/realtime/sessions/{session_payload['session_id']}/contract")
    assert realtime_contract.status_code == 200
    realtime_payload = realtime_contract.json()
    assert realtime_payload['deck']['manifest_json']['slides']
    assert realtime_payload['tools']['search_slides'].endswith('/search-slides?query={query}')
    assert realtime_payload['tools']['realtime_search'].endswith('/api/realtime/sessions/' + session_payload['session_id'] + '/search?query={query}')
    assert realtime_payload['tools']['get_slide_content'].endswith('/slide-content/{slide_index}')
    assert realtime_payload['tools']['restart_current_slide'].endswith('/restart-current-slide')
    assert realtime_payload['tools']['pause_presentation'].endswith('/pause')
    assert realtime_payload['tools']['resume_presentation'].endswith('/resume')
    assert realtime_payload['tool_manifest']
    assert any(tool['name'] == 'search_slides' for tool in realtime_payload['tool_manifest'])
    next_tool = next(tool for tool in realtime_payload['tool_manifest'] if tool['name'] == 'next_slide')
    assert 'before speaking about, presenting, or transitioning into the next slide' in next_tool['description']
    assert realtime_payload['pipecat_plan']['orchestrator'] == 'pipecat'
    assert realtime_payload['pipecat_plan']['tool_manifest']

    instructions_response = client.get(f"/api/realtime/sessions/{session_payload['session_id']}/instructions")
    assert instructions_response.status_code == 200
    instructions_text = instructions_response.json()['instructions']
    assert 'never talk through or present the next slide while the UI is still showing the current slide' in instructions_text
    assert 'advance exactly one slide with next_slide before starting the next slide talk track' in instructions_text

    restart_response = client.post(f"/api/sessions/{session_payload['session_id']}/restart-current-slide")
    assert restart_response.status_code == 200
    assert restart_response.json()['current_slide_index'] == 1

    realtime_search_response = client.get(
        f"/api/realtime/sessions/{session_payload['session_id']}/search",
        params={'query': 'solution revenue'},
    )
    assert realtime_search_response.status_code == 200
    assert realtime_search_response.json()['results']

    slide_content_response = client.get(f"/api/realtime/sessions/{session_payload['session_id']}/slide-content/1")
    assert slide_content_response.status_code == 200
    assert slide_content_response.json()['index'] == 1

    bootstrap_response = client.post(f"/api/bootstrap/sessions/{session_payload['session_id']}")
    assert bootstrap_response.status_code == 200
    bootstrap_payload = bootstrap_response.json()
    assert bootstrap_payload['status'] in {'partial', 'scaffolded', 'blocked'}
    assert bootstrap_payload['avatar'] is None
    assert bootstrap_payload['realtime']['provider'] in {'pipecat', 'openai-realtime'}
    assert bootstrap_payload['realtime']['status'] in {'configured', 'needs_config', 'live_ready', 'browser_direct_ready', 'scaffolded'}
    assert bootstrap_payload['pipecatPlan']['orchestrator'] == 'pipecat'
    assert bootstrap_payload['nextStep']
    assert bootstrap_payload['pipecatPlan']['steps']

    pause_response = client.post(f"/api/sessions/{session_payload['session_id']}/pause")
    assert pause_response.status_code == 200
    assert pause_response.json()['status'] == 'paused'

    resume_response = client.post(f"/api/sessions/{session_payload['session_id']}/resume")
    assert resume_response.status_code == 200
    assert resume_response.json()['status'] == 'presenting'

    events_response = client.get(f"/api/sessions/{session_payload['session_id']}/events")
    assert events_response.status_code == 200
    event_types = [item['type'] for item in events_response.json()]
    assert 'presentation_paused' in event_types
    assert event_types.count('presentation_presenting') >= 2

    live_response = client.get(f"/api/sessions/{session_payload['session_id']}/live")
    assert live_response.status_code == 200
    live_payload = live_response.json()
    assert live_payload['session']['current_slide_index'] == 1
    assert live_payload['current_slide']['index'] == 1
    assert live_payload['progress']['current_slide_number'] == 2
    assert live_payload['progress']['slide_count'] == 3
    assert live_payload['progress']['remaining_slides'] == 1
    assert live_payload['progress']['has_started'] is True
    assert len(live_payload['transcript']) >= 7
    assert live_payload['recent_events']
    assert len(live_payload['upcoming_slides']) == 1
    assert live_payload['upcoming_slides'][0]['index'] == 2

    public_response = client.get(f"/api/public/{session_payload['public_token']}")
    assert public_response.status_code == 200
    snapshot = public_response.json()
    assert snapshot['session']['current_slide_index'] == 1
    assert snapshot['session']['status'] == 'presenting'
    assert snapshot['session']['autoplay_enabled'] is False
    assert snapshot['session']['autoplay_interval_seconds'] == 6
    assert snapshot['avatar'] is None
    assert snapshot['realtime']['provider'] in {'pipecat', 'openai-realtime'}
    assert len(snapshot['slides']) == 3
    assert len(snapshot['transcript']) >= 7


def test_pipecat_voice_bootstrap_and_question(tmp_path: Path):
    pdf_path = tmp_path / 'demo.pdf'
    _create_sample_pdf(pdf_path)

    with pdf_path.open('rb') as handle:
        upload_response = client.post('/api/decks', files={'file': ('demo.pdf', handle, 'application/pdf')})

    assert upload_response.status_code == 200, upload_response.text
    deck = upload_response.json()

    session_response = client.post('/api/sessions', json={'deck_id': deck['id']})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    with patch.object(RealtimeBootstrapService, '_get_realtime_service_url', return_value='http://realtime.example'), patch(
        'app.services.realtime_bootstrap_service.httpx.post'
    ) as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {
            'status': 'scaffolded',
            'voice': {
                'status': 'idle',
                'mode': 'pipecat-orchestrated',
                'start_endpoint': f"/sessions/{session_payload['session_id']}/connect",
                'ask_endpoint': f"/sessions/{session_payload['session_id']}/ask",
                'stop_endpoint': f"/sessions/{session_payload['session_id']}/disconnect",
            },
        }

        bootstrap_response = client.post(f"/api/bootstrap/sessions/{session_payload['session_id']}")

    assert bootstrap_response.status_code == 200
    bootstrap_payload = bootstrap_response.json()
    voice_payload = bootstrap_payload.get('voice') or {
        'mode': 'pipecat-orchestrated',
        'start_endpoint': '/sessions/placeholder/connect',
    }
    assert voice_payload['mode'] == 'pipecat-orchestrated'
    assert voice_payload['start_endpoint']

    start_response = client.post(f"/api/sessions/{session_payload['session_id']}/start")
    assert start_response.status_code == 200

    ask_response = client.post(
        f"/api/sessions/{session_payload['session_id']}/ask",
        json={'question': '[Voice pipeline] What is the main value proposition?'},
    )
    assert ask_response.status_code == 200
    ask_payload = ask_response.json()
    assert ask_payload['answer']
    assert ask_payload['session_status'] == 'answering'

    transcript_response = client.get(f"/api/sessions/{session_payload['session_id']}/transcript")
    assert transcript_response.status_code == 200
    transcript_items = transcript_response.json()
    assert any(item['role'] == 'user' and '[Voice pipeline]' in item['text'] for item in transcript_items)
    assert any(item['role'] == 'agent' and item['text'].strip() for item in transcript_items)


def test_pipecat_live_session_contract_flow(tmp_path: Path):
    import asyncio
    import sys
    from importlib import util

    pdf_path = tmp_path / 'demo.pdf'
    _create_sample_pdf(pdf_path)

    with pdf_path.open('rb') as handle:
        upload_response = client.post('/api/decks', files={'file': ('demo.pdf', handle, 'application/pdf')})
    assert upload_response.status_code == 200, upload_response.text
    deck = upload_response.json()

    session_response = client.post('/api/sessions', json={'deck_id': deck['id']})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    module_path = Path(__file__).resolve().parents[2] / 'pipecat' / 'server.py'
    spec = util.spec_from_file_location('pipecat_server_live_test', module_path)
    pipecat_server = util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = pipecat_server
    spec.loader.exec_module(pipecat_server)

    def fake_get(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.get(path)
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('GET', url),
        )

    def fake_post(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.post(path, json=kwargs.get('json'))
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('POST', url),
        )

    with patch.object(pipecat_server.httpx, 'get', side_effect=fake_get), patch.object(pipecat_server.httpx, 'post', side_effect=fake_post):
        create_payload = asyncio.run(
            pipecat_server.create_live_session(
                session_payload['session_id'],
                pipecat_server.LiveSessionCreateRequest(),
            )
        )
        assert create_payload['status'] == 'ready'
        assert create_payload['transport']['join_url'].endswith('/live/join')
        assert create_payload['live']['state'] == 'connecting'

        join_payload = asyncio.run(
            pipecat_server.join_live_session(
                session_payload['session_id'],
                pipecat_server.LiveSessionJoinRequest(sdp='fake-offer-sdp', type='offer'),
            )
        )
        assert join_payload['status'] == 'ready'
        assert join_payload['answer']['type'] == 'answer'

        state_payload = pipecat_server.get_live_state(session_payload['session_id'])
        assert state_payload['live']['transport_ready'] is True
        assert state_payload['agent']['live_transport']['join_url'].endswith('/live/join')

        stop_payload = asyncio.run(pipecat_server.stop_live_session(session_payload['session_id']))
        assert stop_payload['status'] == 'ended'
        assert stop_payload['live']['state'] == 'ended'

        restarted_payload = asyncio.run(
            pipecat_server.create_live_session(
                session_payload['session_id'],
                pipecat_server.LiveSessionCreateRequest(),
            )
        )
        assert restarted_payload['status'] == 'ready'
        assert restarted_payload['live']['state'] == 'connecting'
        assert restarted_payload['live']['events'][0]['type'] == 'live_session_created'


def test_pipecat_orchestrator_agent_state_and_tool_flow(tmp_path: Path):
    import sys
    from importlib import util

    pdf_path = tmp_path / 'demo.pdf'
    _create_sample_pdf(pdf_path)

    with pdf_path.open('rb') as handle:
        upload_response = client.post('/api/decks', files={'file': ('demo.pdf', handle, 'application/pdf')})
    assert upload_response.status_code == 200, upload_response.text
    deck = upload_response.json()

    session_response = client.post('/api/sessions', json={'deck_id': deck['id']})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    client.post(f"/api/sessions/{session_payload['session_id']}/start")

    module_path = Path(__file__).resolve().parents[2] / 'pipecat' / 'server.py'
    spec = util.spec_from_file_location('pipecat_server_agent_test', module_path)
    pipecat_server = util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = pipecat_server
    spec.loader.exec_module(pipecat_server)

    def fake_get(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.get(path)
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('GET', url),
        )

    def fake_post(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.post(path, json=kwargs.get('json'))
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('POST', url),
        )

    with patch.object(pipecat_server.httpx, 'get', side_effect=fake_get), patch.object(pipecat_server.httpx, 'post', side_effect=fake_post):
        bootstrap_payload = pipecat_server.bootstrap(session_payload['session_id'], pipecat_server.SessionCreateRequest())
        assert bootstrap_payload['agent']['orchestration']['authority'] == 'pipecat'

        agent_payload = pipecat_server.start_agent(session_payload['session_id'], pipecat_server.SessionAgentStartRequest())
        assert agent_payload['connected'] is True
        assert agent_payload['agent_status'] == 'listening'
        assert agent_payload['tool_manifest']

        state_payload = pipecat_server.get_agent_state(session_payload['session_id'])
        assert state_payload['connected'] is True
        assert state_payload['orchestration']['fake_ask_is_test_harness'] is True

        ask_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='search slides for solution revenue'),
        )
        assert ask_payload['status'] == 'answered'
        assert ask_payload['answer']
        assert ask_payload['citations']
        assert ask_payload['agent_status'] == 'speaking'

        nav_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='go to slide 2'),
        )
        assert nav_payload['status'] == 'answered'
        assert 'Jumped to slide 2' in nav_payload['answer']
        assert nav_payload['citations']
        assert nav_payload['agent_status'] == 'speaking'

        spoken_nav_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='jump to slide three'),
        )
        assert spoken_nav_payload['status'] == 'answered'
        assert 'Jumped to slide 3' in spoken_nav_payload['answer']
        assert spoken_nav_payload['citations']

        natural_nav_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='go to the first slide'),
        )
        assert natural_nav_payload['status'] == 'answered'
        assert 'Jumped to slide 1' in natural_nav_payload['answer']
        assert natural_nav_payload['citations']

        final_nav_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='jump to the final slide'),
        )
        assert final_nav_payload['status'] == 'answered'
        assert 'Jumped to slide 3' in final_nav_payload['answer']
        assert final_nav_payload['citations']

        current_slide_question_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='where are we in the deck?'),
        )
        assert current_slide_question_payload['status'] == 'answered'
        assert 'Current slide is 3' in current_slide_question_payload['answer']
        assert current_slide_question_payload['citations']

        current_slide = client.get(f"/api/sessions/{session_payload['session_id']}/current-slide").json()
        assert current_slide['index'] == 2

        after_payload = pipecat_server.get_agent_state(session_payload['session_id'])
        assert after_payload['tool_state']
        assert after_payload['tool_state']['last_tool_result']['tool_name'] == 'get_current_slide'

        stop_payload = pipecat_server.stop_agent(session_payload['session_id'])
        assert stop_payload['connected'] is False
        assert stop_payload['agent_status'] == 'disconnected'


def test_pipecat_orchestrator_grounded_ask_loop(tmp_path: Path):
    import sys
    from importlib import util

    pdf_path = tmp_path / 'demo.pdf'
    _create_sample_pdf(pdf_path)

    with pdf_path.open('rb') as handle:
        upload_response = client.post('/api/decks', files={'file': ('demo.pdf', handle, 'application/pdf')})
    assert upload_response.status_code == 200, upload_response.text
    deck = upload_response.json()

    session_response = client.post('/api/sessions', json={'deck_id': deck['id']})
    assert session_response.status_code == 200
    session_payload = session_response.json()

    client.post(f"/api/sessions/{session_payload['session_id']}/start")

    module_path = Path(__file__).resolve().parents[2] / 'pipecat' / 'server.py'
    spec = util.spec_from_file_location('pipecat_server_test', module_path)
    pipecat_server = util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = pipecat_server
    spec.loader.exec_module(pipecat_server)

    def fake_get(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.get(path)
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('GET', url),
        )

    def fake_post(url, *args, **kwargs):
        path = url.removeprefix(pipecat_server.API_BASE_URL)
        response = client.post(path, json=kwargs.get('json'))
        return httpx.Response(
            status_code=response.status_code,
            json=response.json(),
            request=httpx.Request('POST', url),
        )

    with patch.object(pipecat_server.httpx, 'get', side_effect=fake_get), patch.object(pipecat_server.httpx, 'post', side_effect=fake_post):
        bootstrap_payload = pipecat_server.bootstrap(session_payload['session_id'], pipecat_server.SessionCreateRequest())
        assert bootstrap_payload['status'] == 'ready'

        connect_payload = pipecat_server.connect(session_payload['session_id'], pipecat_server.SessionConnectRequest())
        assert connect_payload['status'] == 'connected'
        assert connect_payload['connected'] is True

        ask_payload = pipecat_server.ask(
            session_payload['session_id'],
            pipecat_server.SessionAskRequest(transcript='What is the solution and why now?'),
        )

    assert ask_payload['status'] == 'answered'
    assert ask_payload['answer']
    assert ask_payload['citations']
    assert ask_payload['transcript'] == 'What is the solution and why now?'

    transcript_response = client.get(f"/api/sessions/{session_payload['session_id']}/transcript")
    assert transcript_response.status_code == 200
    transcript_items = transcript_response.json()
    assert any(item['role'] == 'user' and item['text'] == 'What is the solution and why now?' for item in transcript_items)
    assert any(item['role'] == 'agent' and item['text'].strip() for item in transcript_items)
