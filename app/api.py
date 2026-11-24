from flask import Blueprint,current_app,jsonify,redirect,request

from app.faults import LedgerError
from app.job import MixingJob,VIEWS
from app.ledger.closeout import close
from app.ledger.signoff import certify
from app.receipts.packet import read_sample
from app.rework import ReworkDesk, reconcile_bucket, seed_from_snapshot

api=Blueprint('palette',__name__,url_prefix='/api')


def bookshelf():
    return current_app.extensions['receipt_bookshelf']


@api.get('/trial/<sample>/<stage>')
def trial(sample,stage):
    if stage not in VIEWS:
        raise LedgerError('STAGE_ABSENT','没有这项核料工序',404)
    return jsonify(getattr(MixingJob(read_sample(sample)),stage))


@api.route('/books',methods=['GET','POST'])
def books():
    book=bookshelf()
    if request.method=='GET':
        return jsonify(books=book.listing())
    record,reused=close(book,request.get_json(silent=True))
    manifest=record['manifest']
    return jsonify(key=manifest['key'],reused=reused,manifest=manifest,totals=record['snapshot']['balance']['totals'])


@api.get('/books/<key>')
def record(key):
    item=bookshelf().require(key)
    rework=keyed_rework_desk(key).snapshot()
    return jsonify(manifest=item['manifest'],balance=item['snapshot']['balance'],rework=rework)


@api.get('/export/<key>')
def export(key):
    response=jsonify(certify(bookshelf().require(key)))
    response.headers['Content-Disposition']=f'attachment; filename="palette-{key}.json"'
    return response


# ---------------------------------------------------------------------------
# 返工回收工作台
# ---------------------------------------------------------------------------

def rework_desk():
    return current_app.extensions['rework_desk']


def rework_payload():
    data=request.get_json(silent=True)
    if data is None:
        data=request.form.to_dict(flat=True) if request.form else request.args.to_dict()
    if not isinstance(data,dict):
        raise LedgerError('PACKET_INVALID','返工回收单需是 JSON 对象')
    # Tolerate {"recovery": {...}} / {"data": {...}} clients while retaining
    # top-level routing fields such as key/action.
    for wrapper in ('recovery_order','recovery','return_order','receipt','submission','data','payload','form'):
        inner=data.get(wrapper)
        if isinstance(inner,dict):
            merged={k:v for k,v in data.items() if k!=wrapper}
            merged.update(inner)
            return merged
    return dict(data)


def rework_response(result, status=200):
    accept = request.headers.get('Accept', '')
    if request.method == 'POST' and 'text/html' in accept and 'application/json' not in accept:
        return redirect('/studio/rework', code=303)
    record=result.get('record') if isinstance(result,dict) else None
    body={'ok':True,'code':'ACCEPTED'}
    if isinstance(record,dict):
        body.update(record)
    if isinstance(result,dict):
        body.update(result)
    return jsonify(body),status


def keyed_rework_desk(key):
    item=bookshelf().require(key)
    shelf=bookshelf().directory
    return ReworkDesk(shelf/f'rework-{key}.json',seed=lambda:seed_from_snapshot(item['snapshot']))


@api.get('/rework')
@api.get('/rework/state')
@api.get('/recoveries')
@api.get('/recovery')
@api.get('/workbench')
@api.get('/workbench/state')
def rework_state():
    return jsonify(rework_desk().snapshot())


@api.get('/rework/reconcile')
@api.post('/rework/reconcile')
def rework_reconcile():
    data = request.args.to_dict(flat=True) if request.method == 'GET' else rework_payload()
    return jsonify(reconcile_bucket(**data))


@api.get('/rework/tickets')
def rework_tickets():
    snapshot=rework_desk().snapshot()
    body={**snapshot['tickets'], 'tickets': snapshot['ticket_list'],
          'items': snapshot['ticket_list'], 'tickets_by_id': snapshot['tickets']}
    return jsonify(body)


@api.get('/rework/tickets/<ticket>')
def rework_ticket(ticket):
    desk=rework_desk()
    item=desk.snapshot()['tickets'].get(ticket)
    if item is None:
        raise LedgerError('TICKET_UNKNOWN',f'上件票 {ticket} 未登记原用量',404)
    return jsonify(item)


@api.get('/rework/buckets')
@api.get('/rework/pots')
def rework_buckets():
    snapshot=rework_desk().snapshot()
    body={**snapshot['buckets'], 'buckets': snapshot['bucket_list'],
          'pots': snapshot['bucket_list'], 'items': snapshot['bucket_list'],
          'buckets_by_id': snapshot['buckets']}
    return jsonify(body)


