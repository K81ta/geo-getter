import http.client
import unittest
from unittest import mock

from geo_getter.errors import GeoGetterError
from geo_getter.http_client import fetch_text


class HttpClientTest(unittest.TestCase):
    def test_fetch_text_wraps_socket_style_errors_as_network_failed(self):
        for error in (TimeoutError("timed out"), http.client.HTTPException("bad response"), OSError("socket reset")):
            with self.subTest(error=type(error).__name__):
                with mock.patch("geo_getter.http_client.urllib.request.urlopen", side_effect=error):
                    with self.assertRaises(GeoGetterError) as context:
                        fetch_text("https://example.invalid")
                self.assertEqual(context.exception.code, "network_failed")


if __name__ == "__main__":
    unittest.main()