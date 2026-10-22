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
    disc = discovery.discovery_messages(a, "PLN")
    assert len(disc) == 12 and len({t for t, _ in disc}) == 12
    for topic, payload in disc:
        d = json.loads(payload)
        assert topic.startswith("homeassistant/") and d["unique_id"].startswith("am_pralka_")
        assert d["device"]["identifiers"] == ["appliance_monitor_pralka"]
        assert ("command_topic" in d) == topic.startswith("homeassistant/button/")
    cost = json.loads(dict(disc)["homeassistant/sensor/am_pralka/cost/config"])
    assert cost["unit_of_measurement"] == "PLN"
    states = dict(discovery.state_messages(a, 100, {"cost": "0.97", "today_cycles": 0, "program": None}))
    assert states["appliance_monitor/pralka/state"] == "running"
    assert states["appliance_monitor/pralka/running"] == "ON"
    assert states["appliance_monitor/pralka/cost"] == "0.97" and states["appliance_monitor/pralka/today_cycles"] == "0"
    assert "appliance_monitor/pralka/program" not in states and states["appliance_monitor/pralka/eta"] == "none"
    rm = discovery.removal_topics("pralka")
    assert len(rm) == 12 + 11 and all(t in rm for t, _ in disc)


def test_curve_and_peak_recorded():
    a = make()
    ev = feed(a, 1000, 1000 + 3600, 200) + feed(a, 1000 + 3600, 1000 + 3600 + 400, 0)
    fin = [i for k, i in ev if k == "finish"]
    assert len(fin) == 1
    c = fin[0]["curve"]
    assert c and len(c["w"]) <= 120 and c["step"] > 0
    assert c["t0"] <= 1000 and fin[0]["peak_w"] == 200
    assert max(c["w"]) == 200 and c["w"][-1] == 0          # ogon po końcu też jest na wykresie
    assert a.cycle_samples == [] and a.last_cycle["peak_w"] == 200


def test_cycle_samples_survive_restart():
    a = make()
    feed(a, 0, 600, 100)
    assert a.state == RUNNING and len(a.cycle_samples) > 30
    b = Appliance(a.cfg, json.loads(json.dumps(a.runtime_export())))
    assert b.state == RUNNING and 15 < len(b.cycle_samples) < len(a.cycle_samples)
    # po restarcie dalej zbiera i kończy cykl z krzywą
    ev = feed(b, 600, 3000, 100) + feed(b, 3000, 3400, 0)
    fin = [i for k, i in ev if k == "finish"]
    assert fin and fin[0]["curve"]["t0"] <= 15              # krzywa zaczyna się od początku cyklu, nie od restartu


def test_replay_matches_live_and_skips_idle():
    from replay import replay
    cfg = make().cfg
    # odczyty tylko przy zmianach (jak w historii HA): 0 -> 120 W na 40 min -> 0, dwa razy w odstępie 9 h
    r, t = [], 0.0
    for _ in range(2):
        r += [(t, 0.0), (t + 30, 120.0), (t + 30 + 2400, 0.0)]
        t += 9 * 3600
    r.append((t, 0.0))
    cycles = replay(cfg, r)
    assert len(cycles) == 2
    assert all(abs(c["duration_s"] - 2400) < 15 for c in cycles)
    assert all(c["curve"] and c["peak_w"] == 120 for c in cycles)
    assert cycles[0]["start"] < cycles[1]["start"]
    # za krótki impuls nie daje cyklu
    assert replay(cfg, [(0, 0.0), (100, 120.0), (160, 0.0), (5000, 0.0)]) == []


def test_replay_unavailable_does_not_end_cycle():
    from replay import replay
    cfg = make().cfg
    r = [(0, 0.0), (60, 150.0), (1000, None), (1900, 150.0), (3000, 0.0), (4000, 0.0)]
    cycles = replay(cfg, r)
    assert len(cycles) == 1 and cycles[0]["duration_s"] > 2500


def test_reminders_and_unload():
    a = make(remind_every=1, remind_max=2, reset_after=240)
    ev = feed(a, 0, 1000, 100) + feed(a, 1000, 2000, 0)
    assert a.state == FINISHED
    rem = [i for k, i in ev if k == "remind"]
    assert len(rem) == 2 and 55 < rem[0]["ago_s"] < 70 and 115 < rem[1]["ago_s"] < 130   # po 1 i 2 min, potem stop
    assert kinds(a.unload()) == ["unloaded"] and a.state == IDLE and a.reminders_sent == 0
    assert a.unload() == []                                                              # drugi raz nic nie robi
    # bez przypomnień (domyślnie) nic nie przychodzi
    b = make()
    ev = feed(b, 0, 1000, 100) + feed(b, 1000, 4000, 0)
    assert "remind" not in kinds(ev)


def test_render_placeholders_and_plural():
    from engine import plural_cykl
    info = {"name": "Pralka", "duration_s": 3600, "energy_wh": 900, "cost": "0.90 zł", "program": "Eko", "ago_s": 1800}
    assert render("{name} {program}: {cost}, gotowe od {ago}", info) == "Pralka Eko: 0.90 zł, gotowe od 30 min"
    assert render("{name}  {program}  koniec", {"name": "Pralka"}) == "Pralka koniec"   # pusty program nie zostawia dziur
    assert [plural_cykl(n) for n in (1, 2, 5, 12, 22, 25)] == ["cykl", "cykle", "cykli", "cykli", "cykle", "cykli"]


def test_programs_and_anomaly():
    import programs as pg
    hist = [{"start": i, "duration_s": 4800 + (i % 3) * 60, "energy_wh": 900 + (i % 4) * 25} for i in range(10)]
    cls = pg.clusters(hist)
    assert len(cls) == 1 and cls[0]["count"] == 10 and 4750 < cls[0]["duration_s"] < 4950
    normal = {"duration_s": 4850, "energy_wh": 920}
    long_ = {"duration_s": 7800, "energy_wh": 1100}           # inny, dłuższy program
    assert pg.assign(normal, cls) == 0 and pg.assign(long_, cls) is None
    assert pg.anomaly(normal, hist) is None
    text = pg.anomaly(long_, hist)
    assert text and "nietypowy" in text and "2 h 10 min" in text
    assert pg.anomaly(long_, hist[:5]) is None                  # za mało historii
    assert pg.anomaly({"duration_s": 5300, "energy_wh": 980}, hist) is None   # lekko inny: bez alarmu
    # nazwy: zapisana wygrywa, reszta numerowana
    two = hist + [{"start": 100 + i, "duration_s": 7800 + i * 30, "energy_wh": 1150 + i * 10} for i in range(3)]
    cls2 = pg.clusters(two)
    assert len(cls2) == 2
    named = pg.name_programs(cls2, [{"name": "Intensywny", "duration_s": 7900, "energy_wh": 1160}])
    assert [n["name"] for n in named] == ["Program 1", "Intensywny"] and [n["custom"] for n in named] == [False, True]
    assert pg.program_of(long_, named) == "Intensywny" and pg.program_of({"duration_s": 100, "energy_wh": 5}, named) is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("WSZYSTKO OK")
