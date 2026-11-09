"""
Zbiera godzinowe pomiary pyłu (PM10, PM2.5) ze stacji GIOŚ w Toruniu
i liczy z nich średnie dobowe z ostatnich 30 dni do data/last30.json.

Tryby:
  python fetch_history.py             -> dopisuje dane bieżące (ostatnie ~3 doby, uruchamiane co godzinę)
  python fetch_history.py --backfill  -> jednorazowo pobiera archiwum z ostatnich 35 dni
                                         (wolne: limit 2 zapytania/min, ok. 10 min – uruchom u siebie)

Pliki:
  data/history.csv  – surowe pomiary godzinowe (ostatnie 60 dni)
  data/last30.json  – średnie dobowe dla strony historia.html

Źródło danych: Główny Inspektorat Ochrony Środowiska (GIOŚ)
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from fetch_now import BASE, STACJE, TZ

HISTORY = Path("data/history.csv")
LAST30 = Path("data/last30.json")

# stanowiska pyłowe w Toruniu: id stanowiska -> (id stacji, wskaźnik)
STANOWISKA = {
    853: (145, "PM10"),
    1405: (206, "PM10"),
    1406: (206, "PM10"),
    1408: (206, "PM2.5"),
    1426: (208, "PM10"),
    21163: (208, "PM2.5"),
}

NORMA_PM10 = 50          # dopuszczalny poziom dobowy PM10, µg/m³
MIN_GODZIN = 18          # średnia dobowa liczona tylko, gdy jest min. 75% pomiarów
TRZYMAJ_DNI = 60         # tyle dni surowych danych trzymamy w history.csv

# --- endpointy (gdyby GIOŚ zmienił nazwy, poprawiasz tylko tutaj) ---
URL_BIEZACE = BASE + "/data/getData/{sid}"                         # czas lokalny, limit 1500/min
URL_ARCHIWUM = BASE + "/archivalData/getDataBySensor/{sid}"        # czas UTC+1, limit 2/min
ARCHIWUM_FORMAT_DATY = "%Y-%m-%d %H:%M"

DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def pobierz_strony(url, params, przerwa=0):
    """Pobiera wszystkie strony odpowiedzi i zwraca jedną listę rekordów."""
    rekordy, strona = [], 0
    while True:
        r = requests.get(url, params={**params, "page": strona, "size": 500}, timeout=60)
        if r.status_code == 429:  # przekroczony limit – czekamy i ponawiamy
            time.sleep(61)
            continue
        r.raise_for_status()
        odp = r.json()
        lista = next((v for v in odp.values() if isinstance(v, list)), None)
        if lista is None:
            raise ValueError(f"Nieoczekiwana odpowiedź z {r.url}: klucze {list(odp)}")
        rekordy += lista
        strony = odp.get("totalPages") or 1
        strona += 1
        if strona >= strony:
            return rekordy
        time.sleep(przerwa)


def na_pomiary(rekordy):
    """Z rekordów GIOŚ wyciąga pary (czas, wartość) niezależnie od dokładnych nazw pól."""
    wynik = []
    for rec in rekordy:
        czas = next((v for v in rec.values() if isinstance(v, str) and DATA_RE.match(v)), None)
        wartosc = next((v for k, v in rec.items() if k.startswith("Warto")), None)
        if czas is not None and wartosc is not None:
            wynik.append((czas[:16], float(wartosc)))
    return wynik


def do_ramki(pomiary, sid, strefa):
    """Zamienia pomiary na ramkę z czasem w UTC (archiwum: stałe UTC+1, bieżące: czas polski)."""
    if not pomiary:
        return pd.DataFrame()
    df = pd.DataFrame(pomiary, columns=["czas", "wartosc"])
    czas = pd.to_datetime(df["czas"], format="%Y-%m-%d %H:%M")
    if strefa == "lokalny":
        czas = czas.dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward")
    else:
        czas = czas.dt.tz_localize("Etc/GMT-1")  # UTC+1 bez czasu letniego
    df["czas_utc"] = czas.dt.tz_convert("UTC")
    df = df.dropna(subset=["czas_utc"])
    stacja, wskaznik = STANOWISKA[sid]
    df["stanowisko"], df["stacja"], df["wskaznik"] = sid, stacja, wskaznik
    return df[["stanowisko", "stacja", "wskaznik", "czas_utc", "wartosc"]]


def pobierz_biezace():
    ramki = []
    for sid in STANOWISKA:
        try:
            rek = pobierz_strony(URL_BIEZACE.format(sid=sid), {})
            ramki.append(do_ramki(na_pomiary(rek), sid, "lokalny"))
        except (requests.RequestException, ValueError) as e:
            print(f"Stanowisko {sid}: błąd – {e}", file=sys.stderr)
    return ramki


def pobierz_archiwum(dni=35):
    koniec = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    params = {
        "dateFrom": (koniec - timedelta(days=dni)).strftime(ARCHIWUM_FORMAT_DATY),
        "dateTo": koniec.strftime(ARCHIWUM_FORMAT_DATY),
    }
    ramki = []
    for i, sid in enumerate(STANOWISKA):
        if i:
            time.sleep(31)
        print(f"Archiwum: stanowisko {sid} ({i + 1}/{len(STANOWISKA)})…")
        try:
            rek = pobierz_strony(URL_ARCHIWUM.format(sid=sid), params, przerwa=31)
            df = do_ramki(na_pomiary(rek), sid, "utc+1")
            print(f"  {len(df)} pomiarów")
            ramki.append(df)
        except (requests.RequestException, ValueError) as e:
            print(f"  błąd – {e}", file=sys.stderr)
    return ramki


def scal_z_historia(nowe):
    stare = []
    if HISTORY.exists():
        h = pd.read_csv(HISTORY)
        h["czas_utc"] = pd.to_datetime(h["czas_utc"], utc=True)
        stare = [h]
    df = pd.concat(stare + [r for r in nowe if not r.empty], ignore_index=True)
    if df.empty:
        return df
    # nowszy odczyt tej samej godziny zastępuje starszy (GIOŚ weryfikuje dane z opóźnieniem)
    df = df.drop_duplicates(subset=["stanowisko", "czas_utc"], keep="last")
    granica = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=TRZYMAJ_DNI)
    df = df[df["czas_utc"] >= granica].sort_values(["stanowisko", "czas_utc"])
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(HISTORY, index=False)
    return df


def policz_last30(df):
    df = df.copy()
    df["dzien"] = df["czas_utc"].dt.tz_convert(TZ).dt.date
    dzis = datetime.now(TZ).date()
    # 30 pełnych dni kończących się wczoraj (dzisiejsza doba jest zawsze niepełna)
    dni = [dzis - timedelta(days=i) for i in range(30, 0, -1)]
    df = df[df["dzien"].isin(dni)]

    stacje_out = []
    for st_id, nazwa in STACJE.items():
        wpis = {"station_id": st_id, "station": nazwa}
        for wskaznik, klucz in (("PM10", "pm10"), ("PM2.5", "pm25")):
            d = df[(df["stacja"] == st_id) & (df["wskaznik"] == wskaznik)]
            if d.empty:
                wpis[klucz] = None
                continue
            # gdy stacja ma kilka stanowisk tego wskaźnika, bierzemy to z największą liczbą pomiarów
            najlepsze = d["stanowisko"].value_counts().idxmax()
            d = d[d["stanowisko"] == najlepsze]
            dobowe = d.groupby("dzien")["wartosc"].agg(["mean", "count"])
            seria = []
            for dzien in dni:
                ok = dzien in dobowe.index and dobowe.loc[dzien, "count"] >= MIN_GODZIN
                seria.append({
                    "date": dzien.isoformat(),
                    "value": round(float(dobowe.loc[dzien, "mean"]), 1) if ok else None,
                })
            wpis[klucz] = seria
        stacje_out.append(wpis)

    wynik = {
        "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "norm_pm10": NORMA_PM10,
        "min_hours": MIN_GODZIN,
        "stations": stacje_out,
        "source": "GIOŚ – Główny Inspektorat Ochrony Środowiska",
    }
    LAST30.write_text(json.dumps(wynik, ensure_ascii=False, indent=2), encoding="utf-8")
    return wynik


def main():
    nowe = pobierz_archiwum() if "--backfill" in sys.argv else pobierz_biezace()
    df = scal_z_historia(nowe)
    if df.empty:
        print("Brak jakichkolwiek danych – nic nie zapisuję", file=sys.stderr)
        sys.exit(1)
    wynik = policz_last30(df)
    for s in wynik["stations"]:
        dni = sum(1 for p in (s["pm10"] or []) if p["value"] is not None)
        print(f"{s['station']}: {dni}/30 dni z pełną średnią PM10")
    print(f"Zapisano {HISTORY} ({len(df)} wierszy) i {LAST30}")


if __name__ == "__main__":
    main()
