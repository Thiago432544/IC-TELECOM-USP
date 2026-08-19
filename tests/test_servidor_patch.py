import importlib.util
import sys
import types
from pathlib import Path

SRV = Path("deploy/pc/Servidor_receb_Imagens_Rasp.py")

def _load():
    # cv2/numpy nao existem na maquina de dev; o modulo so os usa dentro de funcoes
    sys.modules.setdefault("cv2", types.ModuleType("cv2"))
    sys.modules.setdefault("numpy", types.ModuleType("numpy"))
    spec = importlib.util.spec_from_file_location("servidor", SRV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)      # seguro: servidor so inicia sob __main__
    return mod

def test_timeout_unico_de_30s():
    mod = _load()
    assert mod.SOCKET_TIMEOUT == 30

def test_interval_por_cliente_foi_removido():
    src = SRV.read_text(encoding="utf-8")
    assert "INTERVAL" not in src
    assert "settimeout(SOCKET_TIMEOUT)" in src
