from src.utils.time import iso


def dataset(item):
    return {'id': item.id, 'name': item.name, 'filename': item.filename, 'row_count': item.row_count,
            'checksum': item.checksum, 'author': item.author, 'created_at': iso(item.created_at)}


def run(item, detail=False):
    item_view = {'id': item.id, 'dataset_id': item.dataset_id, 'name': item.name, 'status': item.status,
                 'author': item.author, 'review': item.review, 'decision': item.decision,
                 'created_at': iso(item.created_at), 'finished_at': iso(item.finished_at),
                 'scheduled_at': iso(item.scheduled_at), 'schedule_id': item.schedule_id,
                 'has_export': bool(item.export_file), 'error': item.error}
    if detail:
        item_view.update(params=item.params, result=item.result, review_note=item.review_note)
    return item_view


def schedule(item):
    from src.domain.spec import SPEC
    return {'id': item.id, 'name': item.name, 'dataset_id': item.dataset_id, 'recipient': item.recipient,
            'next_at': iso(item.next_at), 'repeat_daily': item.repeat_daily, 'enabled': item.enabled,
            'next_report_day': item.params.get(SPEC['schedule_date_param']) if item.enabled else None}


def delivery(item):
    return {'id': item.id, 'run_id': item.run_id, 'recipient': item.recipient,
            'state': item.state, 'detail': item.detail, 'created_at': iso(item.created_at)}


def pool_view(item):
    return {'id': item.id, 'code': item.code, 'name': item.name,
            'trigger_date': item.trigger_date.isoformat() if item.trigger_date else None,
            'source_run_id': item.source_run_id, 'dwell_min': item.dwell_min,
            'enabled': item.enabled, 'updated_at': iso(item.updated_at)}


def edge_view(item):
    return {'id': item.id, 'node_a': item.node_a, 'node_b': item.node_b,
            'travel_min': item.travel_min, 'blocked': item.blocked,
            'note': item.note, 'updated_at': iso(item.updated_at)}


def shift_view(item):
    return {'id': item.id, 'shift_date': item.shift_date.isoformat(), 'team': item.team,
            'start_clock': item.start_clock.isoformat(timespec='minutes'),
            'end_clock': item.end_clock.isoformat(timespec='minutes'),
            'note': item.note, 'created_at': iso(item.created_at)}


def plan_view(item):
    return {'id': item.id, 'shift_id': item.shift_id, 'version': item.version, 'state': item.state,
            'route': item.route, 'trigger_snapshot': item.trigger_snapshot,
            'on_time_count': item.on_time_count, 'total_min': item.total_min,
            'trigger_reason': item.trigger_reason, 'author': item.author,
            'created_at': iso(item.created_at), 'activated_at': iso(item.activated_at)}


def checkin_view(item):
    return {'id': item.id, 'shift_id': item.shift_id, 'plan_id': item.plan_id, 'seq': item.seq,
            'pool_code': item.pool_code, 'kind': item.kind,
            'event_at': iso(item.event_at), 'actor': item.actor, 'note': item.note,
            'created_at': iso(item.created_at)}
