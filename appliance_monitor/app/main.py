"""Appliance Monitor: serwer panelu (ingress), odpytywanie HA, MQTT discovery, powiadomienia."""
import asyncio
import json
import logging
import os
import re
import time
import unicodedata
from pathlib import Path

import paho.mqtt.client as mqtt
from aiohttp import ClientSession, ClientTimeout, web

import discovery
from engine import (DEFAULT_MSG_FINISH, DEFAULT_MSG_START, FINISHED, PRESETS,
                    RUNNING, Appliance, render)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("am")

DATA = Path(os.environ.get("DATA_DIR", "/data"))
TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
HA_BASE = os.environ.get("HA_BASE", "http://supervisor/core/api")
SUP_BASE = os.environ.get("SUP_BASE", "http://supervisor")
PORT = int(os.environ.get("PORT", "8099"))
DEV = os.environ.get("DEV") == "1"
POLL_S = 5
STATIC = Path(__file__).parent / "static"
HISTORY_MAX = 50


# ---------- pliki ----------
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def slugify(name):
    s = name.replace("ł", "l").replace("Ł", "L")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return s or "urzadzenie"


# ---------- MQTT ----------
class MqttOut:
    def __init__(self, mon):
        self.mon = mon
        self.client = None
        self.connected = False
        self.loop = None

    async def start(self):
        self.loop = asyncio.get_running_loop()
        try:
            async with self.mon.session.get(f"{SUP_BASE}/services/mqtt", headers=self.mon.hdr()) as r:
                data = (await r.json())["data"]
            host, port = data["host"], int(data["port"])
        except Exception as e:
            log.warning("Brak brokera MQTT (%s). Encje w HA nie będą tworzone.", e)
            return
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="appliance_monitor")
        if data.get("username"):
            c.username_pw_set(data["username"], data.get("password"))
        if data.get("ssl"):
            c.tls_set()
        c.will_set(discovery.STATUS_TOPIC, "offline", qos=1, retain=True)
        c.on_connect = self._on_connect
        c.on_disconnect = self._on_disconnect
        c.connect_async(host, port, keepalive=60)
        c.loop_start()
        self.client = c
        log.info("MQTT: łączę z %s:%s", host, port)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if getattr(reason_code, "is_failure", False):
            log.warning("MQTT: odmowa połączenia: %s", reason_code)
            return
        self.connected = True
        client.publish(discovery.STATUS_TOPIC, "online", qos=1, retain=True)
        log.info("MQTT: połączono")
        self.loop.call_soon_threadsafe(self.mon.republish_all)

    def _on_disconnect(self, client, userdata, flags, reason_code=None, properties=None):
        self.connected = False
        log.warning("MQTT: rozłączono")

    def publish(self, topic, payload, retain=True):
        if self.client and self.connected:
            self.client.publish(topic, payload, qos=1, retain=retain)
            return True
        return False