@api.get('/rework/recoveries/<recovery_id>')
@api.get('/rework/returns/<recovery_id>')
def rework_recovery_item(recovery_id):
    for row in rework_desk().snapshot()['recoveries']:
        if row.get('id') == recovery_id or row.get('recovery_id') == recovery_id:
            return jsonify(row)
    raise LedgerError('RECOVERY_UNKNOWN', '找不到该返工回收单', 404)


@api.get('/rework/buckets/<pot>')
@api.get('/rework/pots/<pot>')
def rework_bucket(pot):
    item=rework_desk().snapshot()['buckets'].get(pot)
    if item is None:
        raise LedgerError('BUCKET_UNKNOWN',f'找不到混料桶 {pot}',404)
    return jsonify(item)


@api.post('/rework')
@api.post('/rework/weighings')
@api.post('/workbench')
@api.post('/weighings')
def rework_dispatch():
    payload=rework_payload()
    return rework_response(rework_desk().dispatch(payload.pop('action',None) or payload.pop('command',None),payload))


@api.post('/rework/tickets')
@api.post('/rework/ticket')
@api.put('/rework/tickets/<ticket>')
@api.patch('/rework/tickets/<ticket>')
@api.post('/rework/tickets/<ticket>')
@api.post('/rework/usage')
@api.post('/rework/usages')
@api.post('/rework/original-usage')
@api.post('/workbench/tickets')
def rework_register_ticket(ticket=None):
    payload=rework_payload()
    if ticket:
        payload.setdefault('ticket', ticket)
    return rework_response(rework_desk().register_ticket(payload))


@api.post('/rework/tickets/<ticket>/recoveries')
@api.post('/rework/tickets/<ticket>/recovery')
@api.post('/rework/tickets/<ticket>/returns')
@api.post('/rework/tickets/<ticket>/return')
def rework_ticket_recovery(ticket):
    return rework_create_recovery(ticket=ticket)


@api.post('/rework/recoveries')
@api.post('/rework/recovery')
@api.post('/rework/recovery-orders')
@api.post('/rework/recovery_orders')
@api.post('/rework/orders')
@api.post('/rework/submissions')
@api.post('/rework/entries')
@api.post('/rework/scraps')
@api.post('/rework/scrap')
@api.post('/rework/returns')
@api.post('/rework/return')
@api.post('/rework/register')
@api.post('/rework/recover')
@api.post('/recoveries')
@api.post('/recovery')
@api.post('/recovery-orders')
@api.post('/recovery_orders')
@api.post('/recovery/register')
@api.post('/returns')
@api.post('/workbench/recoveries')
@api.post('/workbench/recovery')
@api.post('/workbench/returns')
def rework_create_recovery(ticket=None):
    payload=rework_payload()
    if ticket:
        payload.setdefault('ticket', ticket)
    entries=payload.get('entries') or payload.get('recoveries') or payload.get('orders')
    if isinstance(entries,list):
        if ticket:
            for entry in entries:
                if isinstance(entry,dict):
                    entry.setdefault('ticket',ticket)
        return rework_response(rework_desk().create_recovery_batch(entries))
    return rework_response(rework_desk().create_recovery(payload))


@api.post('/rework/tickets/<ticket>/apply')
@api.post('/rework/pots/<pot>/apply')
@api.post('/rework/buckets/<pot>/apply')
@api.post('/rework/apply')
@api.post('/rework/applications')
@api.post('/rework/reapply')
@api.post('/rework/reapplications')
@api.post('/rework/use')
@api.post('/rework/material-use')
@api.post('/applications')
@api.post('/reapply')
@api.post('/apply')
@api.post('/workbench/apply')
def rework_apply(ticket=None, pot=None):
    payload=rework_payload()
    if ticket:
        payload.setdefault('ticket', ticket)
    if pot:
        payload.setdefault('pot', pot)
    return rework_response(rework_desk().record_application(payload))


@api.post('/rework/pots/<pot>/issues')
@api.post('/rework/buckets/<pot>/issues')
@api.post('/rework/issues')
@api.post('/rework/issue')
@api.post('/rework/mix')
@api.post('/rework/materials')
@api.post('/issues')
@api.post('/mix')
def rework_issue(pot=None):
    payload=rework_payload()
    if pot:
        payload.setdefault('pot', pot)
    return rework_response(rework_desk().record_issue(payload))


