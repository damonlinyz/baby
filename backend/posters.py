"""Only the fixed public template may be encoded in a platform mini program code."""
import os
import json
import urllib.parse
import urllib.request


def mini_code():
    import domain as d,push_notifications
    if os.environ.get('WX_POSTER_ENABLED')!='1' or not all(os.environ.get(k) for k in ('WX_APP_ID','WX_APP_SECRET')):
        raise d.Problem(503,'小程序码尚未配置，可生成带网页二维码的通用海报')
    try:
        token=push_notifications.get_token()
        payload={'scene':'template','page':'pages/main/main','check_path':True,'env_version':'release','width':280}
        req=urllib.request.Request('https://api.weixin.qq.com/wxa/getwxacodeunlimit?access_token='+urllib.parse.quote(token,safe=''),
             data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as response:
            image=response.read(1000001)
        if len(image)>1000000 or not image.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('platform did not provide a PNG')
        return image
    except Exception:raise d.Problem(503,'小程序码暂时不可用，请使用网页二维码海报')
