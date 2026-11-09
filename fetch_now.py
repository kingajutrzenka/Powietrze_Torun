import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = "https://api.gios.gov.pl/pjp-api/v1/rest" #api
TZ = ZoneInfo("Europe/Warsaw")
OUT = Path("data/now.json")

STACJE = {
    145: "Toruń, ul. Wały gen. Sikorskiego",
    206: "Toruń, ul. Dziewulskiego",
    208: "Toruń, ul. Przy Kaszowniku",
}


def pole(slownik, poczatek):
    for k, v in slownik.items():
        if k.startswith(poczatek):
            return v
    return None


def indeks_stacji(sid):
    r = requests.get(f"{BASE}/aqindex/getIndex/{sid}", timeout=30)
    r.raise_for_status()
    aq = r.json()["AqIndex"]
    wartosc = aq.get("Wartość indeksu")
    if wartosc is None or not (0 <= int(wartosc) <= 5):
        wartosc = None 
    czas = pole(aq, "Data danych źródłowych, z których policzono wartość indeksu dla wskaźnika st")
    if czas:
        czas = datetime.strptime(czas, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ).isoformat()
    return {
        "station_id": sid,
        "station": STACJE[sid],
        "level": None if wartosc is None else int(wartosc),
        "category": aq.get("Nazwa kategorii indeksu") if wartosc is not None else None,
        "measured_at": czas,
    }


def main():
    wyniki = []
    for sid in STACJE:
        try:
            wyniki.append(indeks_stacji(sid))
        except (requests.RequestException, KeyError, ValueError) as e:
            print(f"Stacja {sid}: błąd – {e}", file=sys.stderr)
            wyniki.append({"station_id": sid, "station": STACJE[sid], "level": None,
                           "category": None, "measured_at": None})

    poprawne = [w for w in wyniki if w["level"] is not None]
    if not poprawne:
        print("Brak danych z żadnej stacji – zostawiam poprzedni now.json", file=sys.stderr)
        sys.exit(1)
        
    glowny = max(poprawne, key=lambda w: w["level"])

    dane = {
        **glowny,
        "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "stations": wyniki,
        "source": "GIOŚ – Główny Inspektorat Ochrony Środowiska",
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dane, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Zapisano {OUT}: poziom {dane['level']} ({dane['category']}), {dane['station']}")


if __name__ == "__main__":
    main()
