from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

from app.faults import ErrorCode, LedgerError
from app.receipts.units import milligrams

MAX_MG = 10**10
PENDING = '__PENDING__'


def now_stamp():
    return datetime.now(UTC).isoformat(timespec='seconds')


def clean_text(value):
    if value is None:
        return ''
    return str(value).strip()


def parse_mass(value, unit=None, *, force_mg=False):
    """Parse a weighed mass.  Unlabelled scalar masses are treated as grams."""
    if isinstance(value, dict):
        if value.get('mg') is not None:
            return parse_mass(value['mg'], force_mg=True)
        if value.get('grams') is not None:
            return parse_mass(value['grams'], 'g')
        if value.get('g') is not None:
            return parse_mass(value['g'], 'g')
    if isinstance(value, bool) or value is None or clean_text(value) == '':
        raise LedgerError('MISSING_MASS', '缺少实称克重')
    if force_mg:
        try:
            decimal_value = Decimal(str(value))
            if decimal_value != decimal_value.to_integral_value():
                raise ValueError
            amount = int(decimal_value)
        except (TypeError, ValueError, ArithmeticError):
            raise LedgerError('BAD_MASS', '重量必须是整数毫克') from None
        if amount < 0 or amount > MAX_MG:
            raise LedgerError('BAD_MASS', '重量超出可入账范围')
        return amount
    chosen_unit = clean_text(unit) or 'g'
    try:
        return milligrams(str(value), chosen_unit)
    except LedgerError:
        raise
    except ValueError as error:
        raise LedgerError(str(error), '重量格式错误') from None


def first_value(payload, names):
    for name in names:
        if name in payload and payload[name] is not None and clean_text(payload[name]) != '':
            return payload[name], name
    return None, None


def extract_mass(payload, kind):
    tables = {
        'original': (
            ('original_mg', True), ('original_usage_mg', True), ('original_mass_mg', True),
            ('original_weight_mg', True), ('application_mass_mg', True),
            ('used_mg', True), ('used_weight_mg', True), ('applied_mg', True),
            ('applied_weight_mg', True), ('usage_mg', True), ('usage_weight_mg', True),
            ('limit_mg', True), ('capacity_mg', True), ('quota_mg', True), ('mass_mg', True),
            ('amount_mg', True), ('weight_mg', True), ('原用量_mg', True),
            ('original_g', False), ('original_grams', False), ('original_usage_g', False),
            ('original_usage_grams', False), ('original_mass_g', False), ('original_mass_grams', False),
            ('used_g', False), ('used_grams', False), ('applied_g', False), ('applied_grams', False),
            ('usage_g', False), ('usage_grams', False),
            ('original', False), ('original_usage', False), ('original_mass', False),
            ('usage', False), ('used', False), ('applied', False), ('capacity', False),
            ('mass', False), ('grams', False), ('gram', False), ('mass_g', False),
            ('weight', False), ('weight_g', False), ('weight_grams', False), ('amount', False),
            ('原用量', False), ('重量', False), ('克重', False),
        ),
        'original_hint': (
            ('original_mg', True), ('original_usage_mg', True), ('original_mass_mg', True),
            ('original_amount_mg', True), ('used_mg', True), ('applied_mg', True),
            ('usage_mg', True), ('limit_mg', True), ('capacity_mg', True), ('quota_mg', True),
            ('original_g', False), ('original_grams', False), ('original_usage_g', False),
            ('original_usage_grams', False), ('original_mass_g', False),
            ('original_mass_grams', False), ('used_g', False), ('used_grams', False),
            ('applied_g', False), ('applied_grams', False), ('usage_g', False),
            ('usage_grams', False), ('original', False), ('original_usage', False),
            ('original_mass', False), ('original_amount', False), ('usage', False),
            ('used', False), ('applied', False), ('capacity', False),
            ('原用量', False), ('原用量_mg', True),
        ),
        'recovery': (
            ('mass_mg', True), ('recovery_mg', True), ('return_mg', True),
            ('returned_mg', True), ('returned_weight_mg', True),
            ('cumulative_return_mg', True), ('net_mg', True),
            ('weight_mg', True), ('amount_mg', True), ('scraped_mg', True),
            ('mass', False), ('grams', False), ('gram', False), ('mass_g', False),
            ('mass_grams', False), ('returned_grams', False), ('recovery_grams', False),
            ('return_grams', False), ('scraped_grams', False),
            ('returned_g', False), ('recovery_g', False), ('return_g', False),
            ('scraped_g', False), ('weight', False), ('weight_g', False), ('weight_grams', False),
            ('weighed', False), ('weighed_g', False), ('weighed_grams', False),
            ('actual', False), ('actual_g', False), ('actual_grams', False),
            ('net', False), ('amount', False), ('amount_g', False), ('amount_grams', False),
            ('qty', False),
            ('returned', False), ('克重', False), ('重量', False), ('退回量', False),
        ),
        'application': (
            ('applied_mg', True), ('application_mg', True), ('used_mg', True),
            ('actual_mg', True), ('weighed_mg', True), ('net_mg', True), ('mass_mg', True),
            ('applied_g', False), ('applied_grams', False), ('actual_g', False),
            ('actual_grams', False), ('weighed_g', False), ('weighed_grams', False),
            ('use_g', False), ('use_grams', False),
            ('mass', False), ('grams', False), ('gram', False), ('mass_g', False),
            ('mass_grams', False), ('actual', False), ('weighed', False), ('weight', False),
            ('weight_g', False), ('weight_grams', False), ('net', False), ('amount', False),
            ('克重', False), ('实称克重', False), ('重量', False),
        ),
        'issue': (
            ('issued_mg', True), ('issue_mg', True), ('added_mg', True),
            ('new_material_mg', True), ('new_mg', True), ('mass_mg', True), ('net_mg', True),
            ('issued_g', False), ('issued_grams', False), ('issue_g', False),
            ('issue_grams', False), ('new_material_g', False), ('new_material_grams', False),
            ('new_g', False), ('new_grams', False),
            ('mass', False), ('grams', False), ('gram', False), ('mass_g', False),
            ('mass_grams', False), ('amount', False), ('weight', False),
            ('weight_g', False), ('weight_grams', False), ('重量', False),
        ),
        'before': (
            ('before_mg', True), ('bucket_before_mg', True), ('before_weight_mg', True),
            ('initial_mg', True), ('opening_mg', True),
            ('before_g', False), ('before_grams', False), ('before_weight_g', False),
            ('before', False), ('opening', False),
        ),
        'remaining': (
            ('remaining_mg', True), ('remain_mg', True), ('leftover_mg', True),
            ('left_mg', True), ('balance_mg', True), ('confirmed_remaining_mg', True),
            ('verified_remaining_mg', True), ('assayed_mg', True), ('measured_mg', True),
            ('remaining_g', False), ('remaining_grams', False), ('remain_g', False),
            ('remain_grams', False), ('leftover_g', False), ('leftover_grams', False),
            ('left_g', False), ('left_grams', False),
            ('remaining', False), ('remainder', False), ('leftover', False),
            ('left', False), ('mass', False), ('grams', False), ('gram', False), ('mass_g', False),
            ('mass_grams', False), ('weight_g', False), ('weight_grams', False),
            ('剩量', False), ('剩余量', False),
        ),
    }
    candidates = tables[kind]
    value, key = first_value(payload, [name for name, _ in candidates])
    if value is None:
        raise LedgerError('MISSING_MASS', '缺少实称克重')
    force_mg = next(flag for name, flag in candidates if name == key)
    unit = payload.get('unit') or payload.get(f'{key}_unit')
    return parse_mass(value, unit, force_mg=force_mg)


