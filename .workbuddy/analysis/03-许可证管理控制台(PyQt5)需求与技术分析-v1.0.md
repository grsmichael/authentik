# 03 · 许可证管理控制台（PyQt5）需求与技术分析

| 项 | 值 |
|---|---|
| 文档编号 | 03 |
| 版本 | v1.0 |
| 日期 | 2026-09-27 |
| 状态 | **已实现**（控制台已开发，代码见 `tools/enterprise-license/console/`；M1–M5 落地，M6 打包脚本就绪，源码运行：`python run_console.py`） |
| 上游文档 | [00 文档索引](00-文档索引-v1.0.md)、[01 需求分析说明书](01-需求分析说明书-v1.0.md)、[02 自签企业版许可证技术方案](02-自签企业版许可证技术方案-v1.0.md) |
| 使用者 | 本人（个人自用、非生产） |

## 修订记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v1.0 | 2026-09-27 | 首版。选题从「Web 控制台」改为「PyQt5 桌面控制台」，按「先文档、后开发」的顺序产出 |

## 一、文档目标

02 方案已经把许可证「签得出来、发得进去」，但全过程仍是命令行 + 手工拼 SQL：

```
gen_license.py 生成 → 手抄 license.jwt → psql 插表 → ak shell 清缓存 → 再验一次状态
```

每换一次配置就要重走一遍，且插表 SQL、清缓存语句都靠人记。本文档定义一款**桌面应用**把这些动作收敛成几次点击，并明确它的边界、技术选型与实现要点，作为后续开发的唯一输入。

**本文档只做分析与设计，不含实现代码**（`gen_license.py` 的改造属既有改动，见第 3 章）。

## 二、背景与现状

### 2.1 已落地的事实（均已在 2026-09-27 实测）

| 事实 | 出处 |
|---|---|
| 许可证校验完全离线，唯一信任锚是 `authentik/enterprise/public.pem` | `authentik/enterprise/license.py` |
| JWT header 的 `x5c` 必须用**标准 base64** 编码，否则 DER 损坏、报含糊的 "Unable to verify license" | 实测踩坑记录（02 文档第 8 章） |
| 校验通过后整个 EE 门控体系自动打开，authentik 源码零改动 | 02 方案 |
| 当前实例状态：`status = valid`，席位 50/500，到期 2051-09-21 | `docker exec authentik-server ak shell -c "..."` |
| 许可证存在表 `authentik_enterprise_license`，7 列全 `not null`，主键 `license_uuid` | `psql -c '\d authentik_enterprise_license'` |
| `CACHES` 是 `DatabaseCache`（12h TTL），改完必须显式 delete 缓存键，重启容器无效 | 实测踩坑记录 |
| `/api/v3/**` 不接受 basic auth，因此只能用「直接插库 + 清缓存」的路子 | 实测 |

### 2.2 痛点

| # | 痛点 | 后果 |
|---|---|---|
| P1 | 签一次票要敲 5 步命令 | 换配置成本高 |
| P2 | `license.jwt` 是 1900+ 字符单行，手抄易错 | 出错后报错信息不含糊，定位困难 |
| P3 | 插表 SQL 依赖人记（`gen_random_uuid()`、`to_timestamp(...)`） | 换机器就断 |
| P4 | 清缓存语句要 `docker exec ... ak shell -c`，引号嵌套易出错 | 清不干净会读到 12h 前的旧结论 |
| P5 | 没有「当前到底生效哪一张」的视图 | 发布是否成功只能靠猜 |
| P6 | 改错想回滚，步骤也散在 02 文档里 | 心里没底 |

## 三、已完成的前置改造（本次已在工作区，待评审）

为了让控制台能直接复用，已重构 `tools/enterprise-license/gen_license.py`（**CLI 行为完全不变，回归测试通过**）：

