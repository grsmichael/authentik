# authentik 本地开发服务导航

一个零依赖的 Python 小服务 + 一个导航页，把本地调试要点的地址、账号、健康状态集中到一页。
服务清单与默认账号**只有一份数据源**（`services.json`），探测、卡片、账号面板全部由它驱动。

```
tools/nav/
├── services.json      ← 【唯一数据源】服务清单 + 默认账号 + 快速上手
├── serve_nav.py       ← 托管导航页 + 3 个只读 JSON 端点（零第三方依赖）
├── 服务导航.html       ← 导航页本体（不含任何硬编码账号/host）
├── Dockerfile         ← 容器镜像（python:3.12-alpine + docker-cli）
├── docker-compose.yml ← 容器化启动（挂 docker.sock + host-gateway）
└── README.md
```

## 现状：服务跑在 Docker 里

现在推荐用 `deploy/docker-compose.yml` 起 authentik 全栈，导航页配上容器一起跑：

```bash
# 1) authentik 全栈（postgres + server + worker）→ 9000 / 9443
cd deploy && docker compose up -d

# 2) 服务导航（本目录）→ http://localhost:8899/
docker compose -p authentik-nav -f tools/nav/docker-compose.yml up -d --build
docker compose -p authentik-nav -f tools/nav/docker-compose.yml logs -f   # 看探测日志
docker compose -p authentik-nav -f tools/nav/docker-compose.yml down      # 停掉
```

⚠️ **必须带 `-p authentik-nav`。** 本目录叫 `nav`，别的仓库也有 `tools/nav/`，默认 project name
都算成 `nav`，不带 `-p` 时 compose 会把那边同名的导航容器一并建/删（已踩过：把隔壁项目的
`zhontai-admin-core-nav` 重建掉了）。

然后打开 <http://localhost:8899/>。<b>导航页自己也是个容器</b>（`authentik-nav`），它探的是
**宿主机端口**（`host.docker.internal`）与 **docker 容器**，所以：

- 卡片上 9000 / 9443 显示真实状态，说明容器确实在跑；
- Postgres 卡片是「恒灰」的——Docker 部署不映射宿主机端口，只能看它的「容器状态」是否变绿。

源码模式（`make run` / `scripts/compose.yml`）的服务清单依然保留在 `services.json` 里，
但那套拓扑现在不是默认流程。

## 导航页单独跑（宿主机 Python）

不想留常驻 Python 进程时才需要：

```bash
python tools/nav/serve_nav.py              # 默认 8899，被占自动顺延（8898/8897/8896）
python tools/nav/serve_nav.py --port 9000  # 指定端口
python tools/nav/serve_nav.py --bind 127.0.0.1   # 只给本机访问
python tools/nav/serve_nav.py --list       # 只打印本机内网 IP 后退出
python tools/nav/serve_nav.py --open       # 启动后自动开浏览器
```

然后打开 http://localhost:8899/ ，Ctrl+C 停止。
⚠️ 默认监听 `0.0.0.0`，同网段设备也能看到（页面上有端口和账号），不需要时停掉或加 `--bind 127.0.0.1`。

## 用 Docker 跑

不想在宿主机留一个常驻 Python 进程时用 compose：

```bash
docker compose -p authentik-nav -f tools/nav/docker-compose.yml up -d --build   # 改过脚本/清单就带 --build
docker compose -p authentik-nav -f tools/nav/docker-compose.yml logs -f          # 看探测日志
docker compose -p authentik-nav -f tools/nav/docker-compose.yml down             # 停掉
# 换端口时：docker-compose.yml 的 NAV_PORT（或 .env）+ services.json「服务导航」那一项同步改
```

同样打开 <http://localhost:8899/>。**compose 里只定义导航页自己**—— authentik 本体在
`deploy/docker-compose.yml` 里。导航服务探的是宿主机端口，照样能正确反映状态。

**容器化的关键坑：容器里的 `127.0.0.1` 是容器自己。** 照搬宿主机的探测写法会让所有服务
全部误报 down。所以脚本新增了 `NAV_PROBE_HOST` 把探测目标指到宿主机，compose 里已配好
`host.docker.internal`，配合 `extra_hosts: host-gateway`（Linux 必需，Docker Desktop 无害）。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `NAV_PROBE_HOST` | `127.0.0.1` | 健康探测目标主机。**容器内必须指到宿主机** |
| `NAV_HOST_IP` | 自动解析 | 页面「共享给其他设备」显示的宿主机内网 IP |
| `NAV_PORT` | `8899` | 监听端口（被占仍自动顺延） |
| `NAV_BIND` | `0.0.0.0` | 监听地址 |