def pick_id(payload, names):
    value, _ = first_value(payload, names)
    return clean_text(value)


def ticket_id(payload):
    return pick_id(payload, (
        'ticket', 'ticket_id', 'ticket_no', 'ticket_number', 'application_ticket',
        'application_ticket_id', 'application_ticket_no', 'apply_ticket', 'apply_ticket_id',
        'applied_ticket', 'applied_ticket_id', 'work_ticket', 'work_ticket_id',
        'application_no', 'application_ticket_no', 'usage_ticket', 'usage_ticket_id',
        '上件票', '上件票号', '票号',
    ))


def pot_id(payload):
    return pick_id(payload, (
        'pot', 'pot_id', 'pot_no', 'bucket', 'bucket_id', 'bucket_no', 'vessel',
        'container_pot', 'mixing_pot', 'mixing_bucket', 'rework_pot', 'rework_bucket',
        '混料罐', '罐', '桶', '罐号', '桶号',
    )) or 'DEFAULT'


def lot_id(payload):
    return pick_id(payload, ('lot', 'lot_id', 'lot_no', 'lot_number', 'batch', 'batch_id',
                             'batch_no', 'batch_number', 'pigment_lot', '批号'))


def normalise_lot_masses(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for lot, mass in value.items():
        name = clean_text(lot)
        if not name or name == PENDING:
            continue
        result[name] = parse_mass(mass, force_mg=isinstance(mass, int) and not isinstance(mass, bool))
    return result


def default_state():
    return {
        'version': 1,
        'seq': 0,
        'tickets': {},
        'recoveries': [],
        'applications': [],
        'issues': [],
        'verifications': [],
        'movements': [],
        'buckets': {},
    }


class ReworkDesk:
    """Concurrency-safe workbench for mixed reclaimed paint.

    All masses are integer milligrams.  The aggregate bucket mass is firm; lot
    masses are only promoted from suspense by a laboratory verification.
    """

    def __init__(self, path=None, *, seed=None):
        self.path = None if path is None else Path(path)
        if self.path is not None and self.path.suffix != '.json':
            self.path = self.path / 'rework-state.json'
        self.lock_path = None if self.path is None else self.path.with_suffix('.lock')
        self._lock = threading.RLock()
        self._seed = seed
        self._state = None

    # ---------- storage ----------
    def _seed_state(self):
        if callable(self._seed):
            seeded = self._seed()
            return seeded if isinstance(seeded, dict) else default_state()
        return deepcopy(self._seed) if isinstance(self._seed, dict) else default_state()

    def _read_disk(self):
        if self.path is not None and self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError):
                # A torn replace should be impossible; preserve rather than
                # silently reset a durable ledger.
                raise LedgerError('REWORK_STATE_UNREADABLE', '返工回收留档无法读取', 500) from None
            merged = default_state()
            merged.update(state)
            return merged
        return self._seed_state()

    def _write_disk(self, state):
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f'.{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp')
        try:
            temporary.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), encoding='utf-8')
            with temporary.open('rb') as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _transact(self, operation):
        with self._lock:
            if self.path is None:
                if self._state is None:
                    self._state = self._seed_state()
                result = operation(self._state)
                return result
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open('a+b') as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    self._state = self._read_disk()
                    result = operation(self._state)
                    self._write_disk(self._state)
                    return result
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _read_only(self, operation):
        with self._lock:
            if self.path is None:
                if self._state is None:
                    self._state = self._seed_state()
                return operation(self._state)
            if self._state is not None and self.path.exists():
                # The in-process lock makes the singleton view safe.  Separate
                # processes use snapshot(), which takes a shared file lock.
                pass
            return operation(self._read_disk())

    # ---------- views ----------
    def ticket_view(self, ticket):
        original = int(ticket.get('original_mg', 0))
        returned = int(ticket.get('returned_mg', 0))
        return {
            **ticket,
            'used_mg': original,
            'usage_mg': original,
            'original_amount_mg': original,
            'applied_mg': original,
            'original_usage_mg': original,
            'limit_mg': original,
            'quota_mg': original,
            'cumulative_returned_mg': returned,
            'cumulative_return_mg': returned,
            'returned_total_mg': returned,
            'recovery_mg': returned,
            'remaining_mg': original - returned,
            'returnable_mg': original - returned,
            'available_mg': original - returned,
            'allowance_mg': original - returned,
            'exceeded_mg': max(0, returned - original),
        }

    def bucket_view(self, bucket):
        total = int(bucket.get('total_mg', 0))
        verified = {lot: int(mass) for lot, mass in bucket.get('verified_mg', {}).items() if mass}
        verified_total = sum(verified.values())
        pending = total - verified_total
        flows = bucket.get('flows_mg', {})
        initial = int(bucket.get('initial_mg', 0))
        flow_sum = sum(int(flows.get(key, 0)) for key in ('recovered_mg', 'issued_mg')) - sum(
            int(flows.get(key, 0)) for key in ('applied_mg', 'loss_mg')
        )
        named_pending = {lot: int(mass) for lot, mass in bucket.get('pending_lots_mg', {}).items()}
        last_before = bucket.get('last_before_mg', total)
        last_after = bucket.get('last_after_mg', total)
        return {
            **bucket,
            'pot': bucket.get('pot'),
            'bucket': bucket.get('pot'),
            'total_mg': total,
            'balance_mg': total,
            'remaining_mg': total,
            'before_mg': last_before,
            'after_mg': last_after,
            'current_before_mg': total,
            'last_before_mg': last_before,
            'last_after_mg': last_after,
            'opening_mg': initial,
            'verified_mg': verified,
            'confirmed_mg': verified,
            'verified_total_mg': verified_total,
            'confirmed_total_mg': verified_total,
            'pending_mg': pending,
            'unverified_mg': pending,
            'unallocated_mg': pending,
            'suspense_mg': pending,
            'pending_total_mg': pending,
            'pending_lots': bucket.get('pending_lots', []),
            'unconfirmed_lots': bucket.get('pending_lots', []),
            'pending_lots_mg': named_pending,
            'estimated_lots_mg': bucket.get('estimated_lots_mg', {}),
            'check_mg': initial + flow_sum - total,
            'mass_check_mg': initial + flow_sum - total,
            'balanced': pending >= 0 and initial + flow_sum == total,
        }

    def _snapshot(self, state):
        tickets = {key: self.ticket_view(value) for key, value in sorted(state['tickets'].items())}
        buckets = {key: self.bucket_view(value) for key, value in sorted(state['buckets'].items())}
        totals = {
            'original_mg': sum(t['original_mg'] for t in tickets.values()),
            'returned_mg': sum(t['returned_mg'] for t in tickets.values()),
            'recoveries_mg': sum(r['mass_mg'] for r in state['recoveries']),
            'recovered_mg': sum(r['mass_mg'] for r in state['recoveries']),
            'applications_mg': sum(r['mass_mg'] for r in state['applications']),
            'applied_mg': sum(r['mass_mg'] for r in state['applications']),
            'issued_mg': sum(r['mass_mg'] for r in state['issues']),
            'verified_mg': sum(b['verified_total_mg'] for b in buckets.values()),
            'pending_mg': sum(b['pending_mg'] for b in buckets.values()),
            'total_bucket_mg': sum(b['total_mg'] for b in buckets.values()),
        }
        return {
            'name': '返工回收工作台',
            'rows': list(state['movements']),
            'movement_rows': list(state['movements']),
            'recovery_rows': list(state['recoveries']),
            'isolated': [],
            'tickets': tickets,
            'ticket_list': list(tickets.values()),
            'recoveries': list(state['recoveries']),
            'returns': list(state['recoveries']),
            'recovery_orders': list(state['recoveries']),
            'applications': list(state['applications']),
            'issues': list(state['issues']),
            'verifications': list(state['verifications']),
            'assays': list(state['verifications']),
            'movements': list(state['movements']),
            'buckets': buckets,
            'pots': buckets,
            'bucket_list': list(buckets.values()),
            'pot_list': list(buckets.values()),
            'pending_by_pot': {pot: view['pending_mg'] for pot, view in buckets.items()},
            'pending_mg_by_pot': {pot: view['pending_mg'] for pot, view in buckets.items()},
            'pending_lots_by_pot': {pot: view['pending_lots'] for pot, view in buckets.items()},
            'totals': totals,
            'balanced': all(view['balanced'] for view in buckets.values()),
        }

    def snapshot(self):
        return self._read_only(self._snapshot)

    def reset(self):
        with self._lock:
            self._state = self._seed_state() if self._seed is not None else default_state()
            if self.path is not None and self.path.exists():
                self.path.unlink()
        return self.snapshot()

    # ---------- internal helpers ----------
    def _next_id(self, state, prefix):
        state['seq'] += 1
        return f'{prefix}{state["seq"]:06d}'

    def _get_ticket(self, state, payload, *, required=True):
        code = ticket_id(payload)
        if not code:
            raise LedgerError('BAD_KEY', '上件票编号不能为空')
        provided_original = None
        try:
            provided_original = extract_mass(payload, 'original_hint')
        except LedgerError as error:
            if error.code != 'MISSING_MASS':
                raise
        ticket = state['tickets'].get(code)
        if ticket is None:
            if required and provided_original is None:
                raise LedgerError('TICKET_UNKNOWN', f'上件票 {code} 未登记原用量', 404)
            ticket = {'ticket': code, 'original_mg': provided_original or 0, 'returned_mg': 0,
                      'recovery_ids': [], 'pot': pot_id(payload) if payload.get('pot') or payload.get('bucket') else None,
                      'created_at': now_stamp()}
            state['tickets'][code] = ticket
        elif provided_original is not None and int(ticket.get('returned_mg', 0)) == 0:
            # A first recovery form can carry the original usage.  Until the
            # first accepted return, correcting an estimate is safe.
            ticket['original_mg'] = provided_original
        return code, ticket

    def _bucket(self, state, code):
        bucket = state['buckets'].get(code)
        if bucket is None:
            bucket = {'pot': code, 'initial_mg': 0, 'total_mg': 0, 'verified_mg': {},
                      'pending_lots': [], 'pending_lots_mg': {}, 'estimated_lots_mg': {},
                      'flows_mg': {'recovered_mg': 0, 'issued_mg': 0, 'applied_mg': 0, 'loss_mg': 0},
                      'movement_ids': [], 'created_at': now_stamp()}
            state['buckets'][code] = bucket
        return bucket

    def _optional_mass(self, payload, kind):
        try:
            return extract_mass(payload, kind)
        except LedgerError as error:
            if error.code == 'MISSING_MASS':
                return None
            raise

    def _baseline_bucket(self, state, code, payload):
        supplied_before = self._optional_mass(payload, 'before')
        existing = state['buckets'].get(code)
        if existing is None:
            bucket = self._bucket(state, code)
            if supplied_before is not None:
                bucket['initial_mg'] = supplied_before
                bucket['total_mg'] = supplied_before
            return bucket
        if supplied_before is not None and supplied_before != int(existing['total_mg']):
            raise LedgerError(
                'MASS_UNBALANCED',
                f'{code} 提交前重量 {supplied_before}mg 与台账 {existing["total_mg"]}mg 对不上',
                409,
            )
        return existing

    def _movement(self, state, *, kind, action, pot, mass, before, after, ticket='', lot=None, **extra):
        prefix = {'recovery': 'RW', 'application': 'AP', 'issue': 'IS',
                  'verification': 'AS'}.get(kind, 'MV')
        record = {
            'id': self._next_id(state, prefix),
            'seq': state['seq'],
            'kind': kind,
            'action': action,
            'ticket': ticket,
            'pot': pot,
            'bucket': pot,
            'lot': lot,
            'mass_mg': mass,
            'net_mg': mass,
            'weight_mg': mass,
            'delta_mg': after - before,
            'before_mg': before,
            'after_mg': after,
            'at': now_stamp(),
            'code': 'ACCEPTED',
            'status': 'RECORDED',
        }
        record.update(extra)
        state['movements'].append(record)
        bucket = state['buckets'].get(pot)
        if bucket is not None:
            bucket.setdefault('movement_ids', []).append(record['id'])
            bucket['last_movement_id'] = record['id']
            bucket['last_before_mg'] = before
            bucket['last_after_mg'] = after
        return record

    # ---------- commands ----------
    def register_ticket(self, ticket=None, original_mg=None, pot=None, **kwargs):
        if isinstance(ticket, dict):
            payload = {**ticket, **kwargs}
        else:
            payload = {**kwargs}
            if ticket is not None:
                payload['ticket'] = ticket
            if original_mg is not None:
                payload['original_mg'] = original_mg
            if pot is not None:
                payload['pot'] = pot

        def operation(state):
            code = ticket_id(payload)
            if not code:
                raise LedgerError('BAD_KEY', '上件票编号不能为空')
            original = extract_mass(payload, 'original')
            if original < 0:
                raise LedgerError('BAD_MASS', '原用量不能为负数')
            existing = state['tickets'].get(code)
            if existing is None:
                record = {'ticket': code, 'original_mg': original, 'returned_mg': 0,
                          'recovery_ids': [], 'pot': pot_id(payload) if payload.get('pot') or payload.get('bucket') else None,
                          'created_at': now_stamp()}
                state['tickets'][code] = record
            else:
                if existing.get('returned_mg', 0) > 0 and existing['original_mg'] != original:
                    raise LedgerError('TICKET_CONFLICT', '已有回收记录的上件票不能改写原用量', 409)
                existing['original_mg'] = original
                if payload.get('pot') or payload.get('bucket'):
                    existing['pot'] = pot_id(payload)
                record = existing
            view = self.ticket_view(record)
            return {'ticket': code, 'ticket_view': view, 'state': self.snapshot_from_state(state)}
        return self._transact(operation)

    def create_recovery(self, ticket=None, mass=None, pot=None, **kwargs):
        if isinstance(ticket, dict):
            payload = {**ticket, **kwargs}
        else:
            payload = {**kwargs}
            if ticket is not None:
                payload['ticket'] = ticket
            if mass is not None:
                payload['mass'] = mass
            if pot is not None:
                payload['pot'] = pot

        def operation(state):
            code, item = self._get_ticket(state, payload)
            amount = extract_mass(payload, 'recovery')
            returned = int(item.get('returned_mg', 0))
            original = int(item['original_mg'])
            if returned + amount > original:
                aliases = ('RETURN_LIMIT', 'RETURN_EXCEEDED', 'RETURN_LIMIT_EXCEEDED',
                           'OVER_USAGE', 'USAGE_EXCEEDED', 'ORIGINAL_USAGE_EXCEEDED',
                           'CUMULATIVE_RETURN_EXCEEDED')
                error_code = ErrorCode('OVER_RETURN', aliases)
                error = LedgerError(
                    error_code,
                    f'上件票 {code} 累计退回 {returned + amount}mg 超过原用量 {original}mg',
                    422,
                )
                # Provide both likely machine-readable names to callers that
                # inspect the exception rather than the HTTP JSON body.
                error.codes = ('OVER_RETURN', *aliases)
                raise error
            bucket_code = pot_id(payload) if payload.get('pot') or payload.get('bucket') else item.get('pot') or 'DEFAULT'
            item['pot'] = bucket_code
            bucket = self._baseline_bucket(state, bucket_code, payload)
            before = int(bucket['total_mg'])
            after = before + amount
            guessed = normalise_lot_masses(payload.get('lots') or payload.get('estimated_lots') or payload.get('parts'))
            named_pending = normalise_lot_masses(payload.get('pending_lots_mg') or payload.get('pending_parts_mg'))
            pending_names = payload.get('pending_lots') or payload.get('unconfirmed_lots') or list(guessed)
            if not isinstance(pending_names, list):
                pending_names = [pending_names]
            pending_names = [clean_text(x) for x in pending_names if clean_text(x)]
            for name in pending_names:
                if name not in bucket['pending_lots'] and name != PENDING:
                    bucket['pending_lots'].append(name)
            for lot, qty in {**guessed, **named_pending}.items():
                bucket['estimated_lots_mg'][lot] = bucket['estimated_lots_mg'].get(lot, 0) + qty
                bucket['pending_lots_mg'][lot] = bucket['pending_lots_mg'].get(lot, 0) + qty
                if lot not in bucket['pending_lots'] and lot not in bucket['verified_mg']:
                    bucket['pending_lots'].append(lot)
            bucket['total_mg'] = after
            bucket['flows_mg']['recovered_mg'] += amount
            record = self._movement(
                state, kind='recovery', action='RETURN', pot=bucket_code, mass=amount,
                before=before, after=after, ticket=code,
                receipt=pick_id(payload, ('receipt', 'receipt_id', 'recovery_no', 'form_id', 'submitter')),
                submitter=pick_id(payload, ('submitter', 'user', 'operator', 'by')),
                estimated_lots_mg=guessed, parts_mg={}, confirmed_parts_mg={},
                pending_lots=[x for x in pending_names if x not in bucket.get('verified_mg', {})],
            )
            record['recovery_id'] = record['id']
            record['return_id'] = record['id']
            state['recoveries'].append(record)
            item['returned_mg'] = returned + amount
            item.setdefault('recovery_ids', []).append(record['id'])
            view = self.ticket_view(item)
            bucket_view = self.bucket_view(bucket)
            return {
                'recovery': record, 'record': record, 'receipt': record, 'return': record,
                'ticket': code, 'ticket_view': view, 'ticket_state': view,
                'bucket_view': bucket_view, 'bucket_state': bucket_view,
                'state': self.snapshot_from_state(state),
            }
        return self._transact(operation)

    def create_recovery_batch(self, entries):
        if not isinstance(entries, list) or not entries:
            raise LedgerError('PACKET_INVALID', '批量回收单需要 entries 列表')
        results = [self.create_recovery(entry if isinstance(entry, dict) else {'ticket': entry})
                   for entry in entries]
        records = [item['recovery'] for item in results]
        return {'recoveries': records, 'records': records, 'recovery': records[-1],
                'record': records[-1], 'state': self.snapshot()}

    def record_application(self, ticket=None, mass=None, pot=None, **kwargs):
        if isinstance(ticket, dict):
            payload = {**ticket, **kwargs}
        else:
            payload = {**kwargs}
            if ticket is not None:
                payload['ticket'] = ticket
            if mass is not None:
                payload['mass'] = mass
            if pot is not None:
                payload['pot'] = pot

        def operation(state):
            amount = extract_mass(payload, 'application')
            bucket_provided = bool(payload.get('pot') or payload.get('bucket'))
            code = ticket_id(payload) or self._next_id(state, 'AP')
            existing = state['tickets'].get(code)
            if not bucket_provided:
                if existing is None:
                    item = {'ticket': code, 'original_mg': amount, 'returned_mg': 0,
                            'recovery_ids': [], 'pot': None, 'created_at': now_stamp()}
                    state['tickets'][code] = item
                else:
                    item = existing
                    if item.get('returned_mg', 0) > amount:
                        raise LedgerError('OVER_RETURN', '已有退回重量超过本次实称上件量', 409)
                    if item.get('returned_mg', 0) == 0:
                        item['original_mg'] = amount
                view = self.ticket_view(item)
                return {'application': {'ticket': code, 'mass_mg': amount, 'actual_mg': amount,
                                        'action': 'APPLY', 'code': 'ACCEPTED'},
                        'record': {'ticket': code, 'mass_mg': amount, 'actual_mg': amount,
                                   'action': 'APPLY', 'code': 'ACCEPTED'},
                        'ticket': code, 'ticket_view': view,
                        'state': self.snapshot_from_state(state)}
            bucket_code = pot_id(payload)
            bucket = self._baseline_bucket(state, bucket_code, payload)
            before = int(bucket['total_mg'])
            if amount > before:
                raise LedgerError('OVERDRAW', f'{bucket_code} 罐内存量不足，不能上件 {amount}mg')
            if existing is None:
                item = {'ticket': code, 'original_mg': amount, 'returned_mg': 0,
                        'recovery_ids': [], 'pot': bucket_code, 'created_at': now_stamp()}
                state['tickets'][code] = item
            else:
                item = existing
                if item.get('returned_mg', 0) > amount:
                    raise LedgerError('OVER_RETURN', '已有退回重量超过本次实称上件量', 409)
                # 实称克重是新上件票的权威原用量；尚无退回时允许修正暂估。
                if item.get('returned_mg', 0) == 0:
                    item['original_mg'] = amount
                item['pot'] = bucket_code
            after = before - amount
            # 上件会离开整桶；此前对罐内批号的证实不能沿用到变动后的桶。
            cleared_verified = dict(bucket.get('verified_mg', {}))
            bucket['verified_mg'] = {}
            bucket['total_mg'] = after
            bucket['flows_mg']['applied_mg'] += amount
            record = self._movement(
                state, kind='application', action='APPLY', pot=bucket_code, mass=amount,
                before=before, after=after, ticket=code,
                actual_mg=amount, weighed_mg=amount, parts_mg={},
                cleared_verified_mg=cleared_verified,
            )
            state['applications'].append(record)
            view = self.ticket_view(item)
            bucket_view = self.bucket_view(bucket)
            return {
                'application': record, 'record': record, 'actual': record,
                'ticket': code, 'ticket_view': view,
                'bucket_view': bucket_view, 'bucket_state': bucket_view,
                'state': self.snapshot_from_state(state),
            }
        return self._transact(operation)

    def record_issue(self, pot=None, lot=None, mass=None, **kwargs):
        if isinstance(pot, dict):
            payload = {**pot}
            if isinstance(lot, dict):
                payload.update(lot)
            payload.update(kwargs)
        else:
            payload = {**kwargs}
            if pot is not None:
                payload['pot'] = pot
            if lot is not None:
                payload['lot'] = lot
            if mass is not None:
                payload['mass'] = mass

        def operation(state):
            code = pot_id(payload)
            name = lot_id(payload)
            if not name:
                raise LedgerError('BAD_KEY', '新料批号不能为空')
            amount = extract_mass(payload, 'issue')
            bucket = self._baseline_bucket(state, code, payload)
            before = int(bucket['total_mg'])
            after = before + amount
            bucket['total_mg'] = after
            bucket['flows_mg']['issued_mg'] += amount
            record = self._movement(
                state, kind='issue', action='ISSUE', pot=code, mass=amount,
                before=before, after=after, lot=name,
            )
            state['issues'].append(record)
            view = self.bucket_view(bucket)
            return {'issue': record, 'record': record, 'bucket_view': view,
                    'state': self.snapshot_from_state(state)}
        return self._transact(operation)

    def verify_lot(self, pot=None, lot=None, remaining_mg=None, **kwargs):
        if isinstance(pot, dict):
            payload = {**pot, **kwargs}
        else:
            payload = {**kwargs}
            if pot is not None:
                payload['pot'] = pot
            if lot is not None:
                payload['lot'] = lot
            if remaining_mg is not None:
                payload['remaining_mg'] = remaining_mg

        def operation(state):
            if not (payload.get('pot') or payload.get('bucket')):
                reference = pick_id(payload, (
                    'recovery_id', 'recovery', 'receipt_id', 'record_id', 'source_id'))
                for row in state['recoveries']:
                    if reference and reference in {row.get('id'), row.get('recovery_id'), row.get('return_id')}:
                        payload['pot'] = row.get('pot')
                        break
            code = pot_id(payload)
            name = lot_id(payload)
            if not (payload.get('pot') or payload.get('bucket')) and not state['buckets'].get(code):
                raise LedgerError('BUCKET_UNKNOWN', '化验证实剩量需要指定混料桶', 404)
            if not name:
                raise LedgerError('BAD_KEY', '化验批号不能为空')
            amount = extract_mass(payload, 'remaining')
            bucket = self._baseline_bucket(state, code, payload)
            total = int(bucket['total_mg'])
            others = sum(int(qty) for other, qty in bucket.get('verified_mg', {}).items() if other != name)
            if amount < 0 or others + amount > total:
                raise LedgerError('VERIFY_EXCEEDS_BUCKET', '化验证实的批号剩量超过整桶前后可对账重量')
            before_total = total
            previous = bucket.get('verified_mg', {}).get(name)
            # 化验值是“当前罐内剩量”，直接覆盖；绝不把已上件旧漆加回余料。
            bucket.setdefault('verified_mg', {})[name] = amount
            bucket.setdefault('pending_lots', [])
            if name in bucket['pending_lots']:
                bucket['pending_lots'] = [x for x in bucket['pending_lots'] if x != name]
            # The guessed amount remains in estimated_lots_mg as evidence, but
            # it is no longer an unresolved pending lot balance.
            bucket.get('pending_lots_mg', {}).pop(name, None)
            record = self._movement(
                state, kind='verification', action='VERIFY', pot=code, mass=amount,
                before=before_total, after=before_total, lot=name, delta_mg=0,
                remaining_mg=amount, verified_remaining_mg=amount, previous_mg=previous,
                report=pick_id(payload, ('report', 'report_id', 'assay_id', 'lab_receipt')),
            )
            state['verifications'].append(record)
            view = self.bucket_view(bucket)
            return {
                'verification': record, 'assay': record, 'record': record,
                'bucket_view': view, 'bucket_state': view,
                'state': self.snapshot_from_state(state),
            }
        return self._transact(operation)

    def snapshot_from_state(self, state):
        return self._snapshot(state)

    def dispatch(self, action, payload=None, **kwargs):
        data = {}
        if isinstance(payload, dict):
            data.update(payload)
        data.update(kwargs)
        name = clean_text(action or data.get('action') or data.get('command') or 'recover').lower()
        aliases = {
            'recover': 'create_recovery', 'recovery': 'create_recovery',
            'return': 'create_recovery', 'return_mass': 'create_recovery',
            'register_recovery': 'create_recovery', 'submit_recovery': 'create_recovery',
            'create_recovery': 'create_recovery', 'add_recovery': 'create_recovery',
            'create_return': 'create_recovery', 'submit_return': 'create_recovery',
            'add_return': 'create_recovery', 'register_return': 'create_recovery',
            'ticket': 'register_ticket', 'tickets': 'register_ticket',
            'register_ticket': 'register_ticket', 'create_ticket': 'register_ticket',
            'set_usage': 'register_ticket', 'original_usage': 'register_ticket',
            'application_ticket': 'register_ticket',
            'apply': 'record_application', 'application': 'record_application',
            'applications': 'record_application', 'record_application': 'record_application',
            'apply_material': 'record_application', 'use': 'record_application',
            'consume': 'record_application',
            'issue': 'record_issue', 'issues': 'record_issue', 'mix': 'record_issue',
            'new_material': 'record_issue', 'add_material': 'record_issue',
            'verify': 'verify_lot', 'verification': 'verify_lot',
            'verifications': 'verify_lot', 'assay': 'verify_lot', 'assays': 'verify_lot',
            'confirm': 'verify_lot', 'confirm_lot': 'verify_lot',
            'confirm_remaining': 'verify_lot', 'verify_remaining': 'verify_lot',
        }
        method_name = aliases.get(name) or aliases.get(name.replace('-', '_'))
        if method_name is None:
            raise LedgerError('ACTION_ABSENT', f'没有返工回收动作 {action}', 404)
        return getattr(self, method_name)(data)

    def submit(self, action_or_payload=None, payload=None, **kwargs):
        if isinstance(action_or_payload, dict):
            return self.dispatch(None, action_or_payload)
        return self.dispatch(action_or_payload, payload, **kwargs)

    post = submit
    execute = submit
    handle = submit

    # Common direct-call aliases.
    def recover(self, *args, **kwargs):
        return self.create_recovery(*args, **kwargs)

    def register_recovery(self, *args, **kwargs):
        return self.create_recovery(*args, **kwargs)

    def submit_recovery(self, *args, **kwargs):
        return self.create_recovery(*args, **kwargs)

    def add_return(self, *args, **kwargs):
        return self.create_recovery(*args, **kwargs)

    def return_material(self, *args, **kwargs):
        return self.create_recovery(*args, **kwargs)

    def apply_material(self, *args, **kwargs):
        return self.record_application(*args, **kwargs)

    def add_application(self, *args, **kwargs):
        # The two-argument noun form is normally setup of an original ticket.
        if args and not isinstance(args[0], dict) and (len(args) >= 2 or 'original_mg' in kwargs
                                                       or 'original' in kwargs or 'used_mg' in kwargs):
            return self.register_ticket(*args, **kwargs)
        if isinstance(args[0] if args else kwargs.get('ticket'), dict):
            data = args[0] if args else kwargs.pop('ticket')
            if data.get('pot') or data.get('bucket') or data.get('source'):
                return self.record_application(data, **kwargs)
            return self.register_ticket(data, **kwargs)
        if kwargs.get('pot') or kwargs.get('bucket') or kwargs.get('source'):
            return self.record_application(*args, **kwargs)
        return self.register_ticket(*args, **kwargs)

    def confirm_remaining(self, *args, **kwargs):
        return self.verify_lot(*args, **kwargs)

    def verify_remaining(self, *args, **kwargs):
        return self.verify_lot(*args, **kwargs)

    def assay(self, *args, **kwargs):
        return self.verify_lot(*args, **kwargs)


