"""Explicit lifecycle corrections. Original events and child records are retained."""

def correct_birth(c, fid, user, pregnancy, data, event_id):
    import domain as d
    if pregnancy['subject_id'] != user:
        raise d.Problem(403, '误录分娩须由健康资料本人纠正')
    if data.get('confirm_correction') is not True or not data.get('reason', '').strip():
        raise d.Problem(400, '请确认这是误录，并填写纠正说明')
    births = [e for e in d.rows(c, 'pregnancy_event', fid)
              if e['pregnancy_id'] == pregnancy['id'] and e['event'] == 'birth']
    original = next((e for e in births if e['id'] == data.get('birth_event_id')), None)
    latest = max(births, key=lambda e: e['revision'], default=None)
    if not original or original != latest or original['before']['state'] != 'pregnant':
        raise d.Problem(409, '分娩原始事件不匹配，请刷新后重新核对')
    if original['id'] in pregnancy.get('corrected_birth_events', []):
        raise d.Problem(409, '这次误录已经纠正')
    babies = [b for b in d.rows(c, 'baby', fid)
              if b['pregnancy_id'] == pregnancy['id'] and b['status'] != 'entry_corrected']
    if any(b['status'] not in ('growing', 'unconfirmed') for b in babies) or any(e['baby_id'] in {b['id'] for b in babies} and e.get('event') != 'birth_confirmation' for e in d.rows(c, 'baby_event', fid)):
        raise d.Problem(409, '宝宝已有后续状态记录，不能直接纠正为尚未分娩')
    for baby in babies:
        before_baby = dict(baby)
        baby.update(status='entry_corrected', corrected_by_event=event_id, corrected_at=d.now())
        after_baby = d.put(c, 'baby', baby, fid)
        d.put(c, 'baby_event', {'id': d.uid(), 'event': 'birth_correction', 'pregnancy_id': pregnancy['id'],
                               'baby_id': baby['id'], 'actor': user, 'before': before_baby, 'after': after_baby,
                               'reason': data['reason'].strip(), 'effective_date': str(d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))),
                               'created_at': d.now(), 'corrected_by_event': event_id}, fid)
    for task in d.rows(c, 'task', fid):
        if task.get('pregnancy_id') != pregnancy['id']:
            continue
        if task.get('template_key') and task.get('stage') == 'postpartum' and task['status'] not in ('done', 'skipped', 'cancelled'):
            d.invalidate_approvals(c, fid, 'task', task['id'])
            d.reset_reminder(c, fid, task)
            task.update(status_before_lifecycle=task['status'], status='cancelled', lifecycle_cancelled_by=event_id)
            d.put(c, 'task', task, fid)
        elif task.get('template_key') and task['status'] == 'cancelled' and task.get('lifecycle_cancelled_by') == original['id']:
            task['status'] = 'in_progress' if task.get('status_before_lifecycle') == 'pending' else task.get('status_before_lifecycle', 'todo')
            task.pop('lifecycle_cancelled_by', None)
            task.pop('status_before_lifecycle', None)
            d.reset_reminder(c, fid, task)
            d.put(c, 'task', task, fid)
    pregnancy.update(state='pregnant', birth_date=None, birth_event_id=None, paused=True,
                     corrected_birth_events=pregnancy.get('corrected_birth_events', []) + [original['id']])


# These are general family milestones, separate from maternal recovery support.
QUIET_TEMPLATE_KEYS = {'family-time', 'growth-archive', 'birthday-plan'}
BABY_TEMPLATE_KEYS = QUIET_TEMPLATE_KEYS | {'birth-document', 'insurance', 'documents-store'}


def can_read_babies(c, fid, user, pregnancy):
    import domain as d
    if not d.health_members_active(c, fid, user, pregnancy):
        return False
    return user == pregnancy['subject_id'] or any(
        g['subject'] == pregnancy['subject_id'] and g['grantee'] == user
        and g['scope'] == 'baby_read' and not g.get('revoked_at')
        for g in d.rows(c, 'consent', fid))


def quiet(c, fid, pregnancy_id):
    import domain as d
    return any(b['pregnancy_id'] == pregnancy_id and b['status'] in ('unconfirmed', 'hospitalized', 'lost')
               for b in d.rows(c, 'baby', fid))