| 改造 | 说明 |
|---|---|
| 抽出 `make_chain()` | 一次生成「根 → 中间 → 叶子」三级链，返回 3 组 (证书, 私钥) |
| 抽出 `build_license()` | 只签票、无副作用，返回 `(jwt 字符串, payload)` |
| 抽出 `load_private_key()` | 读 PKCS8 私钥 |
| 抽出 `verify_license()` | 逐项复刻 `license.py` 的校验顺序，返回 `{"ok", "steps":[{name,ok,detail}], "payload"}`，供 UI 显示「校验通过 ✓ / 失败原因」 |
| 新增产物 `chain/intermediate.crt.pem`、`chain/leaf.crt.pem` | **关键**：原先只落了叶子私钥，导致以后无法再用同一条链签新票（重建出的叶子证书与原链不一致）。现在证书与私钥配套落盘 |

回归验证（临时目录实跑，已清理）：7 个产物齐全，自检 6 项全绿，CLI 输出与改造前一致。

> 注意：`deploy/enterprise-pki/` 的 `chain/`（intermediate.crt.pem、leaf.crt.pem）已**直接从现有 `license.jwt` 的 x5c 抽取补齐**（与线上票完全一致，零风险），无需再 `--force` 重签。控制台「重签整条 CA 链」是另一次有意的信任锚轮换操作（旧票会失效）。

## 四、目标与非目标

### 4.1 目标（In Scope）

1. 一台装了 Python 的机器上双击即用，无需 Docker Inside Docker。
2. 一次点击完成「读 install_id → 填参数 → 签票 → 本地校验 → 发布 → 回读生效状态」。
3. 任何时刻都能看清：信任锚是谁、当前生效的是哪张票、还剩多久。
4. 危险操作（重签整条链、回滚）有备份与二次确认，可一键还原。

### 4.2 非目标（Out of Scope）

| 项 | 原因 |
|---|---|
| 管理**多个** authentik 实例 | 单机自用，一张许可证足够；多实例作为配置扩展点预留即可 |
| 面向多人/团队的分发与审计 | 无此场景 |
| 替换 `deploy/docker-compose.yml` 里的挂载 | 控制台只负责签发，不改部署产物（挂载仍由 compose 维护） |
| 打包成 exe 分发 | **已决议**：PyInstaller 打包，见 M6 |
| 导入/校验**官方**签发的许可证 | **不做**（Q-07 决议 b）：校验函数天然只认本 PKI 的链 |

## 五、用户与场景

单人运维（即使用者本人）。典型会话：

```
场景 A「换个年限重签」
  打开控制台 → 概览页确认当前 install_id → 切到「签发」
  → 年限改 50 年、席位按需要调 → 签发
  → 结果页一键「发布到 authentik」
  → 页面回读 status，显示 valid → 完成

场景 B「查看现在到底生效哪张」
  → 打开控制台就是概览页：信任锚 CN/有效期/指纹，当前许可证名称、席位、到期日
  → 若与预期不符，点「重新读取」

场景 C「改错了，退回去」
  → 概览页「移除许可证」→ authentik 回到社区版可用状态
  → 若还改过信任锚，点「恢复上一步的 public.pem」（保留最近的备份）

场景 D「换机器 / 换容器名」
  → 设置页改容器名 → 点「测试连接」→ 通过后正常发布
```

## 六、功能需求

优先级：**P0** 必须有，**P1** 强烈建议，**P2** 锦上添花。