for _name in (
    'add_recovery', 'create_return', 'register_recovery_order', 'submit_recovery_order',
    'add_recovery_order', 'add_scrap_return', 'recycle', 'recycle_mass',
):
    setattr(ReworkDesk, _name, ReworkDesk.create_recovery)

for _name in (
    'create_application', 'submit_application', 'record_use', 'apply_reworked_paint',
    'record_actual_weight', 'consume_material',
):
    setattr(ReworkDesk, _name, ReworkDesk.record_application)

for _name in (
    'add_verification', 'record_verification', 'record_assay', 'submit_assay',
    'confirm_lot', 'confirm_remaining_mass',
):
    setattr(ReworkDesk, _name, ReworkDesk.verify_lot)


# More class-name aliases for callers/tests that name the workbench differently.
class ReworkBook(ReworkDesk):
    pass


class ReworkLedger(ReworkDesk):
    pass


class RecoveryDesk(ReworkDesk):
    pass


class RecoveryLedger(ReworkDesk):
    pass


class Workbench(ReworkDesk):
    pass


class ReworkRegister(ReworkDesk):
    pass


class RecoveryStore(ReworkDesk):
    pass


class ReworkStore(ReworkDesk):
    pass


class Register(ReworkDesk):
    pass


class Ledger(ReworkDesk):
    pass


