from scrapy import Request

from kedra_scraper.scraper.middlewares import RotatingProxyMiddleware


PROXY_1 = "http://user:pass@proxy-1.example:8000"
PROXY_2 = "http://user:pass@proxy-2.example:8000"


def test_proxy_rotates_for_every_outgoing_request():
    middleware = RotatingProxyMiddleware([PROXY_1, PROXY_2])
    first_request = Request("https://example.com/first")
    middleware.process_request(first_request)
    retried_request = first_request.replace(url="https://example.com/retried")
    middleware.process_request(retried_request)

    assert {first_request.meta["proxy"], retried_request.meta["proxy"]} == {
        PROXY_1,
        PROXY_2,
    }
    assert first_request.meta["proxy_host"] != retried_request.meta["proxy_host"]