| 编号 | 优先级 | 名称 | 说明与验收标准 |
|---|---|---|---|
| FR-01 | P0 | 启动与配置 | 入口 `python console/main.py`；`--config` 可指定配置文件；默认读 `console/config.json`。验收：改配置文件后重启，界面显示新值 |
| FR-02 | P0 | 状态总览 | 顶部一次展示：①信任锚（Root CA 的 CN、有效期、SHA256 指纹、证书文件路径）；②已发布到 DB 的许可证（名称、到期、内/外部席位、写入时间）；③ authentik 实际生效状态（`LicenseKey.cached_summary()` 的 status/席位/到期）。验收：三个区块数值与 `psql`/`ak shell` 手工查到的一致 |
| FR-03 | P0 | 读取 install_id | 从 `authentik_install_id` 表读取，也可手填。带「从 authentik 读取」按钮。验收：与 DB 查询结果一致 |
| FR-04 | P0 | 签发许可证 | 表单：名称、install_id、年限、内部席位、外部席位、flags。点「签发」后用 `chain/leaf.crt.pem` + `private/leaf.key.pem` 签票，**先本地逐项校验**（`verify_license()`）再落盘。验收：校验不通过不允许保存；通过后产物落在 `issued/<时间戳>-<slug>.jwt` + 同名 `.json` 元数据 |
| FR-05 | P0 | 发布到 authentik | ①按 payload 生成 insert 语句写 `authentik_enterprise_license`；②清 `CACHE_KEY_ENTERPRISE_LICENSE` 缓存；③回读 `cached_summary()` 展示结果。验收：发布后 authentik 侧 `status` 变为 `valid` |
| FR-06 | P1 | 许可证库 | 列出 `issued/` 下所有票：名称、到期、席位、audience、校验状态。操作：复制全文到剪贴板、导出到任意路径、删除（删除需二次确认）。验收：列表与实际文件一一对应 |
| FR-07 | P1 | 校验面板 | 对任意一张票（库里的、或粘贴进来的）跑 `verify_license()`，逐行展示「解析 x5c / 叶子←中间 / 中间←根 CA / JWT 验签 / audience / 未过期」六项结果。验收：一张正确票六项全绿；把 audience 改错能明确报出不匹配 |
| FR-08 | P1 | 重签整条 CA 链 | 备份现有的 `public.pem` 为 `public.pem.bak-<时间戳>`，生成全新三级链，写盘后提示「需执行 `docker compose up -d` 重新挂载，且旧票全部作废」。验收：备份文件存在、旧票仍能通过本地校验（ chain 变了就应该失败，此处应明确提示） |
| FR-09 | P1 | 回滚 | ①「移除许可证」：从 DB 删除行并清缓存；②「恢复信任锚」：用最近的 `public.pem.bak-*` 还原。验收：移除后 authentik 回到 `unlicensed` |
| FR-10 | P2 | 操作日志 | 底部日志区（或可打开的日志窗）按时间记录每次签发/发布/回滚/重签的结果，含耗时。日志同时落盘 `console/console.log`（utf-8） |
| FR-11 | P2 | 内置说明 | 一个「原理」页签，图文说明 02 方案的离线校验原理与三个坑，避免换机器后重新踩 |
| FR-12 | P0 | 数据库连接与执行通道 | 见下方「执行通道设计」。配置项：主机、端口、库名、用户、密码、SSL 模式、匿名用户名（默认 `AnonymousUser`），存 `console/config.json`，支持「测试连接」。通道优先级 **auto = 直连优先、失败自动回退 `docker exec`**，也可手动锁定为 `direct` 或 `docker`。验收：把主机写成不可达地址时，界面明确报「连接失败」并提示回退已生效，不抛 traceback |
| FR-13 | P1 | 离线条：本地推算生效状态 | 无 docker / 无 `ak shell` 时，按镜像 `LicenseKey` 的逻辑在本地复算状态，并在界面标注「本地推算」。验收：直连查询的席位数与 `ak shell` 输出一致（本机已实测对齐：internal=1 / external=0） |
| NFR-01 | P0 | 不阻塞 UI | 所有阻塞调用（psycopg2 连接与查询、docker exec）一律放工作线程（QThreadPool/QRunnable），按钮置灰 + 进度提示。验收：容器名写错或数据库不可达时界面不假死 |
| NFR-02 | P0 | 私钥不出界 | 界面只展示私钥文件名与指纹，**不显示私钥内容**；「显示 leaf 私钥」需显式勾选 + 确认。导出按钮只针对 `.jwt`，私钥不可单独导出 |
| NFR-03 | P0 | 路径兜底 | 私钥/证书缺失时给出可操作的提示（指向「重签整条 CA 链」），而不是 traceback |
| NFR-04 | P1 | 可移植 | 依赖仅三项且**已全部就位**（隔离 Python 3.13.14）：`PyQt5` 5.15.11、`cryptography` 50.0.1、**`psycopg2` 2.9.13**；Windows/Linux/macOS 都能起得来（`docker` 命令调用做平台分支） |
| NFR-05 | P1 | 可维护 | 控制台与 `gen_license.py` 解耦：控制台只调用 `build_license()` / `verify_license()`，不复制密码学逻辑。 authentik 升级时只需重跑 02 的自检 |

