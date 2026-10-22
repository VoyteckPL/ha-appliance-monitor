"""Budowanie wiadomości MQTT (discovery i stany) dla jednego urządzenia. Czysta logika."""
import json
from datetime import datetime, timezone

from engine import RUNNING, PRESETS

STATUS_TOPIC = "appliance_monitor/status"
STATE_OPTIONS = ["idle", "running", "finished"]
COMMAND_SUBSCRIBE = "appliance_monitor/+/unloaded/set"

# (komponent, klucz tematu) wszystkich encji urządzenia
ENTITIES = [
    ("sensor", "state"), ("binary_sensor", "running"), ("sensor", "energy"), ("sensor", "duration"),
    ("sensor", "last_finished"), ("sensor", "cost"), ("sensor", "program"), ("sensor", "today_cycles"),
    ("sensor", "today_energy"), ("sensor", "week_cycles"), ("sensor", "eta"), ("button", "unloaded"),
]


def base_topic(cid):
    return f"appliance_monitor/{cid}"


def command_topic(cid):
    return f"{base_topic(cid)}/unloaded/set"


def _t(cid, key):
    return f"{base_topic(cid)}/{key}"


def discovery_messages(a, currency="zł"):
    """Lista (topic, payload_json) konfiguracji discovery."""
    cid = a.cfg["id"]
    icon = PRESETS.get(a.cfg.get("preset"), PRESETS["other"])["icon"]
    common = {
        "availability_topic": STATUS_TOPIC,
        "device": {
            "identifiers": [f"appliance_monitor_{cid}"],
            "name": a.cfg["name"],
            "manufacturer": "Appliance Monitor",
            "model": "Detektor cyklu",
        },
        "has_entity_name": True,
        "origin": {"name": "Appliance Monitor"},
    }
    spec = {
        "state": {"name": "Stan", "device_class": "enum", "options": STATE_OPTIONS, "icon": icon},
        "running": {"name": "Pracuje", "payload_on": "ON", "payload_off": "OFF", "device_class": "power"},
        "energy": {"name": "Energia cyklu", "device_class": "energy", "unit_of_measurement": "kWh"},
        "duration": {"name": "Czas cyklu", "device_class": "duration", "unit_of_measurement": "min"},
        "last_finished": {"name": "Ostatnie zakończenie", "device_class": "timestamp"},
        "cost": {"name": "Koszt ostatniego cyklu", "unit_of_measurement": currency, "icon": "mdi:cash"},
        "program": {"name": "Program ostatniego cyklu", "icon": "mdi:format-list-bulleted"},
        "today_cycles": {"name": "Cykle dziś", "state_class": "measurement", "icon": "mdi:counter"},
        "today_energy": {"name": "Energia dziś", "device_class": "energy", "unit_of_measurement": "kWh",
                         "state_class": "total_increasing"},
        "week_cycles": {"name": "Cykle (7 dni)", "state_class": "measurement", "icon": "mdi:counter"},
        "eta": {"name": "Szacowany koniec", "device_class": "timestamp",
                "value_template": "{{ none if value == 'none' else value }}"},
        "unloaded": {"name": "Wyjęte", "icon": "mdi:check-circle-outline", "payload_press": "PRESS",
                     "command_topic": command_topic(cid)},
    }
    out = []
    for comp, key in ENTITIES:
        payload = {"unique_id": f"am_{cid}_{key}", **spec[key], **common}
        if comp != "button":
            payload["state_topic"] = _t(cid, key)
        out.append((f"homeassistant/{comp}/am_{cid}/{key}/config", json.dumps(payload)))
    return out


def removal_topics(cid):
    cfg = [f"homeassistant/{c}/am_{cid}/{k}/config" for c, k in ENTITIES]
    return cfg + [_t(cid, k) for c, k in ENTITIES if c != "button"]


def state_messages(a, now, extra=None):
    """Lista (topic, payload) aktualnych stanów. extra: policzone w main (koszt, program, dziś, 7 dni, eta)."""
    extra = extra or {}
    cid = a.cfg["id"]
    running = a.state == RUNNING
    if running and a.cycle_start is not None:
        energy = a.energy_wh / 1000
        duration = int((now - a.cycle_start) / 60)
    elif a.last_cycle:
        energy = a.last_cycle["energy_wh"] / 1000
        duration = int(a.last_cycle["duration_s"] / 60)
    else:
        energy, duration = 0.0, 0
    msgs = [
        (_t(cid, "state"), a.state),
        (_t(cid, "running"), "ON" if running else "OFF"),
        (_t(cid, "energy"), f"{energy:.3f}"),
        (_t(cid, "duration"), str(duration)),
    ]
    if a.last_cycle:
        iso = datetime.fromtimestamp(a.last_cycle["end"], timezone.utc).isoformat()
        msgs.append((_t(cid, "last_finished"), iso))
    for key in ("cost", "program", "today_cycles", "today_energy", "week_cycles"):
        if extra.get(key) is not None:
            msgs.append((_t(cid, key), str(extra[key])))
    msgs.append((_t(cid, "eta"), extra.get("eta") or "none"))
    return msgs
