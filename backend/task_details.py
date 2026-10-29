"""Bounded task instructions and live references to published life knowledge."""


def validate(c, data, defaults=False):
    import domain as d
    result = {}
    if 'steps' in data or defaults:
        steps = data.get('steps', [])
        if not isinstance(steps, list) or len(steps) > 12:
            raise d.Problem(400, '任务步骤需为列表，最多 12 条')
        result['steps'] = [d.text(step, 200) for step in steps]
    if 'knowledge_ids' in data or defaults:
        ids = data.get('knowledge_ids', [])
        if not isinstance(ids, list) or len(ids) > 5 or any(not isinstance(id, str) for id in ids) or len(set(ids)) != len(ids):
            raise d.Problem(400, '关联知识最多 5 篇，且不能重复')
        published = {k['id'] for k in d.published_knowledge(c)}
        if any(id not in published for id in ids):
            raise d.Problem(409, '关联知识已下架或不可用，请重新选择')
        result['knowledge_ids'] = list(ids)
    return result


def decorate(obj, knowledge):
    visible = {k['id']: k for k in knowledge}
    ids = [id for id in obj.get('knowledge_ids', []) if id in visible]
    return dict(obj, steps=obj.get('steps', []), knowledge_ids=ids,
                linked_knowledge=[{'id': id, 'title': visible[id]['title'], 'source': visible[id]['source'],
                                   'content_version': visible[id]['content_version']} for id in ids])


def read_knowledge(c, fid, user, task_id, knowledge_id):
    import domain as d
    me = d.member(c, fid, user)
    task = d.require_active(c, fid, 'task', task_id)
    if not d.can_view_task(c, fid, user, task) or (me['role'] == 'member' and task['assignee'] != user):
        raise d.Problem(403, '没有查看此任务资料的权限')
    if knowledge_id not in task.get('knowledge_ids', []):
        raise d.Problem(409, '任务关联内容已变更，请重新打开任务')
    knowledge = next((k for k in d.published_knowledge(c) if k['id'] == knowledge_id), None)
    if not knowledge:
        raise d.Problem(409, '该内容已下架或到期，请重新选择')
    return {'knowledge': knowledge}


# These instructions cover everyday organization only, not medical schedules or care.
CONTENT = {
    '陪伴': (['先确认对方现在是否方便聊一会儿', '听完对方想表达的需要，不急着下结论', '共同决定一件具体、力所能及的小事'], ['base-0', 'base-3']),
    '资料准备': (['向实际办理机构核对材料与流程', '由资料本人或监护人确认存放位置与使用范围', '记录尚缺材料和下一步负责人，不公开私密资料'], ['base-2']),
    '家庭协作': (['一起确认目前需要分担的事项', '按实际精力指定负责人和可行的时间', '完成后核对是否需要调整后续安排'], ['base-2', 'base-0']),
    '物品准备': (['先盘点家中已有物品', '结合机构要求与实际需要记录缺口', '只补需要的物品，注明负责人和数量'], ['base-1']),
    '出行准备': (['核对实际目的地、入口和联系信息', '确认可用交通方式与备用方案', '把必要信息交给需要知晓的家人'], ['base-2']),
    '生活准备': (['询问家人目前最需要哪些生活支持', '按实际空间和精力整理常用物品', '一起核对是否方便日常拿取和休息'], ['base-3', 'base-2']),
}


def template_content(c, category):
    import domain as d
    steps, ids = CONTENT[category]
    available = {k['id'] for k in d.published_knowledge(c)}
    return {'steps': list(steps), 'knowledge_ids': [id for id in ids if id in available],
            'template_source': '产品编辑 · 一般生活事务模板'}
