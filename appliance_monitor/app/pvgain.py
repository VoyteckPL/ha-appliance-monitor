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
