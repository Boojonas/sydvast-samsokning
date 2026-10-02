"""
SAMSÖKNING NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API. Gör ETT anrop och navigerar
lokalt i verk -> instans -> exemplar-strukturen för att korrekt avgöra
vilka av de nio biblioteken som har boken i FYSISKT format.

Om sökningen ger flera olika verk (t.ex. en vanlig titel med flera olika
upphov) får användaren välja rätt bok i en lista, istället för att appen
bara gissar på det mest relevanta. Ger sökningen bara ETT verk visas
resultatet direkt, precis som innan - inget extra klick då.
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
    för djuplänkar till bibliotekens egna kataloger. mediaClass_index:book
    begränsar till tryckta böcker, så e-boksposter (separata katalogposter i
    Arena, till skillnad från Libris) inte dyker upp som en extra träff."""
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
    och formatfilter matchar olika instanser av samma verk."""
    sokterm_rensad = re.sub(r'["()]', "", sokterm)
    if soktyp == "ISBN":
        isbn_rensat = re.sub(r"[\s-]", "", sokterm_rensad)
        return f'isbn:({isbn_rensat}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    fraga = f'title:({sokterm_rensad}) instanceCategory:"https://id.kb.se/term/saobf/Print"'
    forfattare_rensad = re.sub(r'["()]', "", forfattare).strip()
    if forfattare_rensad:
        fraga += f' contributor:({forfattare_rensad})'
    return fraga


def forfattare_fran_verk(verk: dict):
    """Plockar ut en kommaseparerad författarlista från ett verk. Tålig mot
    varierande datastrukturer i Libris-svaret - t.ex. att 'agent' ibland är
    en lista med flera upphov istället för en enda post."""
    forfattare_lista = []
    for contrib in verk.get("contribution", []):
        if not isinstance(contrib, dict):
            continue
        agent = contrib.get("agent", {})
        agenter = agent if isinstance(agent, list) else [agent]
        for a in agenter:
            if not isinstance(a, dict):
                continue
            if a.get("givenName") or a.get("familyName"):
                forfattare_lista.append(
                    f"{a.get('givenName', '')} {a.get('familyName', '')}".strip()
                )
            elif a.get("name"):
                forfattare_lista.append(a["name"])
    return ", ".join(forfattare_lista) if forfattare_lista else None


def forsta_fysiska_instansen(verk: dict):
    """Returnerar den första PhysicalResource-instansen för ett verk, om någon."""
    for instans in verk.get("@reverse", {}).get("instanceOf", []):
        if instans.get("@type") == "PhysicalResource":
            return instans
    return None


def verk_etikett(verk: dict, sokterm: str) -> str:
    """Bygger en läsbar rad för valmenyn: 'Titel – Författare (år)'."""
    titel_info = verk.get("hasTitle", [{}])
    titel = titel_info[0].get("mainTitle", sokterm) if titel_info else sokterm
    forfattare = forfattare_fran_verk(verk)

    ar = None
    instans = forsta_fysiska_instansen(verk)
    if instans:
        publikationer = instans.get("publication", [])
        if publikationer:
            ar = publikationer[0].get("year")

    etikett = titel
    if forfattare:
        etikett += f" – {forfattare}"
    if ar:
        etikett += f" ({ar})"
    return etikett


def hamta_omslag_url(isbn: str):
    """Slår upp omslagsbild via Google Books API utifrån ISBN. Google Books
    är byggt för att tredjepartsappar ska kunna visa bilderna (till skillnad
    från Libris egna bilder, som gav 403 Forbidden vid direktlänkning), så
    vi länkar direkt till adressen istället för att hämta bilddata själva.
    Returnerar None tyst vid fel eller om omslag saknas - ingen bild är
    inte ett problem värt att larma om."""
    try:
        resp = requests.get(
            "https://www.googleapis.com/books/v1/volumes",
            params={"q": f"isbn:{isbn}"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        poster = data.get("items", [])
        if not poster:
            return None
        bild_lankar = poster[0].get("volumeInfo", {}).get("imageLinks", {})
        url = bild_lankar.get("thumbnail") or bild_lankar.get("smallThumbnail")
        if url:
            url = url.replace("http://", "https://")  # undvik blandat innehåll i webbläsaren
        return url
    except requests.exceptions.RequestException:
        return None


def extrahera_bokinfo(verk: dict, instans: dict, sokterm: str) -> dict:
    """Plockar ut titel, författare och sammanfattning från ett verk/instans-par.
    OBS: Libris bilder (dataset/images/...) går inte att hämta eller länka till
    direkt utifrån - både vårt eget anrop och en vanlig webbläsare får avslag
    (403 Forbidden) vid försök. Ingen bildvisning byggs därför in."""
    titel_info = verk.get("hasTitle", [{}])
    titel = titel_info[0].get("mainTitle", sokterm) if titel_info else sokterm

    sammanfattning = None
    summary_lista = instans.get("summary", [])
    if summary_lista:
        label = summary_lista[0].get("label")
        sammanfattning = label if isinstance(label, str) else (label[0] if label else None)

    isbn_lista = [i.get("value") for i in instans.get("identifiedBy", []) if i.get("value")]
    omslag_url = hamta_omslag_url(isbn_lista[0]) if isbn_lista else None

    return {
        "titel": titel,
        "forfattare": forfattare_fran_verk(verk),
        "sammanfattning": sammanfattning,
        "omslag_url": omslag_url,
    }


def berakna_bestand(verk: dict) -> dict:
    """Räknar antal matchande exemplarposter per bibliotek för ETT valt verk."""
    traffar_per_kod = {kod: 0 for kod in SIGLAR}
    instans = forsta_fysiska_instansen(verk)
    if not instans:
        return traffar_per_kod

    for exemplar in instans.get("@reverse", {}).get("itemOf", []):
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

    return traffar_per_kod


@st.cache_data(ttl=600, show_spinner=False)  # cachar identiska sökningar i 10 minuter
def hamta_verk_lista(sokterm: str, soktyp: str, forfattare: str = "", forsok: int = 3):
    """Gör ETT anrop mot Libris och returnerar (verk_lista, fel_per_kod).
    Själva uträkningen av bestånd görs separat (berakna_bestand), lokalt,
    utan ytterligare nätverksanrop - så ett bokval i gränssnittet kräver
    ingen ny sökning mot Libris."""
    fraga = bygg_sokfraga(sokterm, soktyp, forfattare)

    senaste_fel = None
    data = None
    for försök_nr in range(1, forsok + 1):
        try:
            # Libris standardgräns på 20 träffar per sökning - gott om
            # marginal för en enstaka titel, som normalt ger ett fåtal verk.
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

    if data is None:
        fel_per_kod = {kod: senaste_fel for kod in SIGLAR}
        return [], fel_per_kod

    return data.get("items", []), {}


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon="📚")

# --- Ökad kontrast ---
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

# Vid en ny sökning: hämta kandidatlistan och spara i session_state,
# så att ett eventuellt bokval senare inte kräver en ny Libris-sökning.
if sok_knapp and sokterm.strip():
    with st.spinner("Söker..."):
        verk_lista, fel_per_kod = hamta_verk_lista(
            sokterm.strip(), soktyp, forfattare.strip()
        )
    st.session_state["sok_verk_lista"] = verk_lista
    st.session_state["sok_fel_per_kod"] = fel_per_kod
    st.session_state["sok_sokterm"] = sokterm.strip()
    st.session_state["sok_soktyp"] = soktyp
    st.session_state["sok_forfattare"] = forfattare.strip()
    st.session_state["sok_valt_index"] = 0  # återställ val vid ny sökning

# Visa resultat om en sökning gjorts (ligger kvar i session_state mellan
# ett eventuellt bokval och nästa omritning av sidan)
if "sok_sokterm" in st.session_state:
    verk_lista = st.session_state["sok_verk_lista"]
    fel_per_kod = st.session_state["sok_fel_per_kod"]
    sokterm_vy = st.session_state["sok_sokterm"]
    soktyp_vy = st.session_state["sok_soktyp"]
    forfattare_vy = st.session_state["sok_forfattare"]

    valt_verk = None

    if fel_per_kod:
        pass  # inget verk att välja - felet visas längre ner via tabellen
    elif len(verk_lista) == 1:
        valt_verk = verk_lista[0]
    elif len(verk_lista) > 1:
        etiketter = [verk_etikett(v, sokterm_vy) for v in verk_lista]
        st.radio(
            "Flera böcker hittades – välj rätt:",
            options=range(len(verk_lista)),
            format_func=lambda i: etiketter[i],
            key="sok_valt_index",
        )
        valt_verk = verk_lista[st.session_state["sok_valt_index"]]

    bokinfo = None
    traffar_per_kod = {kod: 0 for kod in SIGLAR}

    if valt_verk is not None:
        instans = forsta_fysiska_instansen(valt_verk)
        if instans:
            bokinfo = extrahera_bokinfo(valt_verk, instans, sokterm_vy)
        traffar_per_kod = berakna_bestand(valt_verk)

    def visa_bokinfo_text():
        st.markdown(f"### {bokinfo['titel']}")
        if bokinfo["forfattare"]:
            st.markdown(f"**{bokinfo['forfattare']}**")
        if bokinfo["sammanfattning"]:
            sammanfattning = bokinfo["sammanfattning"]
            FORHANDSVISNING_LANGD = 220
            if len(sammanfattning) > FORHANDSVISNING_LANGD:
                sammanfattning = sammanfattning[:FORHANDSVISNING_LANGD].rsplit(" ", 1)[0] + " …"
            st.caption(sammanfattning)

    if bokinfo:
        if bokinfo.get("omslag_url"):
            kol_bild, kol_text = st.columns([1, 4])
            with kol_bild:
                st.image(bokinfo["omslag_url"], width=90)
            with kol_text:
                visa_bokinfo_text()
        else:
            visa_bokinfo_text()
    elif not fel_per_kod and soktyp_vy == "ISBN":
        st.warning(f"ISBN \"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera siffrorna.")
    elif not fel_per_kod:
        st.warning(
            f"\"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera stavning, "
            "eller pröva med ISBN."
        )

    resultat = []
    for kod, info in SIGLAR.items():
        if soktyp_vy == "ISBN":
            arena_fraga = sokterm_vy
        else:
            arena_fraga = bygg_arena_titelfraga(sokterm_vy)
            if forfattare_vy:
                arena_fraga = f"{bygg_arena_forfattarfraga(forfattare_vy)} AND {arena_fraga}"
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
        st.warning("Boken hittades inte hos något av de nio biblioteken.")

    st.caption(
        "Bygger på bibliotekens rapporterade bestånd i LIBRIS. Äldre bestånd "
        "är inte sökbart och aktuell lånestatus visas inte. Klicka på någon "
        "av de länkade biblioteken för att se lånestatus."
    )

elif sok_knapp:
    st.warning("Skriv in en sökterm.")
