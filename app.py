"""
SAMSÖK – NÄTVERKET SYDVÄST - Webbgränssnitt (Streamlit)

Sök boktitel eller ISBN mot LIBRIS öppna API. Gör ETT anrop och navigerar
lokalt i verk -> instans -> exemplar-strukturen för att korrekt avgöra
vilka av de nio biblioteken som har boken i FYSISKT format.

Om sökningen ger flera olika verk (t.ex. en vanlig titel med flera olika
upphov) får användaren välja rätt bok i en lista. Ger sökningen bara ETT
verk visas resultatet direkt.
"""

import html
from pathlib import Path
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


def ar_isbn(sokterm: str) -> bool:
    """Avgör om söktermen är ett ISBN: 10 tecken (sista får vara X) eller
    13 siffror, efter att bindestreck och mellanslag tagits bort. Korta
    sifferstitlar som "1984" räknas därmed som titelsökning."""
    rensad = re.sub(r"[\s-]", "", sokterm)
    return bool(re.fullmatch(r"\d{9}[\dXx]|\d{13}", rensad))


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


def md(text: str) -> str:
    """Skyddar tecken som annars tolkas som Markdown (t.ex. * och _ i titlar)."""
    return re.sub(r"([\\*_\[\]#|`<>])", r"\\\1", text)


def _lista(varde) -> list:
    """LIBRIS anger ibland ett enskilt objekt och ibland en lista - gör alltid lista."""
    if varde is None:
        return []
    return varde if isinstance(varde, list) else [varde]


def _text(varde):
    """Plockar ut en läsbar sträng ur LIBRIS varierande format: ren sträng,
    lista av strängar, språkmärkt värde ({'@value': ...}) eller objekt med
    'label'/'name'. Returnerar None om inget hittas."""
    if isinstance(varde, str):
        return varde.strip() or None
    if isinstance(varde, dict):
        for nyckel in ("@value", "label", "name", "code"):
            if nyckel in varde:
                return _text(varde[nyckel])
        return None
    if isinstance(varde, list):
        for v in varde:
            t = _text(v)
            if t:
                return t
    return None


def titel_fran(verk: dict, instans, reserv: str):
    """Huvudtitel och undertitel - i första hand från instansen (utgåvan),
    annars från verket."""
    for kalla in (instans or {}, verk):
        for t in _lista(kalla.get("hasTitle")):
            if isinstance(t, dict) and _text(t.get("mainTitle")):
                return _text(t.get("mainTitle")), _text(t.get("subtitle"))
    return reserv, None


def katalogpost(verk: dict, instans, sokterm: str) -> dict:
    """Bygger en bibliografisk beskrivning av utgåvan i ISBD-anda:
    Titel : undertitel / upphov. – Upplaga. – Ort : Förlag, år. – ISBN.
    Alla delar är valfria - saknas något i LIBRIS hoppas det över."""
    instans = instans or {}
    titel, undertitel = titel_fran(verk, instans, sokterm)

    upphov = _text(instans.get("responsibilityStatement")) or forfattare_fran_verk(verk)
    upplaga = _text(instans.get("editionStatement"))

    ort = forlag = ar = None
    publikationer = _lista(instans.get("publication"))
    if publikationer:
        pub = publikationer[0]
        ort = _text(pub.get("place"))
        forlag = _text(pub.get("agent"))
        ar = _text(pub.get("year")) or _text(pub.get("date"))

    isbn_lista = []
    for ident in _lista(instans.get("identifiedBy")):
        if isinstance(ident, dict) and ident.get("@type") == "ISBN":
            varde = _text(ident.get("value"))
            if varde and varde not in isbn_lista:
                isbn_lista.append(varde)

    post_id = instans.get("@id", "")
    post_url = post_id.split("#")[0] if post_id.startswith("http") else None

    return {
        "titel": titel, "undertitel": undertitel, "upphov": upphov,
        "upplaga": upplaga, "ort": ort, "forlag": forlag, "ar": ar,
        "isbn": isbn_lista, "post_url": post_url,
    }


def utgivning_text(post: dict):
    """'Ort : Förlag, år' - med de delar som finns."""
    delar = ""
    if post["ort"] and post["forlag"]:
        delar = f"{post['ort']} : {post['forlag']}"
    else:
        delar = post["ort"] or post["forlag"] or ""
    if post["ar"]:
        delar = f"{delar}, {post['ar']}" if delar else post["ar"]
    return delar or None


