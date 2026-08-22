import os
import time
from monitor.watcher import FrameWatcher

def _day(now):
    return time.strftime("%Y_%m_%d", time.localtime(now))

def _mkframe(root, day, cam, name, mtime):
    d = root / day / cam
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"x")
    os.utime(p, (mtime, mtime))

def test_first_poll_has_no_rate_but_has_age(tmp_path):
    now = time.time()
    _mkframe(tmp_path, _day(now), "102", "a.jpg", now - 30)
    w = FrameWatcher(tmp_path, {"102": 10})
    [s] = w.poll(now)
    assert s.frames_per_min is None
    assert 25 <= s.last_frame_age_s <= 35

def test_rate_corrected_by_save_every(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"102": 10})
    w.poll(now - 60)                          # abre a janela
    for i in range(6):                        # 6 gravados em 60s
        _mkframe(tmp_path, _day(now), "102", f"f{i}.jpg", now - 50 + i * 8)
    [s] = w.poll(now)
    # 6 arquivos * save_every 10 / 1 min = 60 frames/min
    assert abs(s.frames_per_min - 60.0) < 1.0

def test_missing_today_falls_back_to_yesterday(tmp_path):
    now = time.time()
    yesterday = now - 86400
    _mkframe(tmp_path, _day(yesterday), "106", "old.jpg", yesterday)
    w = FrameWatcher(tmp_path, {"106": 10})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0
    assert s.last_frame_age_s >= 86000

def test_never_any_frame(tmp_path):
    now = time.time()
    w = FrameWatcher(tmp_path, {"105": 1})
    w.poll(now - 60)
    [s] = w.poll(now)
    assert s.frames_per_min == 0.0 and s.last_frame_age_s is None


def test_taxa_nao_e_quantizada_pela_cadencia_do_poll(tmp_path):
    """Taxa medida em janela longa, nao no intervalo de um poll.

    Com poll de 10s e save_every=10, cada arquivo visto vale 60 f/min: a
    metrica so conseguia assumir 0, 60, 120, 180. Uma camera entregando 30
    f/min de verdade virava uma onda quadrada 0/120 - ilegivel no grafico e,
    pior, incapaz de disparar o alerta de degradada.
    """
    now = time.time()
    w = FrameWatcher(tmp_path, {"106": 10}, rate_window_s=300)
    t0 = now - 700
    w.poll(t0)
    # 1 arquivo a cada 20s = 3 arq/min * save_every 10 = 30 frames/min
    chegadas = [t0 + i * 20 + 1 for i in range(35)]
    vistos, amostras = 0, []
    for k in range(1, 61):                    # 60 polls de 10s
        t = t0 + k * 10
        while vistos < len(chegadas) and chegadas[vistos] <= t:
            _mkframe(tmp_path, _day(now), "106", f"f{vistos}.jpg",
                     chegadas[vistos])
            vistos += 1
        [s] = w.poll(t)
        if s.frames_per_min is not None:
            amostras.append(s.frames_per_min)
    estaveis = amostras[30:]                  # depois da janela encher
    assert all(abs(v - 30.0) < 6.0 for v in estaveis), sorted(set(estaveis))


def test_janela_longa_nao_atrasa_a_primeira_taxa(tmp_path):
    """Depois de reiniciar, a taxa sai no segundo poll como sempre saiu."""
    now = time.time()
    w = FrameWatcher(tmp_path, {"102": 10}, rate_window_s=300)
    w.poll(now - 10)
    _mkframe(tmp_path, _day(now), "102", "a.jpg", now - 5)
    [s] = w.poll(now)
    assert s.frames_per_min is not None and s.frames_per_min > 0


def test_taxa_degradada_fica_abaixo_do_limiar_sem_oscilar(tmp_path):
    """O alerta de degradada exige 600s CONTINUOS abaixo do limiar.

    Com a taxa quantizada em 0/60/120 a serie subia acima do limiar a cada
    poucos polls e zerava o contador - o alerta nunca chegava a disparar. Aqui
    a camera entrega 1/4 do normal e a serie tem que ficar embaixo o tempo
    todo, que e' o que o AlertEngine precisa receber.
    """
    now = time.time()
    w = FrameWatcher(tmp_path, {"106": 10}, rate_window_s=300)
    t0 = now - 1500
    w.poll(t0)
    limiar = 30.0                             # 0.5 * linha de base 60 f/min
    chegadas = [t0 + i * 40 + 1 for i in range(40)]   # 15 f/min de verdade
    vistos, abaixo = 0, []
    for k in range(1, 121):
        t = t0 + k * 10
        while vistos < len(chegadas) and chegadas[vistos] <= t:
            _mkframe(tmp_path, _day(now), "106", f"g{vistos}.jpg",
                     chegadas[vistos])
            vistos += 1
        [s] = w.poll(t)
        if s.frames_per_min is not None:
            abaixo.append(s.frames_per_min < limiar)
    assert all(abaixo[30:]), "serie subiu acima do limiar e zerou o contador"