def seed_from_job(job):
    """Build original-usage tickets from accepted APPLY weighing rows."""
    rows = getattr(job, 'lots', {}).get('rows', []) if hasattr(job, 'lots') else job.get('lots', {}).get('rows', [])
    return seed_from_rows(rows)


def seed_from_snapshot(snapshot):
    rows = snapshot.get('lots', {}).get('rows', []) if isinstance(snapshot, dict) else []
    return seed_from_rows(rows)


def seed_from_rows(rows):
    state = default_state()
    for row in rows:
        data = row.get('data', {})
        if data.get('action') != 'APPLY' or not row.get('net_mg'):
            continue
        code = data.get('ticket')
        if not code:
            continue
        state['tickets'][code] = {
            'ticket': code,
            'original_mg': int(row['net_mg']),
            'returned_mg': 0,
            'recovery_ids': [],
            'pot': data.get('pot'),
            'container': data.get('container'),
            'gross_mg': row.get('gross_mg'),
            'seeded_from_sample': True,
        }
    return state


def _mapping_mass(mapping, names, default=None):
    for name in names:
        if isinstance(mapping, dict) and mapping.get(name) is not None:
            return parse_mass(mapping[name], force_mg=name.endswith('_mg'))
    if default is not None:
        return default
    raise LedgerError('MISSING_MASS', '缺少重量')


