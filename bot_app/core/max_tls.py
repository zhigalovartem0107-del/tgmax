"""Доверенные сертификаты для официального API MAX."""
import ssl
from pathlib import Path

from requests.adapters import HTTPAdapter

MAX_API_ORIGIN = "https://platform-api2.max.ru"
CA_FILE = Path(__file__).with_name("max-ca.crt")


class MaxTLSAdapter(HTTPAdapter):
    def __init__(self, **options):
        self.context = ssl.create_default_context()
        self.context.load_verify_locations(cafile=str(CA_FILE))
        super().__init__(**options)

    def init_poolmanager(self, connections, maxsize, block=False, **options):
        super().init_poolmanager(connections, maxsize, block, ssl_context=self.context, **options)

    def proxy_manager_for(self, proxy, **options):
        return super().proxy_manager_for(proxy, ssl_context=self.context, **options)

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        # Requests 2.32+ может подставить свой контекст для отдельного соединения.
        host, options = super().build_connection_pool_key_attributes(request, verify, cert)
        options["ssl_context"] = self.context
        return host, options


def configure_max_tls(session, **options):
    session.mount(MAX_API_ORIGIN + "/", MaxTLSAdapter(**options))