def beskrivning_text(post: dict) -> str:
    """Raden under titeln: 'Upphov. – Upplaga. – Ort : Förlag, år. – ISBN ...'"""
    delar = [post["upphov"], post["upplaga"], utgivning_text(post)]
    if post["isbn"]:
        delar.append("ISBN " + " ; ".join(post["isbn"]))
    delar = [d.rstrip(" .") for d in delar if d]
    return ". – ".join(delar) + ("." if delar else "")


def verk_etikett(verk: dict, sokterm: str) -> str:
    """Rad i träfflistan: 'Titel / Upphov. – Ort : Förlag, år'."""
    post = katalogpost(verk, forsta_fysiska_instansen(verk), sokterm)
    etikett = post["titel"]
    if post["upphov"]:
        etikett += f" / {post['upphov']}"
    utgivning = utgivning_text(post)
    if utgivning:
        etikett += f". – {utgivning}"
    return etikett


def bibliotek_med_bestand(verk: dict) -> set:
    """Returnerar koderna (nycklar i SIGLAR) för de bibliotek som har bestånd
    av den första tryckta utgåvan av ETT valt verk."""
    med_bestand = set()
    instans = forsta_fysiska_instansen(verk)
    if not instans:
        return med_bestand

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
            if any(sigel in sigel_kandidater for sigel in info["sigler"]):
                med_bestand.add(kod)

    return med_bestand


