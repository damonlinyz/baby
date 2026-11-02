# Ubuntu 24.04 首次部署

本轮按用户提供的服务器信息准备：Ubuntu 24.04、约 8 GiB 内存，约 6.3 GiB 可用；40 GB 根盘剩余约 7.8 GB。已有 Caddy 监听 80/443，Docker 暴露 8081 和本机 5432，Ollama 监听 11434。不能把它当作空服务器，也不能通过清理 Docker、模型或覆盖 Caddy 来腾空间。

独立服务域名采用 `baba.onedrivememory.com`，A 记录指向用户的服务器。用户截图已显示记录启用；这不等于已核验公网 DNS、HTTPS 或应用可用。下载页继续留在 GitHub Pages，安装包留在 Releases。

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

先确认域名实际解析到服务器，再核对 `caddy.service` 正在使用的配置文件和格式。当前只知道进程存在，尚未读取或修改其配置；不要假设它一定使用 `/etc/caddy/Caddyfile`，也不要用新的整份配置替换原网站。

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

核对实际配置后，备份原文件，将该独立站点片段以只读文件加入现有配置；确保没有同名站点且没有落入其他站点的大括号中。先执行 Caddy 对完整配置的验证，通过后才 reload。失败时恢复原文件并保留当前正常运行的配置，不 restart 其他服务。使用 Caddy 自动 HTTPS 前需确保此服务器现有 80/443 入口、安全组及域名解析满足证书签发条件；不额外开放 8766。

当前应用直接看到反向代理的本机地址，部分按 IP 的限流会聚合代理后的用户。不能通过无条件信任 `X-Forwarded-For` 来修补；大范围对外开放前需独立实现可信代理边界、访问压力与限流验证。本轮首先用于自用部署准备，不宣称完成公众生产验收。

公网接通后需复验首页与健康检查、注册登录、安全 Cookie、家庭协作、服务重启后的数据持久化，以及已有站点仍能正常使用。尚未完成正式 App 构建时可先通过浏览器验收；不得把模拟客户端测试当作真机结果。

## 第三步：备份与剩余空间

现有 [备份自动任务](备份自动任务.md) 和 [异地备份与发布工具](异地备份与发布工具.md) 与本次数据目录兼容，但必须实际配置恢复证书、保留周期、独立存储并完成恢复演练。恢复私钥不随备份上传，也不保存在公共仓库。

根盘已使用约 80%，本轮不下载模型或 Docker 大镜像，不把正式 APK 放入 Git 或服务器代码目录。备份保留需要按数据库实际大小和剩余空间设置，不能直接照搬会占满磁盘的批次数。先检查占用、必要时扩盘，不擅自删除既有业务数据。异地备份不能只落在这台服务器的另一个目录。

## 工具验证与实际状态

```sh
python -m unittest discover -s tests -p test_server_install.py -v
python tools/server_install.py --revision 完整提交SHA --domain baba.onedrivememory.com
```

测试在临时目录模拟账号、命令、源码和健康检查，验证计划不写系统、拒绝注入与覆盖、固定提交、异常停止和数据保留；没有在工作环境执行真实 root 安装，也没有连接用户服务器。systemd 单元另外进行语法检查。实际服务安装、DNS/HTTPS、真实备份和恢复以服务器后续输出为准。
