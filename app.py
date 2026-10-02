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
    samma brus som en obegränsad fritextsökning skulle ge. mediaClass_index:book
    begränsar till tryckta böcker, så e-boksposter (separata katalogposter i
    Arena, till skillnad från Libris) inte dyker upp som en extra, förvirrande
    träff bredvid den tryckta."""
    ord_lista = re.sub(r'["()]', "", sokterm).split()
    grupper = [f"(title_index:{ord} OR titleMain_index:{ord})" for ord in ord_lista]
    return "mediaClass_index:book AND " + " AND ".join(grupper)


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


def extrahera_bokinfo(verk: dict, instans: dict, sokterm: str) -> dict:
    """Plockar ut titel, författare och sammanfattning från ett verk/instans-par.
    OBS: Libris bilder (dataset/images/...) går inte att hämta eller länka till
    direkt utifrån - både vårt eget anrop och en vanlig webbläsare får avslag
    (403 Forbidden) vid försök. Ingen bildvisning byggs därför in."""
    titel_info = verk.get("hasTitle", [{}])
    titel = titel_info[0].get("mainTitle", sokterm) if titel_info else sokterm

    forfattare_lista = []
    for contrib in verk.get("contribution", []):
        agent = contrib.get("agent", {})
        if agent.get("givenName") or agent.get("familyName"):
            forfattare_lista.append(
                f"{agent.get('givenName', '')} {agent.get('familyName', '')}".strip()
            )
        elif agent.get("name"):
            forfattare_lista.append(agent["name"])

    sammanfattning = None
    summary_lista = instans.get("summary", [])
    if summary_lista:
        label = summary_lista[0].get("label")
        sammanfattning = label if isinstance(label, str) else (label[0] if label else None)

    return {
        "titel": titel,
        "forfattare": ", ".join(forfattare_lista) if forfattare_lista else None,
        "sammanfattning": sammanfattning,
    }


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
    bokinfo = None
    bokinfo_reserv = None  # bokinfo oavsett bibliotek-träff, som fallback

    if data is None:
        # Anropet misslyckades helt - markera alla bibliotek med samma fel,
        # så användaren ser det tydligt istället för att allt bara visar "Finns ej"
        fel_per_kod = {kod: senaste_fel for kod in SIGLAR}
        return traffar_per_kod, fel_per_kod, bokinfo

    verk_lista = data.get("items", [])

    for verk in verk_lista:
        verk_traff_innan = sum(traffar_per_kod.values())
        instanser = verk.get("@reverse", {}).get("instanceOf", [])
        for instans in instanser:
            if instans.get("@type") != "PhysicalResource":
                continue  # hoppa över e-böcker/ljudböcker etc.
            exemplar_lista = instans.get("@reverse", {}).get("itemOf", [])
            for exemplar in exemplar_lista:
                held_by = exemplar.get("heldBy", {})
                eget_bibliotek_id = held_by.get("@id", "").split("/")[-1]
                if eget_bibliotek_id.startswith("7"):
                    # Skolbibliotek (vedertagen sigel-konvention) - räknas inte,
                    # eftersom fjärrlån inte kan göras därifrån
                    continue
                sigel_kandidater = {
                    eget_bibliotek_id,
                    held_by.get("isPartOf", {}).get("@id", "").split("/")[-1],
                }
                for kod, info in SIGLAR.items():
                    for sigel in info["sigler"]:
                        if sigel in sigel_kandidater:
                            traffar_per_kod[kod] += 1

            # Fånga alltid en reservversion av bokinfo från första fysiska
            # instans vi ser, oavsett om den gav bibliotek-träff - så vi kan
            # visa titel/författare även om inget av våra nio bibliotek har
            # boken (men den ändå finns i Libris).
            if bokinfo_reserv is None:
                bokinfo_reserv = extrahera_bokinfo(verk, instans, sokterm)

            # Om just DET HÄR verket gav minst en träff och vi inte redan
            # har plockat ut bokinfo, använd dess info - undviker att visa
            # fel bok vid tvetydiga titlar med flera verk
            if bokinfo is None and sum(traffar_per_kod.values()) > verk_traff_innan:
                bokinfo = extrahera_bokinfo(verk, instans, sokterm)

    if bokinfo is None:
        bokinfo = bokinfo_reserv

    return traffar_per_kod, {}, bokinfo


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon="📚")

# --- Ökad kontrast ---
# Streamlits standardtext för hjälptexter (captions) är ljusgrå och svårläst
# på stora skärmar. Färgerna följer webbläsarens/systemets ljust/mörkt-läge.
st.markdown(
    """
    <style>
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {
        opacity: 1 !important;
        font-size: 0.95rem !important;
    }
    [data-testid="stMarkdownContainer"] table th,
    [data-testid="stMarkdownContainer"] table td {
        border: 1px solid #888 !important;
    }
    @media (prefers-color-scheme: light) {
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] * { color: #1a1a1a !important; }
        input::placeholder { color: #4a4a4a !important; opacity: 1 !important; }
        [data-testid="stMarkdownContainer"] table th,
        [data-testid="stMarkdownContainer"] table td { color: #111111 !important; }
    }
    @media (prefers-color-scheme: dark) {
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] * { color: #f2f2f2 !important; }
        input::placeholder { color: #c8c8c8 !important; opacity: 1 !important; }
        [data-testid="stMarkdownContainer"] table th,
        [data-testid="stMarkdownContainer"] table td { color: #ffffff !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)
# --- Slut kontrast ---

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
            "titel, gärna med författare om titeln är vanlig."
        )
    sok_knapp = st.form_submit_button("Sök", type="primary")

if sok_knapp and sokterm.strip():
    sokterm = sokterm.strip()
    forfattare = forfattare.strip()

    with st.spinner("Söker..."):
        traffar_per_kod, fel_per_kod, bokinfo = sok_alla_bibliotek(sokterm, soktyp, forfattare)

    if bokinfo:
        st.markdown(f"### {bokinfo['titel']}")
        if bokinfo["forfattare"]:
            st.markdown(f"**{bokinfo['forfattare']}**")
        if bokinfo["sammanfattning"]:
            sammanfattning = bokinfo["sammanfattning"]
            FORHANDSVISNING_LANGD = 220
            if len(sammanfattning) > FORHANDSVISNING_LANGD:
                sammanfattning = sammanfattning[:FORHANDSVISNING_LANGD].rsplit(" ", 1)[0] + " …"
            st.caption(sammanfattning)
    elif soktyp == "ISBN":
        st.warning(f"ISBN \"{sokterm}\" hittades inte i LIBRIS. Kontrollera siffrorna.")
    else:
        st.warning(
            f"\"{sokterm}\" hittades inte i LIBRIS. Kontrollera stavning, "
            "eller pröva med ISBN."
        )

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
    if antal_traffar > 0:
        st.success(f"Boken finns hos {antal_traffar} av 9 bibliotek.")
    elif bokinfo:
        # bokinfo finns (titeln är känd i LIBRIS) men inget av våra nio har den -
        # meddelandet ovanför täcker redan fallet att titeln inte hittades alls
        st.warning("Boken hittades inte hos något av de nio biblioteken.")

    st.caption(
        "Bygger på bibliotekens rapporterade bestånd i LIBRIS. Äldre bestånd "
        "är inte sökbart och aktuell lånestatus visas inte. Klicka på någon "
        "av de länkade biblioteken för att se lånestatus."
    )

elif sok_knapp:
    st.warning("Skriv in en sökterm.")
