"""Wykrywanie cyklu pracy urządzenia po poborze mocy. Czysta logika, bez I/O."""
import re
from collections import deque

IDLE, RUNNING, FINISHED = "idle", "running", "finished"

DEFAULT_MSG_START = "{name}: cykl rozpoczęty"
DEFAULT_MSG_FINISH = "{name}: cykl zakończony ({duration}, {energy})"
DEFAULT_MSG_REMIND = "{name}: gotowe od {ago}, wyjmij zawartość"
DEFAULT_MSG_HANDOFF = "{name}: gotowe od {ago}, przełóż pranie do: {target}"

# start_delay, stop_delay, min_duration w sekundach; reset_after i remind_every w minutach
PRESETS = {
    "washer": {
        "label": "Pralka", "icon": "mdi:washing-machine",
        "start_threshold": 10, "start_delay": 30,
        "stop_threshold": 4, "stop_delay": 180,
        "reset_after": 240, "min_duration": 300, "remind_every": 60,
    },
    "dryer": {
        "label": "Suszarka", "icon": "mdi:tumble-dryer",
        "start_threshold": 50, "start_delay": 60,
        "stop_threshold": 8, "stop_delay": 480,
        "reset_after": 240, "min_duration": 600, "remind_every": 60,
    },
    "dishwasher": {
        "label": "Zmywarka", "icon": "mdi:dishwasher",
        "start_threshold": 10, "start_delay": 60,
        "stop_threshold": 4, "stop_delay": 180,
        "reset_after": 120, "min_duration": 900, "remind_every": 0,
    },
    "other": {
        "label": "Inne", "icon": "mdi:power-plug",
        "start_threshold": 10, "start_delay": 30,
        "stop_threshold": 4, "stop_delay": 120,
        "reset_after": 120, "min_duration": 120, "remind_every": 0,
    },
}

# pola dodane w 0.4.0: uzupełniane w konfiguracji urządzeń zapisanych wcześniej
CFG_DEFAULTS = {
    "unload_button": True, "remind_every": 0, "remind_max": 3,
    "msg_remind": DEFAULT_MSG_REMIND, "quiet": True, "notify_anomaly": True,
    "handoff_to": "", "handoff_wait": 20, "handoff_repeat": 40, "handoff_max": 2, "msg_handoff": DEFAULT_MSG_HANDOFF,
}


def plural_cykl(n):
    if n == 1:
        return "cykl"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "cykle"
    return "cykli"

SAMPLE_EVERY_S = 10
SENSOR_STALE_S = 120
MAX_CYCLE_SAMPLES = 5000  # ok. 14 h co 10 s
CURVE_POINTS = 120


def make_curve(samples, max_points=CURVE_POINTS):
    """Zagęszcza próbki (t, W) do krzywej: {t0, step, w:[...]} (średnia w przedziałach)."""
    if len(samples) < 2:
        return None
    t0, t1 = samples[0][0], samples[-1][0]
    span = max(t1 - t0, 1.0)
    n = min(max_points, len(samples))
    step = span / n
    buckets = [[] for _ in range(n)]
    for t, p in samples:
        buckets[min(int((t - t0) / step), n - 1)].append(p)
    w, last = [], samples[0][1]
    for b in buckets:
        if b:
            last = sum(b) / len(b)
        w.append(int(round(last)))
    return {"t0": int(t0), "step": round(step, 2), "w": w}


def entry_from_info(info):
    """Wpis historii z informacji o zakończonym cyklu."""
    return {
        "start": int(info["started_at"]), "end": int(info["ended_at"]),
        "duration_s": int(info["duration_s"]), "energy_wh": round(info["energy_wh"], 1),
        "peak_w": info.get("peak_w", 0), "curve": info.get("curve"),
    }


