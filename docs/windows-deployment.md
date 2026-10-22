# Windows / WSL2 本地部署

本机使用 WSL2，在 Windows 项目目录运行 `start.cmd` 即可启动独立的 Cloudflare connector
和桥牌服务；窗口中按 Ctrl-C 停止桥牌服务。
首次运行将 `.env.example` 复制为 `.env`，填入为 BridgeDealer 单独创建的 Tunnel token。
Windows 部署配置的唯一来源是项目根目录的 `.env`：

| 配置项 | 示例 | 用途 |
| --- | --- | --- |
| `LOCAL_SERVER_HOST` | `127.0.0.1` | 仅允许本机监听，不能改为公网地址 |
| `LOCAL_SERVER_PORT` | `12707` | 桥牌服务端口 |
| `LOCAL_SERVER_PUBLIC_ORIGIN` | `https://bridge.kanami.top` | 严格校验的公网来源 |
| `WSL_DISTRIBUTION` | `Ubuntu` | 已安装 Python / DDS 的 WSL 发行版 |
| `TUNNEL_TOKEN` | 留在本机填写 | BridgeDealer 专用 Tunnel token |

配置文件只接受未加引号的字面量 `KEY=value` 和整行注释；不展开变量、不执行命令。
启动入口不再接受端口、来源或发行版覆盖参数，也不继承系统环境变量中的同名配置。
Docker Compose 使用 `env_file` 的 `raw` 格式直接读取同一个 `.env`，需要 Compose 2.30.0 以上版本。
修改 `.env` 后停止桥牌服务，再运行 `start.cmd`，会将变更应用到服务和 connector。
不要输出 `docker compose config` 的展开结果，也不要提交 `.env` 或 token。

只允许本机访问时运行 `start.cmd -LocalOnly`，不会启动 connector，也不会允许公网 Host / Origin。
connector 使用 `unless-stopped` 随 Docker 恢复；桥牌服务不安装 Windows 服务或开机任务，
Windows 或 WSL 重启后需重新运行 `start.cmd`。

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

在 Cloudflare 的 **Networking → Tunnels** 选择 `.env` 中 token 对应的 BridgeDealer 专用 Tunnel，
在 **Routes → Add route → Published application** 添加：

| 字段 | 值 |
| --- | --- |
| Subdomain | `bridge` |
| Domain | `kanami.top` |
| Path | 留空 |
| Service type | `HTTP` |
| Service URL（Docker connector） | `host.docker.internal:12707`（端口与 `.env` 一致） |
| HTTP Host Header | 留空，保留 `bridge.kanami.top` |

Windows 或相同 WSL 环境中的独立 connector 可使用 `http://127.0.0.1:12707`。
Docker connector 必须验证能通过 `host.docker.internal` 访问 Windows 的 WSL localhost 转发；
若当前 Docker / WSL 网络模式不支持，应使用同一 Windows 主机上的 connector，
不能通过绑定 `0.0.0.0` 或改用 WSL 动态 IP 直接暴露服务来绕过。
专用 connector 名为 `kanami-bridge-cloudflared`，与 CLIProxy / Usage Keeper 的 connector 分开管理。
Tunnel 发布路由仍由 Cloudflare 控制台管理；修改本机端口时，也需同步修改控制台回源端口。
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
