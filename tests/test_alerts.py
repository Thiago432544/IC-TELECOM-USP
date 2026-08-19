from monitor.alerts import AlertEngine, CamState, Snapshot
from monitor.config import AlertCfg

CFG = AlertCfg(disk_min_gb=60.0, down_after_s=180, degraded_ratio=0.5,
               degraded_after_s=600, flap_count=5, flap_window_s=900,
               realert_s=1800, link_rsrp_min=-110.0, link_rsrq_min=-15.0,
               link_after_s=300)

def cam(age=10.0, rate=60.0, base=60.0, disc=0):
    return CamState(frames_per_min=rate, last_frame_age_s=age,
                    baseline=base, disconnects_15min=disc)

def snap(cams, disk=200.0, rsrp=None, rsrq=None):
    return Snapshot(cameras=cams, disk_free_gb=disk, rsrp=rsrp, rsrq=rsrq)

def kinds(alerts):
    return [(a.kind, a.origin) for a in alerts]

def test_down_fires_once_then_recovers_then_refires():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"102": cam(age=200)}))) == [("down", "102")]
    assert e.evaluate(1030, snap({"102": cam(age=230)})) == []          # suprimido
    assert kinds(e.evaluate(1100, snap({"102": cam(age=5)}))) == [("recovered", "102")]
    assert kinds(e.evaluate(1200, snap({"102": cam(age=300)}))) == [("down", "102")]

def test_group_down_replaces_individuals():
    e = AlertEngine(CFG)
    s = snap({"102": cam(age=200), "106": cam(age=210), "105": cam(age=5)})
    out = e.evaluate(1000, s)
    assert kinds(out) == [("group_down", "*")]
    assert "102" in out[0].text and "106" in out[0].text

def test_degraded_needs_persistence_and_baseline():
    e = AlertEngine(CFG)
    low = {"102": cam(age=5, rate=20.0, base=60.0)}
    assert e.evaluate(1000, snap(low)) == []                 # comecou a contar
    assert e.evaluate(1000 + 599, snap(low)) == []           # ainda nao
    assert kinds(e.evaluate(1000 + 601, snap(low))) == [("degraded", "102")]
    e2 = AlertEngine(CFG)
    nobase = {"102": cam(age=5, rate=20.0, base=None)}
    assert e2.evaluate(1000, snap(nobase)) == []
    assert e2.evaluate(2000, snap(nobase)) == []

def test_flapping():
    e = AlertEngine(CFG)
    assert kinds(e.evaluate(1000, snap({"106": cam(disc=5)}))) == [("flapping", "106")]
    assert e.evaluate(1030, snap({"106": cam(disc=6)})) == []  # suprimido 30min

def test_link_needs_persistence():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, rsrp=-115.0, rsrq=-6.0)
    assert e.evaluate(1000, s) == []
    assert kinds(e.evaluate(1000 + 301, s)) == [("link", "cpe")]

def test_disk_realerts_daily_not_halfhourly():
    e = AlertEngine(CFG)
    s = snap({"102": cam()}, disk=50.0)
    assert kinds(e.evaluate(1000, s)) == [("disk", "pc")]
    assert e.evaluate(1000 + 7200, s) == []                   # 2h: nada
    assert kinds(e.evaluate(1000 + 86401, s)) == [("disk", "pc")]
