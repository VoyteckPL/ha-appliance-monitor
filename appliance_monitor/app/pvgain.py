"""Ile daje fotowoltaika: oszczędność na energii zużytej na miejscu + depozyt za oddaną. Czysta logika."""


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
