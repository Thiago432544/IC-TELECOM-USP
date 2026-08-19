from monitor.config import CpeCfg
from monitor.cpe import CpeScraper

CFG = CpeCfg(enabled=True, url="http://cpe/", username="", password="",
             rsrp_re=r'(-?\d+)\s*dBm\s*\(RSRP\)', rsrq_re=r'(-?\d+)\s*dB\s*\(RSRQ\)',
             interval_s=60)

HTML = "<td>-91 dBm (RSRP)</td><td>-6 dB (RSRQ)</td><td>Conectado</td>"

def test_parses_rsrp_rsrq_connected():
    sc = CpeScraper(CFG, get=lambda url, auth: HTML)
    r = sc.fetch()
    assert r.rsrp == -91.0 and r.rsrq == -6.0 and r.connected is True

def test_http_failure_returns_none():
    sc = CpeScraper(CFG, get=lambda url, auth: None)
    assert sc.fetch() is None

def test_page_without_metrics_still_reports():
    sc = CpeScraper(CFG, get=lambda url, auth: "<html>login</html>")
    r = sc.fetch()
    assert r.rsrp is None and r.rsrq is None and r.connected is False
