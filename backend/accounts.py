"""Credential changes require fresh proof; recovery codes are one-use and hash-only."""
import hashlib
import hmac
import secrets


def settings(c,user):
    import domain as d
    recovery=next((r for r in d.rows(c,'account_recovery') if r['user_id']==user),None)
    return {'recovery_ready':bool(recovery and not recovery.get('used')),
            'wechat_bound':any(r['user_id']==user for r in d.rows(c,'wechat_identity'))}


def prove(c,user,data,identity=None):
    import domain as d
    if identity is not None:
        if not any(r['user_id']==user and r['appid']==identity['appid'] and r['openid']==identity['openid'] for r in d.rows(c,'wechat_identity')):
            raise d.Problem(403,'请使用当前账号已绑定的微信身份验证')
    else:
        row=c.execute('SELECT password FROM users WHERE id=?',(user,)).fetchone()
        if not row or not d.password_ok(data.get('password'),row['password']):raise d.Problem(403,'当前密码不正确，请重新验证')


def issue_recovery(c,user,data,identity=None):
    import domain as d
    prove(c,user,data,identity)
    code=secrets.token_urlsafe(32)
    d.put(c,'account_recovery',{'id':user,'user_id':user,'digest':hashlib.sha256(code.encode()).hexdigest(),'used':False,'created_at':d.now()})
    return {'code':code,'notice':'仅本次显示；请离线保管。重新生成立即使旧码失效，成功找回后此码作废。'}


def change_password(c,user,data,identity=None):
    import domain as d
    prove(c,user,data,identity)
    password=d.password_hash(data.get('new_password'))
    c.execute('UPDATE users SET password=? WHERE id=?',(password,user))
    # Password changes also invalidate saved recovery proofs and every device session.
    c.execute("DELETE FROM records WHERE kind='account_recovery' AND id=?",(user,))
    c.execute('DELETE FROM sessions WHERE user_id=?',(user,))
    return {'ok':True,'signed_out':True}


def recover(c,data):
    import domain as d
    login=str(data.get('login','')).lower().strip();code=data.get('code')
    digest=hashlib.sha256(code.encode()).hexdigest() if isinstance(code,str) and len(code)<=200 else ''
    row=c.execute('SELECT id FROM users WHERE login=?',(login,)).fetchone()
    proof=next((r for r in d.rows(c,'account_recovery') if row and r['user_id']==row['id']),None)
    if not proof or proof.get('used') or not hmac.compare_digest(proof['digest'],digest):
        raise d.Problem(400,'账号或恢复码无效；未保存恢复码时请使用原登录方式')
    password=d.password_hash(data.get('new_password'))
    c.execute('UPDATE users SET password=? WHERE id=?',(password,row['id']))
    proof.update(used=True,digest='');d.put(c,'account_recovery',proof)
    c.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
    return {'ok':True,'signed_out':True}


def delete_verified(c,user,data,identity):
    import domain as d
    if data.get('confirmed') is not True:raise d.Problem(400,'请确认删除范围')
    prove(c,user,data,identity)
    return d.delete_verified_account(c,user)
