"""Odczyt faktur Energa (tekst z pdftotext -layout) i statystyki z rachunków. Czysta logika."""
import re

VAT = 1.23


def num(s):
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def _norm(text):
    return [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


FEE_RE = re.compile(r"^(.+?) (\d\d\.\d\d\.\d{4}) (\d\d\.\d\d\.\d{4}) ([\d ]*[\d,.]+) (kWh|MC|MWh) ([\d,]+) ([\d ]*,\d\d) (\d+)$", re.M)


def _date(d, m, y):
    from datetime import date
    return date(int(y), int(m), int(d))


def parse_invoice(text):
    """Zwraca słownik z danymi faktury Energa (każdy układ: z fotowoltaiką i bez) albo rzuca ValueError."""
    from datetime import timedelta
    lines = _norm(text)
    joined = "\n".join(lines)
    m = re.search(r"Faktura (korygująca )?VAT (\S+)", joined)
    if not m:
        if re.search(r"[Nn]ota odsetkowa", joined):
            raise ValueError("To nota odsetkowa (odsetki za spóźnioną wpłatę). Nie jest potrzebna.")
        raise ValueError("To nie wygląda na fakturę Energa.")
    if m.group(1):
        raise ValueError("To faktura korygująca (np. premia prądowa). Nie jest potrzebna: wartości bierzemy z faktur rozliczeniowych i CSV.")
    number = m.group(2)
    p = re.search(r"Rozliczenie za okres (\d\d)\.(\d\d)\.(\d{4}) - (\d\d)\.(\d\d)\.(\d{4})", joined)
    if not p:
        raise ValueError("Nie znalazłem okresu rozliczeniowego.")
    d0, d1 = _date(p.group(1), p.group(2), p.group(3)), _date(p.group(4), p.group(5), p.group(6))
    end_eff = d1 - timedelta(days=1) if d1.day == 1 else d1
    month = f"{end_eff.year}-{end_eff.month:02d}"

    energy_v = energy_q = excise_p = dist_v = fixed = 0.0
    for r in FEE_RE.finditer(joined):
        name, qty, unit, price, value = r.group(1), num(r.group(4)), r.group(5), num(r.group(6)), num(r.group(7))
        if name.startswith("Energia czynna"):
            if "akcyza" in name:
                excise_p = price / 1000.0
            else:
                energy_v += value
                energy_q += qty
        elif name.startswith("Opłata"):
            if unit == "MC":
                fixed += value
            else:
                dist_v += value
    if energy_q <= 0:
        z = re.search(r"Zużycie energii elektrycznej: (\d+) kWh", joined)
        if z:
            energy_q = float(z.group(1))
        elif fixed > 0 or dist_v > 0:
            energy_q = 0.0  # faktura bez energii (same opłaty)
        else:
            raise ValueError("Nie znalazłem pozycji „Energia czynna całodobowa”.")
    billed = energy_q

    reads = [num(x) for x in re.findall(r"^G1\d\S* \d+ \S+ .* (\d+(?:,\d+)?) [A-Z] \d+$", joined, re.M)]
    imp = reads[0] if reads else billed
    exp = reads[1] if len(reads) > 1 else 0.0

    d = re.search(r"^Suma godzinowych sald ujemnych \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} (\d+) kWh ([\d, .]+)$", joined, re.M)
    surplus = dep_value = dep_price = 0.0
    if d:
        surplus = float(d.group(1))
        vals = d.group(2).split()
        dep_value = num(vals[-1])
        dep_price = num(vals[0]) if len(vals) > 1 else 0.0

    t = re.search(r"Rozliczenie energii elektrycznej i świadczenia usługi dystrybucji ([\d ]+,\d\d) ([\d ]+,\d\d) ([\d ]+,\d\d)", joined)
    if not t:
        raise ValueError("Nie znalazłem kwoty faktury.")
    net, gross = num(t.group(1)), num(t.group(3))
    used = re.search(r"Pobrano z DEPOZYTU ([\d ]+,\d\d)", joined)
    deposit_used = num(used.group(1)) if used else 0.0
    interest = re.search(r"odsetki za nieterminową wpłatę ([\d ]+,\d\d)", joined)
    interest = num(interest.group(1)) if interest else 0.0
    e_price = energy_v / energy_q if energy_q else 0.0
    var_price = dist_v / billed if billed else 0.0
    return {
        "number": number, "from": d0.isoformat(), "to": d1.isoformat(), "month": month,
        "import_kwh": imp, "export_kwh": exp, "billed_kwh": billed, "surplus_kwh": surplus,
        "energy_price_net": round(e_price, 4), "dist_var_net": round(var_price, 4), "excise_net": excise_p,
        "fixed_net": round(fixed, 2), "deposit_price_net": dep_price,
        "deposit_value": dep_value, "deposit_used": deposit_used,
        "net": net, "gross": gross, "interest": interest, "to_pay": round(max(gross - deposit_used, 0.0) + interest, 2),
        # ceny przeliczone na brutto — to z nich korzysta liczenie kosztu cykli
        "import_price": round((e_price + var_price + excise_p) * VAT, 4),
        "export_price": round(dep_value / surplus, 4) if surplus > 0 and dep_value > 0 else 0.0,
    }


ADDITIVE = ("import_kwh", "export_kwh", "billed_kwh", "surplus_kwh", "net", "gross", "fixed_net",
            "deposit_value", "deposit_used", "interest", "to_pay")


def by_month(bills):
    """Faktury (po numerze) -> rekordy miesięczne. Faktura obejmująca kilka miesięcy (lub kilka faktur w
    jednym miesiącu) jest rozdzielana/sumowana proporcjonalnie do dni."""
    from datetime import date, timedelta
    out = {}
    for b in bills:
        d0, d1 = date.fromisoformat(b["from"]), date.fromisoformat(b["to"])
        end = d1 - timedelta(days=1) if d1.day == 1 else d1
        days, d = [], d0
        while d <= end:
            days.append(f"{d.year}-{d.month:02d}")
            d += timedelta(days=1)
        if not days:
            days = [b["month"]]
        for mo in set(days):
            f = days.count(mo) / len(days)
            r = out.setdefault(mo, {"month": mo, **{k: 0.0 for k in ADDITIVE}, "_pw": 0.0, "_imp": 0.0, "_exp": 0.0, "numbers": []})
            for k in ADDITIVE:
                r[k] += b[k] * f
            w = max(b["billed_kwh"] * f, 0.001)
            r["_pw"] += w
            r["_imp"] += b["import_price"] * w
            r["_exp"] += b["export_price"] * w
            r["numbers"].append(b["number"])
    res = []
    for r in out.values():
        w = r.pop("_pw")
        r["import_price"] = round(r.pop("_imp") / w, 4)
        r["export_price"] = round(r.pop("_exp") / w, 4)
        for k in ADDITIVE:
            r[k] = round(r[k], 3)
        res.append(r)
    return sorted(res, key=lambda x: x["month"])


def stats(bills):
    """Praktyczne podsumowanie z listy faktur (jedna na miesiąc)."""
    bs = sorted(bills, key=lambda b: b["month"])
    if not bs:
        return None
    n = len(bs)
    s = lambda k: sum(b[k] for b in bs)
    imp, exp, billed = s("import_kwh"), s("export_kwh"), s("billed_kwh")
    gross, paid, dep = s("gross"), s("to_pay"), s("deposit_value")
    exp_value = sum(b["export_kwh"] * b["export_price"] for b in bs)
    buy_value = sum(b["export_kwh"] * b["import_price"] for b in bs)
    last = bs[-1]
    return {
        "months": n, "from": bs[0]["month"], "to": last["month"],
        "avg_paid": round(paid / n, 2), "avg_gross": round(gross / n, 2),
        "total_paid": round(paid, 2), "total_deposit": round(dep, 2),
        "import_kwh": round(imp, 1), "export_kwh": round(exp, 1), "billed_kwh": round(billed, 1),
        "netted_kwh": round(imp - billed, 1),                       # energia z sieci skompensowana w tej samej godzinie
        "fixed_share": round(sum(b["fixed_net"] for b in bs) * VAT / gross * 100, 1) if gross else 0,
        "export_vs_buy": round(exp_value / buy_value * 100, 0) if buy_value else 0,   # ile % ceny zakupu dostajesz za oddaną kWh
        "real_kwh_cost": round(paid / billed, 2) if billed else 0,   # wydane z kieszeni na rozliczoną kWh
        "buy_value": round(buy_value, 2),                           # tyle kosztowałaby oddana energia po cenie zakupu
        "fixed_month": round(sum(x["fixed_net"] for x in bs) * VAT / n, 2),
        "last": {"month": last["month"], "to_pay": last["to_pay"], "gross": last["gross"],
                 "import_price": last["import_price"], "export_price": last["export_price"]},
    }


def parse_csv(text):
    """Zestawienie rozliczeń z Energa 24 (CSV) -> {miesiąc: {month, gross, billed_kwh, source}}.
    Dla każdego okresu bierze najnowszy dokument (korekta wygrywa z fakturą), zużycie z faktury rozliczeniowej,
    a okresy dłuższe/krótsze niż miesiąc rozdziela na miesiące proporcjonalnie do dni."""
    import csv
    import io
    from datetime import date, timedelta
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿")), delimiter=";"))
    if not rows or "Nr faktury" not in rows[0] or "Data od" not in rows[0]:
        raise ValueError("To nie wygląda na zestawienie rozliczeń z Energa 24.")
    fl = lambda s: float((s or "0").replace(" ", "").replace(",", "."))
    best, kwh = {}, {}
    for r in rows:
        if not r.get("Data od") or not r.get("Data do"):
            continue  # noty odsetkowe
        kind = r["Nr faktury"].split("/")[1] if "/" in r["Nr faktury"] else ""
        if kind not in ("FES", "KES"):
            continue
        k = (r["Data od"], r["Data do"])
        key = (r["Data wystawienia"], r["Nr faktury"])
        if k not in best or key > best[k][0]:
            best[k] = (key, fl(r["Kwota faktury"]))
        if kind == "FES":
            kwh[k] = fl(r["Zużycie całkowite"])
    out = {}
    for (a, b), (_, amt) in best.items():
        d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
        days = max((d1 - d0).days, 1)
        # „Data od” to dzień poprzedniego odczytu: przy odczycie 1. dnia okres zaczyna się nim,
        # przy odczycie na koniec miesiąca (od 2024) dzień później
        d = d0 if d0.day == 1 else d0 + timedelta(days=1)
        stop = d1 if d0.day == 1 else d1 + timedelta(days=1)
        while d < stop:
            m = f"{d.year}-{d.month:02d}"
            rec = out.setdefault(m, {"month": m, "gross": 0.0, "billed_kwh": 0.0, "source": "csv"})
            rec["gross"] += amt / days
            rec["billed_kwh"] += kwh.get((a, b), 0.0) / days
            d += timedelta(days=1)
    for rec in out.values():
        rec["gross"], rec["billed_kwh"] = round(rec["gross"], 2), round(rec["billed_kwh"], 1)
    return out
