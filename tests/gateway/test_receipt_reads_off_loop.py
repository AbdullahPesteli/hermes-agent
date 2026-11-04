"""Receipt storage waits must leave the shared owner loop available to other sessions."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from gateway.session_authority import LiveSession, SessionAuthority
from gateway.session_contract import Principal, SessionRef
from gateway.session_controls import AuthorityConnection
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, admit_session_input, begin_runtime_epoch

REF = SessionRef('p', 'owned')
ACTOR = Principal('human', 'p', frozenset({'session:submit'}), 'viewer')


@pytest.fixture
def owner(tmp_path):
    db = SessionDB(tmp_path / 'state.db')
    db.create_session('owned', source='test')
    epoch = begin_runtime_epoch(db, instance_id='owner')
    authority = SessionAuthority(SimpleNamespace(), profile_id='p', instance_id='owner', db=db, epoch=epoch)
    authority.sessions['owned'] = LiveSession(None, 'route')
    row = admit_session_input(db, epoch=epoch, principal_id='human', session_id='owned', request_id='one', payload={})
    try:
        yield authority, row
    finally:
        db.close()


def delayed_read(monkeypatch, module, name, while_waiting=None):
    """Only the owner loop can release this read; stock code hits the bounded fallback."""
    loop = asyncio.get_running_loop()
    release, finished = threading.Event(), threading.Event()
    heartbeat = []
    original = getattr(module, name)

    def tick():
        heartbeat.append(not finished.is_set())
        if while_waiting is not None:
            while_waiting()
        release.set()

    def read(*args, **kwargs):
        loop.call_soon_threadsafe(tick)
        release.wait(1)
        finished.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, read)
    return heartbeat


@pytest.mark.asyncio
async def test_receipt_storage_wait_does_not_starve_heartbeat(owner, monkeypatch):
    import gateway.session_authority as module
    authority, row = owner
    heartbeat = delayed_read(monkeypatch, module, 'get_session_admission')
    receipt = await authority.receipt(ACTOR, REF, row['admission_id'])
    await asyncio.sleep(0)
    assert receipt.status == 'queued'
    assert heartbeat == [True], 'receipt storage blocked the owner loop'


@pytest.mark.asyncio
@pytest.mark.parametrize('change,reason', [('delete', 'not_found'), ('replace', 'not_found'),
                                         ('retire', 'runtime_retired'), ('revoke', 'permission_denied')])
async def test_receipt_rechecks_owner_after_storage_wait(owner, monkeypatch, change, reason):
    import gateway.session_authority as module
    from gateway.config import Platform
    authority, row = owner

    def change_owner():
        if change == 'delete':
            authority.sessions.pop('owned')
        elif change == 'replace':
            authority.sessions['owned'] = LiveSession(None, 'new-route')
        elif change == 'retire':
            authority.retired = True
        else:
            authority.sessions['owned'].source = SimpleNamespace(platform=Platform.LOCAL, user_id='another-human')

    delayed_read(monkeypatch, module, 'get_session_admission', change_owner)
    with pytest.raises(RuntimeStoreError, match=reason):
        await authority.receipt(ACTOR, REF, row['admission_id'])
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_receipt_preserves_unpublished_settlement_mask(owner, monkeypatch):
    import gateway.session_authority as module
    authority, row = owner
    authority.sessions['owned'].settling[row['admission_id']] = dict(row, status='started', generation=1)
    delayed_read(monkeypatch, module, 'get_session_admission')
    receipt = await authority.receipt(ACTOR, REF, row['admission_id'])
    assert (receipt.status, receipt.execution_generation) == ('started', 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('principal', ['human', 'other'])
async def test_receipt_keeps_admission_principal_check(owner, principal):
    authority, row = owner
    actor = Principal(principal, 'p', ACTOR.capabilities, 'viewer')
    if principal == 'other':
        with pytest.raises(RuntimeStoreError, match='permission_denied'):
            await authority.receipt(actor, REF, row['admission_id'])
    else:
        assert (await authority.receipt(actor, REF, row['admission_id'])).admission_id == row['admission_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('retire', [False, True])
async def test_terminal_result_storage_wait_keeps_loop_and_owner_fence(owner, monkeypatch, retire):
    import gateway.session_results as results
    from hermes_state_runtime import cancel_session_input
    authority, row = owner
    cancel_session_input(authority.db, epoch=authority.epoch, admission_id=row['admission_id'])
    connection = AuthorityConnection(authority, SimpleNamespace(), {'user_id': 'human', 'profile_id': 'p'})
    change = (lambda: setattr(authority, 'retired', True)) if retire else None
    heartbeat = delayed_read(monkeypatch, results, 'admission_result', change)
    if retire:
        with pytest.raises(RuntimeStoreError, match='runtime_retired'):
            await connection.receipt(REF, {'admission_id': row['admission_id'], 'include_result': True})
    else:
        receipt = await connection.receipt(REF, {'admission_id': row['admission_id'], 'include_result': True})
        assert receipt['status'] == 'terminal'
    await asyncio.sleep(0)
    assert heartbeat == [True], 'result storage blocked the owner loop'


@pytest.mark.asyncio
async def test_terminal_receipt_retains_structured_result_and_usage(owner):
    from gateway.session_results import retain_result
    from hermes_state_runtime import claim_session_input
    authority, row = owner
    claimed = claim_session_input(authority.db, epoch=authority.epoch, session_id='owned')
    saved = {'result': {'final_response': 'done', 'exit_reason': 'completed'}, 'usage': {'output_tokens': 3}}
    retain_result(authority.db, epoch=authority.epoch, row=claimed, result=saved)
    connection = AuthorityConnection(authority, SimpleNamespace(), {'user_id': 'human', 'profile_id': 'p'})
    receipt = await connection.receipt(REF, {'admission_id': row['admission_id'], 'include_result': True})
    assert receipt['status'] == 'terminal'
    assert receipt['result'] == saved['result']
    assert receipt['usage'] == saved['usage']


@pytest.mark.asyncio
async def test_receipt_rejects_another_sessions_admission(owner):
    authority, row = owner
    authority.db.create_session('other', source='test')
    authority.sessions['other'] = LiveSession(None, 'other-route')
    with pytest.raises(RuntimeStoreError, match='not_found'):
        await authority.receipt(ACTOR, SessionRef('p', 'other'), row['admission_id'])


@pytest.mark.asyncio
async def test_receipt_checks_capability_before_reading_storage(owner, monkeypatch):
    import gateway.session_authority as module
    authority, row = owner

    def unexpected_read(*args, **kwargs):
        pytest.fail('unauthorized receipt reached storage')

    monkeypatch.setattr(module, 'get_session_admission', unexpected_read)
    actor = Principal('human', 'p', frozenset(), 'viewer')
    with pytest.raises(RuntimeStoreError, match='permission_denied'):
        await authority.receipt(actor, REF, row['admission_id'])
