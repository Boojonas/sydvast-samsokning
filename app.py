"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API, filtrerat på de nio
biblioteken i Nätverket Sydväst. Kör sökningarna parallellt för snabbare
svarstid.
"""

import streamlit as st
import requests
import re
import socket
import time
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed

# Tvingar IPv4 - vissa molnmiljöer (Streamlit Cloud, Colab) har opålitlig
# eller saknad IPv6-anslutning, vilket kan orsaka "Network unreachable"-fel
# mot servrar som annonserar en IPv6-adress (AAAA-post).
import urllib3.util.connection as urllib3_cn


def _tvinga_ipv4():
    return socket.AF_INET


urllib3_cn.allowed_gai_family = _tvinga_ipv4

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
            "https://bibliotek.vellinge.se/web/arena/search"
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
    "User-Agent": "SydvastSamsokning/1.0 (kontakt: jonas.g.elofsson@vellinge.se)",
    "Accept": "application/ld+json, application/json",
}

MAX_PARALLELLA_ANROP = 3  # sänkt från 6 - färre samtidiga anrop är mindre "robotlikt"


def bygg_arena_titelfraga(sokterm: str) -> str:
    """Bygger en Arena-specifik titelfältssökning (title_index/titleMain_index)
    för djuplänkar till bibliotekens egna kataloger, så att länken inte visar
    samma brus som en obegränsad fritextsökning skulle ge."""
    ord_lista = re.sub(r'["()]', "", sokterm).split()
    grupper = [f"(title_index:{ord} OR titleMain_index:{ord})" for ord in ord_lista]
    return " AND ".join(grupper)


def bygg_sokfraga(sokterm: str, soktyp: str, forfattare: str = "") -> str:
    sokterm_rensad = re.sub(r'["()]', "", sokterm)
    if soktyp == "ISBN":
        isbn_rensat = re.sub(r"[\s-]", "", sokterm_rensad)
        return f'isbn:({isbn_rensat}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    # Bekräftad syntax direkt från Libris find-API: title:(ord1 ord2 ord3)
    # riktar sökningen mot titelfältet. instanceCategory bekräftat korrekt
    # (till skillnad från idrda:Volume) för att korrekt utesluta e-böcker
    # som delar verkspost med den tryckta utgåvan.
    fraga = f'title:({sokterm_rensad}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    forfattare_rensad = re.sub(r'["()]', "", forfattare).strip()
    if forfattare_rensad:
        # Bekräftad syntax: contributor:(namn) - samma parentesmönster som titel
        fraga += f' contributor:({forfattare_rensad})'
    return fraga


def sok_bibliotek(bas_fraga: str, sigel: str, forsok: int = 3):
    """Söker ett enskilt bibliotek och returnerar (sigel, antal_träffar, felmeddelande).
    Försöker om vid tillfälliga anslutningsfel (timeout etc), med kort paus emellan,
    innan det räknas som ett riktigt fel."""
    query = f'{bas_fraga} library:"libris:library/org/{sigel}"'
    senaste_fel = None

    for försök_nr in range(1, forsok + 1):
        try:
            resp = requests.get(FIND_URL, params={"_q": query}, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            return sigel, data.get("totalItems", 0), None
        except requests.exceptions.RequestException as e:
            senaste_fel = str(e)
            if försök_nr < forsok:
                time.sleep(2 * försök_nr)  # 2s, 4s, ... - ökande paus mellan försök
        except ValueError:
            senaste_fel = "kunde inte tolka svaret"
            break  # inte en anslutningsfråga - inget att vinna på att försöka igen

    return sigel, None, senaste_fel


@st.cache_data(ttl=600, show_spinner=False)  # cachar identiska sökningar i 10 minuter
def sok_alla_bibliotek(sokterm: str, soktyp: str, forfattare: str = ""):
    """Söker alla bibliotek för en given term och returnerar (träffar_per_kod, fel_per_kod)."""
    bas_fraga = bygg_sokfraga(sokterm, soktyp, forfattare)

    uppgifter = [
        (kod, sigel)
        for kod, info in SIGLAR.items()
        for sigel in info["sigler"]
    ]

    traffar_per_kod = {kod: 0 for kod in SIGLAR}
    fel_per_kod = {}

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

    return traffar_per_kod, fel_per_kod


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon="📚")

# --- Enkelt lösenordsskydd ---
def kolla_losenord():
    def losenord_angivet():
        if st.session_state.get("losenord_falt") == st.secrets.get("app_losenord"):
            st.session_state["inloggad"] = True
            del st.session_state["losenord_falt"]
        else:
            st.session_state["inloggad"] = False

    if st.session_state.get("inloggad"):
        return True

    st.text_input(
        "Lösenord", type="password", on_change=losenord_angivet, key="losenord_falt"
    )
    if st.session_state.get("inloggad") is False:
        st.error("Fel lösenord.")
    return False


if not kolla_losenord():
    st.stop()
# --- Slut lösenordsskydd ---

st.title("📚 Samsökning – Nätverket Sydväst")
st.caption(
    "Sök i bokbeståndet hos de nio biblioteken i Nätverket Sydväst, "
    "via LIBRIS öppna API. Visar endast tryckta böcker."
)

soktyp = st.radio(
    "Sök på", options=["Titel", "ISBN"], horizontal=True, index=0,
    label_visibility="collapsed",
)
platshallare = "Boktitel" if soktyp == "Titel" else "ISBN (10 eller 13 siffror)"

with st.form("sok_form"):
    sokterm = st.text_input(
        "Sökterm", placeholder=platshallare, label_visibility="collapsed",
    )
    forfattare = ""
    if soktyp == "Titel":
        forfattare = st.text_input(
            "Författare (valfritt)", placeholder="Författare (valfritt)",
            label_visibility="collapsed",
        )
        st.caption(
            "💡 För bästa resultat: sök på fullständig titel, gärna med "
            "författare om titeln är vanlig. Klicka på någon av de länkade "
            "biblioteken för att se lånestatus."
        )
    else:
        st.caption("💡 Klicka på någon av de länkade biblioteken för att se lånestatus.")
    sok_knapp = st.form_submit_button("Sök", type="primary")

if sok_knapp and sokterm.strip():
    sokterm = sokterm.strip()
    forfattare = forfattare.strip()

    with st.spinner(f"Söker hos {len(SIGLAR)} bibliotek samtidigt..."):
        traffar_per_kod, fel_per_kod = sok_alla_bibliotek(sokterm, soktyp, forfattare)

    resultat = []
    for kod, info in SIGLAR.items():
        if soktyp == "ISBN":
            # ISBN hör inte hemma i ett titelfält - länka med rå sökterm istället
            arena_fraga = sokterm
        else:
            arena_fraga = bygg_arena_titelfraga(sokterm)
        sok_lank = info["sok_url"].format(query=quote_plus(arena_fraga))
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
        "är inte sökbart och aktuell lånestatus visas inte. För titlar som "
        "finns i både tryckt och digital utgåva kan resultatet ibland bli "
        "missvisande – dubbelkolla vid osäkerhet genom att klicka på biblioteket."
    )

elif sok_knapp:
    st.warning("Skriv in en sökterm.")
