"""Budowanie wiadomości MQTT (discovery i stany) dla jednego urządzenia. Czysta logika."""
import json
from datetime import datetime, timezone

from engine import RUNNING, PRESETS

STATUS_TOPIC = "appliance_monitor/status"
STATE_OPTIONS = ["idle", "running", "finished"]


def _topics(a):
    base = f"appliance_monitor/{a.cfg['id']}"
    return {k: f"{base}/{k}" for k in ("state", "running", "energy", "duration", "last_finished")}


def discovery_messages(a):
    """Lista (topic, payload_json) konfiguracji discovery."""
    cid = a.cfg["id"]
    t = _topics(a)
    icon = PRESETS.get(a.cfg.get("preset"), PRESETS["other"])["icon"]
    device = {
        "identifiers": [f"appliance_monitor_{cid}"],
        "name": a.cfg["name"],
        "manufacturer": "Appliance Monitor",
        "model": "Detektor cyklu",
    }
    common = {
        "availability_topic": STATUS_TOPIC,
        "device": device,
        "has_entity_name": True,
        "origin": {"name": "Appliance Monitor"},
    }
    ents = [
        ("sensor", "state", {
            "name": "Stan", "unique_id": f"am_{cid}_state", "state_topic": t["state"],
            "device_class": "enum", "options": STATE_OPTIONS, "icon": icon,
        }),
        ("binary_sensor", "running", {
            "name": "Pracuje", "unique_id": f"am_{cid}_running", "state_topic": t["running"],
            "payload_on": "ON", "payload_off": "OFF", "device_class": "running",
        }),
        ("sensor", "energy", {
            "name": "Energia cyklu", "unique_id": f"am_{cid}_energy", "state_topic": t["energy"],
            "device_class": "energy", "unit_of_measurement": "kWh",
        }),
        ("sensor", "duration", {
            "name": "Czas cyklu", "unique_id": f"am_{cid}_duration", "state_topic": t["duration"],
            "device_class": "duration", "unit_of_measurement": "min",
        }),
        ("sensor", "last_finished", {
            "name": "Ostatnie zakończenie", "unique_id": f"am_{cid}_last_finished",
            "state_topic": t["last_finished"], "device_class": "timestamp",
        }),
    ]
    out = []
    for comp, obj, payload in ents:
        payload.update(common)
        out.append((f"homeassistant/{comp}/am_{cid}/{obj}/config", json.dumps(payload)))
    return out


def removal_topics(cid):
    comps = [("sensor", "state"), ("binary_sensor", "running"), ("sensor", "energy"),
             ("sensor", "duration"), ("sensor", "last_finished")]
    cfg = [f"homeassistant/{c}/am_{cid}/{o}/config" for c, o in comps]
    base = f"appliance_monitor/{cid}"
    return cfg + [f"{base}/{k}" for k in ("state", "running", "energy", "duration", "last_finished")]


def state_messages(a, now):
    """Lista (topic, payload) aktualnych stanów."""
    t = _topics(a)
    running = a.state == RUNNING
    if running and a.cycle_start:
        energy = a.energy_wh / 1000
        duration = int((now - a.cycle_start) / 60)
    elif a.last_cycle:
        energy = a.last_cycle["energy_wh"] / 1000
        duration = int(a.last_cycle["duration_s"] / 60)
    else:
        energy, duration = 0.0, 0
    msgs = [
        (t["state"], a.state),
        (t["running"], "ON" if running else "OFF"),
        (t["energy"], f"{energy:.3f}"),
        (t["duration"], str(duration)),
    ]
    if a.last_cycle:
        iso = datetime.fromtimestamp(a.last_cycle["end"], timezone.utc).isoformat()
        msgs.append((t["last_finished"], iso))
    return msgs