@st.cache_data(ttl=600, show_spinner=False)  # cachar identiska sökningar i 10 minuter
def hamta_verk_lista(sokterm: str, soktyp: str, forfattare: str = "", forsok: int = 3):
    """Gör ETT anrop mot Libris och returnerar (verk_lista, felmeddelande).
    felmeddelande är None om anropet lyckades.
    Själva kontrollen av bestånd görs separat (bibliotek_med_bestand), lokalt,
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
        return [], senaste_fel or "okänt fel"

    return data.get("items", []), None


# ---------------------------------------------------------------------------
# WEBBGRÄNSSNITT
# ---------------------------------------------------------------------------
# Bokikonen (Googles Noto Emoji, Apache 2.0) används bara i webbläsarfliken.
IKON_SOKVAG = Path(__file__).parent / "bilder" / "bocker.png"

st.set_page_config(page_title="Samsök – Nätverket Sydväst", page_icon=str(IKON_SOKVAG))

# --- Utseende (appen har ett fast ljust tema, se .streamlit/config.toml) ---
st.markdown(
    """
    <style>
    /* --- Beståndstabellen --- */
    .bestand {
        --linje: rgba(128, 128, 128, 0.22);
        --rubrik: rgba(110, 110, 120, 1);
        width: 100%;
        border: 1px solid var(--linje);
        border-radius: 0.6rem;
        overflow: hidden;
        margin: 0.5rem 0 1rem 0;
    }
    .bestand table {
        width: 100%;
        border-collapse: collapse;
        margin: 0 !important;
        border: none !important;
    }
    .bestand th, .bestand td {
        border: none !important;
        text-align: left;
    }
    .bestand th {
        font-size: 0.75rem !important;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--rubrik) !important;
        padding: 0.75rem 1.25rem !important;
        border-bottom: 1px solid var(--linje) !important;
    }
    .bestand td {
        font-size: 1.05rem !important;
        padding: 0.8rem 1.25rem !important;
    }
    .bestand tr + tr td {
        border-top: 1px solid var(--linje) !important;
    }
    .bestand td a {
        font-weight: 600;
        text-decoration: none;
    }
    .bestand td a:hover { text-decoration: underline; }
    .bestand td:last-child, .bestand th:last-child { text-align: right !important; }

    /* Statusetiketter */
    .etikett {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.2rem 0.7rem;
        border-radius: 999px;
        font-size: 0.9rem;
        font-weight: 600;
        white-space: nowrap;
    }
    .etikett::before {
        content: "";
        width: 0.5rem; height: 0.5rem;
        border-radius: 50%;
        background: currentColor;
    }
    /* Dämpad grön för "Finns" (lugnare mot den blå sidan),
       "Finns ej" tonas ner så att det som finns sticker ut */
    .etikett.finns { background: #e6f2ec; color: #276749; }
    .etikett.saknas { background: transparent; color: #8a94a3; }
    .etikett.saknas::before { background: #c5ccd6; }

    /* Sammanfattning överst i tabellens ram */
    .bestand-topp {
        padding: 0.9rem 1.25rem;
        font-size: 1.1rem;
        color: #1a365d;
        background: #f8fafc;
        border-bottom: 1px solid var(--linje);
    }
    .bestand-topp strong { font-weight: 700; }

    /* --- Sidhuvud och ingress --- */
    .sidhuvud { margin: 0.5rem 0 0.75rem 0; }
    .sidhuvud-titel {
        font-size: 3.25rem;
        font-weight: 700;
        line-height: 1.1;
        letter-spacing: -0.01em;
        color: #1a365d;
    }
    .sidhuvud-undertitel {
        font-size: 1.25rem;
        font-weight: 500;
        line-height: 1.3;
        margin-top: 0.2rem;
        color: #4a5a70;
    }
    .ingress {
        font-size: 1.1rem;
        line-height: 1.5;
        margin-bottom: 0.75rem;
        color: #5b6675;
    }

    /* --- Sökformuläret: lätt tonad bakgrund, högre fält, bredare knapp --- */
    [data-testid="stForm"] {
        background: #f8fafc;
    }
    [data-testid="stForm"] [data-testid="stTextInputRootElement"] {
        height: 46px;
    }
    /* Vita fält mot den tonade bakgrunden, så att de lyfts fram */
    [data-testid="stForm"] [data-baseweb="input"],
    [data-testid="stForm"] [data-baseweb="base-input"],
    [data-testid="stForm"] input {
        background-color: #ffffff !important;
    }
    [data-testid="stForm"] [data-baseweb="input"] {
        border: 1px solid #d5dce5 !important;
    }
    [data-testid="stForm"] input {
        font-size: 1.05rem !important;
    }
    [data-testid="stFormSubmitButton"] button {
        padding-left: 17px !important;
        padding-right: 17px !important;
    }

    /* Tydligare platshållartext i sökfälten */
    input::placeholder { color: #4a4a4a !important; opacity: 1 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)
# --- Slut utseende ---

# --- Enkelt lösenordsskydd ---
def hamta_losenord():
    """Lösenordet hämtas från Streamlits secrets (app_losenord). Finns det inget
    lösenord inlagt är appen öppen för alla."""
    try:
        return st.secrets.get("app_losenord") or None
    except Exception:  # ingen secrets-fil alls
        return None


def kolla_losenord():
    losenord = hamta_losenord()
    if not losenord:
        return True

    def losenord_angivet():
        if st.session_state.get("losenord_falt") == losenord:
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

# Rubrik i två nivåer: tjänstens namn dominerar, nätverket är sekundärt
st.markdown(
    '<div class="sidhuvud">'
    '<div class="sidhuvud-titel">Samsök</div>'
    '<div class="sidhuvud-undertitel">Nätverket Sydväst</div>'
    "</div>",
    unsafe_allow_html=True,
)
# Ingress: brödtextstorlek, dämpad färg
st.markdown(
    '<p class="ingress">'
    "Gemensam sökfunktion för biblioteken i fjärrlånesamarbetet. Bygger på "
    "bibliotekens rapporterade bestånd av tryckta titlar i LIBRIS.</p>",
    unsafe_allow_html=True,
)

with st.form("sok_form"):
    sokterm = st.text_input(
        "Sökterm", placeholder="Titel eller ISBN", label_visibility="collapsed",
    )
    forfattare = st.text_input(
        "Filtrera på författare", placeholder="Filtrera på författare",
        label_visibility="collapsed",
    )
    sok_knapp = st.form_submit_button("Sök", type="primary")

# Vid en ny sökning: hämta kandidatlistan och spara i session_state,
# så att ett eventuellt bokval senare inte kräver en ny Libris-sökning.
if sok_knapp and sokterm.strip():
    # Appen avgör själv om söktermen är ett ISBN eller en titel
    soktyp = "ISBN" if ar_isbn(sokterm) else "Titel"
    if soktyp == "ISBN":
        forfattare = ""
    with st.spinner("Söker..."):
        verk_lista, fel = hamta_verk_lista(
            sokterm.strip(), soktyp, forfattare.strip()
        )
    st.session_state["sok_verk_lista"] = verk_lista
    st.session_state["sok_fel"] = fel
    st.session_state["sok_sokterm"] = sokterm.strip()
    st.session_state["sok_soktyp"] = soktyp
    st.session_state["sok_forfattare"] = forfattare.strip()
    st.session_state["sok_valt_index"] = 0  # återställ val vid ny sökning

# Visa resultat om en sökning gjorts (ligger kvar i session_state mellan
# ett eventuellt bokval och nästa omritning av sidan)
if "sok_sokterm" in st.session_state:
    verk_lista = st.session_state["sok_verk_lista"]
    fel = st.session_state.get("sok_fel")
    sokterm_vy = st.session_state["sok_sokterm"]
    soktyp_vy = st.session_state["sok_soktyp"]
    forfattare_vy = st.session_state["sok_forfattare"]

    valt_verk = None

    if fel:
        st.error(
            "Kunde inte nå LIBRIS just nu. Försök igen om en stund."
        )
        with st.expander("Felmeddelande (för felsökning)"):
            st.code(fel, language=None)
        st.stop()

    if len(verk_lista) == 1:
        valt_verk = verk_lista[0]
    elif len(verk_lista) > 1:
        etiketter = [verk_etikett(v, sokterm_vy) for v in verk_lista]
        st.radio(
            "Flera poster matchar. Välj ett alternativ i listan nedan.",
            options=range(len(verk_lista)),
            format_func=lambda i: etiketter[i],
            key="sok_valt_index",
        )
        valt_verk = verk_lista[st.session_state["sok_valt_index"]]

    post = None
    med_bestand = set()

    if valt_verk is not None:
        instans = forsta_fysiska_instansen(valt_verk)
        if instans:
            post = katalogpost(valt_verk, instans, sokterm_vy)
        med_bestand = bibliotek_med_bestand(valt_verk)

    if post:
        rubrik = md(post["titel"])
        if post["undertitel"]:
            rubrik += f" : {md(post['undertitel'])}"
        st.markdown(f"### {rubrik}")
        st.markdown(md(beskrivning_text(post)))
        if post["post_url"]:
            st.markdown(f"[Visa posten i LIBRIS ↗]({post['post_url']})")
    elif soktyp_vy == "ISBN":
        st.warning(f"ISBN \"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera siffrorna.")
    else:
        st.warning(
            f"\"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera stavning, "
            "eller pröva med ISBN."
        )

    rader = []
    for kod, info in SIGLAR.items():
        if soktyp_vy == "ISBN":
            arena_fraga = sokterm_vy
        else:
            arena_fraga = bygg_arena_titelfraga(sokterm_vy)
            if forfattare_vy:
                arena_fraga = f"{bygg_arena_forfattarfraga(forfattare_vy)} AND {arena_fraga}"
        sok_lank = info["sok_url"].format(query=quote_plus(arena_fraga))
        kommun_lankad = (
            f'<a href="{html.escape(sok_lank, quote=True)}" target="_blank">'
            f"{html.escape(info['namn'])}</a>"
        )
        if kod in med_bestand:
            status = '<span class="etikett finns">Finns</span>'
        else:
            status = '<span class="etikett saknas">Finns ej</span>'
        rader.append(f"<tr><td>{kommun_lankad}</td><td>{status}</td></tr>")

    # Sammanfattningen står överst i tabellens ram i stället för i en egen
    # färgad ruta, så att resultatet hålls ihop och följer sidans färger
    antal_traffar = len(med_bestand)
    if antal_traffar > 0:
        sammanfattning = (
            f"Finns hos <strong>{antal_traffar}</strong> av {len(SIGLAR)} bibliotek"
        )
    elif post:
        sammanfattning = "Finns inte hos något av biblioteken"
    else:
        sammanfattning = ""
    topp = f'<div class="bestand-topp">{sammanfattning}</div>' if sammanfattning else ""

    st.markdown(
        f'<div class="bestand">{topp}<table>'
        "<thead><tr><th>Bibliotek</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rader)}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    # Vägledande text under resultatet: vanlig brödtextstorlek
    st.markdown("Klicka på något av de länkade biblioteken för att se lånestatus.")

elif sok_knapp:
    st.warning("Skriv in en sökterm.")

# ---------------------------------------------------------------------------
# OM TJÄNSTEN - kort information för användaren längst ner på sidan
# ---------------------------------------------------------------------------
st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
with st.expander("Om tjänsten"):
    st.markdown(
        "Samsök visar vilka av de nio folkbiblioteken i fjärrlånesamarbetet "
        "som har en viss tryckt titel. Den bygger på det bestånd som biblioteken "
        "rapporterat till LIBRIS.\n\n"
        "**Varför kan ett bibliotek visas med Finns ej fast titeln står på hyllan?**\n"
        "- Äldre bestånd som aldrig förts in i LIBRIS syns inte.\n"
        "- LIBRIS håller isär olika utgåvor, till exempel pocket och inbunden. "
        "Har biblioteket en annan utgåva än den som visas kan det stå Finns ej. "
        "Bibliotekens egna kataloger slår ofta ihop utgåvorna.\n\n"
        "**Bara tryckta titlar.** E-böcker, ljudböcker och andra medier ingår inte.\n\n"
        "**Lånestatus** syns inte här. Klicka på bibliotekets namn för att se den "
        "i bibliotekets egen katalog.\n\n"
        "[Källkod och teknisk beskrivning på GitHub ↗]"
        "(https://github.com/Boojonas/sydvast-samsokning)"
    )
