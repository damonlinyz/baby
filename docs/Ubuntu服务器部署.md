# Ubuntu 24.04 首次部署

本轮按用户提供的服务器信息准备：Ubuntu 24.04、约 8 GiB 内存，约 6.3 GiB 可用；40 GB 根盘剩余约 7.8 GB。已有 Caddy 监听 80/443，Docker 暴露 8081 和本机 5432，Ollama 监听 11434。不能把它当作空服务器，也不能通过清理 Docker、模型或覆盖 Caddy 来腾空间。

独立服务域名采用 `baba.onedrivememory.com`，A 记录指向用户的服务器。用户截图已显示记录启用，之后回传的安装输出确认本机后端安装和健康检查通过，版本为 `3e1fb985a0bd7df8b85cfb69ded0ea40fe4b95ed`。这些证据不等于公网 HTTPS 已接通；下载页继续留在 GitHub Pages，安装包留在 Releases。

## 第一步：仅本机安装

`tools/server_install.py` 是首次安装工具，不是升级工具。默认只输出计划，不改系统；`--apply` 才安装。必须给出固定完整 Git SHA，不接受 `main` 或可变标签。安装脚本本身也应下载自固定提交，在 root 执行前核对提供的 SHA-256；先保存并查看文件，不把未经检查的可变网址直接管道到 root Shell。

```sh
python3 /root/baba-server-install.py \
  --revision 实际完整的40位提交SHA \
  --domain baba.onedrivememory.com

python3 /root/baba-server-install.py \
  --revision 实际完整的40位提交SHA \
  --domain baba.onedrivememory.com \
  --apply
```

安装器检查 root、Ubuntu 24.04、systemd、Python、Git、时区数据、剩余磁盘、账号与目录冲突及 8766 端口。不安装系统软件，不自动清理文件。缺依赖时停止，按实际输出处理。只有所有检查通过，才从固定公开仓库浅拉取源码并核对实际提交与文件类型。

| 项目 | 位置与行为 |
|---|---|
| 代码 | `/opt/baba`，root 所有，服务账号只读 |
| 服务账号 | 新建 `baba`，禁止交互登录，无 sudo |
| 数据 | `/var/lib/baba/private/baba.sqlite3`，含独立删除记录文件 |
| 私有目录 | `0700`，`baba` 所有；数据库文件由程序以 `0600` 保存 |
| 进程配置 | `/etc/baba/service.env`，root 所有、`0600` |
| 服务 | `/etc/systemd/system/baba.service`，`User=baba`，仅数据目录可写 |
| 监听 | 固定 `127.0.0.1:8766`，不开放公网端口 |
| 公共来源 | `https://baba.onedrivememory.com`，为后续 HTTPS 准备安全 Cookie |
| Caddy | 仅生成 `/etc/baba/Caddyfile.fragment`，不修改或重载现有配置 |

支付、AI、微信订阅默认关闭。安装器不配置 PostgreSQL，不调用已有 Ollama，也不修改现有 Docker、SSH、防火墙或网站。健康检查通过后才设置自启动；健康检查失败会停止新服务，保留文件与数据供诊断。已有账号、组、目录或服务时拒绝覆盖，不要通过删除数据库来重跑。

安装器没有安装备份定时器：**本机健康检查成功不代表公网服务或灾备已经完成。** 当前 SQLite 也没有字段级加密。业务数据仍处于服务器磁盘上，需要访问隔离、备份及恢复验收后再保存重要真实资料。

安装后只读检查：

```sh
systemctl --no-pager --full status baba.service
curl --fail --silent --show-error http://127.0.0.1:8766/api/health
systemctl show --property=ExecStart --value caddy.service
```

不要分享 `service.env`、支付证书、SSH 私钥、数据库或可能含用户资料的完整日志。需要排错时，先查看服务器本机错误，再提供不含凭据和家庭内容的相关行。

## 第二步：接入已有 Caddy

用户回传的 `ExecStart` 已确认服务使用 `/usr/bin/caddy run --environ --config /etc/caddy/Caddyfile`。现在使用独立工具 `tools/server_proxy.py` 接入，不重装 App、不升级 `/opt/baba`、不修改数据库。工具同样保存到服务器后再执行，使用提供的固定提交网址和 SHA-256 校验，不从可变分支直接执行 root 管道脚本。

```sh
# 默认只预览，不检查网络也不修改系统。
python3 /root/baba-server-proxy.py \
  --domain baba.onedrivememory.com --address 163.7.5.202

# 明确执行 DNS / 服务检查、备份、完整验证和重载。
python3 /root/baba-server-proxy.py \
  --domain baba.onedrivememory.com --address 163.7.5.202 --apply

# 后续只读复验，不重新修改或重载配置。
python3 /root/baba-server-proxy.py \
  --domain baba.onedrivememory.com --address 163.7.5.202 --verify
```

