"""Testy logiki wykrywania cyklu. Uruchomienie: python tests/test_engine.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "appliance_monitor" / "app"))

from engine import FINISHED, IDLE, PRESETS, RUNNING, Appliance, render  # noqa: E402
import discovery  # noqa: E402


def make(preset="washer", **over):
    p = PRESETS[preset]
    cfg = {"id": "pralka", "name": "Pralka", "preset": preset, "power_entity": "sensor.x",
           **{k: p[k] for k in ("start_threshold", "start_delay", "stop_threshold",
                                "stop_delay", "reset_after", "min_duration")}, **over}
    return Appliance(cfg)


def feed(a, t0, t1, power, step=5):
    """Podaje stałą moc w przedziale [t0, t1), zwraca wszystkie zdarzenia."""
    ev, t = [], t0
    while t < t1:
        a.set_power(power, t)
        ev += a.tick(t)
        t += step
    return ev


def kinds(ev):
    return [k for k, _ in ev]


def test_full_cycle_with_pause():
    a = make()  # start 10 W/30 s, stop 4 W/180 s, min 300 s
    t = 1000.0
    assert kinds(feed(a, t, t + 20, 120)) == [] and a.state == IDLE      # za krótko
    ev = feed(a, t + 20, t + 60, 120)
    assert kinds(ev) == ["start"] and a.state == RUNNING
    assert a.cycle_start == t                                             # start liczony od pierwszego przekroczenia
    # przerwa 2 min (krótsza niż stop_delay) nie kończy cyklu
    assert kinds(feed(a, t + 60, t + 1800, 150)) == []
    assert kinds(feed(a, t + 1800, t + 1920, 1)) == [] and a.state == RUNNING
    assert kinds(feed(a, t + 1920, t + 2400, 150)) == [] and a.state == RUNNING
    # prawdziwy koniec
    ev = feed(a, t + 2400, t + 2400 + 200, 1)
    assert kinds(ev) == ["finish"] and a.state == FINISHED
    info = ev[0][1]
    assert abs(info["duration_s"] - 2400) < 1                             # koniec liczony od początku niskiego poboru
    assert 0.05 < info["energy_wh"] / 1000 < 0.2                          # ok. 150 W przez ~38 min
    assert a.last_cycle["duration_s"] == info["duration_s"]


def test_short_blip_is_aborted():
    a = make(min_duration=300)
    t = 0.0
    feed(a, t, t + 60, 100)
    assert a.state == RUNNING
    ev = feed(a, t + 60, t + 400, 0)
    assert "abort" in kinds(ev) and "finish" not in kinds(ev) and a.state == IDLE


def test_reset_after_finished():
    a = make(reset_after=10)
    feed(a, 0, 1000, 100)
    feed(a, 1000, 1300, 0)
    assert a.state == FINISHED
    ev = feed(a, 1300, 1300 + 11 * 60, 0)
    assert "reset" in kinds(ev) and a.state == IDLE


def test_new_cycle_from_finished():
    a = make()
    feed(a, 0, 1000, 100)
    feed(a, 1000, 1300, 0)
    assert a.state == FINISHED
    ev = feed(a, 1400, 1500, 100)
    assert kinds(ev) == ["start"] and a.state == RUNNING


def test_unknown_power_keeps_state():
    a = make()
    feed(a, 0, 100, 100)
    assert a.state == RUNNING
    for t in range(100, 2000, 5):
        a.set_power(None, t)
        assert a.tick(t) == []
    assert a.state == RUNNING and not a.sensor_ok(2000)


def test_runtime_roundtrip():
    a = make()
    feed(a, 0, 100, 100)
    b = Appliance(a.cfg, a.runtime_export())
    assert b.state == RUNNING and b.cycle_start == a.cycle_start


def test_render_and_discovery():
    msg = render("{name}: {duration}, {energy}", {"name": "Pralka", "duration_s": 4500, "energy_wh": 850})
    assert msg == "Pralka: 1 h 15 min, 0.85 kWh"
    a = make()
    feed(a, 0, 100, 100)
    disc = discovery.discovery_messages(a)
    assert len(disc) == 5
    for topic, payload in disc:
        d = json.loads(payload)
        assert topic.startswith("homeassistant/") and d["unique_id"].startswith("am_pralka_")
        assert d["device"]["identifiers"] == ["appliance_monitor_pralka"]
    states = dict(discovery.state_messages(a, 100))
    assert states["appliance_monitor/pralka/state"] == "running"
    assert states["appliance_monitor/pralka/running"] == "ON"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("WSZYSTKO OK")
