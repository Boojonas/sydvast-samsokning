"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API, filtrerat på de nio
biblioteken i Nätverket Sydväst. Kör sökningarna parallellt för snabbare
svarstid.
"""

import streamlit as st
import requests
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

FIND_URL = "https://libris.kb.se/find"

SIGLAR = {
    "Arlo": {"namn": "Burlöv", "sigler": ["Arlo"], "url": "https://bibliotek.burlov.se"},
    "Eslo": {"namn": "Eslöv", "sigler": ["ESLO"], "url": "https://bibliotek.eslov.se"},
    "Hoor": {"namn": "Höör", "sigler": ["Hoor"], "url": "https://bibliotek.hoor.se"},
    "Kavl": {"namn": "Kävlinge", "sigler": ["Kavl"], "url": "https://bibliotek.kavlinge.se"},
    "LommaBjarred": {"namn": "Lomma/Bjärred", "sigler": ["LOBJ"], "url": "https://biblioteklb.se"},
    "Staf": {"namn": "Staffanstorp", "sigler": ["Staf"], "url": "https://bibliotek.staffanstorp.se"},
    "Trel": {"namn": "Trelleborg", "sigler": ["Trel"], "url": "https://bibliotek.trelleborg.se"},
    "Vell": {"namn": "Vellinge", "sigler": ["Vell"], "url": "https://bibliotek.vellinge.se"},
    "Sved": {"namn": "Svedala", "sigler": ["Sved"], "url": "https://bibliotek.svedala.se"},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/ld+json, application/json",
}

MAX_PARALLELLA_ANROP = 6  # hövlig gräns - inte alla anrop på en gång


def ar_isbn(text: str) -> bool:
    """Avgör om söktexten ser ut som ett ISBN (10 eller 13 siffror, ev. med
    bindestreck/mellanslag, ISBN-10 kan sluta på X)."""
    rensat = re.sub(r"[\s-]", "", text)
    return bool(re.fullmatch(r"\d{9}[\dXx]|\d{13}", rensat))


def bygg_sokfraga(sokterm: str) -> str:
    if ar_isbn(sokterm):
        isbn_rensat = re.sub(r"[\s-]", "", sokterm)
        return f'isbn:{isbn_rensat} instanceCategory:"idrda:Volume"'
    return f'{sokterm} instanceCategory:"idrda:Volume"'


def sok_bibliotek(bas_fraga: str, sigel: str):
    """Söker ett enskilt bibliotek och returnerar (sigel, antal_träffar, felmeddelande)."""
    query = f'{bas_fraga} library:"libris:library/org/{sigel}"'
    try:
        resp = requests.get(FIND_URL, params={"_q": query}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return sigel, data.get("totalItems", 0), None
    except requests.exceptions.RequestException as e:
        return sigel, None, str(e)
    except ValueError:
        return sigel, None, "kunde inte tolka svaret"


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon="📚")

st.title("📚 Samsökning – Nätverket Sydväst")
st.caption(
    "Sök i bokbeståndet hos de nio biblioteken i Nätverket Sydväst, "
    "via LIBRIS öppna API. Visar endast tryckta böcker."
)

with st.form("sok_form"):
    sokterm = st.text_input(
        "Boktitel eller ISBN",
        placeholder="Boktitel eller ISBN",
        label_visibility="collapsed",
    )
    st.caption("💡 För bästa resultat: sök på fullständig titel.")
    sok_knapp = st.form_submit_button("Sök", type="primary")

if sok_knapp and sokterm.strip():
    sokterm = sokterm.strip()
    bas_fraga = bygg_sokfraga(sokterm)

    # Bygg en lista av (kod, sigel) att söka - vissa bibliotek har flera sigler
    uppgifter = [
        (kod, sigel)
        for kod, info in SIGLAR.items()
        for sigel in info["sigler"]
    ]

    traffar_per_kod = {kod: 0 for kod in SIGLAR}
    fel_per_kod = {}

    with st.spinner(f"Söker hos {len(SIGLAR)} bibliotek samtidigt..."):
        with ThreadPoolExecutor(max_workers=MAX_PARALLELLA_ANROP) as executor:
            framtida = {
                executor.submit(sok_bibliotek, bas_fraga, sigel): kod
                for kod, sigel in uppgifter
            }
            for f in as_completed(framtida):
                kod = framtida[f]
                sigel, antal, felmeddelande = f.result()
                if felmeddelande:
                    fel_per_kod[kod] = felmeddelande
                elif antal:
                    traffar_per_kod[kod] += antal

    resultat = []
    for kod, info in SIGLAR.items():
        kommun_lankad = f"[{info['namn']}]({info['url']})"
        if kod in fel_per_kod:
            status = "⚠️ Fel"
            antal_visning = "-"
        elif traffar_per_kod[kod] > 0:
            status = "✅ Finns"
            antal_visning = traffar_per_kod[kod]
        else:
            status = "❌ Finns ej"
            antal_visning = 0
        resultat.append({"Bibliotek": kommun_lankad, "Status": status, "Antal poster": antal_visning})

    st.subheader(f"Resultat för \"{sokterm}\"")

    # Bygger en markdown-tabell manuellt så att biblioteksnamnen blir klickbara länkar
    tabell_rader = ["| Bibliotek | Status | Antal poster |", "|---|---|---|"]
    for r in resultat:
        tabell_rader.append(f"| {r['Bibliotek']} | {r['Status']} | {r['Antal poster']} |")
    st.markdown("\n".join(tabell_rader))

    antal_traffar = sum(1 for r in resultat if r["Status"] == "✅ Finns")
    if antal_traffar == 0:
        st.warning("Boken hittades inte hos något av de nio biblioteken.")
    else:
        st.success(f"Boken finns hos {antal_traffar} av 9 bibliotek.")

    st.caption(
        "Bygger på bibliotekens rapporterade bestånd i LIBRIS. Äldre bestånd "
        "är inte sökbart och aktuell lånestatus visas inte."
    )

elif sok_knapp:
    st.warning("Skriv in en boktitel eller ett ISBN att söka efter.")