实际执行前要求：

- root 账号；配置及父目录是受保护的 root 普通路径；App 和 Caddy 已运行。
- 进程启动配置路径、已安装 App 的公共域名与命令一致。
- 域名解析结果恰为指定公网 IPv4；未核对的 AAAA/CDN 记录或 DNS 尚未生效会停止，不自动改 DNS。
- 本机 `/api/health` 成功，且现有完整 Caddyfile 能转换为配置。
- 不存在同名非本工具管理的站点或片段，避免覆盖现有业务。

工具先保存原文件到 `/etc/caddy/Caddyfile.before-baba-日期-随机后缀`，权限 `0600`，原配置可能含凭据，因此备份也不应发送或提交 Git。新站点放在 `/etc/caddy/baba-app.caddy`，候选配置完整保留原字节，再添加独立 import。以实际服务账号验证候选完整配置，验证通过且原文件未被并发修改后才原子替换、`systemctl reload caddy.service`。不会 restart 服务或改动其他系统服务。

生成片段为独立站点：

```caddyfile
baba.onedrivememory.com {
    encode zstd gzip
    request_body {
        max_size 128KB
    }
    reverse_proxy 127.0.0.1:8766
}
```

候选验证失败不会重载；重载失败会恢复原文件、移除新片段并重载原配置。若恢复本身失败，会明确输出备份位置并要求在服务器核对，不声称恢复成功。并发修改不会被盲目覆盖。重复执行已核对的同一托管片段不会重复添加 import；发现陌生片段则拒绝覆盖。

成功应用返回 `mode=configured`、`proxy=reloaded`。**只有 `https=passed` 才表示本次 HTTPS 健康检查也通过。** Caddy 签发证书有时需要等待；`pending` 保留已验证的配置，稍后用 `--verify` 复查。验证保持 TLS 证书校验，不使用 `curl -k`；服务端出口回环不通也可能导致服务器自检失败，需要另从浏览器或独立网络复验。使用自动 HTTPS 需确保现有 80/443 入口、安全组及域名解析满足证书签发条件；不额外开放 8766。

当前应用直接看到反向代理的本机地址，部分按 IP 的限流会聚合代理后的用户。不能通过无条件信任 `X-Forwarded-For` 来修补；大范围对外开放前需独立实现可信代理边界、访问压力与限流验证。本轮首先用于自用部署准备，不宣称完成公众生产验收。

公网接通后需复验首页与健康检查、注册登录、安全 Cookie、家庭协作、服务重启后的数据持久化，以及已有站点仍能正常使用。尚未完成正式 App 构建时可先通过浏览器验收；不得把模拟客户端测试当作真机结果。

## 第三步：备份与剩余空间

现有 [备份自动任务](备份自动任务.md) 和 [异地备份与发布工具](异地备份与发布工具.md) 与本次数据目录兼容，但必须实际配置恢复证书、保留周期、独立存储并完成恢复演练。恢复私钥不随备份上传，也不保存在公共仓库。

根盘已使用约 80%，本轮不下载模型或 Docker 大镜像，不把正式 APK 放入 Git 或服务器代码目录。备份保留需要按数据库实际大小和剩余空间设置，不能直接照搬会占满磁盘的批次数。先检查占用、必要时扩盘，不擅自删除既有业务数据。异地备份不能只落在这台服务器的另一个目录。

## 工具验证与实际状态

```sh
python -m unittest discover -s tests -p test_server_install.py -v
python -m unittest discover -s tests -p test_server_proxy.py -v
python tools/server_install.py --revision 完整提交SHA --domain baba.onedrivememory.com
python tools/server_proxy.py --domain baba.onedrivememory.com --address 163.7.5.202
```

测试在临时目录模拟账号、命令、源码和健康检查，验证计划不写系统、拒绝注入与覆盖、固定提交、异常停止和数据保留。HTTPS 工具另有失败回滚、二次恢复失败、并发编辑保留、重复接入、DNS/AAAA 不匹配、待签发和只读复验测试。原站点加新 App 的组合配置用官方 Caddy 2.10.2 实际校验通过，下载二进制前核对了 GitHub 发布摘要，没有启动监听服务。systemd 单元也进行语法检查。

工作环境没有直接登录用户服务器；用户已经通过控制台完成第一次 App 安装，本轮提供的 HTTPS 接入脚本还需用户执行。实际公网 HTTPS、真实备份和恢复以服务器后续输出为准，模拟命令测试不冒充远端运维成功。
