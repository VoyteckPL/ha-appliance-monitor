"""Ile daje fotowoltaika: oszczędność na energii zużytej na miejscu + depozyt za oddaną. Czysta logika."""
import io
import re
import zipfile
import xml.etree.ElementTree as ET


def parse_production(raw):
    """Eksport z aplikacji falownika (xlsx, np. Solarman 'Statystyki roczne/miesięczne').
    Zwraca {'YYYY': kWh} albo {'YYYY-MM': kWh}. Bez zewnętrznych bibliotek (xlsx = zip z XML)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise ValueError("To nie jest plik xlsx.")
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    names = z.namelist()
    shared = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
            shared.append("".join(t.text or "" for t in si.iter("{%s}t" % ns["m"])))
    sheet = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    if not sheet:
        raise ValueError("Pusty plik xlsx.")
    rows = []
    for row in ET.fromstring(z.read(sheet[0])).iter("{%s}row" % ns["m"]):
        cells = {}
        for c in row.findall("m:c", ns):
            col = re.match(r"[A-Z]+", c.get("r", "")).group(0)
            t = c.get("t")
            if t == "s":
                v = c.find("m:v", ns)
                val = shared[int(v.text)] if v is not None and v.text else ""
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter("{%s}t" % ns["m"]))
            else:
                v = c.find("m:v", ns)
                val = v.text if v is not None and v.text else ""
            cells[col] = val.replace("\xa0", " ").strip()
        rows.append(cells)
    if not rows:
        raise ValueError("Plik jest pusty.")
    head = {k: v.lower() for k, v in rows[0].items()}
    prod_col = next((k for k, v in head.items() if (v.startswith("produkcj") or v.startswith("wytwarzani"))), None)
    time_col = next((k for k, v in head.items() if "czas" in v and "stref" not in v), None)
    if not prod_col or not time_col:
        raise ValueError("Nie znalazłem kolumn z czasem i produkcją (kWh). To eksport „Statystyki roczne/miesięczne” z aplikacji falownika?")
    out = {}
    for r in rows[1:]:
        t = re.sub(r"[/.]", "-", r.get(time_col, ""))
        m = re.match(r"(\d{4})(?:-(\d{1,2}))?", t)
        try:
            kwh = float(r.get(prod_col, "").replace(",", "."))
        except ValueError:
            continue
        if not m or kwh < 0:
            continue
        out[m.group(1) if not m.group(2) else "%s-%02d" % (m.group(1), int(m.group(2)))] = kwh
    if not out:
        raise ValueError("W pliku nie ma wierszy z produkcją.")
    return out


def annual(prod, months, cost=0.0, pv_start=None):
    """Zysk rok po roku z rocznej produkcji i faktur (bez założeń o zużyciu sprzed paneli).
    Zysk = (produkcja − oddane) × średnia cena zakupu + depozyt. Pompa basenowa to zwykłe zużycie własne."""
    inv = [m for m in months if m["export_kwh"] is not None]
    if not inv or not prod:
        return None
    last_inv = max(m["month"] for m in inv)
    starts = [m["month"] for m in inv if m["export_kwh"] > 5]
    pv_start = pv_start or (min(starts) if starts else None)
    by_y = {}
    for m in inv:
        by_y.setdefault(m["month"][:4], []).append(m)
    rows = []
    for y in sorted(prod):
        if len(y) != 4:
            continue
        have = {m["month"]: m for m in by_y.get(y, [])}
        first = max(y + "-01", pv_start or y + "-01")
        last = min(y + "-12", last_inv)
        need = [f"{y}-{i:02d}" for i in range(1, 13) if first <= f"{y}-{i:02d}" <= last]
        miss = [mo for mo in need if mo not in have]
        ms = [have[mo] for mo in need if mo in have]
        if not ms:
            continue
        E = sum(m["export_kwh"] for m in ms)
        P = prod[y]
        S = max(P - E, 0.0)
        w = [(m["import_price"], m["export_kwh"]) for m in ms if m["import_price"] > 0]
        sw = sum(x[1] for x in w)
        p = sum(a * b for a, b in w) / sw if sw > 0 else (sum(a for a, _ in w) / len(w) if w else 0.0)
        dep = sum(m["deposit_value"] for m in ms)
        rows.append({"year": y, "production": round(P, 1), "export": round(E, 1), "self": round(S, 1),
                     "price": round(p, 4), "avoided": round(S * p, 2), "deposit": round(dep, 2),
                     "benefit": round(S * p + dep, 2), "months": len(ms), "missing": miss,
                     "partial": y == last_inv[:4] and last_inv < y + "-12"})
    if not rows:
        return None
    cum = 0.0
    for r in rows:
        cum += r["benefit"]
        r["cumulative"] = round(cum, 2)
    full = [r for r in rows if not r["partial"] and not r["missing"] and r["months"] == 12]
    ref = full[-1]["benefit"] if full else (rows[-1]["benefit"] / max(rows[-1]["months"], 1) * 12)
    tot = {"benefit": round(cum, 2), "cost": cost, "per_year": round(ref, 2), "years": len(rows),
           "left_years": round((cost - cum) / ref, 1) if cost > cum and ref > 0 else 0.0}
    return {"years": rows, "total": tot}


def compute(prod, months, cost=0.0):
    """prod: {miesiąc: kWh wyprodukowane (falownik)}; months: rekordy miesięczne z faktur (bills.by_month).
    Samozużycie = produkcja − oddane do sieci; wartość = samozużycie × cena zakupu + depozyt z oddanej energii."""
    inv = {m["month"]: m for m in months}
    prices = sorted((m["month"], m["import_price"]) for m in months if m["import_price"] > 0 and m["billed_kwh"] > 0)
    out, missing = [], []
    for mo in sorted(prod):
        b = inv.get(mo)
        if not b or b["export_kwh"] is None:
            missing.append(mo)
            continue
        p = b["import_price"]
        est = False
        if not (p > 0 and b["billed_kwh"] > 0) and prices:
            before = [x for x in prices if x[0] <= mo]
            p = (before[-1] if before else prices[0])[1]  # miesiąc bez energii: cena z najbliższej faktury
            est = True
        P, E = prod[mo], b["export_kwh"]
        S = max(P - E, 0.0)
        avoided, dep = S * p, b["deposit_value"]
        out.append({"month": mo, "production": round(P, 1), "export": round(E, 1), "self": round(S, 1),
                    "price": round(p, 4), "avoided": round(avoided, 2), "deposit": round(dep, 2),
                    "benefit": round(avoided + dep, 2), "estimated": est})
    cum = 0.0
    for r in out:
        cum += r["benefit"]
        r["cumulative"] = round(cum, 2)
    tot = {k: round(sum(r[k] for r in out), 2) for k in ("production", "export", "self", "avoided", "deposit", "benefit")}
    last = out[-12:]
    tot["avg_month"] = round(sum(r["benefit"] for r in last) / len(last), 2) if last else 0.0
    tot["months"] = len(out)
    tot["cost"] = cost
    return {"months": out, "total": tot, "missing": missing}


def estimate(csv_months, months, pv_start=None, cost=0.0):
    """Szacunek zysku od początku instalacji bez falownika, tylko z faktur.
    Porównuje to, co zapłaciłeś (faktura − depozyt), z tym, ile kosztowałby prąd przy zużyciu z lat przed panelami
    (średnia z tych samych miesięcy kalendarzowych) po cenach z danego miesiąca + te same opłaty stałe."""
    VAT = 1.23
    inv = {m["month"]: m for m in months}
    if not pv_start:
        starts = [m["month"] for m in months if m["export_kwh"] > 5]
        pv_start = min(starts) if starts else None
    if not pv_start:
        return None
    kwh = {m["month"]: m["billed_kwh"] for m in months}
    for mo, r in (csv_months or {}).items():
        kwh[mo] = r["billed_kwh"]  # CSV ma kompletne zużycie sprzed paneli
    first = min(csv_months) if csv_months else None  # pierwszy miesiąc CSV jest niepełny (umowa zaczęła się w jego trakcie)
    pre = {mo: v for mo, v in kwh.items() if mo < pv_start and v > 0 and mo != first}
    if len(pre) < 12:
        return {"error": "Za mało danych sprzed paneli (potrzebne co najmniej 12 miesięcy z CSV).", "start": pv_start}
    by_cal = {}
    for mo, v in pre.items():
        by_cal.setdefault(mo[5:], []).append(v)
    overall = sum(pre.values()) / len(pre)
    prices = sorted((m["month"], m["import_price"]) for m in months if m["import_price"] > 0 and m["billed_kwh"] > 0)
    rows = []
    for mo in sorted(inv):
        if mo < pv_start:
            continue
        b = inv[mo]
        base = sum(by_cal[mo[5:]]) / len(by_cal[mo[5:]]) if mo[5:] in by_cal else overall
        p, est = b["import_price"], False
        if not (p > 0 and b["billed_kwh"] > 0) and prices:
            before = [x for x in prices if x[0] <= mo]
            p, est = (before[-1] if before else prices[0])[1], True
        fixed = b["fixed_net"] * VAT
        counter = base * p + fixed
        actual = b["gross"] - b["deposit_value"]
        rows.append({"month": mo, "baseline_kwh": round(base, 1), "price": round(p, 4), "counterfactual": round(counter, 2),
                     "actual": round(actual, 2), "saving": round(counter - actual, 2), "estimated": est})
    cum = 0.0
    for r in rows:
        cum += r["saving"]
        r["cumulative"] = round(cum, 2)
    last = rows[-12:]
    tot = {"saving": round(cum, 2), "months": len(rows), "start": pv_start, "cost": cost,
           "avg_month": round(sum(r["saving"] for r in last) / len(last), 2) if last else 0.0,
           "baseline_months": len(pre)}
    return {"months": rows, "total": tot}
