# GitHub 下载页与 App 发布

更新：2026-11-02。此项对应产品方案第 16 章，也就是用户指定的“像示例那样在 GitHub 提供 App 下载”。此前只有打包工具，没有公开下载页；本轮补齐页面、Pages 部署和 Release 发布工具。

## 四部分各自负责什么

| 部分 | 用途 |
|---|---|
| Git 仓库 | 保存代码、文档和版本；不把安装包、密钥、数据库写进历史 |
| GitHub Releases | 存放正式签名 APK、源码 ZIP、版本清单与 SHA-256 校验和 |
| GitHub Pages | 提供产品介绍、下载按钮、安装说明、更新记录与文件核对 |
| 应用后端 | 另行部署账号、家庭数据、协作与同步；Pages 不运行 Python/SQLite 后端 |

当前目标公开网址是 **https://damonlinyz.github.io/baby/**。仓库检查时 `has_pages=false`，需要先在 GitHub 设置中启用 Pages；此地址不能在启用和部署成功前宣称已经上线。

## 下载页面

`website/` 是独立静态页面，参考用户示例的信息结构，使用本产品的米白、暖橙与绿色风格。页面包含功能介绍、明确标注的界面示意、Android 下载、iOS 当前状态、版本/大小/最低 Android 系统、文件摘要、发布证书指纹、更新说明和常见问题。

没有真实 Release 安装包时，Android 按钮没有下载地址并显示“尚未发布”；无法确认发布信息时也不生成下载链接。iOS 没有 TestFlight/App Store 地址时只显示当前未发布状态，不伪造 IPA 安装入口。正式 Android 入口绑定具体版本标签，不使用容易错配摘要的 `latest/download` 跳转。

安装后仍需填写已部署的 HTTPS 家庭服务地址。本轮没有部署公共家庭服务，不宣称下载后即可使用尚不存在的云端账号服务。此公开页面不加载家庭应用、不提供登录、不访问家庭数据库，不采集健康资料。

## 首次启用 Pages

1. 打开 [仓库 Pages 设置](https://github.com/damonlinyz/baby/settings/pages)。
2. 在 **Build and deployment → Source** 选择 **GitHub Actions**。
3. 打开 [Publish download page 工作流](https://github.com/damonlinyz/baby/actions/workflows/download-pages.yml)，选择 **Run workflow → main**；若已有因 Pages 未启用而失败的运行，也可以重新运行。
4. 以 Actions 的 `Deploy GitHub Pages` 步骤实际成功及输出网址为准，再对外分享下载页地址。

后续改动 `website/` 会自动部署；正式 Release 发布、修改或删除也会触发更新。草稿和预发布不进入正式下载页。GitHub 下载与 Pages 访问受网络条件影响，不把它作为未经验证的国内可用性承诺。

工作流只上传白名单公开文件：HTML、CSS、JS、图标、公开配置、经过校验的发布信息与 `.nojekyll`。不会发布根目录的家庭应用、后端、数据库、账号、环境变量或签名私钥。

## 正式 Android 安装包准备

当前 `clients/app/manifest.json` 的 DCloud 标识和正式包名尚未配置，仓库也没有正式 APK。本轮没有编造或上传安装包。发布前需完成实际配置、构建和签名：

1. 在可信打包机配置 DCloud 标识、正式 Android 包名、服务端地址和必要模块，用自己的发布密钥构建 APK。私钥留在本机或安全的构建环境。
2. 在 `website/config.json` 固定 `android_package` 和 `android_signer_sha256`，与 `clients/app/manifest.json` 的正式包名一致。指纹是发布证书的公开 SHA-256，不是私钥；当前保留 `null`，因此不能误开正式下载。
3. 确认源码版本号和整数版本代码与 APK 一致，提交并推送代码。已知危险旧版本可通过 `minimum_android_version_code` 禁止进入下载页。

需安装 Android SDK `apksigner` 和 `aapt`。以下命令中的路径、版本和指纹需要替换为实际值：

```sh
python3 tools/release_bundle.py \
  --output dist/release-0.2.2-android \
  --apk /path/to/signed.apk \
  --expected-signer-sha256 '替换为发布证书的64位SHA256摘要'
```

工具在临时副本上核验真实 APK 签名、预期发布证书、包名、版本名/代码及最低系统要求，再写入元数据并核对复制后的文件摘要。任一不匹配就拒绝。只有源码时仍可生成源码包，但不能用它发布 App 下载。

输出固定四项：

```text
baba-android.apk
baba-source.zip
release.json
SHA256SUMS
```

安装包需小于 2 GiB，且不会进入 Git 历史。更新说明使用本人准备的 Markdown 文件，不自动附加家庭数据。

## 创建草稿或正式发布

在已正常授权 GitHub CLI 的可信打包机执行：

```sh
# 默认只创建 Release 草稿，核对附件与标签来源后再发布。
python3 tools/publish_release.py \
  --bundle dist/release-0.2.2-android \
  --notes-file /path/to/release-notes.md

# 明确确认成品后，可以直接正式发布。
python3 tools/publish_release.py \
  --bundle dist/release-0.2.2-android \
  --notes-file /path/to/release-notes.md \
  --publish
```

可加 `--dry-run` 只核验、不上传。工具再次实测 APK，校验完整目录、文件摘要、证书、包名、来源提交和版本；默认草稿，只有显式 `--publish` 才正式发布。新版本使用新标签，不覆写历史安装包。实际创建的标签形式为 `v0.2.2`，并指定明确来源提交。

正式发布触发 Pages 工作流。CI 会重新下载实际 APK，在临时目录使用 Android SDK 核对签名、包名、版本和最低系统，并对照来源提交的 App 配置；不会只信任附件清单的声明。构建器只接受本仓库稳定发布，核对标签实际提交、schema 2 安装包清单、固定发布证书和应用包名、GitHub 自身计算的附件摘要/大小、`SHA256SUMS` 与最低允许版本。草稿、预发布、源码包、错签名、跨仓库链接、未完成附件及超过大小限制的文件均不能进入下载按钮。校验失败时停止部署，保留上一份已部署站点；已有页面展示的是其明确版本，不声称检查失败时仍是最新版本。

## 本地预览与验收

```sh
python3 tools/download_site.py --output dist/download-page
python3 -m http.server 8767 --directory dist/download-page
```

打开 `http://127.0.0.1:8767/`。默认离线预览使用真实的“尚未发布安装包”状态，不访问账号或模型；CI 用 `--github` 获取已验证的正式版本。浏览器页面只读取站点自身的公开 JSON，不让每个访客调用 GitHub API，也不在前端放访问令牌。

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests
node tests/client_download_page.test.cjs
python tests/run_browser_download_site.py
```

浏览器环境同其他 Playwright 验收脚本。独立临时静态服务在 `/baby/` 项目子路径复验手机/桌面布局、未发布状态、有效下载、摘要、纯文本更新说明、不安全信息拒绝及后端/私有文件不暴露；有效下载数据仅为本机合成测试，不会发布为真实安装包。