## 七、界面设计

主窗口 1100×720，单文档四区，顶部保留常驻状态条。

```
┌──────────────────────────────────────────────────────────────────────────┐
│ authentik 许可证管理控制台      [● 当前: valid · 50/500 · 2051-09-21]    │ ← 状态条
├──────────────────────────────────────────────────────────────────────────┤
│ 概览 │ 签发 │ 许可证库 │ 校验 │ 设置 │ 原理                              │ ← 页签
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                        当前页签内容                                       │
│  (表单区, 结果区, 表格区…)                                                │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ 2026-09-27 13:40:02  已发布 → status=valid，耗时 1.8s        [清空]       │ ← 日志条
└──────────────────────────────────────────────────────────────────────────┘
```

### 7.1 概览页

三张卡片纵向排列：

| 区块 | 字段 |
|---|---|
| 信任锚 | Root CA CN / 有效期 / SHA256 指纹（截断可读）/ 文件路径 / 「重签整条链」按钮 |
| 已发布许可证 | 名称 / 到期 / 内外部席位 / audience / 写入时间 / 「移除」按钮 |
| authentik 生效状态 | status / 席位占用 / latest_valid / 「重新读取」按钮 |

### 7.2 签发页

左表单右结果：

```
名称           [Personal                     ]
install_id     [6282f10e-…  ][从 authentik 读取]
年限 (年)       [ 25 ]    内部席位 [ 50 ]
外部席位        [ 500 ]    flags   [ ]
                                       [ 签 发 ]
──────────────────────────────────────────────
结果：
  ✓ 解析 x5c   叶子 CN=authentik Enterprise License
  ✓ 叶子 ← 中间
  ✓ 中间 ← 根 CA
  ✓ JWT 验签   ES512
  ✓ audience   匹配
  ✓ 未过期      2051-09-21
  ┌──────────────────────────────────────────┐
  │ eyJ0eXAiOiJKV1QiLCJhbGc…（只读文本框）    │
  └──────────────────────────────────────────┘
  [ 复制 ]  [ 保存到 issued/ ]  [ 发布到 authentik ]
```

### 7.3 许可证库页

表格列：文件名 / 名称 / 到期 / 席位 / audience / 本地校验 / 操作（复制、导出、发布、删除）。

### 7.4 设置页

可编辑项：`pki_dir`、`issued_dir`、`pg_container`、`pg_user`、`pg_db`、`server_container`、install_id 默认值；「测试连接」按钮依次验证 docker CLI 可用 → postgres 容器可达 → server 容器可达。

### 7.5 配色

沿用 02 文档里控制台的定位——本地单机工具，不做花哨主题；默认浅色，正文等宽字体呈现 JWT。

## 八、技术方案

### 8.1 选型

| 决策 | 选择 | 理由 |
|---|---|---|
| UI 框架 | **PyQt5**（用户指定） | 本机隔离 Python 已装 5.15.11 / Qt 5.15.2 / sip 12.19，开箱可用 |
| 密码学 | 复用 `gen_license.py` | 单一事实来源，避免两份实现漂移 |
| 与 authentik 通信 | `docker exec` 子进程 | 无需改造容器、无需映射端口、无需额外 Python 依赖 |
| 打包 | **PyInstaller 打包（M6）**，源码运行方式保留 |

