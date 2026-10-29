"""Versioned packing-list maintenance with reversible removal."""
PACKS = ('妈妈入院包', '宝宝用品包', '证件资料包')
LEVELS = ('必备', '建议', '可选')
FIELDS = ('name', 'note', 'pack', 'quantity', 'level', 'assignee')


def content(c, fid, data, current):
    import domain as d
    obj = dict(current)
    for key in FIELDS:
        if key not in data:
            continue
        value = data[key]
        if key == 'name':
            value = d.text(value, 100)
        elif key == 'note':
            if not isinstance(value, str) or len(value) > 500:
                raise d.Problem(400, '备注最多 500 字')
        elif key == 'quantity':
            if type(value) is not int or not 1 <= value <= 100:
                raise d.Problem(400, '数量需为 1—100 的整数')
        elif key == 'pack' and value not in PACKS:
            raise d.Problem(400, '请选择有效的物品分类')
        elif key == 'level' and value not in LEVELS:
            raise d.Problem(400, '请选择必备、建议或可选')
        elif key == 'assignee':
            if not isinstance(value, str):
                raise d.Problem(400, '请选择有效的负责人')
            d.member(c, fid, value)
        obj[key] = value
    return obj


def cancel_pending(c, fid, obj):
    import domain as d
    refs = {obj['id']}
    for a in d.rows(c, 'approval', fid):
        if a['resource'] == 'item' and a['target'] == obj['id'] and a['status'] == 'pending':
            refs.add(a['id'])
    d.invalidate_approvals(c, fid, 'item', obj['id'])
    for notice in d.rows(c, 'notification', fid):
        if notice.get('ref') in refs and not notice.get('read') and not notice.get('cancelled'):
            notice['cancelled'] = True
            d.put(c, 'notification', notice, fid)


def mutate(c, user, fid, action, data):
    import domain as d
    me = d.member(c, fid, user)
    if action == 'item.create':
        d.member(c, fid, user, ('dad',))
        obj = content(c, fid, data, {'id': d.uid(), 'status': 'todo', 'quantity': 1,
                     'pack': PACKS[0], 'level': '建议', 'note': '', 'assignee': user, 'created_at': d.now()})
        obj['name'] = d.text(obj.get('name'), 100)
        d.put(c, 'item', obj, fid)
        if obj['assignee'] != user:
            d.notify(c, fid, obj['assignee'], '你收到一项新的物品准备安排', obj['id'])
    else:
        obj = d.require_active(c, fid, 'item', data.get('id'))
        d.version(obj, data)
        if action in ('item.delete', 'item.restore'):
            d.member(c, fid, user, ('dad',))
            deleting = action == 'item.delete'
            if bool(obj.get('deleted')) == deleting:
                return {'id': obj['id']}
            if deleting:
                cancel_pending(c, fid, obj)
                if obj['status'] == 'pending':
                    obj['status'] = 'in_progress'
                obj.update(deleted=True, deleted_at=d.now(), deleted_by=user)
            else:
                # A departed executor is never silently replaced with another person.
                previous_assignee = obj['assignee']
                obj = content(c, fid, {'assignee': data.get('assignee', obj['assignee'])}, obj)
                if obj['assignee'] != previous_assignee and obj['status'] == 'done':
                    obj['status'] = 'todo'
                    obj['completed_at'] = None
                obj.update(deleted=False, restored_at=d.now(), restored_by=user)
                if obj['assignee'] != user and obj['status'] not in ('done', 'skipped', 'cancelled'):
                    d.notify(c, fid, obj['assignee'], '有一项物品准备安排已恢复', obj['id'])
        else:
            if obj.get('deleted'):
                raise d.Problem(409, '物品已删除，请先由统筹者恢复')
            if me['role'] == 'member' and obj['assignee'] != user:
                raise d.Problem(403, '只能操作自己负责的物品')
            edited = any(key in data for key in FIELDS)
            if edited:
                d.member(c, fid, user, ('dad',))
            revised = content(c, fid, data, obj)
            changed = any(revised.get(key) != obj.get(key) for key in FIELDS)
            desired = data.get('status', obj['status'])
            if desired not in ('todo', 'in_progress', 'done', 'skipped', 'cancelled') and not (desired == 'pending' and 'status' not in data):
                raise d.Problem(400, '状态不正确')
            if desired != obj['status']:
                if obj['assignee'] != user:
                    raise d.Problem(403, '只能提交自己负责物品的执行结果')
                if changed:
                    raise d.Problem(400, '请先保存物品内容，再提交执行结果')
            elif changed:
                if obj['status'] in ('pending', 'done'):
                    revised['status'] = 'in_progress' if obj['status'] == 'pending' else 'todo'
                    revised['completed_at'] = None
            if not changed and desired == obj['status']:
                return {'id': obj['id']}
            cancel_pending(c, fid, obj)
            previous = obj
            obj = revised
            if desired != previous['status']:
                f = d.get(c, 'family', fid)
                if desired == 'done' and f['mode'] == 'all' and me['role'] != 'mom':
                    result = d.propose(c, fid, user, 'item', 'complete', {'version': obj['version'] + 1}, obj['id'])
                    obj['status'] = 'pending'
                    d.put(c, 'item', obj, fid)
                    d.audit(c, fid, user, action, {'id': obj['id']})
                    return result
                obj['status'] = desired
                obj['completed_at'] = d.now() if desired == 'done' else None
            if previous['assignee'] != obj['assignee'] and obj['assignee'] != user:
                d.notify(c, fid, obj['assignee'], '你收到一项物品准备分工', obj['id'])
        d.put(c, 'item', obj, fid)
        if action=='item.update' and obj['status']=='done' and previous['status']!='done':
            import engagement
            engagement.record(c,fid,'item',obj)
    d.audit(c, fid, user, action, {'id': obj['id']})
    return {'id': obj['id']}
