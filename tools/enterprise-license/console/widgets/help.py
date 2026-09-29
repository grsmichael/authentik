"""原理页：离线校验原理与三个坑（换机器后对照，避免重踩）。"""
from __future__ import annotations

from PyQt5.QtWidgets import QTextEdit, QVBoxLayout, QWidget


class HelpTab(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        doc = QTextEdit()
        doc.setReadOnly(True)
        doc.setHtml(
            "<h2>authentik 企业版许可证 · 离线自签原理</h2>"
            "<p>authentik 的许可证校验在 <code>authentik/enterprise/license.py</code> 里，"
            "<b>完全离线</b>：它读取容器内的 <code>/authentik/enterprise/public.pem</code> 当作 "
            "<b>Root CA 证书</b>（唯一信任锚），再用 JWT 的 <code>x5c</code> 里携带的叶子证书、中间证书逐级校验：</p>"
            "<ol>"
            "<li>解析 <code>x5c</code>：叶子证书、中间证书（DER）。</li>"
            "<li>叶子证书 <code>verify_directly_issued_by</code> 中间证书。</li>"
            "<li>中间证书 <code>verify_directly_issued_by</code> Root CA（即 public.pem）。</li>"
            "<li>用<b>叶子证书公钥</b>验签 JWT（仅允许 ES384 / ES512）。</li>"
            "<li><code>aud</code> 必须严格等于 <code>enterprise.goauthentik.io/license/&lt;install_id&gt;</code>。</li>"
            "</ol>"
            "<p>因此只要自备「根 CA → 中间 CA → 叶子」三级链，把 <code>public.pem</code> 换成自签根 CA，"
            "再用叶子私钥签许可证 JWT，官方校验就会全数通过 —— <b>authentik 源码零改动</b>。</p>"
            "<h3>三个踩过的坑</h3>"
            "<ul>"
            "<li><b>R1 · x5c 必须用标准 base64</b>：license.py 用标准字母表解码，若用 base64url 的 "
            "<code>-</code>/<code>_</code> 会被丢弃，DER 损坏报 “Unable to verify license”。本控制台一律走 "
            "<code>build_license()</code>，不自己编码。</li>"
            "<li><b>R2 · 缓存跨进程</b>：CACHES 是 DatabaseCache（12h TTL），改完必须显式删缓存键 "
            "<code>goauthentik.io/enterprise/license</code>，重启容器无效。发布流程已强制清缓存。</li>"
            "<li><b>R3 · 开发机可能没有 docker</b>：许可证写在 Postgres 表，缓存也在 Postgres 表，"
            "同库所以「写表 + 清缓存」无需 docker；唯一需要 docker 的是读服务端实时状态，"
            "无 docker 时按 FR-13 在本地复算（已与 Django 对齐）。</li>"
            "</ul>"
            "<h3>关键事实</h3>"
            "<ul>"
            "<li>install_id 来自 <code>public.authentik_install_id(id)</code>（django-tenants，public schema）。</li>"
            "<li>许可证表 <code>public.authentik_enterprise_license</code>（6 列：license_uuid/key/name/expiry/internal_users/external_users）。</li>"
            "<li>用户计数口径：<code>username &lt;&gt; 'AnonymousUser' AND is_active AND type='internal'/'external'</code>。</li>"
            "</ul>"
        )
        layout.addWidget(doc)