# ---------- logika aplikacji ----------
class Monitor:
    def __init__(self):
        self.appliances = {}
        self.history = {}
        self.session = None
        self.mqtt = MqttOut(self)
        self.published = {}
        self.ha_ok = False

    # -- trwałość
    def load(self):
        cfgs = load_json(DATA / "appliances.json", [])
        rt = load_json(DATA / "state.json", {})
        self.history = load_json(DATA / "history.json", {})
        for c in cfgs:
            self.appliances[c["id"]] = Appliance(c, rt.get(c["id"]))

    def save_cfg(self):
        save_json(DATA / "appliances.json", [a.cfg for a in self.appliances.values()])

    def save_runtime(self):
        save_json(DATA / "state.json", {i: a.runtime_export() for i, a in self.appliances.items()})

    def save_history(self):
        save_json(DATA / "history.json", self.history)

    # -- HA REST
    def hdr(self):
        return {"Authorization": f"Bearer {TOKEN}"}

    async def ha_get(self, path):
        async with self.session.get(f"{HA_BASE}{path}", headers=self.hdr()) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} dla {path}")
            return await r.json()

    async def ha_post(self, path, data):
        async with self.session.post(f"{HA_BASE}{path}", headers=self.hdr(), json=data) as r:
            if r.status >= 400:
                raise RuntimeError(f"HTTP {r.status} dla {path}")

    async def fetch_power(self, entity):
        try:
            d = await self.ha_get(f"/states/{entity}")
            self.ha_ok = True
        except Exception:
            self.ha_ok = False
            return None
        try:
            v = float(d["state"])
        except (ValueError, TypeError, KeyError):
            return None
        unit = d.get("attributes", {}).get("unit_of_measurement", "W")
        if unit == "kW":
            v *= 1000
        elif unit == "mW":
            v /= 1000
        return v

    # -- powiadomienia
    async def notify(self, a, kind, info):
        c = a.cfg
        if kind == "start":
            if not c["notify_start"]:
                return
            msg = render(c["msg_start"], info)
        else:
            if not c["notify_finish"]:
                return
            msg = render(c["msg_finish"], info)
        for target in c["notify_targets"]:
            try:
                await self.ha_post(f"/services/notify/{target}", {"title": c["name"], "message": msg})
            except Exception as e:
                log.warning("Powiadomienie %s nie poszło: %s", target, e)

    # -- MQTT publikacja
    def _pub(self, topic, payload):
        if self.published.get(topic) != payload and self.mqtt.publish(topic, payload):
            self.published[topic] = payload

    def publish_discovery(self, a):
        for topic, payload in discovery.discovery_messages(a):
            self._pub(topic, payload)

    def publish_state(self, a, now):
        for topic, payload in discovery.state_messages(a, now):
            self._pub(topic, payload)

    def republish_all(self):
        self.published.clear()
        now = time.time()
        for a in self.appliances.values():
            self.publish_discovery(a)
            self.publish_state(a, now)

    def remove_from_mqtt(self, cid):
        for topic in discovery.removal_topics(cid):
            self.mqtt.publish(topic, "", retain=True)
            self.published.pop(topic, None)

    # -- pętla odczytu
    async def poll_loop(self):
        while True:
            t0 = time.time()
            try:
                await self.poll_once()
            except Exception:
                log.exception("Błąd w pętli odczytu")
            await asyncio.sleep(max(1.0, POLL_S - (time.time() - t0)))

    async def poll_once(self):
        apps = list(self.appliances.values())
        if not apps:
            return
        entities = sorted({a.cfg["power_entity"] for a in apps})
        values = dict(zip(entities, await asyncio.gather(*(self.fetch_power(e) for e in entities))))
        now = time.time()
        changed = False
        for a in apps:
            a.set_power(values.get(a.cfg["power_entity"]), now)
            events = a.tick(now)
            for kind, info in events:
                changed = True
                log.info("%s: %s", a.cfg["name"], kind)
                if kind == "finish":
                    h = self.history.setdefault(a.cfg["id"], [])
                    h.append(a.last_cycle)
                    del h[:-HISTORY_MAX]
                    self.save_history()
                if kind in ("start", "finish"):
                    asyncio.create_task(self.notify(a, kind, info))
            self.publish_state(a, now)
        if changed:
            self.save_runtime()

    # -- widok dla UI
    def view(self, a, now):
        c = a.cfg
        samples = list(a.samples)
        step = max(1, len(samples) // 240)
        samples = samples[::step]
        running = a.state == RUNNING and a.cycle_start
        return {
            "id": c["id"], "cfg": c, "state": a.state, "power": a.power,
            "sensor_ok": a.sensor_ok(now),
            "elapsed_s": int(now - a.cycle_start) if running else None,
            "energy_wh": round(a.energy_wh, 1) if running else None,
            "finished_at": a.finished_at, "last_cycle": a.last_cycle,
            "history": self.history.get(c["id"], [])[-5:][::-1],
            "samples": [[int(t), round(p, 1)] for t, p in samples],
        }


# ---------- walidacja ----------
def clean_cfg(body):
    def num(key, lo, hi, default):
        try:
            v = float(body.get(key, default))
        except (TypeError, ValueError):
            v = float(default)
        return min(max(v, lo), hi)

    preset = body.get("preset") if body.get("preset") in PRESETS else "other"
    p = PRESETS[preset]
    name = str(body.get("name", "")).strip()[:40]
    if not name:
        raise web.HTTPBadRequest(text="Podaj nazwę urządzenia.")
    entity = str(body.get("power_entity", "")).strip()
    if not re.fullmatch(r"sensor\.[a-z0-9_]+", entity):
        raise web.HTTPBadRequest(text="Wybierz czujnik mocy.")
    targets = [t for t in body.get("notify_targets", [])
               if isinstance(t, str) and re.fullmatch(r"[a-z0-9_]+", t)]
    cfg = {
        "name": name, "preset": preset, "power_entity": entity,
        "start_threshold": num("start_threshold", 0.1, 5000, p["start_threshold"]),
        "start_delay": int(num("start_delay", 0, 3600, p["start_delay"])),
        "stop_threshold": num("stop_threshold", 0, 5000, p["stop_threshold"]),
        "stop_delay": int(num("stop_delay", 5, 7200, p["stop_delay"])),
        "reset_after": int(num("reset_after", 1, 1440, p["reset_after"])),
        "min_duration": int(num("min_duration", 0, 7200, p["min_duration"])),
        "notify_targets": targets,
        "notify_start": bool(body.get("notify_start", False)),
        "notify_finish": bool(body.get("notify_finish", True)),
        "msg_start": str(body.get("msg_start") or DEFAULT_MSG_START)[:200],
        "msg_finish": str(body.get("msg_finish") or DEFAULT_MSG_FINISH)[:200],
    }
    if cfg["stop_threshold"] > cfg["start_threshold"]:
        raise web.HTTPBadRequest(text="Próg końca nie może być wyższy niż próg startu.")
    return cfg


# ---------- API ----------
@web.middleware
async def guard(request, handler):
    if not DEV and request.remote not in ("172.30.32.2", "127.0.0.1", "::1"):
        raise web.HTTPForbidden()
    resp = await handler(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp


async def index(request):
    return web.FileResponse(STATIC / "index.html")


async def api_list(request):
    mon = request.app["mon"]
    now = time.time()
    return web.json_response({
        "appliances": [mon.view(a, now) for a in mon.appliances.values()],
        "mqtt": mon.mqtt.connected, "ha_ok": mon.ha_ok,
    })


async def api_options(request):
    mon = request.app["mon"]
    try:
        states = await mon.ha_get("/states")
        services = await mon.ha_get("/services")
    except Exception as e:
        raise web.HTTPBadGateway(text=f"Nie mogę pobrać danych z HA: {e}")
    sensors = []
    for s in states:
        eid = s["entity_id"]
        if not eid.startswith("sensor."):
            continue
        at = s.get("attributes", {})
        unit = at.get("unit_of_measurement")
        if at.get("device_class") == "power" or unit in ("W", "kW"):
            sensors.append({"entity_id": eid, "name": at.get("friendly_name", eid),
                            "state": s["state"], "unit": unit or ""})
    sensors.sort(key=lambda x: x["name"].lower())
    notify = []
    for d in services:
        if d.get("domain") == "notify":
            for svc in d.get("services", {}):
                if svc == "send_message":
                    continue
                label = ("📱 " + svc[len("mobile_app_"):]) if svc.startswith("mobile_app_") else svc
                notify.append({"service": svc, "label": label})
    notify.sort(key=lambda x: (not x["service"].startswith("mobile_app_"), x["label"].lower()))
    return web.json_response({
        "sensors": sensors, "notify": notify, "presets": PRESETS,
        "default_msg_start": DEFAULT_MSG_START, "default_msg_finish": DEFAULT_MSG_FINISH,
    })


async def api_create(request):
    mon = request.app["mon"]
    cfg = clean_cfg(await request.json())
    base = slugify(cfg["name"])
    cid, n = base, 2
    while cid in mon.appliances:
        cid, n = f"{base}_{n}", n + 1
    cfg["id"] = cid
    a = Appliance(cfg)
    mon.appliances[cid] = a
    mon.save_cfg()
    mon.save_runtime()
    mon.publish_discovery(a)
    mon.publish_state(a, time.time())
    return web.json_response({"id": cid})


async def api_update(request):
    mon = request.app["mon"]
    cid = request.match_info["id"]
    a = mon.appliances.get(cid)
    if not a:
        raise web.HTTPNotFound()
    cfg = clean_cfg(await request.json())
    cfg["id"] = cid
    a.cfg = cfg
    mon.save_cfg()
    mon.publish_discovery(a)
    mon.publish_state(a, time.time())
    return web.json_response({"id": cid})


async def api_delete(request):
    mon = request.app["mon"]
    cid = request.match_info["id"]
    if cid not in mon.appliances:
        raise web.HTTPNotFound()
    del mon.appliances[cid]
    mon.history.pop(cid, None)
    mon.save_cfg()
    mon.save_runtime()
    mon.save_history()
    mon.remove_from_mqtt(cid)
    return web.Response(status=204)


async def api_test_notify(request):
    mon = request.app["mon"]
    body = await request.json()
    name = str(body.get("name") or "Urządzenie")[:40]
    targets = [t for t in body.get("targets", []) if isinstance(t, str) and re.fullmatch(r"[a-z0-9_]+", t)]
    if not targets:
        raise web.HTTPBadRequest(text="Zaznacz przynajmniej jedno urządzenie do powiadomień.")
    errors = []
    for t in targets:
        try:
            await mon.ha_post(f"/services/notify/{t}", {"title": name, "message": "Test powiadomienia"})
        except Exception as e:
            errors.append(f"{t}: {e}")
    if errors:
        raise web.HTTPBadGateway(text="; ".join(errors))
    return web.json_response({"ok": True})


async def make_app():
    mon = Monitor()
    mon.load()
    mon.session = ClientSession(timeout=ClientTimeout(total=10))
    app = web.Application(middlewares=[guard])
    app["mon"] = mon
    app.router.add_get("/", index)
    app.router.add_get("/api/appliances", api_list)
    app.router.add_post("/api/appliances", api_create)
    app.router.add_put("/api/appliances/{id}", api_update)
    app.router.add_delete("/api/appliances/{id}", api_delete)
    app.router.add_get("/api/options", api_options)
    app.router.add_post("/api/test_notify", api_test_notify)
    return app


async def main():
    app = await make_app()
    mon = app["mon"]
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    log.info("Panel działa na porcie %s, urządzeń: %s", PORT, len(mon.appliances))
    await mon.mqtt.start()
    asyncio.create_task(mon.poll_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