> **授权提示**：PyQt5 只有 GPL v3 或商业授权两种选择；若在意这一点，等价的 PySide6 是 LGPL v3。**已决议沿用 PyQt5**，完整说明见第十一章附注。

### 8.2 目录结构（建议，见 Q-01）

```
tools/enterprise-license/
├── gen_license.py              既有：密码学与签发
├── README.md
└── console/                    新增：本控制台
    ├── main.py                 入口（QApplication + 主窗口 + 参数解析）
    ├── config.py               配置读写（json）+ 默认值
    ├── authentik.py            与 authentik 的子进程交互（psql / 清缓存 / 读状态）
    ├── licenses.py             签发、签发库、导入导出
    ├── workers.py              QRunnable 们（docker 调用放进线程池）
    ├── widgets/
    │   ├── overview.py  issue.py  vault.py  verify.py
    │   ├── settings.py  help.py
    ├── styles.qss
    ├── config.json             运行期生成，不入版本库（含私钥路径等本机信息）
    └── console.log
```

### 8.3 与 authentik 的交互通道

```
                  ┌─ 写表：docker exec -i <pg_container> psql -U <user> -d <db>   (SQL 走 stdin)
控制台 ─subprocess─┤
                  └─ 清缓存/读状态：docker exec <server_container> python -c <base64 代码>
```

- 写表 SQL（7 列全 `not null`）：

  ```sql
  delete from authentik_enterprise_license;
  insert into authentik_enterprise_license
    (license_uuid, key, name, expiry, internal_users, external_users)
  values (gen_random_uuid(), '<jwt>', '<name>', to_timestamp(<exp>), <internal>, <external>);
  ```

  > 是否 `delete` 取决于 Q-01（替换 vs 追加）。

- 清缓存与状态回读（用 base64 传代码，规避多层引号嵌套的坑）：

  ```python
  from authentik.enterprise.license import LicenseKey, CACHE_KEY_ENTERPRISE_LICENSE
  from django.core.cache import cache
  cache.delete(CACHE_KEY_ENTERPRISE_LICENSE)
  summary = LicenseKey.cached_summary()
  print(summary.status.value, summary.internal_users, summary.external_users, summary.latest_valid)
  ```

- 若 `docker` CLI 不可用（Q-03），降级为直连 Postgres TCP，需要 `psycopg2-binary`；控制台应把这条通道作为可选项而不是默认。

### 8.4 签发产物规范

```
issued/20260927-134502-personal.jwt      # 许可证正文（单行，末尾换行）
issued/20260927-134502-personal.json     # 元数据，便于列表展示：
{
  "name": "Personal", "install_id": "6282f10e-…", "years": 25,
  "internal_users": 50, "external_users": 500, "flags": [],
  "exp": 4xxxxxxx, "issued_at": "2026-09-27T13:45:02+08:00",
  "sha256": "…"
}
```

### 8.5 线程与错误呈现

- 所有 subprocess 走 `QThreadPool` + `QRunnable` + `pyqtSignal` 回主线程。
- 统一错误格式：`{"ok": false, "stage": "写表", "detail": "..."}`；界面直接显示 stage + detail，不抛 traceback。
- 长任务期间主窗口状态条显示「执行中…」，对应按钮 disabled。

## 九、风险与对策