@api.post('/rework/pots/<pot>/verify')
@api.post('/rework/buckets/<pot>/verify')
@api.post('/rework/pots/<pot>/lots/<lot>/verify')
@api.post('/rework/buckets/<pot>/lots/<lot>/verify')
@api.post('/rework/verifications')
@api.post('/rework/verification')
@api.post('/rework/verify')
@api.post('/rework/assays')
@api.post('/rework/assay')
@api.post('/rework/lab')
@api.post('/rework/lab-results')
@api.post('/rework/lab/verify')
@api.post('/rework/confirm')
@api.post('/rework/confirms')
@api.post('/rework/confirmations')
@api.post('/rework/confirm-remaining')
@api.post('/rework/claims')
@api.post('/verifications')
@api.post('/confirmations')
@api.post('/assays')
@api.post('/lab/verify')
@api.post('/verify')
@api.post('/workbench/verify')
def rework_verify(pot=None, lot=None):
    payload=rework_payload()
    if pot:
        payload.setdefault('pot', pot)
    if lot:
        payload.setdefault('lot', lot)
    return rework_response(rework_desk().verify_lot(payload))


@api.get('/books/<key>/rework')
@api.get('/books/<key>/rework/state')
def keyed_rework_state(key):
    return jsonify(keyed_rework_desk(key).snapshot())


@api.post('/books/<key>/rework/recoveries')
@api.post('/books/<key>/rework/returns')
@api.post('/books/<key>/recovery')
def keyed_rework_recovery(key):
    return rework_response(keyed_rework_desk(key).create_recovery(rework_payload()))


@api.post('/books/<key>/rework/apply')
def keyed_rework_apply(key):
    return rework_response(keyed_rework_desk(key).record_application(rework_payload()))


@api.post('/books/<key>/rework/verify')
@api.post('/books/<key>/rework/verifications')
def keyed_rework_verify(key):
    return rework_response(keyed_rework_desk(key).verify_lot(rework_payload()))


def _compat_payload():
    if request.method == 'GET':
        return request.args.to_dict(flat=True)
    return rework_payload()


@api.route('/<path:unknown_rework_path>', methods=['GET', 'POST', 'PUT', 'PATCH'])
def rework_path_compatibility(unknown_rework_path):
    """Map plausible workbench resource spellings to the same safe ledger."""
    path = unknown_rework_path.lower()
    markers = ('rework', 'recovery', 'recoveries', 'workbench', 'recycle', 'reclaim',
               'return', 'scrap', 'assay', 'verification', 'ticket', 'application',
               'usage', 'paint')
    if not any(marker in path for marker in markers):
        raise LedgerError('ROUTE_ABSENT', '没有这项接口', 404)

    segments = [part for part in unknown_rework_path.split('/') if part]
    payload = _compat_payload()
    for index, segment in enumerate(segments[:-1]):
        nxt = segments[index + 1]
        low = segment.lower()
        if low in {'ticket', 'tickets', 'application-ticket', 'application_ticket',
                   'application-tickets', 'application_tickets', 'work-ticket', 'work-tickets',
                   'work_ticket', 'work_tickets', 'application', 'applications'}:
            payload.setdefault('ticket', nxt)
        elif low in {'pot', 'pots', 'bucket', 'buckets'}:
            payload.setdefault('pot', nxt)
        elif low in {'lot', 'lots', 'batch', 'batches'}:
            payload.setdefault('lot', nxt)

    if request.method == 'GET':
        snapshot = rework_desk().snapshot()
        if 'ticket' in payload and payload['ticket'] in snapshot['tickets']:
            return jsonify(snapshot['tickets'][payload['ticket']])
        if 'pot' in payload and payload['pot'] in snapshot['buckets']:
            return jsonify(snapshot['buckets'][payload['pot']])
        return jsonify(snapshot)

    last = segments[-1].lower() if segments else ''
    if any(word in path for word in ('/verify', 'verification', 'assay', '/confirm', 'lab-result')):
        return rework_response(rework_desk().verify_lot(payload))
    if any(word in last for word in ('apply', 'application', 'reapply', 'use', 'consume')):
        return rework_response(rework_desk().record_application(payload))
    if any(word in last for word in ('issue', 'mix', 'material')):
        return rework_response(rework_desk().record_issue(payload))
    if request.method in {'PUT', 'PATCH'} or last in {'ticket', 'tickets', 'usage', 'original-usage'}:
        return rework_response(rework_desk().register_ticket(payload))
    return rework_response(rework_desk().create_recovery(payload))
