"""Fiacao do monitor: threads supervisionadas sobre os modulos puros."""
from __future__ import annotations
import shutil
import threading
import time
from pathlib import Path

from monitor.alerts import AlertEngine, CamState, Snapshot
from monitor.baseline import hourly_baseline
from monitor.bot import BotHandler
from monitor.charts import render_metric_chart
from monitor.config import Config, load_config
from monitor.metrics import DEFAULT_WINDOW, find_metric, outage_floor
from monitor.cpe import CpeScraper
from monitor.panel import run_panel
from monitor.store import Store
from monitor.summary import build_daily_summary
from monitor.taplog import LogFollower
from monitor.telegram import TelegramClient
from monitor.watcher import FrameWatcher


def build_snapshot(store: Store, cfg: Config, now: float,
                   disk_free_gb: float) -> Snapshot:
    cams = {}
    for cam in cfg.cameras:
        fpm = store.last_sample(cam, "frames_min")
        age = store.last_sample(cam, "last_frame_age_s")
        hour = time.localtime(now).tm_hour
        cams[cam] = CamState(
            frames_per_min=fpm[1] if fpm else None,
            last_frame_age_s=age[1] if age else None,
            baseline=hourly_baseline(store, cam, hour, now),
            disconnects_15min=store.count_events(
                cam, "DISCONNECT", now - cfg.alerts.flap_window_s),
        )
    rsrp = store.last_sample("cpe", "rsrp")
    rsrq = store.last_sample("cpe", "rsrq")
    fresh = cfg.cpe.interval_s * 3
    return Snapshot(
        cameras=cams,
        disk_free_gb=disk_free_gb,
        rsrp=rsrp[1] if rsrp and now - rsrp[0] < fresh else None,
        rsrq=rsrq[1] if rsrq and now - rsrq[0] < fresh else None,
    )


def should_send_summary(last_sent_date, now, hour) -> bool:
    lt = time.localtime(now)
    today = time.strftime("%Y-%m-%d", lt)
    return lt.tm_hour >= hour and last_sent_date != today


def chart_metric_for(kind: str) -> str:
    """Grafico que casa com o alerta.

    Camera "degradada" esta conectada: mandar a faixa de conexao mostraria
    ~100% no ar e contradiria o texto do proprio alerta. O que caiu ali foi a
    taxa de entrega.
    """
    return "frames" if kind == "degraded" else "conexao"


def alert_chart(store: Store, cfg: Config, camera: str, now: float,
                kind: str = "down") -> bytes:
    janela = DEFAULT_WINDOW
    piso = outage_floor(janela, cfg.charts.outage_min_s or None)
    return render_metric_chart(store, camera, find_metric(chart_metric_for(kind)),
                               now, janela, piso)


def route_update(handler, tg, upd: dict, now: float) -> bool:
    """Trata um update do Telegram. True se respondeu alguma coisa.

    Toque em botao edita a foto que ja esta no chat em vez de mandar outra -
    e' o que faz os botoes valerem a pena em vez de virarem enxurrada.
    """
    cb = upd.get("callback_query")
    if cb:
        # antes de desenhar: senao o botao fica girando no celular
        tg.answer_callback(cb.get("id", ""))
        r = handler.handle_callback(cb.get("data", ""), now)
        msg = cb.get("message", {})
        if r.png:
            tg.edit_photo(msg.get("chat", {}).get("id"), msg.get("message_id"),
                          r.png, r.text, r.buttons)
        return True
    text = upd.get("message", {}).get("text", "")
    if not text.startswith("/"):
        return False
    r = handler.handle(text, now)
    if r.png:
        tg.send_photo(r.png, r.text, r.buttons)
    else:
        tg.send_text(r.text)
    return True


def _forever(store: Store, name: str, fn, interval: float):
    def run():
        while True:
            t0 = time.time()
            try:
                fn()
            except Exception as e:
                try:
                    store.add_event(time.time(), "monitor", "worker_error",
                                    f"{name}: {e!r}")
                except Exception:
                    pass
            time.sleep(max(0.5, interval - (time.time() - t0)))
    t = threading.Thread(target=run, name=name, daemon=True)
    t.start()
    return t


def main(config_path: str = "config.toml"):
    cfg = load_config(Path(config_path))
    store = Store(cfg.paths.data / "monitor.db")
    tg = TelegramClient(cfg.telegram, cfg.paths.data) if cfg.telegram.enabled else None
    engine = AlertEngine(cfg.alerts)
    watcher = FrameWatcher(cfg.paths.images, {c: v.save_every
                                              for c, v in cfg.cameras.items()})
    follower = LogFollower(cfg.paths.log, on_event=lambda ev: store.add_event(
        ev.ts, ev.client or "server", ev.kind, ev.detail))
    scraper = CpeScraper(cfg.cpe) if cfg.cpe.enabled else None
    handler = BotHandler(store, cfg)
    state = {"summary_date": None, "last_purge": 0.0, "bot_offset": 0}

    def watch():
        now = time.time()
        for s in watcher.poll(now):
            if s.frames_per_min is not None:
                store.add_sample(now, s.camera, "frames_min", s.frames_per_min)
            if s.last_frame_age_s is not None:
                store.add_sample(now, s.camera, "last_frame_age_s", s.last_frame_age_s)

    def disk():
        free_gb = shutil.disk_usage(cfg.paths.images.anchor or "C:\\").free / 1e9
        store.add_sample(time.time(), "pc", "disk_free_gb", free_gb)

    def cpe():
        now = time.time()
        r = scraper.fetch()
        store.add_sample(now, "cpe", "cpe_up", 0.0 if r is None else 1.0)
        if r:
            if r.rsrp is not None:
                store.add_sample(now, "cpe", "rsrp", r.rsrp)
            if r.rsrq is not None:
                store.add_sample(now, "cpe", "rsrq", r.rsrq)

    def alerts():
        now = time.time()
        last_disk = store.last_sample("pc", "disk_free_gb")
        snap = build_snapshot(store, cfg, now,
                              last_disk[1] if last_disk else 1e9)
        for a in engine.evaluate(now, snap):
            store.add_event(now, a.origin, f"alert_{a.kind}", a.text)
            if tg is None:
                continue
            if a.wants_chart:
                cam = a.origin if a.origin in cfg.cameras else next(iter(cfg.cameras))
                tg.send_photo(alert_chart(store, cfg, cam, now, a.kind), a.text)
            else:
                tg.send_text(a.text)
        if should_send_summary(state["summary_date"], now, cfg.summary_hour):
            state["summary_date"] = time.strftime("%Y-%m-%d", time.localtime(now))
            if tg:
                tg.send_text(build_daily_summary(store, cfg, now))
        if now - state["last_purge"] > 86400:
            state["last_purge"] = now
            store.purge(now)

    def bot_loop():
        for upd in tg.get_updates(state["bot_offset"]):
            state["bot_offset"] = upd["update_id"] + 1
            route_update(handler, tg, upd, time.time())

    _forever(store, "taplog", follower.poll, 2)
    _forever(store, "watcher", watch, 10)
    _forever(store, "disk", disk, 60)
    if scraper:
        _forever(store, "cpe", cpe, cfg.cpe.interval_s)
    _forever(store, "alerts", alerts, 30)
    if tg:
        _forever(store, "outbox", tg.flush_outbox, 60)
        _forever(store, "bot", bot_loop, 1)
    threading.Thread(target=run_panel, args=(store, cfg), daemon=True).start()
    print(f"monitor no ar - painel em http://localhost:{cfg.panel.port}")
    while True:
        time.sleep(60)
