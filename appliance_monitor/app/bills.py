"""Odczyt faktur Energa (tekst z pdftotext -layout) i statystyki z rachunków. Czysta logika."""
import re

VAT = 1.23


def num(s):
    return float(s.replace(" ", "").replace(" ", "").replace(",", "."))


def _norm(text):
    return [re.sub(r"[ \t ]+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]


def parse_invoice(text):
    """Zwraca słownik z danymi faktury albo rzuca ValueError z czytelnym powodem."""
    lines = _norm(text)
    joined = "\n".join(lines)
    m = re.search(r"Faktura (korygująca )?VAT (\S+)", joined)
    if not m:
        raise ValueError("To nie wygląda na fakturę Energa.")
    if m.group(1):
        raise ValueError("To faktura korygująca; wgraj zwykłą fakturę rozliczeniową (FES).")
    number = m.group(2)
    p = re.search(r"Rozliczenie za okres (\d\d)\.(\d\d)\.(\d{4}) - (\d\d)\.(\d\d)\.(\d{4})", joined)
    if not p:
        raise ValueError("Nie znalazłem okresu rozliczeniowego.")
    month = f"{p.group(3)}-{p.group(2)}"
    if (p.group(2), p.group(3)) != (p.group(5), p.group(6)):
        raise ValueError("Obsługuję faktury za pojedynczy miesiąc.")

    reads = [num(x) for x in re.findall(r"^G1\d \d+ \S+ .* (\d+,\d{3}) Z \d", joined, re.M)]
    if len(reads) < 1:
        raise ValueError("Nie znalazłem odczytów licznika.")
    imp = reads[0]
    exp = reads[1] if len(reads) > 1 else 0.0

    def after(label):
        r = re.search(label + r" \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} ([\d ]+,?\d*) (kWh|MC|MWh) ([\d,]+) ([\d ]+,\d\d)", joined)
        return r

    billed = None
    r = re.search(r"Energia czynna całodobowa \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} (\d+) kWh ([\d,]+) ", joined)
    if r:
        billed, e_price = float(r.group(1)), num(r.group(2))
    else:
        r2 = re.search(r"Energia czynna całodobowa \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} (\d+) kWh", joined)
        raise ValueError("Nie znalazłem pozycji „Energia czynna całodobowa”." if not r2 else "Nie odczytałem ceny energii.")
    if billed is not None and r:
        e_price = num(r.group(2))

    var_price = 0.0
    for lab in ("Opłata sieciowa zmienna całodobowa", "Opłata jakościowa", "Opłata OZE", "Opłata kogeneracyjna"):
        r = re.search(lab + r" \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} \d+ kWh ([\d,]+) ", joined)
        if not r:
            raise ValueError(f"Nie znalazłem pozycji „{lab}”.")
        var_price += num(r.group(1))
    ex = re.search(r"akcyza \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} [\d,]+ MWh ([\d,]+) ", joined)
    excise = num(ex.group(1)) / 1000 if ex else 0.005

    fixed = 0.0
    for lab in ("Opłata handlowa", "Opłata abonamentowa", "Opłata sieciowa stała", "Opłata mocowa"):
        r = re.search(lab + r" \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} [\d,]+ MC ([\d,]+) ([\d,]+) 23", joined)
        if r:
            fixed += num(r.group(2))

    d = re.search(r"Suma godzinowych sald ujemnych \d\d\.\d\d\.\d{4} \d\d\.\d\d\.\d{4} (\d+) kWh ([\d,]+) ([\d,]+) [\d.]+ ([\d,]+)", joined)
    surplus, dep_price, dep_value = (float(d.group(1)), num(d.group(2)), num(d.group(4))) if d else (0.0, 0.0, 0.0)

    t = re.search(r"Rozliczenie energii elektrycznej i świadczenia usługi dystrybucji ([\d ]+,\d\d) ([\d ]+,\d\d) ([\d ]+,\d\d)", joined)
    if not t:
        raise ValueError("Nie znalazłem kwoty faktury.")
    net, gross = num(t.group(1)), num(t.group(3))
    used = re.search(r"Pobrano z DEPOZYTU ([\d ]+,\d\d)", joined)
    deposit_used = num(used.group(1)) if used else 0.0
    interest = re.search(r"odsetki za nieterminową wpłatę ([\d ]+,\d\d)", joined)
    interest = num(interest.group(1)) if interest else 0.0
    to_pay = max(gross - deposit_used, 0.0) + interest

    return {
        "number": number, "month": month,
        "import_kwh": imp, "export_kwh": exp, "billed_kwh": billed, "surplus_kwh": surplus,
        "energy_price_net": e_price, "dist_var_net": round(var_price, 4), "excise_net": excise,
        "fixed_net": round(fixed, 2), "deposit_price_net": dep_price,
        "deposit_value": dep_value, "deposit_used": deposit_used,
        "net": net, "gross": gross, "interest": interest, "to_pay": round(to_pay, 2),
        # ceny przeliczone na brutto — to z nich korzysta liczenie kosztu cykli
        "import_price": round((e_price + var_price + excise) * VAT, 4),
        "export_price": round(dep_price * VAT, 4),
    }


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