| # | 风险 | 对策 |
|---|---|---|
| R1 | **x5c 必须用标准 base64**（踩过） | 控制台一律走 `build_license()`，不自己编码；FR-07 的逐项校验把失败原因直接显示出来 |
| R2 | **缓存跨进程**（踩过） | 发布动作强制清缓存后再回读，并把「清缓存」作为发布流程里的独立一步展示结果 |
| R3 | **开发机可能没有 docker，或 docker daemon 在另一台服务器上** | 直接连 Postgres 作为主通道；`docker exec` 降级为可选通道（缺失只影响「向实例核对」与旧部署）。见下方「执行通道设计」 |
| R4 | Windows 上 Python 写文件会把 `\n` 变成 CRLF | PEM/JWT 落盘统一 `newline=""` 或写后转 LF（NFR-04 的移植性要求） |
| R5 | 重签链会让已发布的所有旧票失效 | FR-08 强制备份 + 二次确认 + 结果页列出受影响文件 |
| R6 | 手滑把 `public.pem` 覆盖成别的东西 | 只允许通过 FR-08 改信任锚，且每次都留 `public.pem.bak-<时间戳>` |
| R7 | 容器名随 compose project 变化（踩过 `-p` 同名坑） | 容器名放进配置页并可测试连接，默认值从当前 `docker ps` 探测 |

## 十、里程碑与验收

| 里程碑 | 内容 | 验收方式 |
|---|---|---|
| M1 | 骨架 + 概览页 | 起窗口能看到信任锚/已发布/生效状态三块，数值与手工查询一致 |
| M2 | 签发 + 校验 | 签一张票，六项校验全绿，产物落在 `issued/` |
| M3 | 发布闭环 | 点发布后 authentik `status` 变 `valid`（用 `ak shell` 复核） |
| M4 | 回滚 + 设置 + 日志 | 移除许可证后回到 `unlicensed`；日志区能查到全过程 |
| M5 | 重签链 | 重签后旧票明确提示失效，新票可签可发 |
| M6 | PyInstaller 打包 exe（Q-02 决议 b） | `pip install pyinstaller` 后产出独立目录，双击 exe 可启动且签发/发布/校验三件事与源码运行行为一致 |

## 十一、待确认问题与决议（2026-09-27 已拍板）

| 编号 | 问题 | 决议 | 对实现的约束 |
|---|---|---|---|
| Q-01 | 控制台代码组织 | **(a) 多文件包 `console/`** | 落盘到 `tools/enterprise-license/console/`，按职责分包；不做单文件 |
| Q-02 | 是否打包成 exe | **(b) PyInstaller 打包 exe** | 需要 `PyInstaller` 依赖 + 一份打包脚本；源码运行方式仍要保留，打包作为发布态。见 M6 |
| Q-03 | 数据写入通道 | **(b) psycopg2 直连为主通道**，`docker exec` 降级为可选。理由：开发机可能没有 docker，或 docker daemon 部署在别的服务器上。 psycopg2 2.9.13 已在隔离 Python 中，无需新增安装。见「执行通道设计」与新增 Q-10 |
| Q-04 | 发布策略 | **(a) 替换：删旧插新** | 发布=删除 `authentik_enterprise_license` 全部行 → 插入新行；单实例单票，回滚靠再发一张旧票 |
| Q-05 | PyQt5(GPLv3/商业) | **沿用 PyQt5**（用户已指定） | 保持 PyQt5；授权提示保留在本节，见下方附注 |
| Q-06 | 界面语言 | **(a) 全中文** | 标签/按钮/提示/日志一律中文，技术名词（`JWT`、`install_id`、`License`）原样保留英文 |
| Q-07 | 导入官方许可证 | **(b) 只认自签票** | 不做官方票导入入口；`verify_license()` 天然只接受本 PKI 签出的链（用别的公钥签的验签必失败），无需额外分支 |
| Q-08 | 窗口布局 | **可缩放自适应** | 全部使用布局器 + `setMinimumSize`，禁用固定尺寸；高分屏与笔记本均可用 |
| Q-09 | 是否需要「到期前提醒」 | **(a) 状态条在剩余不足 30 天时变黄（推荐）** | 状态条显示剩余天数，低于阈值换色并给明确提示文案 |
| Q-10 | 本机部署要不要给 Postgres 加端口映射 | (a) 加 `127.0.0.1:5433:5432`（推荐，让直连通道在本机就能实测）(b) 不加，直连只作为将来远程部署的能力预留 | 选 (a) 会改动 `deploy/docker-compose.yml` 并重建 postgres 容器（数据卷不动，不丢数据）；选 (b) 则本机仍走 `docker exec`，直连需连对端 Postgres 才验证得了 |

