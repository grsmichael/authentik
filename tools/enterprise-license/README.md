# 自签 authentik 企业版许可证

离线生成一套「根 CA → 中间 CA → 叶子证书」，再把许可证 JWT 签出来。
**不用申请官方 key，不用改 authentik 一行源码。**

| 产物 | 去向 |
|---|---|
| `public.pem` | 挂进容器覆盖 `/authentik/enterprise/public.pem`（许可证校验的唯一信任锚） |
| `license.jwt` | 填进 authentik → System → Licenses（或直接插库） |
| `private/*.key.pem` | 只留在本机，别外传（生成物默认 0600） |

详见 `.workbuddy/analysis/02-自签企业版许可证技术方案-v1.0.md`。

## 前提

`install_id` —— 决定 JWT 里 `aud` 的值，必须与本实例一致，否则报
`Invalid Install ID in license`：

```bash
docker exec authentik-postgresql psql -U authentik -d authentik \
  -tAc "select * from authentik_install_id limit 1"
```

本机当前值：`6282f10e-ff93-4ff1-8d2b-456d084cd411`

## 用法

```bash
# 生成（默认：25 年有效、50 内部席位 / 500 外部席位）
python gen_license.py \
  --install-id 6282f10e-ff93-4ff1-8d2b-456d084cd411 \
  --out-dir ../../deploy/enterprise-pki

# 常用参数
#   --years N        许可证有效期（默认 25）
#   --internal-users N / --external-users N   席位
#   --name "..."     许可证名字（UI 里展示）
#   --force          已存在产物时覆盖
```

脚本跑完会**自检**：按 `authentik/enterprise/license.py` 的顺序复现一遍
（解 `x5c` → 验证书链 → 用叶子公钥验签 → 比对 `aud`），不通过直接退出。

## 装进 authentik

```bash
# 1) 让容器读到新的信任锚（get_licensing_key 是 lru_cache，必须重建容器）
cd ../../deploy && docker compose up -d

# 2) 入库：UI  System → Licenses → Add，粘贴 license.jwt 全文
#    或  库  insert into authentik_enterprise_license
#           (license_uuid, key, name, expiry, internal_users, external_users)
#         values (gen_random_uuid(), '<license.jwt>', '...', to_timestamp(<exp>), 50, 500);

# 3) 清缓存（缓存是 DatabaseCache，跨进程，12 小时 TTL，restart 清不掉）
docker exec authentik-server ak shell -c "from django.core.cache import cache; \
  cache.delete('goauthentik.io/enterprise/license')"

# 4) 验证
docker exec authentik-server ak shell -c "from authentik.enterprise.license import LicenseKey; \
  s = LicenseKey.cached_summary(); print(s.status.value, s.internal_users, s.external_users)"
# 期望： valid 50 500
```

## 两个必须记住的坑

1. **x5c 用标准 base64，不能用 base64url。** `license.py` 写的是 `from base64 import b64decode`，
   按标准字母表解码；base64url 编码出的 `-` / `_` 会被当成非法字符**直接丢弃**，
   DER 损坏后只报一句含糊的 `Unable to verify license`。第一版就踩了这个。
2. **签名算法只能 ES384 / ES512**，且用 `secp384r1` 密钥（与官方一致）。
   pyjwt 会按 alg 反查曲线，选错曲线报 `InvalidKeyError`。

## 依赖

只用 `cryptography`（authentik 的硬依赖）：

```bash
python -c "import cryptography; print(cryptography.__version__)"   # 已验证 50.0.1 可用
```

`pyjwt` 是可选的 —— 装了会在自检里多做一次官方库复验。
