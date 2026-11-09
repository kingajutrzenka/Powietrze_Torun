import requests, pandas as pd, time

BASE = "https://api.gios.gov.pl/pjp-api/v1/rest"

def get(path, **params):
    r = requests.get(f"{BASE}/{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()

# Stacje Toruń
stacje = pd.DataFrame(get("station/findAll", size=500)["Lista stacji pomiarowych"])
torun = stacje[stacje["Nazwa miasta"] == "Toruń"]
print(torun[["Identyfikator stacji", "Nazwa stacji"]])

# Indeks
for sid in torun["Identyfikator stacji"]:
    idx = get(f"aqindex/getIndex/{sid}")["AqIndex"]
    print(sid, idx["Wartość indeksu"], idx["Nazwa kategorii indeksu"])

# Stanowiska pomiarowe
stanowiska = []
for sid in torun["Identyfikator stacji"]:
    stanowiska += get(f"station/sensors/{sid}")["Lista stanowisk pomiarowych dla podanej stacji"]
    time.sleep(31)

stanowiska = pd.DataFrame(stanowiska)
pyl = stanowiska[stanowiska["Wskaźnik - kod"].isin(["PM10", "PM2.5"])]
print(pyl[["Identyfikator stacji", "Identyfikator stanowiska", "Wskaźnik - kod"]])
