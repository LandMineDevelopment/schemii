"""Retained console types share admission while preserving ordinary query capacity."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import pytest
from schemii.common.postgres.gateway import _ConnectionCapacity, PsycopgPostgresGateway
from schemii.common.postgres.errors import PostgresConnectionCapacityError
from schemii.schemii.console.service import ConsoleService
from test_postgres_gateway import FakeConnectFactory, resolved_connection


def reclaimer(gateway, target, leases, busy=()):
    console = ConsoleService.__new__(ConsoleService)
    console._transient_results_lock = threading.RLock()
    console._active_read_sessions = OrderedDict((str(index),SimpleNamespace(postgres=lease,
        connection_id=target.id,connection_revision=target.revision,owner_id='owner',workspace_id='workspace')) for index,lease in enumerate(leases))
    console._transient_results = {str(index):SimpleNamespace(execution_id=str(index),active_exports=int(index in busy)) for index in range(len(leases))}
    console._result_cursors = {}
    console._maximum_live_read_sessions = 12
    console._record_limit_event = Mock()
    gateway.register_retained_connection_reclaimer(console._reclaim_read_session)
    return console


def test_mixed_raw_transactions_and_reads_leave_catalog_slot_without_raising_limits():
    gateway = PsycopgPostgresGateway(connect_factory=FakeConnectFactory({}),maximum_connections=4,
        maximum_connections_per_identity=4,connection_acquire_timeout=0.01)
    target = resolved_connection()
    reads = [gateway._connect(target,retained=True) for _ in range(2)]
    console = reclaimer(gateway,target,reads)
    raw = gateway._connect(target,retained=True)
    transaction = gateway._connect(target,retained=True)
    assert reads[0].closed and not reads[1].closed
    second_raw = gateway._connect(target,retained=True)
    assert reads[1].closed
    assert not console._active_read_sessions
    assert not raw.closed and not transaction.closed and not second_raw.closed
    with pytest.raises(PostgresConnectionCapacityError):
        gateway._connect(target,retained=True)
    catalog = gateway._connect(target)
    assert gateway._connection_capacity._total == 4
    assert gateway._connection_capacity._retained_total == 3
    for connection in (catalog,raw,transaction,second_raw):
        connection.close()
    assert gateway._connection_capacity._total == gateway._connection_capacity._retained_total == 0


def test_shared_reclamation_never_closes_active_page_or_export():
    gateway = PsycopgPostgresGateway(connect_factory=FakeConnectFactory({}),maximum_connections=4,
        maximum_connections_per_identity=4,connection_acquire_timeout=0.01)
    target = resolved_connection()
    reads = [gateway._connect(target,retained=True) for _ in range(2)]
    console = reclaimer(gateway,target,reads,busy={0})
    raw = gateway._connect(target,retained=True)
    another_raw = gateway._connect(target,retained=True)
    assert not reads[0].closed and reads[1].closed
    assert '0' in console._active_read_sessions
    with pytest.raises(PostgresConnectionCapacityError):
        gateway._connect(target,retained=True)
    for connection in (reads[0],raw,another_raw):
        connection.close()


def test_retained_admission_is_atomic_across_concurrent_session_types():
    capacity = _ConnectionCapacity(4,4)
    barrier = threading.Barrier(8)
    def acquire(_):
        barrier.wait()
        return capacity.acquire('same-target',0,retained=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(acquire,range(8)))
    assert results.count(None) == 3
    assert capacity.acquire('same-target',0) is None
    assert capacity.acquire('same-target',0) is not None
    for _ in range(3):
        capacity.release('same-target',retained=True)
    capacity.release('same-target')
    assert capacity._total == capacity._retained_total == 0


def test_retained_gateway_entrypoints_use_shared_classification(monkeypatch):
    gateway = PsycopgPostgresGateway(connect_factory=FakeConnectFactory({}))
    target = resolved_connection()
    connect = Mock(side_effect=PostgresConnectionCapacityError('test',1,1))
    monkeypatch.setattr(gateway,'_connect',connect)
    with pytest.raises(PostgresConnectionCapacityError):
        gateway.open_console_transaction(target,'public')
    assert connect.call_args.kwargs == {'retained':True}
    with pytest.raises(PostgresConnectionCapacityError):
        gateway.open_console_read_session(target,'public',['SELECT 1'],on_started=lambda pid:True,page_memory_bytes=4096)
    assert connect.call_args.kwargs == {'retained':True}


def test_all_console_owners_can_reclaim_from_the_same_gateway():
    gateway = PsycopgPostgresGateway(connect_factory=FakeConnectFactory({}),maximum_connections=4,
        maximum_connections_per_identity=4,connection_acquire_timeout=0.01)
    target = resolved_connection()
    first_read = gateway._connect(target,retained=True)
    second_read = gateway._connect(target,retained=True)
    first_console = reclaimer(gateway,target,[first_read],busy={0})
    reclaimer(gateway,target,[second_read])
    raw = gateway._connect(target,retained=True)
    second_raw = gateway._connect(target,retained=True)
    assert second_read.closed and not first_read.closed
    first_console._transient_results['0'].active_exports = 0
    transaction = gateway._connect(target,retained=True)
    assert first_read.closed
    assert len(gateway._retained_connection_reclaimers) == 2
    for connection in (raw,second_raw,transaction):
        connection.close()