### 附注：PyQt5 授权（Q-05 结论保留，不因此改技术选型）

 PyQt5 二选一：**GPL v3** 或**商业授权**；等价替代 PySide6 才是 LGPL v3。
 本次按用户指定沿用 PyQt5（个人自用、不分发、不修改 authentik 源码，不构成 GPL 的「衍生作品」分发义务）。
 但若将来要把本控制台**开源或对外分发**，需先切 PySide6 或购买商业授权 —— 届时工作量几乎不变（仅 import 行不同）。

### 执行通道设计（Q-03 翻案后的实现约束）

**为什么直连能替代 docker**：许可证写在 Postgres 表 `authentik_enterprise_license`，
而 authentik 用的缓存后端恰好也是 Postgres 里的表 `django_postgres_cache_cacheentry`
（`django_postgres_cache.backend.DatabaseCache`）。两者都在同一个库里，
所以「写表 + 清缓存」两步**根本不需要碰 docker**：

```sql
DELETE FROM django_postgres_cache_cacheentry
 WHERE cache_key LIKE '%goauthentik.io/enterprise/license';
```

> 踩坑：缓存 key 落库时**带前缀**，实测实际值是
> `public::1:goauthentik.io/enterprise/license`（来自 `cache.delete("goauthentik.io/enterprise/license")`
> 那一行的写入）。所以此处必须按后缀 `LIKE` 匹配，写死成 `= 'goauthentik.io/...'` 会删不到行。

**唯一需要 docker 的环节**：读「authentik 进程实际生效的状态」——
`LicenseKey.cached_summary()` 是服务端 Python 算出来的。没有 docker 时按 FR-13 本地复算：
复刻 `authentik/enterprise/license.py` 的判定顺序（阈值 2/4/2/6 周来自 `enterprise/models.py`，
席位统计口径 `is_active AND username <> 'AnonymousUser' AND type='internal'/'external'`）。
该口径已在本机与 Django 对齐实测（SQL 得 1/0，`get_internal_user_count()` 亦为 1）。

**通道优先级**：`auto`（默认）→ 直连 Postgres，连接失败再试 `docker exec psql`；
也可在设置页锁 `direct` / `docker`。远程 docker host 场景（`DOCKER_HOST` / ssh context）下
直连天然可用，`docker exec` 那条通道仍可保留作兜底。

> **前置条件（Q-10）**：当前 `deploy/docker-compose.yml` 的 `postgresql` 服务**没有 ports 映射**
> （当初为避让本机 5432 上正在跑的其他项目而故意不映射）。要让直连在本地跑通，需在其下加一行
> `ports: - "127.0.0.1:5433:5432"` 并重建该容器。生产/远程场景不受此限——只需对端开放 5432。

### 对影响章节的相关修订

- 第 8.2 目录结构：`console/` 分包方案生效。
- FR-05（发布）按 Q-04 改为「替换式」：先清空 `authentik_enterprise_license` 全部行，再插新行，不再累积历史。
- FR-09（回滚）因此只需「再次发布一张旧票」即可，无需单独实现删除逻辑。
- 里程碑新增 **M6：PyInstaller 打包 exe**（验收：独立目录下双击即可启动，且功能与源码运行一致）。

## 附录 A：本次工作区已有改动

| 文件 | 状态 |
|---|---|
| `tools/enterprise-license/gen_license.py` | 已改：抽出 4 个可复用函数 + 新增 `chain/` 两个证书产物；CLI 行为不变，回归通过 |

（其余按本轮评审结果执行；按协作习惯，改动一律留在工作区，不提交。）
