"""Harmonogram spłat kredytu (BNP Paribas, PDF -> tekst z pdftotext -layout). Czysta logika."""
import re

ROW = re.compile(r"(\d{4}-\d\d-\d\d)\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)\s+(-?[\d ]*\d,\d\d)")


def _num(s):
    return float(s.replace(" ", "").replace(",", "."))


def parse_schedule(text):
    """Zwraca {amount, rows:[[data, kapitał, odsetki, opłaty, rata, saldo], ...]} posortowane po dacie."""
    rows = {}
    for m in ROW.finditer(text):
        d = m.group(1)
        rows[d] = [d] + [round(_num(g), 2) for g in m.groups()[1:]]
    if len(rows) < 3:
        raise ValueError("Nie znalazłem rat w harmonogramie spłat kredytu.")
    rows = [rows[k] for k in sorted(rows)]
    m = re.search(r"([\d ]+,\d\d)\s*PLN", text.split("Loan disbursement amount")[0][-400:]) if "Loan disbursement amount" in text else None
    amount = _num(m.group(1)) if m else round(rows[0][5] + rows[0][1], 2)  # saldo po 1. racie + jej kapitał
    return {"amount": amount, "rows": rows}


def summary(loan, today):
    """today: 'YYYY-MM-DD'. Spłacone raty = z datą <= dziś."""
    rows = loan["rows"]
    paid = [r for r in rows if r[0] <= today]
    left = [r for r in rows if r[0] > today]
    s = lambda rs, i: round(sum(r[i] for r in rs), 2)
    return {"amount": loan["amount"], "count": len(rows), "paid_n": len(paid),
            "paid_principal": s(paid, 1), "paid_interest": s(paid, 2), "paid_fees": s(paid, 3), "paid_total": s(paid, 4),
            "left_total": s(left, 4), "left_interest": s(left, 2), "left_fees": s(left, 3),
            "balance": paid[-1][5] if paid else loan["amount"], "total": s(rows, 4),
            "cost": round(s(rows, 2) + s(rows, 3), 2), "next": left[0][0] if left else None,
            "next_amount": left[0][4] if left else 0.0, "last": rows[-1][0]}