def record_baby(c, fid, user, data):
    import domain as d
    d.member(c, fid, user)
    baby = d.require_active(c, fid, 'baby', data.get('id'))
    d.version(baby, data)
    pregnancy = d.require_active(c, fid, 'pregnancy', baby['pregnancy_id'])
    if pregnancy['subject_id'] != user or not d.health_members_active(c, fid, user, pregnancy):
        raise d.Problem(403, '宝宝状态由本孕次本人以监护人身份记录，健康代录授权不包含此权限')
    if data.get('guardian_consent') is not True:
        raise d.Problem(400, '请确认监护人身份并单独同意处理宝宝状态')
    if pregnancy.get('archived') or pregnancy['state'] != 'postpartum' or baby['status'] == 'entry_corrected':
        raise d.Problem(409, '当前记录不能修改宝宝状态')
    status = data.get('status')
    if status not in ('growing', 'hospitalized', 'lost'):
        raise d.Problem(400, '宝宝状态不正确')
    current = d.today(d.get(c, 'family', fid).get('timezone', 'Asia/Shanghai'))
    birth = d.date(data.get('birth_date', baby['birth_date']))
    if birth > current:
        raise d.Problem(400, '宝宝出生日期不能在未来')
    effective = d.date(data.get('date'))
    if not birth <= effective <= current:
        raise d.Problem(400, '状态日期需在出生日期与家庭时区的今天之间')
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 300:
        raise d.Problem(400, '说明最多 300 字，无需填写原因或诊疗信息')
    if baby['status'] == 'lost' and status != 'lost' and (data.get('correct_previous') is not True or not reason.strip()):
        raise d.Problem(409, '纠正已离世记录需要明确确认误录并填写说明')
    before = dict(baby)
    baby.update(name=d.text(data.get('name', baby['name']), 30), birth_date=str(birth), status=status, status_date=str(effective))
    if 'birth_date' in data:
        baby['birth_date_follows_pregnancy'] = False
    saved = d.put(c, 'baby', baby, fid)
    event_id = d.uid()
    d.put(c, 'baby_event', {'id': event_id, 'event': 'status_record', 'pregnancy_id': pregnancy['id'], 'baby_id': baby['id'],
                           'actor': user, 'before': before, 'after': saved, 'reason': reason.strip(),
                           'effective_date': str(effective), 'guardian_notice_version': '1.0',
                           'correct_previous': data.get('correct_previous') is True, 'created_at': d.now()}, fid)
    # Invalidate queued messages without changing the mother's postpartum state.
    pregnancy['revision'] += 1
    d.put(c, 'pregnancy', pregnancy, fid)
    if quiet(c, fid, pregnancy['id']):
        for task in d.rows(c, 'task', fid):
            if task.get('pregnancy_id') == pregnancy['id'] and task.get('template_key') in QUIET_TEMPLATE_KEYS and task['status'] not in ('done', 'skipped', 'cancelled'):
                d.invalidate_approvals(c, fid, 'task', task['id'])
                d.reset_reminder(c, fid, task)
                task.update(status='cancelled', quiet_cancelled_by=event_id)
                d.put(c, 'task', task, fid)
    else:
        restore_postpartum_tasks(c, fid, pregnancy)
        d.templates.sync(c, fid, pregnancy)
    d.audit(c, fid, user, 'health.baby.updated', {'pregnancy_id': pregnancy['id'], 'baby_id': baby['id']})
    return {'id': baby['id'], 'event_id': event_id}


def preview_baby(c, fid, user, data):
    import domain as d
    if not isinstance(data, dict):
        raise d.Problem(400, '操作参数需要为对象')
    d.member(c, fid, user)
    token = d.transition_fingerprint(c, user, fid, data)
    before = {t['id']: t for t in d.rows(c, 'task', fid)}
    c.execute('SAVEPOINT baby_preview')
    try:
        result = record_baby(c, fid, user, data)
        baby = d.get(c, 'baby', result['id'])
        changes = [{'id': t['id'], 'title': t['title']} for t in d.rows(c, 'task', fid)
                   if t['id'] in before and before[t['id']]['status'] != 'cancelled' and t['status'] == 'cancelled']
        result = {'preview_token': token, 'baby': baby, 'cancelled_tasks': changes,
                  'created_tasks': [{'id': t['id'], 'title': t['title']} for t in d.rows(c, 'task', fid) if t['id'] not in before],
                  'restored_tasks': [{'id': t['id'], 'title': t['title']} for t in d.rows(c, 'task', fid) if t['id'] in before and before[t['id']]['status'] == 'cancelled' and t['status'] != 'cancelled'],
                  'maternal_state': d.get(c, 'pregnancy', baby['pregnancy_id'])['state']}
    finally:
        c.execute('ROLLBACK TO SAVEPOINT baby_preview')
        c.execute('RELEASE SAVEPOINT baby_preview')
    return result


def restore_postpartum_tasks(c, fid, pregnancy):
    """Reopen only tasks cancelled by a recorded mistaken-birth correction."""
    import domain as d
    corrections = {e['id'] for e in d.rows(c, 'pregnancy_event', fid)
                   if e['pregnancy_id'] == pregnancy['id'] and e['event'] == 'correct_birth_record'}
    for task in d.rows(c, 'task', fid):
        if (not task.get('template_key') or task.get('pregnancy_id') != pregnancy['id'] or task.get('stage') != 'postpartum'
                or task['status'] != 'cancelled' or task.get('lifecycle_cancelled_by') not in corrections):
            continue
        if task.get('template_key') in QUIET_TEMPLATE_KEYS and quiet(c, fid, pregnancy['id']):
            continue
        previous = task.pop('status_before_lifecycle', 'todo')
        task['status'] = 'in_progress' if previous == 'pending' else previous
        task.pop('lifecycle_cancelled_by', None)
        d.reset_reminder(c, fid, task)
        d.put(c, 'task', task, fid)