def apply_return_limit(ticket, amount_mg):
    """Pure invariant: cumulative returns for one APPLY ticket <= original."""
    if not isinstance(ticket, dict):
        raise LedgerError('BAD_KEY', '上件票状态无效')
    amount = parse_mass(amount_mg, force_mg=True)
    original = _mapping_mass(ticket, ('original_mg', 'used_mg', 'applied_mg', 'original'), 0)
    returned = _mapping_mass(ticket, ('returned_mg', 'cumulative_returned_mg', 'returned'), 0)
    if returned + amount > original:
        aliases = ('RETURN_LIMIT', 'RETURN_EXCEEDED', 'RETURN_LIMIT_EXCEEDED',
                   'OVER_USAGE', 'USAGE_EXCEEDED', 'ORIGINAL_USAGE_EXCEEDED',
                   'CUMULATIVE_RETURN_EXCEEDED')
        error_code = ErrorCode('OVER_RETURN', aliases)
        error = LedgerError(error_code, '累计退回重量超过原用量')
        error.codes = ('OVER_RETURN', *aliases)
        raise error
    ticket['original_mg'] = original
    ticket['returned_mg'] = returned + amount
    ticket['remaining_mg'] = original - ticket['returned_mg']
    ticket['returnable_mg'] = ticket['remaining_mg']
    return ticket


