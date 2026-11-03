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

import base64
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
    Alla delar är valfria - saknas något i LIBRIS hoppas det över.
    OBS: Libris bilder (dataset/images/...) går inte att hämta eller länka till
    direkt utifrån (403 Forbidden). Ingen bildvisning byggs därför in."""
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
# Bokikonen kommer från Googles Noto Emoji (Apache 2.0) och används som bild,
# så att den ser likadan ut oavsett om besökaren har Windows, Mac eller mobil.
IKON_SOKVAG = Path(__file__).parent / "bilder" / "bocker.png"
IKON_BASE64 = base64.b64encode(IKON_SOKVAG.read_bytes()).decode()

st.set_page_config(page_title="Samsökning Nätverket Sydväst", page_icon=str(IKON_SOKVAG))

# --- Ökad kontrast ---
st.markdown(
    """
    <style>
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] * {
        opacity: 1 !important;
        font-size: 0.95rem !important;
    }
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
    .etikett.finns { background: rgba(22, 163, 74, 0.14); color: #15803d; }
    .etikett.saknas { background: rgba(128, 128, 128, 0.14); color: #5f6368; }
    .etikett.fel { background: rgba(217, 119, 6, 0.16); color: #b45309; }

    @media (prefers-color-scheme: light) {
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] * { color: #1a1a1a !important; }
        input::placeholder { color: #4a4a4a !important; opacity: 1 !important; }
    }
    @media (prefers-color-scheme: dark) {
        [data-testid="stCaptionContainer"],
        [data-testid="stCaptionContainer"] * { color: #f2f2f2 !important; }
        input::placeholder { color: #c8c8c8 !important; opacity: 1 !important; }
        .bestand { --rubrik: rgba(170, 170, 180, 1); }
        .etikett.finns { background: rgba(34, 197, 94, 0.18); color: #4ade80; }
        .etikett.saknas { background: rgba(160, 160, 160, 0.16); color: #b0b3b8; }
        .etikett.fel { background: rgba(245, 158, 11, 0.18); color: #fbbf24; }
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

st.markdown(
    "<h1>"
    f'<img src="data:image/png;base64,{IKON_BASE64}" alt="" '
    'style="height:1.1em; width:auto; vertical-align:-0.18em; margin-right:0.45em;">'
    "Samsökning – Nätverket Sydväst</h1>",
    unsafe_allow_html=True,
)
# Ingress: något större än brödtext, normal vikt
st.markdown(
    '<p style="font-size:1.2rem; line-height:1.5; margin-bottom:0.5rem;">'
    "Gemensam sökfunktion för biblioteken i fjärrlånesamarbetet. Bygger på "
    "bibliotekens rapporterade bestånd av tryckta titlar i LIBRIS.</p>",
    unsafe_allow_html=True,
)

with st.form("sok_form"):
    sokterm = st.text_input(
        "Sökterm", placeholder="Titel eller ISBN", label_visibility="collapsed",
    )
    forfattare = st.text_input(
        "Författare (valfritt)", placeholder="Författare (valfritt)",
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
            "Flera poster matchar. Välj ett alternativ i listan nedan.",
            options=range(len(verk_lista)),
            format_func=lambda i: etiketter[i],
            key="sok_valt_index",
        )
        valt_verk = verk_lista[st.session_state["sok_valt_index"]]

    post = None
    traffar_per_kod = {kod: 0 for kod in SIGLAR}

    if valt_verk is not None:
        instans = forsta_fysiska_instansen(valt_verk)
        if instans:
            post = katalogpost(valt_verk, instans, sokterm_vy)
        traffar_per_kod = berakna_bestand(valt_verk)

    if post:
        rubrik = md(post["titel"])
        if post["undertitel"]:
            rubrik += f" : {md(post['undertitel'])}"
        st.markdown(f"### {rubrik}")
        st.markdown(md(beskrivning_text(post)))
        if post["post_url"]:
            st.markdown(f"[Visa posten i LIBRIS ↗]({post['post_url']})")
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
        kommun_lankad = (
            f'<a href="{html.escape(sok_lank, quote=True)}" target="_blank">'
            f"{html.escape(info['namn'])}</a>"
        )
        if kod in fel_per_kod:
            status, ordning = '<span class="etikett fel">Fel</span>', 1
        elif traffar_per_kod[kod] > 0:
            status, ordning = '<span class="etikett finns">Finns</span>', 0
        else:
            status, ordning = '<span class="etikett saknas">Finns ej</span>', 2
        resultat.append({"Bibliotek": kommun_lankad, "Status": status, "ordning": ordning})

    rader = "".join(
        f"<tr><td>{r['Bibliotek']}</td><td>{r['Status']}</td></tr>" for r in resultat
    )
    st.markdown(
        '<div class="bestand"><table>'
        "<thead><tr><th>Bibliotek</th><th>Status</th></tr></thead>"
        f"<tbody>{rader}</tbody></table></div>",
        unsafe_allow_html=True,
    )

    if fel_per_kod:
        with st.expander("⚠️ Se felmeddelanden (för felsökning)"):
            for kod, felmeddelande in fel_per_kod.items():
                st.write(f"**{SIGLAR[kod]['namn']}**: {felmeddelande}")

    antal_traffar = sum(1 for r in resultat if r["ordning"] == 0)
    if antal_traffar > 0:
        st.success(f"Finns hos {antal_traffar} av {len(SIGLAR)} bibliotek.")
    elif post:
        st.warning("Finns inte hos något av biblioteken.")

    # Vägledande text under resultatet: vanlig brödtextstorlek
    st.markdown("Klicka på något av de länkade biblioteken för att se lånestatus.")

elif sok_knapp:
    st.warning("Skriv in en sökterm.")
