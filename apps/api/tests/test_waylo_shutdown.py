"""SDK stream shutdown must not wait for a disconnect that has not happened."""
import asyncio
from types import SimpleNamespace

import pytest

from app.services.waylo_livekit import LiveKitVoicePeer, pcm_wav, run_waylo_call
from app.services.waylo_target import WayloError


def test_disconnect_releases_stream_before_reader_tasks_are_joined():
    async def exercise():
        disconnected = asyncio.Event()
        reading = asyncio.Event()
        closing = asyncio.Event()

        class Stream:
            def __aiter__(self):
                return self.frames()

            async def frames(self):
                reading.set()
                await asyncio.Event().wait()
                yield None

            async def aclose(self):
                closing.set()
                await disconnected.wait()

        class Room:
            async def disconnect(self):
                disconnected.set()

        peer = object.__new__(LiveKitVoicePeer)
        peer.rtc = SimpleNamespace(AudioStream=lambda *args, **kwargs: Stream())
        peer.room, peer.source, peer.streams = Room(), None, []
        peer.tasks = {asyncio.create_task(peer._receive(None))}
        await reading.wait()
        await asyncio.wait_for(peer.close(), .2)
        assert disconnected.is_set()
        assert closing.is_set()
        assert all(task.done() for task in peer.tasks)

    asyncio.run(exercise())


def test_source_cleanup_failure_still_disconnects_and_joins_readers():
    async def exercise():
        disconnected = asyncio.Event()
        started = asyncio.Event()

        class Source:
            async def aclose(self):
                raise RuntimeError('source cleanup failed')

        class Room:
            async def disconnect(self):
                disconnected.set()

        async def reader():
            started.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(reader())
        await started.wait()
        peer = object.__new__(LiveKitVoicePeer)
        peer.room, peer.source, peer.tasks = Room(), Source(), {task}
        with pytest.raises(RuntimeError, match='source cleanup failed'):
            await peer.close()
        assert disconnected.is_set() and task.done()

    asyncio.run(exercise())


def test_no_response_finishes_failed_with_partial_turns_and_vcon(tmp_path):
    from test_waylo_review_regressions import Client, Peer
    from test_waylo_target import TARGET

    async def exercise():
        disconnected = asyncio.Event()
        started = asyncio.Event()

        async def reader():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                await disconnected.wait()

        class WaitingPeer(Peer):
            close = LiveKitVoicePeer.close

            async def connect(self, bootstrap):
                async def disconnect():
                    disconnected.set()
                self.room = SimpleNamespace(disconnect=disconnect)
                self.source = None
                self.tasks = {asyncio.create_task(reader())}
                await started.wait()

            async def wait_response(self, previous, timeout=25):
                raise WayloError('Target response speech/silence boundary was not observed before timeout.')

        async def wording(turns, index):
            return 'Hi, what size is a bag of Russet Potatoes?'

        async def synthesize(text, index):
            return pcm_wav(b'\x01\x00' * 100)

        result = await asyncio.wait_for(run_waylo_call(
            target=TARGET, correlation_id='test/call', scenario={'id': 'case'}, suite_id='suite',
            artifact_dir=tmp_path, max_exchanges=1, timeout_seconds=30,
            next_utterance=wording, synthesize=synthesize, client=Client(), peer=WaitingPeer()), 5)
        assert result['verdict'] == 'failed'
        assert 'before timeout' in result['final_state']['tester_error']
        assert result['turns'][0]['speaker'] == 'Caller'
        assert result['ietf_vcon_export']
        assert disconnected.is_set()

    asyncio.run(exercise())
