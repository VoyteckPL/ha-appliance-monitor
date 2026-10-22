"""Koszt cyklu przy fotowoltaice (net-billing z saldem godzinowym). Czysta logika, bez I/O."""
import bisect

HOUR = 3600


def hourly_wh(entry):
    """Energia urządzenia w każdej godzinie zegarowej: {początek_godziny: Wh}, z krzywej mocy cyklu."""
    c = entry.get("curve")
    out = {}
    if not c or not c.get("w"):
        return out
    t, step = c["t0"], c["step"]
    for w in c["w"]:
        a, b = t, t + step
        while a < b - 1e-9:
            h = int(a // HOUR) * HOUR
            seg = min(b, h + HOUR) - a
            out[h] = out.get(h, 0.0) + w * seg / HOUR
            a += seg
        t = b
    total = sum(out.values())
    if total > 0 and entry.get("energy_wh"):  # krzywa jest zagęszczona: skalujemy do zmierzonej energii
        k = entry["energy_wh"] / total
        out = {h: v * k for h, v in out.items()}
    return out


def value_at(series, ts):
    """series: posortowane [(t, kWh)]; ostatnia znana wartość do chwili ts (None, gdy brak)."""
    i = bisect.bisect_right([t for t, _ in series], ts) - 1
    return series[i][1] if i >= 0 else None


def cycle_cost(entry, imp, exp, p_imp, p_exp, now):
    """Koszt marginalny: energia pokryta nadwyżką PV kosztuje utraconą wartość oddanej energii (p_exp),
    energia z sieci pełną cenę (p_imp). imp/exp: liczniki skumulowane [(t, kWh)].
    Zwraca (koszt, kWh_z_sieci, kWh_z_PV) albo None, gdy brak danych licznika."""
    grid = pv = 0.0
    for h, wh in hourly_wh(entry).items():
        end = min(h + HOUR, now)
        i0, i1 = value_at(imp, h), value_at(imp, end)
        e0, e1 = value_at(exp, h), value_at(exp, end)
        if None in (i0, i1, e0, e1):
            return None
        a = wh / 1000.0
        net = (i1 - i0) - (e1 - e0)        # saldo domu w godzinie, razem z urządzeniem
        g = min(max(net, 0.0), a)     # tyle musiało przyjść z sieci
        grid += g
        pv += a - g
    return grid * p_imp + pv * p_exp, grid, pv
