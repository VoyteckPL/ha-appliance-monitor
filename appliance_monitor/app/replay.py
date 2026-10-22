"""Odtwarzanie historii poboru mocy przez silnik wykrywania. Czysta logika, bez I/O."""
from engine import IDLE, Appliance, entry_from_info

TICK_S = 5


def replay(cfg, readings, tick=TICK_S):
    """readings: lista (t, W lub None) posortowana rosnąco, wartość obowiązuje do następnego odczytu.
    Zwraca listę wpisów historii (zakończone cykle) wykrytych przy ustawieniach z cfg."""
    if len(readings) < 2:
        return []
    a = Appliance(dict(cfg))
    n = len(readings)
    out, i = [], 0
    t, end = readings[0][0], readings[-1][0]
    thr = cfg["start_threshold"]
    while t <= end:
        while i + 1 < n and readings[i + 1][0] <= t:
            i += 1
        value = readings[i][1]
        a.set_power(value, t)
        for kind, info in a.tick(t):
            if kind == "finish":
                out.append(entry_from_info(info))
        # długie okresy bezczynności przeskakujemy do następnego odczytu
        if a.state == IDLE and a.above_since is None and (value is None or value <= thr):
            if i + 1 >= n:
                break
            t = max(t + tick, readings[i + 1][0])
        else:
            t += tick
    return out
