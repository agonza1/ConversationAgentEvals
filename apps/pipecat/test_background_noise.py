import asyncio
import math
from array import array

import pytest

from background_noise import ClockedNoiseTrack, pcm_wav, read_wav


@pytest.mark.parametrize('kind', ['white', 'office', 'cafe', 'traffic'])
def test_shared_microphone_keeps_noise_between_turns_and_during_target_speech(kind):
    async def run():
        rate = 24000
        pcm = array('h', [round(2000 * math.sin(i * .1)) for i in range(rate // 20)]).tobytes()
        sent, observed = [], []

        async def send(audio, rate): sent.append((audio, rate))
        async def observe(audio, rate, turn): observed.append((audio, rate, turn))

        track = ClockedNoiseTrack(pcm_wav(pcm, rate), {'noise_type': kind, 'snr_db': 10, 'seed': 42}, send=send, observe=observe)
        track.start()
        try:
            first = await asyncio.wait_for(track.play(pcm, 1), 2)
            before = len(sent)
            async with asyncio.timeout(2):
                while len(sent) < before + 3: await asyncio.sleep(.005)
            second = await asyncio.wait_for(track.play(pcm, 2), 2)
            assert read_wav(first)[1] != pcm
            assert read_wav(second)[1] != read_wav(first)[1]
            assert all(any(audio) for audio, _ in sent)
            assert any(turn == 0 for _, _, turn in observed[before:])
            assert [(a, r) for a, r, _ in observed] == sent
        finally:
            await track.close()
        count = len(sent)
        await asyncio.sleep(.03)
        assert len(sent) == count
    asyncio.run(run())


def test_send_failure_does_not_publish_unaccepted_pcm():
    async def run():
        observed = []
        async def send(*_): raise RuntimeError('fake transport failure')
        async def observe(*args): observed.append(args)
        track = ClockedNoiseTrack(pcm_wav(array('h', [1000] * 480).tobytes()), {'seed': 1, 'snr_db': 20}, send=send, observe=observe)
        pending = asyncio.create_task(track.play(bytes(960), 1))
        await asyncio.sleep(0)
        track.start()
        with pytest.raises(RuntimeError, match='stream failed'):
            await asyncio.wait_for(pending, 2)
        assert not observed
        await track.close()
    asyncio.run(run())


@pytest.mark.parametrize('cap,accepted', [(310, False), (420, True)])
def test_public_setup_budget_is_in_addition_to_the_session_budget(cap, accepted):
    async def run():
        sent = []
        async def send(audio, rate): sent.append(audio); track.closed = True
        async def observe(*_): pass
        track = ClockedNoiseTrack(pcm_wav(array('h', [1000] * 480).tobytes()), {'seed': 7, 'snr_db': 20},
            send=send, observe=observe, max_seconds=cap)
        track.mixer.samples = 311 * track.mixer.rate
        await track._run()
        assert bool(sent) == accepted
        assert bool(track.error) != accepted
    asyncio.run(run())