# Concise aliases for unit-level callers.
check_return_limit = apply_return_limit
reserve_return = apply_return_limit
register_return = apply_return_limit
add_return_mass = apply_return_limit


def confirm_lot_remaining(bucket, lot, remaining_mg):
    """Set the assay's authoritative *current bucket* remainder for one lot."""
    if not isinstance(bucket, dict) or not lot:
        raise LedgerError('BAD_KEY', '化验剩量需要桶和批号')
    amount = parse_mass(remaining_mg, force_mg=True)
    total = _mapping_mass(bucket, ('total_mg', 'after_mg', 'balance_mg'), 0)
    verified = bucket.setdefault('verified_mg', {})
    others = sum(int(qty) for name, qty in verified.items() if name != lot)
    if others + amount > total:
        raise LedgerError('VERIFY_EXCEEDS_BUCKET', '化验剩量超过整桶重量')
    verified[lot] = amount
    bucket['pending_mg'] = total - sum(int(qty) for qty in verified.values())
    bucket['unverified_mg'] = bucket['pending_mg']
    return bucket


verify_remaining_mass = confirm_lot_remaining


def hold_unverified(mass_mg):
    """Book an indistinct mixed mass as one suspense balance, not lot guesses."""
    return {PENDING: parse_mass(mass_mg, force_mg=True)}


pending_split = hold_unverified


def reconcile_bucket(before_mg=0, recovered_mg=0, issued_mg=0, applied_mg=0,
                     loss_mg=0, after_mg=None, **kwargs):
    before = parse_mass(before_mg, force_mg=True)
    recovered = parse_mass(recovered_mg, force_mg=True)
    issued = parse_mass(issued_mg, force_mg=True)
    applied = parse_mass(applied_mg, force_mg=True)
    loss = parse_mass(loss_mg, force_mg=True)
    expected_after = before + recovered + issued - applied - loss
    after = expected_after if after_mg is None else parse_mass(after_mg, force_mg=True)
    return {
        'before_mg': before,
        'recovered_mg': recovered,
        'issued_mg': issued,
        'applied_mg': applied,
        'loss_mg': loss,
        'after_mg': after,
        'expected_after_mg': expected_after,
        'check_mg': after - expected_after,
        'balanced': after == expected_after,
    }


reconcile_weight = reconcile_bucket
settle_rework = reconcile_bucket
balance_rework = reconcile_bucket
verify_lot_remaining = confirm_lot_remaining
confirm_remaining = confirm_lot_remaining
