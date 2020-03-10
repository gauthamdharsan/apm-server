import os
import requests
import shutil
import ssl
import subprocess

from nose.tools import raises
from requests.packages.urllib3.exceptions import SubjectAltNameWarning
requests.packages.urllib3.disable_warnings(SubjectAltNameWarning)

from apmserver import ServerBaseTest
from apmserver import TimeoutError, integration_test

INTEGRATION_TESTS = os.environ.get('INTEGRATION_TESTS', False)


@integration_test
class TestSecureServerBaseTest(ServerBaseTest):
    @classmethod
    def setUpClass(cls):
        # According to https://docs.python.org/2/library/unittest.html#setupclass-and-teardownclass setUp and tearDown
        # should be skipped when class is skipped, which is apparently not true.
        # This is a hack to avoid running the setup while it should be skipped
        if not INTEGRATION_TESTS:
            return
        cls.config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "config"))
        cls.cert_path = os.path.join(cls.config_path, "certs")
        shutil.rmtree(cls.cert_path, ignore_errors=True)
        cls.create_certs_cmd = os.path.join(cls.config_path, "create_certs.sh")
        with open(os.devnull, 'wb') as dev_null:
            subprocess.call([cls.create_certs_cmd, cls.config_path, cls.cert_path,
                             "foobar"], stdout=dev_null, stderr=dev_null)
        super(TestSecureServerBaseTest, cls).setUpClass()

    @classmethod
    def tearDownClass(cls):
        if not INTEGRATION_TESTS:
            return
        super(TestSecureServerBaseTest, cls).tearDownClass()
        shutil.rmtree(cls.cert_path)

    def setUp(self):
        self.config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "config"))
        self.cert_path = os.path.join(self.config_path, "certs")
        self.ca_cert = os.path.join(self.cert_path, "ca.crt.pem")
        self.simple_cert = os.path.join(self.cert_path, "simple.crt.pem")
        self.simple_key = os.path.join(self.cert_path, "simple.key.pem")
        self.client_cert = os.path.join(self.cert_path, "client.crt.pem")
        self.client_key = os.path.join(self.cert_path, "client.key.pem")
        self.server_cert = os.path.join(self.cert_path, "server.crt.pem")
        self.server_key = os.path.join(self.cert_path, "server.key.pem")
        self.password = "foobar"
        self.host = "localhost"
        self.port = 8200
        super(TestSecureServerBaseTest, self).setUp()

    def stop_proc(self):
        self.apmserver_proc.kill_and_wait()

    def ssl_overrides(self):
        return {}

    def config(self):
        cfg = super(TestSecureServerBaseTest, self).config()
        overrides = {
            "ssl_enabled": "true",
            "ssl_certificate": self.server_cert,
            "ssl_key": self.server_key,
            "ssl_key_passphrase": self.password
        }
        cfg.update(overrides)
        cfg.update(self.ssl_overrides())
        return cfg

    def ssl_connect(self, protocol=ssl.PROTOCOL_TLSv1_2, ciphers=None, cert=None, key=None, ca_cert=None):
        context = ssl.SSLContext(protocol)
        if ciphers:
            context.set_ciphers(ciphers)
        if not ca_cert:
            ca_cert = self.ca_cert
        context.load_verify_locations(ca_cert)
        if cert and key:
            context.load_cert_chain(certfile=cert, keyfile=key, password=self.password)
        s = context.wrap_socket(ssl.socket())
        s.connect((self.host, self.port))


class TestSSLBadPassphraseTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_key_passphrase": "invalid"}

    @raises(TimeoutError)
    def setUp(self):
        super(TestSecureServerBaseTest, self).setUp()


@integration_test
class TestSSLEnabledNoClientVerificationTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_client_authentication": "none"}

    def test_https_no_cert_ok(self):
        self.ssl_connect()

    def test_http_fails(self):
        with self.assertRaises(Exception):
            with requests.Session() as session:
                try:
                    return session.post("http://localhost:8200/intake/v2/events",
                                        headers={'content-type': 'application/x-ndjson'},
                                        data=self.get_event_payload())
                finally:
                    session.close()


