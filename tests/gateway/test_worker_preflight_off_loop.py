"""Worker claim/verification I/O must not stall other profiles on the owner loop."""
import asyncio
import subprocess
import sys
import threading
from types import SimpleNamespace

import psutil
import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_contract import Principal, SessionRef
from gateway.session_worker import _claim, worker_request
from hermes_cli.process_identity import worker_birth
from hermes_state import SessionDB
from hermes_state_runtime import (RuntimeStoreError, adopt_worker_execution, begin_runtime_epoch,
                                  register_worker_execution)


@pytest.fixture
def worker(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('owned', source='test')
    authority = SessionAuthority(SimpleNamespace(_draining=False), profile_id='p', instance_id='owner', db=db,
                                 epoch=begin_runtime_epoch(db, instance_id='owner'))
    authority.sessions['owned'] = LiveSession(None, 'route')
    authority.wake_after_worker = lambda session_id: None
    connection = SimpleNamespace(authority=authority, actor=Principal(
        'human', 'p', frozenset({'worker:adopt'}), 'worker'))
    child = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    scope = {'profile_id': 'p', 'session_id': 'owned', 'execution_id': 'compute', 'generation': 0,
             'pid': child.pid, 'birth': worker_birth(psutil.Process(child.pid)), 'secret': 'w' * 32}
    try:
        yield connection, SessionRef('p', 'owned'), scope
    finally:
        child.stdin.close()
        child.wait(timeout=5)
        db.close()


def prepare(worker, operation):
    connection, ref, scope = worker
    if operation == 'register':
        return scope | {'kind': 'compute'}
    authority = connection.authority
    claim = _claim(connection, ref, scope)
    fields = {key: scope[key] for key in ('execution_id', 'session_id', 'generation')}
    register_worker_execution(authority.db, epoch=authority.epoch, **fields, kind='compute', adoption_secret=claim)
    if operation == 'adopt':
        return scope
    adopted = adopt_worker_execution(authority.db, epoch=authority.epoch, **fields, adoption_secret=claim)
    return scope | {'epoch': adopted['owner_epoch'], 'sequence': 1, 'operation': 'execution.finish', 'payload': {}}


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,operation', [('claim', 'register'), ('claim', 'adopt'), ('claim', 'persist'),
                                          ('verify', 'adopt'), ('verify', 'persist')])
@pytest.mark.parametrize('retire', [False, True])
async def test_worker_preflight_wait_keeps_loop_live_and_refuses_retirement(worker, monkeypatch,
                                                                          stage, operation, retire):
    import gateway.session_worker as module
    connection, ref, _scope = worker
    params = prepare(worker, operation)
    loop = asyncio.get_running_loop()
    released, finished = threading.Event(), threading.Event()
    heartbeat = []
    original = getattr(module, '_' + stage)

    def tick():
        heartbeat.append(not finished.is_set())
        if retire:
            connection.authority.retired = True
        released.set()

    def wait(*args, **kwargs):
        loop.call_soon_threadsafe(tick)
        released.wait(1)
        finished.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, '_' + stage, wait)
    if retire:
        with pytest.raises(RuntimeStoreError, match='runtime_retired'):
            await worker_request(connection, ref, params, operation=operation)
    else:
        result = await worker_request(connection, ref, params, operation=operation)
        assert result['status'] == {'register': 'registered', 'adopt': 'running', 'persist': 'terminal'}[operation]
    await asyncio.sleep(0)
    assert heartbeat == [True], 'worker preflight blocked the owner loop'