def fmt_duration(sec):
    sec = int(max(sec, 0))
    h, m = divmod(sec // 60, 60)
    if h:
        return f"{h} h {m} min"
    if sec >= 60:
        return f"{m} min"
    return f"{sec} s"


def fmt_energy(wh):
    return f"{wh / 1000:.2f} kWh"


def render(template, info):
    """Podmienia {name} {duration} {energy} {cost} {program} {ago} (bez str.format, żeby obce klamry nie psuły)."""
    out = str(template)
    out = out.replace("{name}", info.get("name", ""))
    out = out.replace("{duration}", fmt_duration(info.get("duration_s", 0)))
    out = out.replace("{energy}", fmt_energy(info.get("energy_wh", 0)))
    out = out.replace("{cost}", info.get("cost") or "")
    out = out.replace("{program}", info.get("program") or "")
    out = out.replace("{ago}", fmt_duration(info.get("ago_s", 0)))
    out = out.replace("{target}", info.get("target") or "")
    return re.sub(r" {2,}", " ", out).strip()


class Appliance:
    def __init__(self, cfg, runtime=None):
        self.cfg = cfg
        r = runtime or {}
        self.state = r.get("state", IDLE)
        self.cycle_start = r.get("cycle_start")
        self.energy_wh = float(r.get("energy_wh", 0.0))
        self.finished_at = r.get("finished_at")
        self.last_cycle = r.get("last_cycle")
        self.reminders_sent = int(r.get("reminders_sent", 0))
        if self.state == RUNNING and self.cycle_start is None:
            self.state = IDLE
        if self.state == FINISHED and self.finished_at is None:
            self.state = IDLE
        self.power = None
        self.last_seen = None
        self.above_since = None
        self.below_since = None
        self.last_tick = None
        self.last_sample = 0.0
        self.samples = deque(maxlen=1080)  # ok. 3 h co 10 s
        self.cycle_samples = []  # próbki (t, W) bieżącego cyklu, do wykresu w historii
        if self.state == RUNNING:
            self.cycle_samples = [(float(t), float(p)) for t, p in r.get("cycle_samples", [])]

    # --- wejście ---
    def set_power(self, value_w, now):
        self.power = value_w
        if value_w is not None:
            self.last_seen = now

    def sensor_ok(self, now):
        return self.last_seen is not None and now - self.last_seen < SENSOR_STALE_S

    # --- logika ---
    def _info(self, ended_at, started_at, energy_wh):
        return {
            "name": self.cfg["name"],
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": max(ended_at - started_at, 0),
            "energy_wh": energy_wh,
        }

    def tick(self, now):
        """Wywoływane przy każdym odczycie i cyklicznie. Zwraca listę (zdarzenie, info)."""
        events = []
        c = self.cfg

        # powrót z "zakończone" do "czeka" nie zależy od odczytu
        if self.state == FINISHED and now - self.finished_at >= c["reset_after"] * 60:
            self.state = IDLE
            self.reminders_sent = 0
            events.append(("reset", {"name": c["name"]}))

        # przypomnienia o wyjęciu zawartości, dopóki nikt nie potwierdzi („Wyjęte")
        if self.state == FINISHED:
            every = c.get("remind_every", 0)
            if (every > 0 and not c.get("handoff_to") and self.reminders_sent < c.get("remind_max", 3)
                    and now >= self.finished_at + (self.reminders_sent + 1) * every * 60):
                self.reminders_sent += 1
                events.append(("remind", {"name": c["name"], "finished_at": self.finished_at,
                                          "ago_s": now - self.finished_at}))

        p = self.power
        if p is None:
            return events

        if self.state == RUNNING and self.last_tick is not None:
            dt = now - self.last_tick
            if 0 < dt < 120:
                self.energy_wh += p * dt / 3600.0
        self.last_tick = now

        if now - self.last_sample >= SAMPLE_EVERY_S:
            self.samples.append((now, p))
            if self.state == RUNNING and len(self.cycle_samples) < MAX_CYCLE_SAMPLES:
                self.cycle_samples.append((now, p))
            self.last_sample = now

        if self.state in (IDLE, FINISHED):
            if p > c["start_threshold"]:
                if self.above_since is None:
                    self.above_since = now
                if now - self.above_since >= c["start_delay"]:
                    self.cycle_start = self.above_since
                    self.energy_wh = p * (now - self.above_since) / 3600.0
                    self.state = RUNNING
                    self.above_since = None
                    self.below_since = None
                    self.finished_at = None
                    self.reminders_sent = 0
                    # krzywa zaczyna się chwilę przed startem (próbki z okresu wykrywania)
                    self.cycle_samples = [(t, v) for t, v in self.samples if t >= self.cycle_start - 30]
                    events.append(("start", {"name": c["name"], "started_at": self.cycle_start}))
            else:
                self.above_since = None

        elif self.state == RUNNING:
            if p < c["stop_threshold"]:
                if self.below_since is None:
                    self.below_since = now
                if now - self.below_since >= c["stop_delay"]:
                    ended = self.below_since
                    info = self._info(ended, self.cycle_start, self.energy_wh)
                    self.below_since = None
                    if info["duration_s"] < c["min_duration"]:
                        self.state = IDLE  # zbyt krótkie, uznajemy za zakłócenie
                        self.cycle_start = None
                        self.cycle_samples = []
                        events.append(("abort", info))
                    else:
                        info["curve"] = make_curve(self.cycle_samples)
                        info["peak_w"] = int(round(max((v for _, v in self.cycle_samples), default=0)))
                        self.state = FINISHED
                        self.finished_at = now
                        self.reminders_sent = 0
                        self.last_cycle = {
                            "start": info["started_at"], "end": ended,
                            "duration_s": info["duration_s"], "energy_wh": round(info["energy_wh"], 1),
                            "peak_w": info["peak_w"],
                        }
                        self.cycle_start = None
                        self.cycle_samples = []
                        events.append(("finish", info))
            else:
                self.below_since = None

        return events

    def unload(self):
        """Użytkownik potwierdził wyjęcie zawartości: „gotowe" wraca do „wyłączone"."""
        if self.state != FINISHED:
            return []
        self.state = IDLE
        self.finished_at = None
        self.reminders_sent = 0
        return [("unloaded", {"name": self.cfg["name"]})]

    # --- zapis stanu ---
    def runtime_export(self):
        return {
            "reminders_sent": self.reminders_sent,
            "state": self.state, "cycle_start": self.cycle_start,
            "energy_wh": self.energy_wh, "finished_at": self.finished_at,
            "last_cycle": self.last_cycle,
            # co 3. próbka (30 s), żeby po restarcie nie zgubić początku wykresu
            "cycle_samples": [[round(t, 1), round(v, 1)] for t, v in self.cycle_samples[::3]]
            if self.state == RUNNING else [],
        }
