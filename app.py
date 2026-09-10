"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API. Gör ETT anrop och navigerar
lokalt i verk -> instans -> exemplar-strukturen för att korrekt avgöra
vilka av de nio biblioteken som har boken i FYSISKT format - detta undviker
ett upptäckt fel där bibliotek- och formatfilter annars kan matcha olika
instanser av samma verk oberoende av varandra.
"""

import streamlit as st
import requests
import re
import socket
import time
from urllib.parse import quote_plus

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


def bygg_arena_titelfraga(sokterm: str) -> str:
    """Bygger en Arena-specifik titelfältssökning (title_index/titleMain_index)
    för djuplänkar till bibliotekens egna kataloger, så att länken inte visar
    samma brus som en obegränsad fritextsökning skulle ge."""
    ord_lista = re.sub(r'["()]', "", sokterm).split()
    grupper = [f"(title_index:{ord} OR titleMain_index:{ord})" for ord in ord_lista]
    return " AND ".join(grupper)


def bygg_arena_forfattarfraga(forfattare: str) -> str:
    """Bygger en Arena-specifik författarsökning (author_index/contributor_index),
    enligt samma bekräftade mönster som titelsökningen."""
    ord_lista = re.sub(r'["()]', "", forfattare).split()
    grupper = [f"(author_index:{ord} OR contributor_index:{ord})" for ord in ord_lista]
    return " AND ".join(grupper)


def bygg_sokfraga(sokterm: str, soktyp: str, forfattare: str = "") -> str:
    """Bygger frågan UTAN bibliotek-filter - vi hämtar alla fysiska instanser
    och kontrollerar bestånd lokalt istället, för att undvika att bibliotek-
    och formatfilter matchar olika instanser av samma verk (se kommentar i
    sok_alla_bibliotek)."""
    sokterm_rensad = re.sub(r'["()]', "", sokterm)
    if soktyp == "ISBN":
        isbn_rensat = re.sub(r"[\s-]", "", sokterm_rensad)
        return f'isbn:({isbn_rensat}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    fraga = f'title:({sokterm_rensad}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    forfattare_rensad = re.sub(r'["()]', "", forfattare).strip()
    if forfattare_rensad:
        fraga += f' contributor:({forfattare_rensad})'
    return fraga


@st.cache_data(ttl=600, show_spinner=False)  # cachar identiska sökningar i 10 minuter
def sok_alla_bibliotek(sokterm: str, soktyp: str, forfattare: str = "", forsok: int = 3):
    """Gör ETT anrop mot Libris (istället för ett per bibliotek) och navigerar
    lokalt i verk -> instans -> exemplar-strukturen. Detta undviker det fel
    vi upptäckte där en kombinerad fråga (titel+format+bibliotek) kan matcha
    olika instanser av samma verk oberoende av varandra (t.ex. att biblioteket
    bara har e-boken, men frågan ändå gav träff eftersom NÅGON instans av
    verket är tryckt OCH NÅGON instans finns hos biblioteket - inte
    nödvändigtvis samma instans).

    Returnerar (träffar_per_kod, fel_per_kod) - träffar_per_kod räknar antal
    matchande exemplarposter (kan vara >1 om titeln inte är unik och flera
    orelaterade verk träffas - använd författarfältet för att undvika det)."""
    fraga = bygg_sokfraga(sokterm, soktyp, forfattare)

    senaste_fel = None
    data = None
    for försök_nr in range(1, forsok + 1):
        try:
            resp = requests.get(FIND_URL, params={"_q": fraga}, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            break
        except requests.exceptions.RequestException as e:
            senaste_fel = str(e)
            if försök_nr < forsok:
                time.sleep(2 * försök_nr)
        except ValueError:
            senaste_fel = "kunde inte tolka svaret"
            break

    traffar_per_kod = {kod: 0 for kod in SIGLAR}

    if data is None:
        # Anropet misslyckades helt - markera alla bibliotek med samma fel,
        # så användaren ser det tydligt istället för att allt bara visar "Finns ej"
        fel_per_kod = {kod: senaste_fel for kod in SIGLAR}
        return traffar_per_kod, fel_per_kod

    verk_lista = data.get("items", [])

    for verk in verk_lista:
        instanser = verk.get("@reverse", {}).get("instanceOf", [])
        for instans in instanser:
            if instans.get("@type") != "PhysicalResource":
                continue  # hoppa över e-böcker/ljudböcker etc.
            exemplar_lista = instans.get("@reverse", {}).get("itemOf", [])
            for exemplar in exemplar_lista:
                held_by = exemplar.get("heldBy", {})
                sigel_kandidater = {
                    held_by.get("@id", "").split("/")[-1],
                    held_by.get("isPartOf", {}).get("@id", "").split("/")[-1],
                }
                for kod, info in SIGLAR.items():
                    for sigel in info["sigler"]:
                        if sigel in sigel_kandidater:
                            traffar_per_kod[kod] += 1

    return traffar_per_kod, {}


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
            "💡 ISBN är att föredra när det finns tillgängligt – det ger säkrast "
            "träff. För bästa resultat vid titelsökning: sök på fullständig "
            "titel, gärna med författare om titeln är vanlig. Klicka på någon "
            "av de länkade biblioteken för att se lånestatus."
        )
    else:
        st.caption("💡 Klicka på någon av de länkade biblioteken för att se lånestatus.")
    sok_knapp = st.form_submit_button("Sök", type="primary")

if sok_knapp and sokterm.strip():
    sokterm = sokterm.strip()
    forfattare = forfattare.strip()

    with st.spinner("Söker..."):
        traffar_per_kod, fel_per_kod = sok_alla_bibliotek(sokterm, soktyp, forfattare)

    resultat = []
    for kod, info in SIGLAR.items():
        if soktyp == "ISBN":
            # ISBN hör inte hemma i ett titelfält - länka med rå sökterm istället
            arena_fraga = sokterm
        else:
            arena_fraga = bygg_arena_titelfraga(sokterm)
            if forfattare:
                arena_fraga = f"{bygg_arena_forfattarfraga(forfattare)} AND {arena_fraga}"
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
        "är inte sökbart och aktuell lånestatus visas inte. Dubbelkolla vid "
        "osäkerhet genom att klicka på biblioteket."
    )

elif sok_knapp:
    st.warning("Skriv in en sökterm.")