宿主机直跑时这些变量留空即可，行为与以前完全一致（探测 `127.0.0.1`）。

容器内还挂载了 `/var/run/docker.sock`（只读）并装了 docker-cli，卡片上的「容器状态」
一栏能真的查到 `docker ps`。本项目 `services.json` 里已经给 server / postgres 配了
`container`，这一栏是活的。

**本机坑：环境里有 `http_proxy`/`HTTPS_PROXY`（指向 127.0.0.1 的沙箱代理）时，
`curl http://127.0.0.1:8899/` 会被代理接走返回 502。** 命令行验证请加 `curl --noproxy '*'`；
浏览器一般不受影响，若打不开就检查系统代理设置。脚本内部探测也已强制直连（绕开代理）。

## 换端口的规矩

**只改 `services.json` 这一处**（服务卡片、探测、账号面板、一键打开按钮都读它），
再同步本 README 的「服务清单」表和「起服务」里的示例命令。compose 与源码配置也要跟着改——
本导航不是端口的发布方。

## 三个端点

| 端点 | 用途 |
|---|---|
| `/__services` | 下发 `services.json`（服务清单 + 默认账号），页面靠它渲染卡片 |
| `/__sysinfo` | 服务端探测本机内网 IP（浏览器 WebRTC 已拿不到真实内网 IP） |
| `/__probe` | 服务端并发探测各服务健康 + docker 容器状态（规避跨端口 CORS） |

## 服务清单（`services.json`）

加服务 / 改端口 / 改账号，**只改这一个文件**，探测、卡片、账号面板自动同步。
字段说明：

| 字段 | 说明 |
|---|---|
| `port` | 端口号，卡片链接与探测都用它 |
| `kind` | `http`（GET 探测 `probe` 路径）/ `tcp`（connect 成功即算在线）/ `udp`（**不探测**，状态恒灰；用于不发布宿主机端口、只能看容器状态的服务） |
| `probe` | http 探测路径。2xx/3xx 判在线；301/302/401/403 也算活着（要登录/要跳转而已） |
| `container` | 额外展示 docker 容器状态（匹配 `docker ps` 的容器名，如 `authentik-server`），**不参与 up/down 判定** |
| `links` | 卡片上的快捷链接（`port` + `path` 拼成，host 跟随页面当前「服务链接主机」） |
| `creds` | 卡片底部的账号小标签，可一键复制 |

当前登记的 11 个服务：

| 端口 | 类型 | 说明 |
|---|---|---|
| 8899 | http | **服务导航自己**（容器 `authentik-nav`），`docker compose -p authentik-nav -f tools/nav/docker-compose.yml up -d` |
| 9000 | http | **authentik server**（容器 `authentik-server`），`deploy/docker-compose.yml`；前端构建产物由本进程直接托管。根 `/` 已登录跳 `/if/admin/`；健康检查 `/-/health/live/`，指标 `/-/metrics/` |
| 8443 | tcp | HTTPS 监听（宿主侧发布端口，容器内仍是 9443——9443 在 Windows 保留段 9366-9465 里，宿主 bind 不了）。浏览器需 `https://127.0.0.1:8443/`；这里用 tcp 探测，HTTP 探 TLS 端口只会拿到握手垃圾 |
| 5432 | udp | **PostgreSQL —— 必需**（容器 `authentik-postgresql`，postgres:16-alpine，数据绑定挂载 `./data/postgres`）。Docker 部署不映射宿主机端口，状态恒灰：看容器状态，或 `docker exec -it authentik-postgresql psql -U authentik`。注意本机 5432 可能是其他项目的 Postgres |
| 8020 | tcp | S3 / zenko cloudserver（源码开发模式 dev 媒体存储，默认未启用；Docker 部署用本地文件后端） |
| 3389 | tcp | LDAP outpost（嵌入式，容器内监听、未发布到宿主机，红了正常） |
| 1812 | tcp | RADIUS outpost（嵌入式，容器内监听、未发布到宿主机，红了正常） |
| 6006 | http | Storybook 组件文档（`make web-storybook-watch`），可选 |
| 9300 | tcp | metrics 监听端口（Prometheus），容器内监听未发布，红了正常 |
| 6379 | tcp | Redis —— **默认未启用**（`cache.url`/`channel.url` 为空，缓存走内存） |
| 9900 | tcp | Rust/Py debug 端口（tokio-console / debug_py），仅调试，默认不监听 |

> Postgres 恒灰：Docker 部署下它没有宿主机端口，本页不猜。
> S3 / Redis / LDAP / RADIUS / metrics / debug 几项红了属于正常，因为默认配置根本没用到它们。
> 导航页顶部也写了这条提示，免得每次都怀疑是不是环境坏了。

