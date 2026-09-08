"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Detta är samma sökmotor som vi byggt och testat i Colab, nu paketerad
som en enkel webbapp. Redo att köras lokalt (streamlit run app.py)
eller deployas gratis på Streamlit Community Cloud (se instruktioner
i chatten).
"""

import streamlit as st
import requests
import time

FIND_URL = "https://libris.kb.se/find"

SIGLAR = {
    "Arlo": {"namn": "Burlöv", "sigler": ["Arlo"]},
    "Eslo": {"namn": "Eslöv", "sigler": ["ESLO"]},
    "Hoor": {"namn": "Höör", "sigler": ["Hoor"]},
    "Kavl": {"namn": "Kävlinge", "sigler": ["Kavl"]},
    "LommaBjarred": {"namn": "Lomma/Bjärred", "sigler": ["LOBJ"]},
    "Staf": {"namn": "Staffanstorp", "sigler": ["Staf"]},
    "Trel": {"namn": "Trelleborg", "sigler": ["Trel"]},
    "Vell": {"namn": "Vellinge", "sigler": ["Vell"]},
    "Sved": {"namn": "Svedala", "sigler": ["Sved"]},
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/ld+json, application/json",
}

DELAY_SECONDS = 1.0


def sok_bibliotek(titel: str, sigel: str):
    """Söker ett enskilt bibliotek och returnerar (antal_träffar, felmeddelande)."""
    query = f'{titel} instanceCategory:"idrda:Volume" library:"libris:library/org/{sigel}"'
    try:
        resp = requests.get(FIND_URL, params={"_q": query}, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data.get("totalItems", 0), None
    except requests.exceptions.RequestException as e:
        return None, str(e)
    except ValueError:
        return None, "kunde inte tolka svaret"


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon="📚")

st.title("📚 Samsökning – Nätverket Sydväst")
st.caption(
    "Sök i bokbeståndet hos de nio biblioteken i Nätverket Sydväst, "
    "via LIBRIS öppna API. Visar endast tryckta böcker."
)

titel = st.text_input("Boktitel", placeholder="t.ex. Blodlust")
sok_knapp = st.button("Sök", type="primary")

if sok_knapp and titel.strip():
    resultat = []
    progress = st.progress(0, text="Söker...")

    for i, (kod, info) in enumerate(SIGLAR.items()):
        kommun = info["namn"]
        traff_totalt = 0
        fel = None

        for sigel in info["sigler"]:
            antal, felmeddelande = sok_bibliotek(titel.strip(), sigel)
            if felmeddelande:
                fel = felmeddelande
            elif antal:
                traff_totalt += antal
            time.sleep(DELAY_SECONDS)

        resultat.append({
            "Bibliotek": kommun,
            "Status": "⚠️ Fel" if fel else ("✅ Finns" if traff_totalt > 0 else "❌ Finns ej"),
            "Antal poster": traff_totalt if not fel else "-",
        })
        progress.progress((i + 1) / len(SIGLAR), text=f"Sökt {kommun}...")

    progress.empty()

    st.subheader(f"Resultat för \"{titel.strip()}\"")
    st.table(resultat)

    antal_traffar = sum(1 for r in resultat if r["Status"] == "✅ Finns")
    if antal_traffar == 0:
        st.warning("Boken hittades inte hos något av de nio biblioteken.")
    else:
        st.success(f"Boken finns hos {antal_traffar} av 9 bibliotek.")

    st.caption(
        "Bygger på bibliotekens rapporterade bestånd i LIBRIS. Dubbelkolla "
        "manuellt vid osäkerhet, särskilt för nyinköpta eller nyutlånade titlar."
    )

elif sok_knapp:
    st.warning("Skriv in en boktitel att söka efter.")
