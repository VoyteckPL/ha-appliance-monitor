"""Appliance Monitor: serwer panelu (ingress), odpytywanie HA, MQTT discovery, powiadomienia."""
import asyncio
import json
import logging
import os
import re
import statistics
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import paho.mqtt.client as mqtt
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

import discovery
import bills as billslib
import pricing
import loan as loanlib
import pvgain
import programs
from engine import (CFG_DEFAULTS, DEFAULT_MSG_FINISH, DEFAULT_MSG_REMIND, DEFAULT_MSG_START,
                    PRESETS, RUNNING, Appliance, entry_from_info, plural_cykl, render)
from replay import replay

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
HISTORY_MAX = 200
RUNTIME_SAVE_EVERY_S = 60
WS_URL = os.environ.get("WS_URL", "ws://supervisor/core/websocket")
ACTION_PREFIX = "AM_UNLOADED_"
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
DEFAULT_SETTINGS = {
    "price_per_kwh": 1.0, "currency": "zł",
    "price_mode": "flat", "import_price": 1.24, "export_price": 0.30,
    "grid_import_entity": "", "grid_export_entity": "",
    "pv_entity": "", "pv_cost": 30000, "pv_start": "",
    "quiet_enabled": True, "quiet_from": "22:00", "quiet_to": "07:00",
    "weekly_enabled": False, "weekly_day": 6, "weekly_time": "18:00", "weekly_targets": [],
}


