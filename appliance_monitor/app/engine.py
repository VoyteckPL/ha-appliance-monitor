"""Wykrywanie cyklu pracy urządzenia po poborze mocy. Czysta logika, bez I/O."""
from collections import deque

IDLE, RUNNING, FINISHED = "idle", "running", "finished"

DEFAULT_MSG_START = "{name}: cykl rozpoczęty"
DEFAULT_MSG_FINISH = "{name}: cykl zakończony ({duration}, {energy})"

# start_delay, stop_delay, min_duration w sekundach; reset_after w minutach
PRESETS = {
    "washer": {
        "label": "Pralka", "icon": "mdi:washing-machine",
        "start_threshold": 10, "start_delay": 30,
        "stop_threshold": 4, "stop_delay": 180,
        "reset_after": 120, "min_duration": 300,
    },
    "dryer": {
        "label": "Suszarka", "icon": "mdi:tumble-dryer",
        "start_threshold": 50, "start_delay": 60,
        "stop_threshold": 8, "stop_delay": 480,
        "reset_after": 120, "min_duration": 600,
    },
    "dishwasher": {
        "label": "Zmywarka", "icon": "mdi:dishwasher",
        "start_threshold": 10, "start_delay": 60,
        "stop_threshold": 4, "stop_delay": 900,
        "reset_after": 120, "min_duration": 900,
    },
    "other": {
        "label": "Inne", "icon": "mdi:power-plug",
        "start_threshold": 10, "start_delay": 30,
        "stop_threshold": 4, "stop_delay": 120,
        "reset_after": 120, "min_duration": 120,
    },
}

SAMPLE_EVERY_S = 10
SENSOR_STALE_S = 120


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
    """Podmienia {name}, {duration}, {energy} (bez str.format, żeby obce klamry nie psuły)."""
    out = str(template)
    out = out.replace("{name}", info.get("name", ""))
    out = out.replace("{duration}", fmt_duration(info.get("duration_s", 0)))
    out = out.replace("{energy}", fmt_energy(info.get("energy_wh", 0)))
    return out


class Appliance:
    def __init__(self, cfg, runtime=None):
        self.cfg = cfg
        r = runtime or {}
        self.state = r.get("state", IDLE)
        self.cycle_start = r.get("cycle_start")
        self.energy_wh = float(r.get("energy_wh", 0.0))
        self.finished_at = r.get("finished_at")
        self.last_cycle = r.get("last_cycle")
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
            events.append(("reset", {"name": c["name"]}))

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
                        events.append(("abort", info))
                    else:
                        self.state = FINISHED
                        self.finished_at = now
                        self.last_cycle = {
                            "start": info["started_at"], "end": ended,
                            "duration_s": info["duration_s"], "energy_wh": round(info["energy_wh"], 1),
                        }
                        self.cycle_start = None
                        events.append(("finish", info))
            else:
                self.below_since = None

        return events

    # --- zapis stanu ---
    def runtime_export(self):
        return {
            "state": self.state, "cycle_start": self.cycle_start,
            "energy_wh": self.energy_wh, "finished_at": self.finished_at,
            "last_cycle": self.last_cycle,
        }