## 默认账号

Docker 部署（优先级最高，账号直接取自 `deploy/.env`）：

- **管理员：`akadmin` / `akdev123!`** —— 用户名固定，由 `blueprints/system/bootstrap.yaml` 在启动时创建，
  密码来自 `AUTHENTIK_BOOTSTRAP_PASSWORD`（`deploy/.env`）。入口 <http://localhost:9000/if/admin/>。
- PostgreSQL：`authentik` / `deploy/.env` 的 `PG_PASS`（库 `authentik`，仅 compose 网络内）
- S3（仅源码模式）：`accessKey1` / `secretKey1`，bucket `authentik-media`，端点 `127.0.0.1:8020`
- Redis：无密码，`127.0.0.1:6379`（默认未启用）

⚠️ 全部是开发/体验用途的默认值，切勿用于生产。

## 起服务（当前推荐流程）

```bash
# 1) authentik 全栈：postgres + server + worker → http://localhost:9000/
cd deploy && docker compose up -d

# 2) 服务导航 → http://localhost:8899/（-p 必须带，见上文坑）
docker compose -p authentik-nav -f tools/nav/docker-compose.yml up -d --build

# 3) 浏览器 http://localhost:9000/ ，用 akadmin / akdev123! 登录
#    管理后台 /if/admin/ ，用户中心 /if/user/ ，API 浏览器 /api/v3/
```

源码开发模式（可选，注意先停 Docker 栈避免端口冲突）：

```bash
make install                 # + make gen-dev-config 生成本地配置
docker compose -f scripts/compose.yml up -d   # 基础设施：Postgres 127.0.0.1:5432 / S3 :8020
make run                     # server + worker 一体，:9000 / :9443
make web-storybook-watch     # 组件文档 → http://localhost:6006/（可选）
```

本机现状（2026-09-27 实测）：

- ✅ Docker 部署栈 postgres/server/worker + 导航容器全部 healthy；`curl --noproxy '*'` 校验 9000 与导航端点是 200
- ⚠️ S3 / Redis / LDAP / RADIUS / metrics / debug 默认没起，导航页这几项红（或灰）属正常
- ✅ 2026-09-26 实测：导航页 / 脚本可正常托管（端口占用自动顺延已验证）

## 链接准确性说明

### 已实测（2026-09-26/27，从仓库源码与 compose 核对）

| 路径/端口 | 来源 |
|---|---|
| `9000/if/admin/`、`/if/user/`、`/api/v3/`、`/api/v3/schema/`、`/-/health/live/`、`/-/metrics/` | `authentik/root/urls.py`、`authentik/core/urls.py`、`authentik/api/v3/urls.py` |
| `9000/setup` | `authentik/core/urls.py`（SetupView，首次初始化向导） |
| `listen` 端口 9000/9443/3389/1812/9300/9900/9901 | `authentik/lib/default.yml` |
| Postgres / S3（源码模式） | `scripts/compose.yml` |
| Postgres / server / worker（Docker 模式） | `deploy/docker-compose.yml`（容器名 authentik-postgresql / authentik-server / authentik-worker） |
| 管理员 akadmin | `blueprints/system/bootstrap.yaml`（`AUTHENTIK_BOOTSTRAP_PASSWORD`） |
| 镜像 tag 2026.8.3 | `deploy/.env` 的 `AUTHENTIK_TAG`（仓库源码版本为 2026.11.0-rc1） |

### 路径是怎么推出来的

- 路由来自 `authentik/root/urls.py`（遍历各 app 的 `mountpoint` 挂载）与 `authentik/core/urls.py`
  （`/if/admin/`、`/if/user/`、`/if/flow/<slug>/`、SetupView）。
- API 浏览器 `/api/v3/`、OpenAPI Schema `/api/v3/schema/` 来自 `authentik/api/v3/urls.py`
  （`APIBrowserView` + `SpectacularAPIView`）。
- 健康检查 `/-/health/live/`、`/-/health/ready/`、指标 `/-/metrics/` 来自 `authentik/root/urls.py`。
- 监听端口来自 `authentik/lib/default.yml` 的 `listen`（http/https/ldap/ldaps/radius/metrics/debug）。
- 数据库/存储来自 `scripts/compose.yml`（源码模式 postgres:18、zenko/cloudserver）与
  `scripts/generate_config.py`（`storage.s3` 默认 endpoint `http://localhost:8020`）；
  Docker 模式见 `deploy/docker-compose.yml` 与 `deploy/.env`。