def default_tz():
    try:
        return ZoneInfo(os.environ.get("TZ") or "UTC")
    except Exception:
        return timezone.utc


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
        c.on_message = self._on_message
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
        client.subscribe(discovery.COMMAND_SUBSCRIBE, qos=1)
        log.info("MQTT: połączono")
        self.loop.call_soon_threadsafe(self.mon.republish_all)

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")  # appliance_monitor/<id>/unloaded/set (przycisk „Wyjęte" w HA)
        if len(parts) == 4 and parts[0] == "appliance_monitor" and parts[2:] == ["unloaded", "set"]:
            self.loop.call_soon_threadsafe(self.mon.unload, parts[1], "przycisk w HA")

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
        self.settings = dict(DEFAULT_SETTINGS)
        self.last_rt_save = 0.0
        self.tz = default_tz()
        self.tz_ok = False
        self.last_tz_try = 0.0
        self.pending = []          # powiadomienia z nocy czekające na zbiorczą wysyłkę
        self.programs = {}         # nazwy programów nadane przez użytkownika
        self.prog_ver = 0
        self._prog_cache = {}

    # -- trwałość
    def load(self):
        cfgs = load_json(DATA / "appliances.json", [])
        rt = load_json(DATA / "state.json", {})
        self.history = load_json(DATA / "history.json", {})
        self.settings.update(load_json(DATA / "settings.json", {}))
        self.programs = load_json(DATA / "programs.json", {})
        self.pending = load_json(DATA / "pending.json", [])
        self.bills = self.migrate_bills(load_json(DATA / "bills.json", {}))
        self.bills_csv = load_json(DATA / "bills_csv.json", {})
        self.pv_prod = load_json(DATA / "pv_production.json", {})
        self.loan = load_json(DATA / "loan.json", None)
        for c in cfgs:
            for k, v in CFG_DEFAULTS.items():  # urządzenia zapisane w starszych wersjach
                c.setdefault(k, v)
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

    async def ha_get(self, path, timeout=None):
        kw = {"timeout": ClientTimeout(total=timeout)} if timeout else {}
        async with self.session.get(f"{HA_BASE}{path}", headers=self.hdr(), **kw) as r:
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status} dla {path}")
            return await r.json()

    async def load_readings(self, entity, days):
        """Historia czujnika z rejestratora HA jako [(t, W|None)], przedłużona do teraz."""
        now = datetime.now(timezone.utc)
        start = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self.ha_get(f"/states/{entity}")
        unit = cur.get("attributes", {}).get("unit_of_measurement", "W")
        factor = 1000.0 if unit == "kW" else 0.001 if unit == "mW" else 1.0
        data = await self.ha_get(
            f"/history/period/{start}?filter_entity_id={entity}&minimal_response&no_attributes",
            timeout=90)
        out = []
        for row in (data[0] if data else []):
            try:
                t = datetime.fromisoformat(row.get("last_changed") or row["last_updated"]).timestamp()
            except (KeyError, ValueError, TypeError):
                continue
            try:
                v = float(row["state"]) * factor
            except (ValueError, TypeError, KeyError):
                v = None
            out.append((t, v))
        out.sort(key=lambda x: x[0])
        if out and out[-1][0] < now.timestamp():
            out.append((now.timestamp(), out[-1][1]))
        return out

    async def load_series(self, entity, start_ts, end_ts):
        """Czujnik skumulowany z rejestratora jako [(t, kWh)], z wartością tuż przed start_ts."""
        start = datetime.fromtimestamp(start_ts - 2 * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = datetime.fromtimestamp(end_ts + 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = await self.ha_get(f"/states/{entity}")
        k = 0.001 if cur.get("attributes", {}).get("unit_of_measurement") == "Wh" else 1.0
        data = await self.ha_get(
            f"/history/period/{start}?filter_entity_id={entity}&end_time={end}&minimal_response&no_attributes", timeout=60)
        out = []
        for row in (data[0] if data else []):
            try:
                out.append((datetime.fromisoformat(row.get("last_changed") or row["last_updated"]).timestamp(),
                            float(row["state"]) * k))
            except (KeyError, ValueError, TypeError):
                continue
        out.sort()
        return out

    async def pv_cost(self, entry):
        """Koszt cyklu wg salda godzinowego licznika; None, gdy tryb prosty lub brak danych."""
        S = self.settings
        if S.get("price_mode") != "pv" or not S.get("grid_import_entity") or not S.get("grid_export_entity"):
            return None
        try:
            imp = await self.load_series(S["grid_import_entity"], entry["start"], entry["end"])
            exp = await self.load_series(S["grid_export_entity"], entry["start"], entry["end"])
        except Exception as e:
            log.warning("Koszt PV: brak danych licznika: %s", e)
            return None
        r = pricing.cycle_cost(entry, imp, exp, S["import_price"], S["export_price"], time.time())
        if r is None:
            return None
        return {"cost": round(r[0], 3), "grid_kwh": round(r[1], 3), "pv_kwh": round(r[2], 3)}

    async def ws_call(self, payload):
        """Jednorazowe polecenie przez WebSocket HA (np. długoterminowe statystyki)."""
        async with ClientSession() as s, s.ws_connect(WS_URL, heartbeat=30) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": TOKEN})
            if (await ws.receive_json()).get("type") != "auth_ok":
                raise RuntimeError("autoryzacja WebSocket nie powiodła się")
            await ws.send_json({"id": 1, **payload})
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), 60)
                if msg.get("id") == 1:
                    if not msg.get("success"):
                        raise RuntimeError(msg.get("error", {}).get("message", "błąd HA"))
                    return msg["result"]

    async def pv_production(self, entity):
        """Miesięczna produkcja z falownika (kWh) z długoterminowych statystyk HA: {YYYY-MM: kWh}."""
        st = await self.ha_get(f"/states/{entity}")
        unit = st.get("attributes", {}).get("unit_of_measurement", "kWh")
        k = {"Wh": 0.001, "MWh": 1000.0}.get(unit, 1.0)
        res = await self.ws_call({"type": "recorder/statistics_during_period", "start_time": "2015-01-01T00:00:00Z",
                                  "statistic_ids": [entity], "period": "month", "types": ["change"]})
        out = {}
        for row in res.get(entity, []):
            t, ch = row.get("start"), row.get("change")
            if t is None or ch is None:
                continue
            dt = (datetime.fromtimestamp(t / 1000, timezone.utc) if isinstance(t, (int, float))
                  else datetime.fromisoformat(str(t).replace("Z", "+00:00"))).astimezone(self.tz)
            out[f"{dt.year}-{dt.month:02d}"] = out.get(f"{dt.year}-{dt.month:02d}", 0.0) + ch * k
        return out

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

    # -- czas lokalny, cisza nocna
    def local(self, ts):
        return datetime.fromtimestamp(ts, self.tz)

    async def load_tz(self):
        try:
            self.tz = ZoneInfo((await self.ha_get("/config"))["time_zone"])
            self.tz_ok = True
        except Exception as e:
            log.warning("Nie mogę ustalić strefy czasowej z HA (%s), używam %s", e, self.tz)

    def in_quiet(self, ts):
        s = self.settings
        try:
            if not s["quiet_enabled"]:
                return False
            mins = lambda v: int(v[:2]) * 60 + int(v[3:5])  # noqa: E731
            a, b = mins(s["quiet_from"]), mins(s["quiet_to"])
            loc = self.local(ts)
            cur = loc.hour * 60 + loc.minute
        except (KeyError, ValueError, TypeError):
            return False
        if a == b:
            return False
        return a <= cur < b if a < b else (cur >= a or cur < b)

    def money_of(self, entries):
        """Suma kosztu wpisów: policzony z salda PV, a gdy go brak, wg ceny stałej."""
        p, cur = self.settings["price_per_kwh"], self.settings["currency"]
        return f"{sum(e['cost'] if e.get('cost') is not None else e['energy_wh'] / 1000 * p for e in entries):.2f} {cur}"

    def money(self, wh, entry=None):
        if entry and entry.get("cost") is not None:  # koszt policzony z salda godzinowego (PV)
            return f"{entry['cost']:.2f} {self.settings['currency']}"
        return f"{wh / 1000 * self.settings['price_per_kwh']:.2f} {self.settings['currency']}"

    # -- powiadomienia
    async def send(self, targets, title, message, unload_for=None):
        for t in targets:
            payload = {"title": title, "message": message}
            if unload_for and t.startswith("mobile_app_"):  # przyciski akcji znają tylko telefony z aplikacją HA
                payload["data"] = {"tag": f"am_{unload_for}",
                                   "actions": [{"action": ACTION_PREFIX + unload_for, "title": "Wyjęte"}]}
            try:
                await self.ha_post(f"/services/notify/{t}", payload)
            except Exception as e:
                log.warning("Powiadomienie %s nie poszło: %s", t, e)

    async def notify(self, a, kind, info):
        c = a.cfg
        info = dict(info)
        if info.get("energy_wh") is not None:
            info["cost"] = self.money(info["energy_wh"], info.get("entry"))
        lines = []
        if kind == "start":
            if c["notify_start"]:
                lines.append(render(c["msg_start"], info))
        elif kind == "finish":
            if c["notify_finish"]:
                lines.append(render(c["msg_finish"], info))
            if info.get("anomaly") and c.get("notify_anomaly", True):
                lines.append("⚠ " + info["anomaly"][:1].upper() + info["anomaly"][1:])
        elif kind == "remind":
            lines.append(render(c.get("msg_remind") or DEFAULT_MSG_REMIND, info))
        if not lines or not c["notify_targets"]:
            return
        message = "\n".join(lines)
        now = time.time()
        if c.get("quiet", True) and self.in_quiet(now):
            if kind == "finish":  # zakończenia z nocy wysyłamy rano zbiorczo, starty i przypomnienia pomijamy
                self.pending.append({"name": c["name"], "ts": now, "text": message.replace("\n", " "),
                                     "targets": list(c["notify_targets"])})
                save_json(DATA / "pending.json", self.pending)
            return
        unload = c["id"] if kind in ("finish", "remind") and c.get("unload_button", True) else None
        await self.send(c["notify_targets"], c["name"], message, unload)

    async def flush_pending(self):
        items, self.pending = self.pending, []
        save_json(DATA / "pending.json", self.pending)
        by_target = {}
        for it in items:
            for t in it["targets"]:
                by_target.setdefault(t, []).append(it)
        for t, its in by_target.items():
            lines = [f"{self.local(i['ts']).strftime('%H:%M')} {i['text']}" for i in its]
            await self.send([t], "Powiadomienia z nocy", "\n".join(lines))

    def weekly_text(self, now):
        lines, total_n, total_wh, all_es = [], 0, 0.0, []
        for a in self.appliances.values():
            es = [h for h in self.history.get(a.cfg["id"], []) if h["start"] >= now - 7 * 86400]
            if es:
                wh = sum(h["energy_wh"] for h in es)
                total_n += len(es)
                total_wh += wh
                all_es += es
                lines.append(f"{a.cfg['name']}: {len(es)} {plural_cykl(len(es))}, {wh / 1000:.2f} kWh, {self.money_of(es)}")
        if not lines:
            return "W ostatnich 7 dniach nie było żadnych cykli."
        lines.append(f"Razem: {total_n} {plural_cykl(total_n)}, {total_wh / 1000:.2f} kWh, {self.money_of(all_es)}")
        return "\n".join(lines)

    async def maybe_weekly(self, now):
        s = self.settings
        if not s.get("weekly_enabled") or not s.get("weekly_targets") or not self.appliances:
            return
        loc = self.local(now)
        if loc.weekday() != int(s["weekly_day"]):
            return
        if (loc.hour, loc.minute) < (int(s["weekly_time"][:2]), int(s["weekly_time"][3:5])):
            return
        key = loc.date().isoformat()
        if s.get("weekly_last") == key:
            return
        s["weekly_last"] = key
        save_json(DATA / "settings.json", s)
        await self.send(s["weekly_targets"], "Podsumowanie tygodnia", self.weekly_text(now))

    async def housekeeping(self, now):
        if not self.tz_ok and now - self.last_tz_try >= 300:
            self.last_tz_try = now
            await self.load_tz()
        if self.pending and not self.in_quiet(now):
            await self.flush_pending()
        await self.maybe_weekly(now)

    # -- „Wyjęte": przycisk w panelu, w HA albo w powiadomieniu
    def unload(self, cid, source=""):
        a = self.appliances.get(cid)
        if not a or not a.unload():
            return False
        log.info("%s: wyjęte (%s)", a.cfg["name"], source)
        self.save_runtime()
        self.publish_state(a, time.time())
        return True

    async def ws_loop(self):
        """Nasłuch akcji z powiadomień telefonu (przycisk „Wyjęte") przez WebSocket HA."""
        while True:
            try:
                async with ClientSession() as s, s.ws_connect(WS_URL, heartbeat=30) as ws:
                    await ws.receive_json()  # auth_required
                    await ws.send_json({"type": "auth", "access_token": TOKEN})
                    msg = await ws.receive_json()
                    if msg.get("type") != "auth_ok":
                        raise RuntimeError(f"autoryzacja WebSocket: {msg.get('type')}")
                    await ws.send_json({"id": 1, "type": "subscribe_events",
                                        "event_type": "mobile_app_notification_action"})
                    log.info("WebSocket HA: nasłuch akcji z powiadomień")
                    async for m in ws:
                        if m.type != WSMsgType.TEXT:
                            continue
                        data = m.json()
                        if data.get("type") == "event":
                            action = str(data["event"].get("data", {}).get("action", ""))
                            if action.startswith(ACTION_PREFIX):
                                self.unload(action[len(ACTION_PREFIX):], "powiadomienie")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("WebSocket HA: %s (ponawiam za 15 s)", e)
            await asyncio.sleep(15)

    # -- programy i statystyki
    def named_programs(self, cid):
        hist = self.history.get(cid, [])
        key = (len(hist), hist[-1]["start"] if hist else 0, self.prog_ver)
        hit = self._prog_cache.get(cid)
        if hit and hit[0] == key:
            return hit[1]
        named = programs.name_programs(programs.clusters(hist), self.programs.get(cid, []))
        self._prog_cache[cid] = (key, named)
        return named

    def typical(self, cid):
        recent = [h["duration_s"] for h in self.history.get(cid, [])[-10:]]
        return int(statistics.median(recent)) if len(recent) >= 3 else None

    def extra(self, a, now):
        """Wartości dodatkowych encji HA: koszt, program, dziś, 7 dni, szacowany koniec."""
        cid = a.cfg["id"]
        hist = self.history.get(cid, [])
        day0 = self.local(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = [h for h in hist if h["start"] >= day0]
        ex = {"today_cycles": len(today), "week_cycles": len([h for h in hist if h["start"] >= now - 7 * 86400]),
              "today_energy": f"{sum(h['energy_wh'] for h in today) / 1000:.3f}"}
        if hist:
            c = hist[-1].get("cost")
            ex["cost"] = f"{c if c is not None else hist[-1]['energy_wh'] / 1000 * self.settings['price_per_kwh']:.2f}"
            ex["program"] = programs.program_of(hist[-1], self.named_programs(cid)) or "nieznany"
        typ = self.typical(cid)
        if a.state == RUNNING and a.cycle_start is not None and typ:
            ex["eta"] = datetime.fromtimestamp(a.cycle_start + typ, timezone.utc).isoformat()
        return ex

    # -- MQTT publikacja
    def _pub(self, topic, payload):
        if self.published.get(topic) != payload and self.mqtt.publish(topic, payload):
            self.published[topic] = payload

    def publish_discovery(self, a):
        for topic, payload in discovery.discovery_messages(a, self.settings["currency"]):
            self._pub(topic, payload)

    def publish_state(self, a, now):
        for topic, payload in discovery.state_messages(a, now, self.extra(a, now)):
            self._pub(topic, payload)

    @staticmethod
    def migrate_bills(raw):
        """Starszy zapis trzymał faktury po miesiącu; teraz po numerze faktury (duplikat nadpisuje samego siebie)."""
        out = {}
        for k, b in raw.items():
            if re.fullmatch(r"\d{4}-\d{2}", k):
                y, mo = int(k[:4]), int(k[5:])
                b.setdefault("from", f"{y}-{mo:02d}-01")
                nxt = (datetime(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)).date().isoformat()
                b.setdefault("to", nxt)
            out[b["number"]] = b
        return out

    def publish_bills(self):
        st = billslib.stats(billslib.by_month(self.bills.values()))
        if not st:
            return
        for topic, payload in discovery.bills_messages(st, self.settings["currency"]):
            if self.published.get(topic) != payload and self.mqtt.publish(topic, payload):
                self.published[topic] = payload

    def republish_all(self):
        self.published.clear()
        self.publish_bills()
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
                await self.housekeeping(time.time())
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
                    cid = a.cfg["id"]
                    h = self.history.setdefault(cid, [])
                    entry = entry_from_info(info)
                    pc = await self.pv_cost(entry)
                    if pc:
                        entry.update(pc)
                    anomaly = programs.anomaly(entry, h)  # porównanie z poprzednimi cyklami, bez bieżącego
                    if anomaly:
                        entry["anomaly"] = anomaly
                    h.append(entry)
                    del h[:-HISTORY_MAX]
                    self.save_history()
                    info = {**info, "entry": entry, "anomaly": anomaly,
                            "program": programs.program_of(entry, self.named_programs(cid))}
                if kind in ("start", "finish", "remind"):
                    asyncio.create_task(self.notify(a, kind, info))
            self.publish_state(a, now)
        # w trakcie cyklu zapisujemy stan co minutę, żeby restart nie zgubił energii i wykresu
        if changed or (any(a.state == RUNNING for a in apps) and now - self.last_rt_save >= RUNTIME_SAVE_EVERY_S):
            self.save_runtime()
            self.last_rt_save = now

    # -- widok dla UI
    def view(self, a, now):
        c = a.cfg
        samples = list(a.samples)
        step = max(1, len(samples) // 240)
        samples = samples[::step]
        running = a.state == RUNNING and a.cycle_start is not None
        hist = self.history.get(c["id"], [])
        last = hist[-1] if hist else None
        return {
            "id": c["id"], "cfg": c, "state": a.state, "power": a.power,
            "sensor_ok": a.sensor_ok(now),
            "elapsed_s": int(now - a.cycle_start) if running else None,
            "energy_wh": round(a.energy_wh, 1) if running else None,
            "finished_at": a.finished_at, "last_cycle": a.last_cycle,
            # typowy czas cyklu (mediana ostatnich) do szacowania końca
            "typical_s": self.typical(c["id"]),
            "last_program": programs.program_of(last, self.named_programs(c["id"])) if last else None,
            "history_count": len(hist),
            "history": [{k: v for k, v in h.items() if k != "curve"} for h in hist[-5:][::-1]],
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
        "unload_button": bool(body.get("unload_button", True)),
        "remind_every": int(num("remind_every", 0, 1440, p.get("remind_every", 0))),
        "remind_max": int(num("remind_max", 1, 20, 3)),
        "msg_remind": str(body.get("msg_remind") or DEFAULT_MSG_REMIND)[:200],
        "quiet": bool(body.get("quiet", True)),
        "notify_anomaly": bool(body.get("notify_anomaly", True)),
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
        "mqtt": mon.mqtt.connected, "ha_ok": mon.ha_ok, "settings": mon.settings,
    })


def get_appliance(request):
    a = request.app["mon"].appliances.get(request.match_info["id"])
    if not a:
        raise web.HTTPNotFound()
    return a


async def api_history(request):
    mon = request.app["mon"]
    a = get_appliance(request)
    cid = a.cfg["id"]
    named = mon.named_programs(cid)
    entries = [{**e, "program": programs.program_of(e, named)} for e in mon.history.get(cid, [])[::-1]]
    return web.json_response({"entries": entries, "programs": named})


async def api_programs_put(request):
    """Zapisuje nazwy programów nadane przez użytkownika (pusta nazwa = wróć do 'Program N')."""
    mon = request.app["mon"]
    a = get_appliance(request)
    body = await request.json()
    saved = []
    for p in body.get("programs", []):
        try:
            d, e = int(p["duration_s"]), float(p["energy_wh"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(p.get("name") or "").strip()[:30]
        if name:
            saved.append({"name": name, "duration_s": d, "energy_wh": e})
    cid = a.cfg["id"]
    mon.programs[cid] = saved
    mon.prog_ver += 1
    save_json(DATA / "programs.json", mon.programs)
    mon.publish_state(a, time.time())
    return web.json_response({"programs": mon.named_programs(cid)})


async def api_unload(request):
    mon = request.app["mon"]
    a = get_appliance(request)
    if not mon.unload(a.cfg["id"], "panel"):
        raise web.HTTPConflict(text="Urządzenie nie jest w stanie „Gotowe”.")
    return web.Response(status=204)


async def api_history_delete(request):
    mon = request.app["mon"]
    a = get_appliance(request)
    try:
        start = int(request.match_info["start"])
    except ValueError:
        raise web.HTTPBadRequest()
    h = mon.history.get(a.cfg["id"], [])
    kept = [e for e in h if e["start"] != start]
    if len(kept) == len(h):
        raise web.HTTPNotFound()
    mon.history[a.cfg["id"]] = kept
    mon.save_history()
    return web.Response(status=204)


def days_from(body, default=14):
    try:
        return min(max(int(body.get("days", default)), 1), 30)
    except (TypeError, ValueError):
        return default


async def api_backfill(request):
    """Uzupełnia historię cykli z rejestratora HA, przy bieżących ustawieniach urządzenia."""
    mon = request.app["mon"]
    a = get_appliance(request)
    days = days_from(await request.json() if request.can_read_body else {})
    try:
        readings = await mon.load_readings(a.cfg["power_entity"], days)
    except Exception as e:
        raise web.HTTPBadGateway(text=f"Nie mogę pobrać historii z HA: {e}")
    found = await asyncio.to_thread(replay, a.cfg, readings)
    h = mon.history.setdefault(a.cfg["id"], [])
    added = replaced = 0
    for e in found:
        same = next((x for x in h if abs(x["start"] - e["start"]) < 300), None)
        if same is None:
            pc = await mon.pv_cost(e)
            if pc:
                e.update(pc)
            h.append(e)
            added += 1
        elif not same.get("curve"):
            h[h.index(same)] = e  # wpis sprzed wersji z wykresami dostaje pełną krzywą
            replaced += 1
    h.sort(key=lambda x: x["start"])
    del h[:-HISTORY_MAX]
    mon.save_history()
    return web.json_response({"days": days, "found": len(found), "added": added, "updated": replaced})


async def api_replay(request):
    """Sprawdza (bez zapisu), ile cykli wykryją podane ustawienia na historii z HA."""
    mon = request.app["mon"]
    body = await request.json()
    cfg = clean_cfg(body)
    days = days_from(body)
    try:
        readings = await mon.load_readings(cfg["power_entity"], days)
    except Exception as e:
        raise web.HTTPBadGateway(text=f"Nie mogę pobrać historii z HA: {e}")
    found = await asyncio.to_thread(replay, cfg, readings)
    return web.json_response({
        "days": days, "readings": len(readings),
        "cycles": [{"start": e["start"], "duration_s": e["duration_s"], "energy_wh": e["energy_wh"]} for e in found],
    })


async def api_bills_get(request):
    mon = request.app["mon"]
    items = billslib.by_month(mon.bills.values())
    hist = sorted(mon.bills_csv.values(), key=lambda b: b["month"])
    return web.json_response({"bills": list(reversed(items)), "stats": billslib.stats(items), "history": hist})


async def api_pv(request):
    mon = request.app["mon"]
    ent = mon.settings.get("pv_entity")
    cost = float(mon.settings.get("pv_cost") or 0)
    months = billslib.by_month(mon.bills.values())
    out = {"loan": loanlib.summary(mon.loan, datetime.now(ZoneInfo(mon.settings.get("timezone") or "Europe/Warsaw")).strftime("%Y-%m-%d")) if mon.loan else None,
           "configured": bool(ent), "annual": pvgain.annual(mon.pv_prod, months, cost, mon.settings.get("pv_start") or None),
           "has_production": bool(mon.pv_prod), "estimate": pvgain.estimate(mon.bills_csv, months, mon.settings.get("pv_start") or None, cost)}
    prod = {k: v for k, v in mon.pv_prod.items() if len(k) == 7}  # miesięczny eksport z aplikacji falownika
    if ent:
        try:
            ha = await mon.pv_production(ent)
            if not ha and not prod:
                raise RuntimeError("HA nie ma długoterminowych statystyk dla tej encji (musi być licznik energii w kWh)")
            prod = {**prod, **(ha or {})}
        except Exception as e:
            out["exact_error"] = f"Nie mogę pobrać statystyk produkcji z HA: {e}"
    if prod:
        out["exact"] = pvgain.compute(prod, months, cost)
        out["first_data"] = min(prod)
    return web.json_response(out)


async def api_loan_post(request):
    """Harmonogram spłat kredytu (PDF z banku): do policzenia realnego kosztu i zwrotu instalacji."""
    mon = request.app["mon"]
    data = await request.read()
    if not data.startswith(b"%PDF"):
        raise web.HTTPBadRequest(text="To nie jest plik PDF.")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pdftotext", "-layout", "-", "-", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(data), 30)
        mon.loan = loanlib.parse_schedule(out.decode("utf-8", "replace"))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    except (OSError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(text="Nie udało się odczytać PDF-a (brak pdftotext?).")
    save_json(DATA / "loan.json", mon.loan)
    return web.json_response({"installments": len(mon.loan["rows"]), "amount": mon.loan["amount"]})


async def api_loan_get(request):
    mon = request.app["mon"]
    if not mon.loan:
        return web.json_response({"loan": None})
    today = datetime.now(ZoneInfo(mon.settings.get("timezone") or "Europe/Warsaw")).strftime("%Y-%m-%d")
    return web.json_response({"loan": mon.loan, "summary": loanlib.summary(mon.loan, today), "today": today})


async def api_loan_delete(request):
    mon = request.app["mon"]
    mon.loan = None
    try:
        (DATA / "loan.json").unlink()
    except OSError:
        pass
    return web.json_response({"ok": True})


async def api_pv_production(request):
    """Eksport produkcji z aplikacji falownika (xlsx): sumy roczne lub miesięczne."""
    mon = request.app["mon"]
    try:
        data = pvgain.parse_production(await request.read())
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    mon.pv_prod.update(data)
    save_json(DATA / "pv_production.json", mon.pv_prod)
    return web.json_response({"saved": len(data), "keys": sorted(data)})


async def api_bills_csv(request):
    """Zestawienie rozliczeń z Energa 24 (CSV): wieloletnia historia kwot i zużycia."""
    mon = request.app["mon"]
    raw = await request.read()
    try:
        months = billslib.parse_csv(raw.decode("utf-8-sig", "replace"))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    if not months:
        raise web.HTTPBadRequest(text="W pliku nie ma żadnych faktur rozliczeniowych.")
    mon.bills_csv = months
    save_json(DATA / "bills_csv.json", months)
    return web.json_response({"months": len(months), "from": min(months), "to": max(months)})


async def api_bills_post(request):
    """Przyjmuje plik PDF faktury Energa (surowe ciało żądania), odczytuje ją i zapisuje."""
    mon = request.app["mon"]
    data = await request.read()
    if not data.startswith(b"%PDF"):
        raise web.HTTPBadRequest(text="To nie jest plik PDF.")
    try:
        proc = await asyncio.create_subprocess_exec(
            "pdftotext", "-layout", "-", "-", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(data), 30)
        bill = billslib.parse_invoice(out.decode("utf-8", "replace"))
    except ValueError as e:
        raise web.HTTPBadRequest(text=str(e))
    except (OSError, asyncio.TimeoutError):
        raise web.HTTPBadGateway(text="Nie udało się odczytać PDF-a (brak pdftotext?).")
    mon.bills[bill["number"]] = bill
    save_json(DATA / "bills.json", mon.bills)
    prices = False
    newest = max(b["to"] for b in mon.bills.values())
    if bill["to"] == newest and bill["export_price"] > 0 and mon.settings.get("price_mode") == "pv":
        mon.settings["import_price"], mon.settings["export_price"] = bill["import_price"], bill["export_price"]
        save_json(DATA / "settings.json", mon.settings)
        prices = True
    mon.publish_bills()
    return web.json_response({"bill": bill, "prices_updated": prices})


async def api_bills_delete(request):
    mon = request.app["mon"]
    month = request.match_info["month"]
    gone = [n for n, b in mon.bills.items() if month in {r["month"] for r in billslib.by_month([b])}]
    if not gone:
        raise web.HTTPNotFound()
    for n in gone:
        del mon.bills[n]
    save_json(DATA / "bills.json", mon.bills)
    return web.Response(status=204)


async def api_settings_get(request):
    return web.json_response(request.app["mon"].settings)


async def api_settings_put(request):
    mon = request.app["mon"]
    body = await request.json()
    try:
        price = min(max(float(body.get("price_per_kwh", 1.0)), 0.0), 100.0)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="Cena musi być liczbą.")
    cur = str(body.get("currency") or "zł").strip()[:8] or "zł"

    def hhmm(key, default):
        v = str(body.get(key) or default)
        if not TIME_RE.fullmatch(v):
            raise web.HTTPBadRequest(text="Godzina musi mieć format GG:MM.")
        return v

    try:
        wday = min(max(int(body.get("weekly_day", 6)), 0), 6)
    except (TypeError, ValueError):
        wday = 6
    targets = [t for t in body.get("weekly_targets", []) if isinstance(t, str) and re.fullmatch(r"[a-z0-9_]+", t)]
    def num(key, default):
        try:
            return round(min(max(float(body.get(key, default)), 0.0), 100.0), 4)
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Ceny muszą być liczbami.")

    def ent(key):
        v = str(body.get(key) or "")
        if v and not re.fullmatch(r"sensor\.[a-z0-9_]+", v):
            raise web.HTTPBadRequest(text="Niepoprawna encja licznika.")
        return v

    pv_ent = str(body.get("pv_entity", mon.settings.get("pv_entity", "")) or "")
    if pv_ent and not re.fullmatch(r"sensor\.[a-z0-9_]+", pv_ent):
        raise web.HTTPBadRequest(text="Niepoprawna encja falownika.")
    try:
        pv_cost = min(max(float(body.get("pv_cost", mon.settings.get("pv_cost", 0)) or 0), 0.0), 10_000_000.0)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text="Koszt instalacji musi być liczbą.")
    mode = "pv" if body.get("price_mode") == "pv" else "flat"
    if mode == "pv" and not (ent("grid_import_entity") and ent("grid_export_entity")):
        raise web.HTTPBadRequest(text="Wybierz oba liczniki: pobór i oddawanie energii.")
    new = {
        "price_per_kwh": round(price, 4), "currency": cur,
        "price_mode": mode, "import_price": num("import_price", 1.24), "export_price": num("export_price", 0.3),
        "grid_import_entity": ent("grid_import_entity"), "grid_export_entity": ent("grid_export_entity"),
        "pv_entity": pv_ent, "pv_cost": round(pv_cost, 2),
        "pv_start": (lambda v: v if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v) else "")(str(body.get("pv_start", mon.settings.get("pv_start", "")) or "")),
        "quiet_enabled": bool(body.get("quiet_enabled", True)),
        "quiet_from": hhmm("quiet_from", "22:00"), "quiet_to": hhmm("quiet_to", "07:00"),
        "weekly_enabled": bool(body.get("weekly_enabled", False)), "weekly_day": wday,
        "weekly_time": hhmm("weekly_time", "18:00"), "weekly_targets": targets,
    }
    if mon.settings.get("weekly_last"):
        new["weekly_last"] = mon.settings["weekly_last"]
    mon.settings = new
    save_json(DATA / "settings.json", mon.settings)
    mon.republish_all()  # zmiana waluty zmienia jednostkę czujnika kosztu
    return web.json_response(mon.settings)


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
    energy = [{"entity_id": s["entity_id"], "name": s.get("attributes", {}).get("friendly_name", s["entity_id"]),
               "state": s["state"]} for s in states
              if s["entity_id"].startswith("sensor.")
              and s.get("attributes", {}).get("device_class") == "energy"]
    energy.sort(key=lambda x: x["name"].lower())
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
        "sensors": sensors, "energy": energy, "notify": notify, "presets": PRESETS,
        "default_msg_start": DEFAULT_MSG_START, "default_msg_finish": DEFAULT_MSG_FINISH,
        "default_msg_remind": DEFAULT_MSG_REMIND,
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
    if mon.programs.pop(cid, None) is not None:
        save_json(DATA / "programs.json", mon.programs)
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
    app = web.Application(middlewares=[guard], client_max_size=10 * 1024 * 1024)
    app["mon"] = mon
    app.router.add_get("/", index)
    app.router.add_get("/api/appliances", api_list)
    app.router.add_post("/api/appliances", api_create)
    app.router.add_put("/api/appliances/{id}", api_update)
    app.router.add_delete("/api/appliances/{id}", api_delete)
    app.router.add_get("/api/appliances/{id}/history", api_history)
    app.router.add_delete("/api/appliances/{id}/history/{start}", api_history_delete)
    app.router.add_post("/api/appliances/{id}/backfill", api_backfill)
    app.router.add_put("/api/appliances/{id}/programs", api_programs_put)
    app.router.add_post("/api/appliances/{id}/unload", api_unload)
    app.router.add_post("/api/replay", api_replay)
    app.router.add_get("/api/bills", api_bills_get)
    app.router.add_get("/api/pv", api_pv)
    app.router.add_post("/api/bills", api_bills_post)
    app.router.add_post("/api/bills/csv", api_bills_csv)
    app.router.add_post("/api/pv/production", api_pv_production)
    app.router.add_get("/api/loan", api_loan_get)
    app.router.add_post("/api/loan", api_loan_post)
    app.router.add_delete("/api/loan", api_loan_delete)
    app.router.add_delete("/api/bills/{month}", api_bills_delete)
    app.router.add_get("/api/settings", api_settings_get)
    app.router.add_put("/api/settings", api_settings_put)
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
    await mon.load_tz()
    asyncio.create_task(mon.poll_loop())
    asyncio.create_task(mon.ws_loop())
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
