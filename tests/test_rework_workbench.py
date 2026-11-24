from concurrent.futures import ThreadPoolExecutor

from app.rework import ReworkDesk


def test_concurrent_recovery_never_exceeds_original_usage(client):
    client.post('/api/rework/tickets', json={'ticket': 'RW-T1', 'original_mg': 100000})

    def submit(_):
        return client.post('/api/rework/recoveries', json={
            'ticket': 'RW-T1', 'mass_mg': 60000, 'pot': 'RW-B1',
        })

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit, range(4)))

    assert sum(response.status_code == 200 for response in responses) == 1
    assert sum(response.status_code == 422 for response in responses) == 3
    ticket = client.get('/api/rework/tickets/RW-T1').json
    assert ticket['returned_mg'] == 60000
    assert ticket['remaining_mg'] == 40000


def test_exact_cumulative_cap_is_allowed(client):
    client.post('/api/rework/tickets', json={'ticket': 'RW-T2', 'original_mg': 100000})
    first = client.post('/api/rework/recoveries', json={
        'ticket': 'RW-T2', 'mass_mg': 60000, 'pot': 'RW-B2'})
    second = client.post('/api/rework/recoveries', json={
        'ticket': 'RW-T2', 'mass_mg': 40000, 'pot': 'RW-B2'})
    over = client.post('/api/rework/recoveries', json={
        'ticket': 'RW-T2', 'mass_mg': 1, 'pot': 'RW-B2'})

    assert (first.status_code, second.status_code, over.status_code) == (200, 200, 422)
    assert client.get('/api/rework/tickets/RW-T2').json['returned_mg'] == 100000


def test_mixed_bucket_actual_apply_assay_and_reconciliation(client):
    recovery = client.post('/api/rework/recoveries', json={
        'ticket': 'RW-OLD', 'original_mg': 100000, 'mass_mg': 100000,
        'pot': 'RW-MIX', 'pending_lots': ['R-017', 'W-041'],
    })
    assert (recovery.json['before_mg'], recovery.json['after_mg']) == (0, 100000)
    assert recovery.json['parts_mg'] == {}

    issue = client.post('/api/rework/issues', json={
        'pot': 'RW-MIX', 'lot': 'NEW', 'mass_mg': 50000})
    assert (issue.json['record']['before_mg'], issue.json['record']['after_mg']) == (100000, 150000)

    applied = client.post('/api/rework/applications', json={
        'ticket': 'RW-NEW-USE', 'pot': 'RW-MIX', 'mass_mg': 80000})
    assert (applied.json['before_mg'], applied.json['after_mg']) == (150000, 70000)
    assert applied.json['parts_mg'] == {}

    assay = client.post('/api/rework/verifications', json={
        'pot': 'RW-MIX', 'lot': 'R-017', 'remaining_mg': 30000})
    bucket = assay.json['bucket_view']
    assert assay.status_code == 200
    assert assay.json['remaining_mg'] == 30000
    assert bucket['verified_mg'] == {'R-017': 30000}
    assert bucket['pending_mg'] == 40000
    assert bucket['check_mg'] == 0
    assert bucket['pending_lots'] == ['W-041']

    state = client.get('/api/rework').json
    assert state['totals']['applied_mg'] == 80000
    assert state['totals']['pending_mg'] == 40000


def test_file_desk_lock_protects_separate_instances(tmp_path):
    path = tmp_path / 'rework.json'
    ReworkDesk(path).register_ticket('RW-FILE', 100)

    def submit():
        try:
            ReworkDesk(path).create_recovery({
                'ticket': 'RW-FILE', 'mass_mg': 60, 'pot': 'RW-FILE-B'})
            return True
        except Exception:
            return False

    with ThreadPoolExecutor(2) as pool:
        accepted = list(pool.map(lambda _: submit(), range(2)))

    assert sorted(accepted) == [False, True]
    assert ReworkDesk(path).snapshot()['tickets']['RW-FILE']['returned_mg'] == 60
