"""Проверяет доверие MAX, проверку имени хоста и границу с другими сервисами."""
import hashlib
import ssl

import requests

from bot_app.core.max_tls import MAX_API_ORIGIN, configure_max_tls


with requests.Session() as session:
    configure_max_tls(session)
    adapter = session.get_adapter(MAX_API_ORIGIN + "/messages")
    ctx = adapter.context
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    trusted = {hashlib.sha256(cert).hexdigest() for cert in ctx.get_ca_certs(binary_form=True)}
    assert {
        "d26d2d0231b7c39f92cc738512ba54103519e4405d68b5bd703e9788ca8ecf31",
        "bbbde2103e790b999ec62bd03cf625a5a2e7c316e10afe6a490eedead8b3fd9b",
    } <= trusted
    for url in ("https://api.telegram.org/", MAX_API_ORIGIN + ".example.org/"):
        assert session.get_adapter(url) is not adapter
    assert adapter.poolmanager.connection_pool_kw["ssl_context"] is ctx
    proxy = adapter.proxy_manager_for("http://localhost:3128")
    assert proxy.connection_pool_kw["ssl_context"] is ctx
    request = requests.Request("GET", MAX_API_ORIGIN + "/me").prepare()
    if hasattr(requests.adapters.HTTPAdapter, "build_connection_pool_key_attributes"):
        _, options = adapter.build_connection_pool_key_attributes(request, True, None)
        assert options["ssl_context"] is ctx

print("MAX TLS: корневой и промежуточный сертификаты, имя сервера, прокси и область доверия — OK")
