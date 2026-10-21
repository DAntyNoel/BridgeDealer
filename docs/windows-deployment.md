# Windows / WSL2 本地部署

本机使用 `Ubuntu` WSL2，在 Windows 项目目录运行 `start.cmd` 即可启动。
默认监听 `127.0.0.1:12707`，并允许公网来源 `https://bridge.kanami.top`；窗口中按 Ctrl-C 停止。
只允许本机访问时运行 `start.cmd -LocalOnly`。
可通过 `-Distribution Ubuntu -Port 12707 -PublicOrigin https://bridge.kanami.top` 指定参数。
这些脚本不安装 Windows 服务，也不设置开机自启；Windows 或 WSL 重启后需重新启动。

该端口按主项目的配置和文档分配：12700 为 local-server，12701 为其调试备用，
12702 为 CLIProxy API，12703 为聊天后端，12704 为 Usage Keeper，12705 为 NapCat，
12706 为 KanamiBot / NoneBot；BridgeDealer 使用下一个未分配的 12707。
已有项目即使暂未运行也保留其端口。

## 首次安装

在 WSL 项目目录执行 README 的锁定安装和 DDS 构建步骤：

```sh
uv sync --locked --python 3.12
uv run --locked python scripts/build_dds.py
.venv/bin/python -m bridge_dealer success examples/four_hands_85.json --exact
```

当前机器把 uv 与托管 Python 放在被 Git 忽略的 `.cache/` 内，
Python 依赖在 `.venv/`，DDS 在 `lib/libdds.so`，构建文件在 `build/dds/`。
本机重新同步依赖时，可在 WSL 项目目录直接使用已安装的私有工具：

```sh
UV_CACHE_DIR="$PWD/.cache/uv" UV_PYTHON_INSTALL_DIR="$PWD/.cache/uv-python" \
  .cache/uv-tool/bin/uv sync --locked --python 3.12
```

日常启动不需要 uv、CMake 或编译器，也不需要修改系统 Python。
Windows 启动入口直接调用 Python 模块，避免 Git 自动转换 CRLF 换行后 shell 入口无法执行。

## Cloudflare Tunnel

在 Cloudflare 的 **Networking → Tunnels** 选择现有 Tunnel，
在 **Routes → Add route → Published application** 添加：

| 字段 | 值 |
| --- | --- |
| Subdomain | `bridge` |
| Domain | `kanami.top` |
| Path | 留空 |
| Service type | `HTTP` |
| Service URL（现有 Docker connector） | `host.docker.internal:12707` |
| HTTP Host Header | 留空，保留 `bridge.kanami.top` |

Windows 或相同 WSL 环境中的独立 connector 可使用 `http://127.0.0.1:12707`。
Docker connector 必须验证能通过 `host.docker.internal` 访问 Windows 的 WSL localhost 转发；
若当前 Docker / WSL 网络模式不支持，应使用同一 Windows 主机上的 connector，
不能通过绑定 `0.0.0.0` 或改用 WSL 动态 IP 直接暴露服务来绕过。
本次安装已从现有 `kanami-cliproxy-cloudflared` 的网络命名空间验证该回源地址可访问 API。
无需安装第二份 DDS、开放路由器端口、使用 WebSocket 或改写 `/api/` 路径。

同源白名单不提供身份认证。当前任务及模板是服务级共享数据，允许的用户可以查看、取消任务及修改模板；
全枚举可能长时间占用计算资源。建议在 **Access → Applications** 新建 Self-hosted 应用，
覆盖整个 `bridge.kanami.top`（不限制 Path），用 Allow 策略只允许指定邮箱，再发布路由。
不要把 Tunnel token、Access service token 或本地用户模板提交 Git。

配置后从公网打开 `https://bridge.kanami.top`，实际提交小空间牌例并确认解析结果。
仅有主页 HTTP 200 或 Tunnel Healthy 不能代替 DDS 求解和 POST 同源校验。

## 本机验证记录

2026-10-21 安装验证：11 项 DDS 原生与安装测试、43 项 HTTP 安全测试通过，
带公网 Host / Origin 的真实 HTTP 查询完成 85 副全枚举，概率为 `[1, 0, 1, 0, 0]`。
本机 WSL / NTFS 环境的求解启动约需 4 秒；原有的两个 3 秒预算测试
`test_multicontract_same_census_matches_independent_native_solver` 和
`test_http_cancel_busy_and_infeasible` 在原始代码基线也返回 `insufficient_samples`。
20 秒预算与上述真值查询正常；本次没有放宽测试预算或伪造完成状态。

官方说明：[创建及发布 Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/get-started/create-remote-tunnel/)、
[Self-hosted Access 应用](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/)。
