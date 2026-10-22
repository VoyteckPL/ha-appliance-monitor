"""Rozpoznawanie programów (grupowanie podobnych cykli) i wykrywanie nietypowych cykli. Czysta logika."""
from engine import fmt_duration, fmt_energy

DUR_TOL, EN_TOL = 0.15, 0.20          # cykle "takie same": czas ±15%, energia ±20%
ANOM_DUR, ANOM_EN = 0.35, 0.45        # nietypowy dopiero, gdy wyraźnie poza każdym programem
MIN_PROGRAM = 3                       # tyle powtórzeń, żeby grupa była programem
MIN_HISTORY_FOR_ANOMALY = 8


def _close(d, e, cd, ce, dt, et):
    return abs(d - cd) <= dt * cd and abs(e - ce) <= et * max(ce, 1.0)


def _dist(d, e, cd, ce):
    return max(abs(d - cd) / max(cd, 1.0), abs(e - ce) / max(ce, 1.0))


def clusters(entries):
    """Grupy podobnych cykli (min. MIN_PROGRAM sztuk), od najkrótszej: {duration_s, energy_wh, count}."""
    groups = []
    for en in sorted(entries, key=lambda x: x["duration_s"]):
        d, e = en["duration_s"], en["energy_wh"]
        for g in groups:
            if _close(d, e, g["d"] / g["n"], g["e"] / g["n"], DUR_TOL, EN_TOL):
                g["d"] += d
                g["e"] += e
                g["n"] += 1
                break
        else:
            groups.append({"d": d, "e": e, "n": 1})
    out = [{"duration_s": int(g["d"] / g["n"]), "energy_wh": round(g["e"] / g["n"], 1), "count": g["n"]}
           for g in groups if g["n"] >= MIN_PROGRAM]
    out.sort(key=lambda c: c["duration_s"])
    return out


def assign(entry, cls):
    """Indeks najbliższej grupy w tolerancji albo None."""
    best, best_d = None, None
    for i, c in enumerate(cls):
        if _close(entry["duration_s"], entry["energy_wh"], c["duration_s"], c["energy_wh"], DUR_TOL, EN_TOL):
            d = _dist(entry["duration_s"], entry["energy_wh"], c["duration_s"], c["energy_wh"])
            if best_d is None or d < best_d:
                best, best_d = i, d
    return best


def name_programs(cls, saved):
    """Nazwy: zapisana przez użytkownika (najbliższy zapisany środek w tolerancji) albo 'Program N'."""
    out = []
    for i, c in enumerate(cls):
        name, best_d = None, None
        for s in saved:
            if _close(c["duration_s"], c["energy_wh"], s["duration_s"], s["energy_wh"], DUR_TOL, EN_TOL):
                d = _dist(c["duration_s"], c["energy_wh"], s["duration_s"], s["energy_wh"])
                if best_d is None or d < best_d:
                    name, best_d = s["name"], d
        out.append({**c, "name": name or f"Program {i + 1}", "custom": name is not None})
    return out


def program_of(entry, named):
    i = assign(entry, named)
    return named[i]["name"] if i is not None else None


def anomaly(entry, history):
    """Tekst opisujący nietypowy cykl albo None. history = poprzednie cykle (bez bieżącego)."""
    if len(history) < MIN_HISTORY_FOR_ANOMALY:
        return None
    cls = clusters(history)
    if not cls or assign(entry, cls) is not None:
        return None
    d, e = entry["duration_s"], entry["energy_wh"]
    best = min(cls, key=lambda c: _dist(d, e, c["duration_s"], c["energy_wh"]))
    if _close(d, e, best["duration_s"], best["energy_wh"], ANOM_DUR, ANOM_EN):
        return None
    return (f"nietypowy cykl: {fmt_duration(d)}, {fmt_energy(e)} "
            f"(zwykle {fmt_duration(best['duration_s'])}, {fmt_energy(best['energy_wh'])})")
