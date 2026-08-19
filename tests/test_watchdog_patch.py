import importlib.util
import sys
import types
from pathlib import Path

WD_PATH = Path("deploy/rasp/watchdog_test.py")

def _fake_selenium(chrome_factory):
    """Registra módulos selenium falsos; chrome_factory() é chamado por webdriver.Chrome()."""
    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("selenium.webdriver")
    webdriver.Chrome = lambda *a, **k: chrome_factory()
    common_by = types.ModuleType("selenium.webdriver.common.by")
    common_by.By = types.SimpleNamespace(XPATH="xpath")
    chrome_opts = types.ModuleType("selenium.webdriver.chrome.options")
    chrome_opts.Options = lambda: types.SimpleNamespace(binary_location=None)
    chrome_svc = types.ModuleType("selenium.webdriver.chrome.service")
    chrome_svc.Service = lambda *a: None
    selenium.webdriver = webdriver
    for name, mod in {
        "selenium": selenium,
        "selenium.webdriver": webdriver,
        "selenium.webdriver.common": types.ModuleType("selenium.webdriver.common"),
        "selenium.webdriver.common.by": common_by,
        "selenium.webdriver.chrome": types.ModuleType("selenium.webdriver.chrome"),
        "selenium.webdriver.chrome.options": chrome_opts,
        "selenium.webdriver.chrome.service": chrome_svc,
    }.items():
        sys.modules[name] = mod

def _load(chrome_factory, tmp_path, monkeypatch):
    _fake_selenium(chrome_factory)
    spec = importlib.util.spec_from_file_location("watchdog_test", WD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "LOG", str(tmp_path / "router.log"))
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    return mod

def test_chrome_failing_to_open_does_not_raise(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("chromedriver ausente")
    mod = _load(boom, tmp_path, monkeypatch)
    assert mod.restart_router() == -1        # original: NameError aqui

def test_success_path_quits_driver(tmp_path, monkeypatch):
    calls = []
    class FakeDriver:
        def get(self, url): calls.append(("get", url))
        def find_element(self, by, xp):
            return types.SimpleNamespace(click=lambda: calls.append(("click", xp)))
        def quit(self): calls.append(("quit", None))
    mod = _load(FakeDriver, tmp_path, monkeypatch)
    assert mod.restart_router() == 0
    assert ("quit", None) in calls
    assert sum(1 for c in calls if c[0] == "click") == 2