@integration_test
class TestSSLEnabledOptionalClientVerificationTest(TestSecureServerBaseTest):
    # no ssl_overrides necessary as `optional` is default

    def test_https_no_certificate_ok(self):
        self.ssl_connect()

    @raises(ssl.SSLError)
    def test_https_verify_cert_if_given(self):
        self.ssl_connect(cert=self.simple_cert, key=self.simple_key)

    @raises(ssl.SSLError)
    def test_https_self_signed_cert(self):
        # CA is not configured server side, so self signed certs are not valid
        self.ssl_connect(cert=self.client_cert, key=self.client_key)


@integration_test
class TestSSLEnabledOptionalClientVerificationWithCATest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_certificate_authorities": self.ca_cert}

    @raises(ssl.SSLError)
    def test_https_no_certificate(self):
        # since CA is configured, client auth is required
        self.ssl_connect()

    @raises(ssl.SSLError)
    def test_https_verify_cert_if_given(self):
        # invalid certificate
        self.ssl_connect(cert=self.simple_cert, key=self.simple_key)

    def test_https_auth_cert_ok(self):
        self.ssl_connect(cert=self.client_cert, key=self.client_key)


@integration_test
class TestSSLEnabledRequiredClientVerificationTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_client_authentication": "required",
                "ssl_certificate_authorities": self.ca_cert}

    @raises(ssl.SSLError)
    def test_https_no_cert_fails(self):
        self.ssl_connect()

    @raises(ssl.SSLError)
    def test_https_invalid_cert_fails(self):
        self.ssl_connect(cert=self.simple_cert, key=self.simple_key)

    def test_https_auth_cert_ok(self):
        self.ssl_connect(cert=self.client_cert, key=self.client_key)


@integration_test
class TestSSLDefaultSupportedProcotolsTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_certificate_authorities": self.ca_cert}

    @raises(ssl.SSLError)
    def test_tls_v1_0(self):
        self.ssl_connect(protocol=ssl.PROTOCOL_TLSv1, cert=self.server_cert, key=self.server_key)

    def test_tls_v1_1(self):
        self.ssl_connect(protocol=ssl.PROTOCOL_TLSv1_1, cert=self.server_cert, key=self.server_key)

    def test_tls_v1_2(self):
        self.ssl_connect(cert=self.server_cert, key=self.server_key)


@integration_test
class TestSSLSupportedProcotolsTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_supported_protocols": ["TLSv1.2"],
                "ssl_certificate_authorities": self.ca_cert}

    @raises(ssl.SSLError)
    def test_tls_v1_1(self):
        self.ssl_connect(protocol=ssl.PROTOCOL_TLSv1_1, cert=self.server_cert, key=self.server_key)

    def test_tls_v1_2(self):
        self.ssl_connect(cert=self.server_cert, key=self.server_key)


@integration_test
class TestSSLSupportedCiphersTest(TestSecureServerBaseTest):
    def ssl_overrides(self):
        return {"ssl_cipher_suites": ['ECDHE-RSA-AES-128-GCM-SHA256'],
                "ssl_certificate_authorities": self.ca_cert}

    def test_https_no_cipher_set(self):
        self.ssl_connect(cert=self.server_cert, key=self.server_key)

    def test_https_supports_cipher(self):
        # set the same cipher in the client as set in the server
        self.ssl_connect(ciphers='ECDHE-RSA-AES128-GCM-SHA256', cert=self.server_cert, key=self.server_key)

    def test_https_unsupported_cipher(self):
        # client only offers unsupported cipher
        with self.assertRaisesRegexp(ssl.SSLError, 'SSLV3_ALERT_HANDSHAKE_FAILURE'):
            self.ssl_connect(ciphers='ECDHE-RSA-AES256-SHA384', cert=self.server_cert, key=self.server_key)

    def test_https_no_cipher_selected(self):
        # client provides invalid cipher
        with self.assertRaisesRegexp(ssl.SSLError, 'No cipher can be selected'):
            self.ssl_connect(ciphers='AES1sd28-CCM8', cert=self.server_cert, key=self.server_key)