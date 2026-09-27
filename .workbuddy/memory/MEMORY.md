# 项目记忆

## zhontai Admin.Core 中台项目（实际工作项目）
- 仓库位置：`C:/Gitee/GSDevCode/Core/Admin.Core`（Gitee 工作区，与 authentik 工作区无关，但会话常在此仓库上下文中进行）。
- 2026-09-27 完成全栈 Docker 部署 + 服务导航容器化：
  - 根 `docker-compose.yml`：postgres 5432 / admin 18010+18011 / dev 18020+18021 / gateway 16010 / web 9010（nginx 托管前端并反代 /api /doc /upload）。
  - 服务导航：`tools/nav/docker-compose.yml` → 容器 `zhontai-admin-core-nav`，端口 8898，探测宿主机端口（NAV_PROBE_HOST=host.docker.internal）。
  - 端口唯一权威来源：`tools/nav/services.json`；改端口必须同步四处：services.json / docker-compose.yml / 源码配置 / docs/Docker部署实施文档.md 端口总表。
  - 8898 端口不能用 8899（Docker Desktop 的 com.docker.backend.exe 占用 8899）。
  - 默认账号 admin/123asd；默认数据库 PostgreSQL 16（admindb/logdb/devdb），2026-09-27 由 Sqlite 切换。
- 红灯正常项：17010(IM)/6379(Redis)/5672(RabbitMQ)/9006(MinIO) 默认未启用。
- 文档：`docs/Docker部署实施文档.md`（第 6 节端口总表、第 11 节服务导航、第 10.1 节换端口步骤）。
