"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API, filtrerat på de nio
biblioteken i Nätverket Sydväst. Kör sökningarna parallellt för snabbare
svarstid.
"""

import streamlit as st
import requests
import re
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

FIND_URL = "https://libris.kb.se/find"

ARENA_STANDARDMALL = (
    "https://{domän}/search"
    "?p_p_id=searchResult_WAR_arenaportlet&p_p_lifecycle=1&p_p_state=normal"
    "&p_r_p_arena_urn%3Aarena_facet_queries="
    "&p_r_p_arena_urn%3Aarena_search_query={{query}}"
    "&p_r_p_arena_urn%3Aarena_search_type=solr"
    "&p_r_p_arena_urn%3Aarena_sort_advice=field%3DRelevance%26direction%3DDescending"
)

SIGLAR = {
    "Arlo": {
        "namn": "Burlöv", "sigler": ["Arlo"],
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.burlov.se"),
    },
    "Eslo": {
        "namn": "Eslöv", "sigler": ["ESLO"],
        # OBS: ej fullt bekräftad - härledd från samma mönster som övriga,
        # eftersom testsökningen bara gav en träff och gick direkt till detaljsidan
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.eslov.se"),
    },
    "Hoor": {
        "namn": "Höör", "sigler": ["Hoor"],
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.hoor.se"),
    },
    "Kavl": {
        "namn": "Kävlinge", "sigler": ["Kavl"],
        "sok_url": (
            "https://bibliotek.kavlinge.se/web/arena/search"
            "?p_p_id=searchResult_WAR_arenaportlet&p_p_lifecycle=1&p_p_state=normal"
            "&p_r_p_arena_urn%3Aarena_search_query={query}"
            "&p_r_p_arena_urn%3Aarena_search_type=solr"
            "&p_r_p_arena_urn%3Aarena_sort_advice=field%3DRelevance%26direction%3DDescending"
        ),
    },
    "LommaBjarred": {
        "namn": "Lomma/Bjärred", "sigler": ["LOBJ"],
        "sok_url": ARENA_STANDARDMALL.format(domän="biblioteklb.se"),
    },
    "Staf": {
        "namn": "Staffanstorp", "sigler": ["Staf"],
        # OBS: ej fullt bekräftad - se kommentar för Eslöv ovan
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.staffanstorp.se"),
    },
    "Trel": {
        "namn": "Trelleborg", "sigler": ["Trel"],
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.trelleborg.se"),
    },
    "Vell": {
        "namn": "Vellinge", "sigler": ["Vell"],
        "sok_url": (
            "https://bibliotek.vellinge.se/web/arena/search-ny"
            "?p_p_id=searchResult_WAR_arenaportlet&p_p_lifecycle=1&p_p_state=normal"
            "&p_r_p_arena_urn%3Aarena_facet_queries="
            "&p_r_p_arena_urn%3Aarena_search_query={query}"
            "&p_r_p_arena_urn%3Aarena_search_type=solr"
            "&p_r_p_arena_urn%3Aarena_sort_advice=field%3DRelevance%26direction%3DDescending"
        ),
    },
    "Sved": {
        "namn": "Svedala", "sigler": ["Sved"],
        "sok_url": ARENA_STANDARDMALL.format(domän="bibliotek.svedala.se"),
    },
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/ld+json, application/json",
}

MAX_PARALLELLA_ANROP = 3  # sänkt från 6 - färre samtidiga anrop är mindre "robotlikt"


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
        sok_lank = info["sok_url"].format(query=quote_plus(sokterm))
        kommun_lankad = f"[{info['namn']}]({sok_lank})"
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

    if fel_per_kod:
        with st.expander("⚠️ Se felmeddelanden (för felsökning)"):
            for kod, felmeddelande in fel_per_kod.items():
                st.write(f"**{SIGLAR[kod]['namn']}**: {felmeddelande}")

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
