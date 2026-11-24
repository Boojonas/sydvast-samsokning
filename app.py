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
from datetime import date
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
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
        # Kommunens fyra folkbibliotek: Vellinge, Höllviken, Skanör, Västra Ingelstad
        "namn": "Vellinge", "sigler": ["Vell", "Holl", "Skan", "Vib"],
        # Libris lånestatus i BOOK-IT (schema bookit33). Ett anrop per enhet;
        # tjänsten kräver bib_id men hittar också exemplar via ISBN.
        "lanestatus": {
            "url": (
                "https://vellingebibliotek.axiell.com/services/rest/libris/holding"
                "?ds=jdbc/bookit33&sigel={sigel}&isbn={isbn}&bib_id={bib_id}"
            ),
            "enheter": {"Holl": "Höllviken", "Skan": "Skanör",
                        "Vell": "Vellinge", "Vib": "Västra Ingelstad"},
        },
        "bestall_i_katalogen": True,  # fjärrlån beställs i Vellinges egen katalog
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
    begränsar till tryckt material, så e-boksposter (separata katalogposter i
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


def behall_sortering():
    """Klick på redan valt sorteringsalternativ avmarkerar det i Streamlit;
    behåll då det tidigare valet så att ett alternativ alltid är aktivt."""
    if st.session_state.get("sok_sortering") is None:
        st.session_state["sok_sortering"] = (
            "År" if st.session_state.get("sok_forra_sortering") == "Nyast först" else "Relevans"
        )


def sorteringsetikett(alternativ: str) -> str:
    """Valt sorteringsalternativ visas i blått och fetstil med pil, det
    andra som vanlig text (Markdown i etiketten, så att det inte hänger på
    Streamlits interna CSS-namn)."""
    if st.session_state.get("sok_sortering", "Relevans") == alternativ:
        return f":blue[**{alternativ} ↓**]"
    return alternativ


def utgivningsar(verk: dict) -> int:
    """Utgivningsåret för verkets första tryckta utgåva som tal, för
    sortering. Saknas år (eller går det inte att tolka) blir det 0."""
    ar = katalogpost(verk, forsta_fysiska_instansen(verk), "")["ar"] or ""
    traff = re.search(r"\d{4}", ar)
    return int(traff.group(0)) if traff else 0


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


# ---------------------------------------------------------------------------
# LUNDS UNIVERSITETSBIBLIOTEK - nästa steg i fjärrlånekedjan
# ---------------------------------------------------------------------------
# Lunds bibliotek med egen sigel i Libris (digitala resurser, Ldig/Ldix,
# är utelämnade eftersom appen bara söker tryckt material).
LUND_SIGLER = {
    "L": "UB",
    "Lsam": "Sambib",
    "Lux": "LUX-biblioteket",
    "Lsol": "SOL-biblioteket",
    "Luan": "Asienbiblioteket",
    "Lbio": "Biologibiblioteket",
    "Lgeo": "Geobiblioteket",
    "Lfa": "Fysik- och astronomibiblioteket",
    "Lma": "Matematiska biblioteket",
    "Lkc": "Kemicentrums bibliotek",
    "Lehl": "Ekonomihögskolans bibliotek",
    "Lju": "Juridiska fakultetens bibliotek",
    "Lmel": "Medicinska fakulteten, Lund",
    "Lmem": "Medicinska fakulteten, Malmö",
    "Lte": "LTH Bibliotek",
    "Ltv": "LTH Bibliotek",
    "Laib": "LTH Bibliotek",
    "Lth": "LTH bibliotek, Studiecentrum",
    "Ltm": "M-husets bibliotek",
    "Lhel": "Campus Helsingborg, biblioteket",
    "LMu": "Konstnärliga fakulteten, Musik",
    "Lkmt": "Konstnärliga fakulteten, Konst/Teater",
    "Lrwi": "Raoul Wallenberginstitutet",
    "Lenv": "Internationella miljöinstitutet",
}

# Lånestatustjänster enligt KB:s format för "Libris lånestatus" (adresserna
# är registrerade i Libris biblioteksdatabas). {bib_id} = postens
# kontrollnummer i Libris, {isbn} = ISBN, {sigel} = bibliotekets sigel.
# Varje sigel returnerar bara sina egna exemplar.
LUND_LANESTATUS_URL = (
    "https://lubcat.lub.lu.se/cgi-bin/koha/lutil/libris_item_search.pl"
    "?Bib_ID={bib_id}&BIBL={sigel}"
)
DEPA_LANESTATUS_URL = (
    "https://axiell.io/api/quria/eu-a/libris-availability/quria-release/"
    "integrations/ncip/6da61cdf-c2af-4bef-beab-5a3f488e45e3/libris-availability"
    "?sigel={sigel}&isbn={isbn}&bibid={bib_id}"
)
LANESTATUS_TIDSGRANS = 5  # sekunder per anrop

# Nästa steg i fjärrlånekedjan, i den ordning de visas under huvudtabellen.
# "visa_namn": skriv ut vilka av bibliotekens enheter som har titeln
# (meningsfullt för Lund med många sigler, inte för en enda sigel).
NASTA_STEG = [
    {
        "nyckel": "lund",
        "rubrik": "Lunds universitetsbibliotek",
        "sigler": LUND_SIGLER,
        "url": LUND_LANESTATUS_URL,
        "visa_namn": True,
        "markera_kurs": True,  # Lunds kursböcker fjärrlånas i regel inte
        # Länkmallar till posten i Lunds katalog Finn, registrerade i Libris
        # biblioteksdatabas: i första hand via kontrollnummer, annars ISBN.
        "katalog": ("Finn",
                    "https://finn.lub.lu.se/libris/bibid/(LIBRIS){bib_id}",
                    "https://finn.lub.lu.se/libris/isbn/{isbn}"),
    },
    {
        "nyckel": "depa",
        "rubrik": "Sveriges depåbibliotek och lånecentral",
        "sigler": {"Umdp": "Depåbiblioteket"},
        "url": DEPA_LANESTATUS_URL,
        "visa_namn": False,
        "katalog": None,  # Depåbiblioteket har ingen egen publik katalog
    },
]

MANADER = ["jan", "feb", "mar", "apr", "maj", "jun",
           "jul", "aug", "sep", "okt", "nov", "dec"]


def sigler_med_bestand(verk: dict, sigler: dict) -> list:
    """Returnerar de sigler (nycklar i `sigler`) som har bestånd av den första
    tryckta utgåvan, i ordningen i `sigler`. Bara bibliotekets egen sigel
    räknas (inte överordnad organisation), så att t.ex. Lunds UB inte visas
    när bara ett fakultetsbibliotek har titeln."""
    instans = forsta_fysiska_instansen(verk)
    if not instans:
        return []
    egna = set()
    for exemplar in instans.get("@reverse", {}).get("itemOf", []):
        egna.add(exemplar.get("heldBy", {}).get("@id", "").split("/")[-1])
    return [sigel for sigel in sigler if sigel in egna]


def ar_kontrollnummer(varde: str) -> bool:
    """Libris kontrollnummer är antingen siffror (äldre poster, t.ex. 11449150)
    eller en bokstavs- och sifferkod (poster skapade i nya Libris, t.ex.
    whcb2rntth1cn4jd). Lånestatustjänsterna tar emot båda."""
    return bool(re.fullmatch(r"[0-9a-z]+", varde))


def libris_kontrollnummer(instans) -> str | None:
    """Plockar ut postens kontrollnummer i Libris (det lånestatustjänsterna
    kallar Bib_ID). Det kan ligga på några olika ställen i svaret."""
    if not instans:
        return None
    meta = instans.get("meta") or {}
    kandidat = _text(meta.get("controlNumber")) if isinstance(meta, dict) else None
    if kandidat and ar_kontrollnummer(kandidat):
        return kandidat
    for lank in _lista(instans.get("sameAs")) + _lista(meta.get("sameAs") if isinstance(meta, dict) else None):
        lank_id = lank.get("@id", "") if isinstance(lank, dict) else str(lank)
        traff = re.search(r"/bib/(\d+)", lank_id)
        if traff:
            return traff.group(1)
    return None


@st.cache_data(ttl=86400, show_spinner=False)
def hamta_kontrollnummer_fran_post(post_url: str):
    """Hämtar kontrollnumret ur hela Libris-posten (<post>/data.jsonld), för
    när det inte finns med i sökresultatet. Numret ändras inte, så det
    sparas ett dygn."""
    try:
        resp = requests.get(f"{post_url.rstrip('/')}/data.jsonld", headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return None
    for nod in _lista(data.get("@graph")):
        if isinstance(nod, dict):
            nummer = _text(nod.get("controlNumber"))
            if nummer and ar_kontrollnummer(nummer):
                return nummer
    return None


def kontrollnummer_for(instans) -> str:
    """Kontrollnumret för utgåvan: först ur sökresultatet, annars ur posten."""
    nummer = libris_kontrollnummer(instans)
    if nummer:
        return nummer
    post_id = (instans or {}).get("@id", "")
    if post_id.startswith("http"):
        return hamta_kontrollnummer_fran_post(post_id.split("#")[0]) or ""
    return ""


@st.cache_data(ttl=600, show_spinner=False)
def hamta_lanestatus(url_mall: str, bib_id: str, isbn: str, sigel: str):
    """Hämtar exemplar med status och lånepolicy för ETT bibliotek (sigel).
    Returnerar en lista med exemplar, eller None om anropet misslyckades."""
    url = url_mall.format(bib_id=quote_plus(bib_id), isbn=quote_plus(isbn),
                          sigel=quote_plus(sigel))
    huvud = {"User-Agent": HEADERS["User-Agent"]}
    try:
        try:
            resp = requests.get(url, headers=huvud, timeout=LANESTATUS_TIDSGRANS)
        except requests.exceptions.SSLError:
            # Vissa servrar (t.ex. BOOK-IT hos Axiell) är konfigurerade för http
            resp = requests.get("http://" + url.split("://", 1)[1], headers=huvud,
                                timeout=LANESTATUS_TIDSGRANS)
        resp.raise_for_status()
        # BOOK-IT skriver t.ex. "Barn & ungdom" med ett oskyddat &, vilket inte
        # är giltig XML. Skydda sådana & innan svaret tolkas.
        innehall = re.sub(rb"&(?!(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z]\w*);)", b"&amp;",
                          resp.content)
        rot = ET.fromstring(innehall)
    except Exception:
        return None

    def falt(item, namn):
        el = item.find(namn)
        return (el.text or "").strip() if el is not None and el.text else ""

    return [
        {
            "status": falt(item, "Status"),
            "datum": falt(item, "Status_Date"),
            "policy": falt(item, "Loan_Policy"),
            "hylla": falt(item, "Call_No"),
            "plats": falt(item, "Location"),
        }
        for item in rot.iter("Item")
    ]


def hamta_status_for_sigler(url_mall: str, bib_id: str, isbn: str, sigler: list) -> dict:
    """Hämtar lånestatus för flera sigler samtidigt.
    Returnerar {sigel: exemplarlista eller None}."""
    with ThreadPoolExecutor(max_workers=min(8, len(sigler))) as pool:
        resultat = pool.map(lambda s: hamta_lanestatus(url_mall, bib_id, isbn, s), sigler)
    return dict(zip(sigler, resultat))


def kort_datum(datum: str) -> str:
    """'2026-11-26' -> '26 nov'."""
    traff = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", datum)
    if not traff:
        return datum
    return f"{int(traff.group(3))} {MANADER[int(traff.group(2)) - 1]}"


# Statusar som betyder att exemplaret står på hyllan (Koha/Quria: "Tillgänglig",
# BOOK-IT: "Ej utlånad")
TILLGANGLIG_STATUS = {"tillgänglig", "ej utlånad"}


def ar_kursbok(ex: dict) -> bool:
    """Kursbok om hyllsignum eller placering nämner "kurs" (Kursbok,
    Kurslitteratur, Kursbokssamling o.s.v.)."""
    return "kurs" in f"{ex.get('hylla', '')} {ex.get('plats', '')}".lower()


def tillganglighet_text(exemplar: list) -> str:
    """Kort tillgänglighet för en enhet, utan lånepolicy: "4 av 7", "åter 3 dec",
    "Försenad" eller "Referensmaterial". Referensexemplar räknas inte."""
    lanbara = [ex for ex in exemplar if "referens" not in (ex["policy"] or "").lower()]
    if not lanbara:
        return "Referensmaterial"
    inne = sum(1 for ex in lanbara if ex["status"].lower() in TILLGANGLIG_STATUS)
    if inne:
        return f"{inne} av {len(lanbara)}"
    idag = date.today().isoformat()
    alla_datum = sorted(ex["datum"] for ex in lanbara if ex["datum"])
    kommande = [d for d in alla_datum if d >= idag]
    if kommande:
        return f"åter {kort_datum(kommande[0])}"
    if alla_datum:
        return "Försenad"
    return ", ".join(sorted({ex["status"] for ex in lanbara if ex["status"]})) or "–"


def sammanfatta_exemplar(exemplar: list, markera_kurs: bool = False) -> list:
    """En rad per lånepolicy: (policy, kursbok, sammanfattning).
    Med markera_kurs får kursböcker en egen rad, eftersom de i regel inte
    fjärrlånas även om lånepolicyn är hemlån. Referensmaterial visas utan
    tillgänglighet. Annars antal tillgängliga, eller - om inget är
    tillgängligt - första återlämningsdatum."""
    grupper = {}
    for ex in exemplar:
        policy = ex["policy"] or "–"
        kurs = markera_kurs and "referens" not in policy.lower() and ar_kursbok(ex)
        grupper.setdefault((policy, kurs), []).append(ex)

    rader = []
    for (policy, kurs), lista in grupper.items():
        if "referens" in policy.lower():
            rader.append((policy, kurs, ""))
            continue
        tillgangliga = sum(1 for ex in lista if ex["status"].lower() in TILLGANGLIG_STATUS)
        if tillgangliga:
            rader.append((policy, kurs, f"{tillgangliga} av {len(lista)}"))
            continue
        idag = date.today().isoformat()
        alla_datum = sorted(ex["datum"] for ex in lista if ex["datum"])
        kommande = [d for d in alla_datum if d >= idag]
        if kommande:
            rader.append((policy, kurs, f"åter {kort_datum(kommande[0])}"))
        elif alla_datum:
            rader.append((policy, kurs, "Försenad"))
        else:
            statusar = sorted({ex["status"] for ex in lista if ex["status"]})
            rader.append((policy, kurs, ", ".join(statusar) or "–"))
    return rader


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
        padding: 0.6rem 1.25rem !important;
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

    /* Huvudlistan med utfällbara rader (ersätter tabellen) */
    .lista-rubrik, .biblioteksrad > summary, .biblioteksrad.enkel {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 0.6rem 1.25rem;
    }
    .lista-rubrik {
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: var(--rubrik);
        padding-top: 0.75rem;
        padding-bottom: 0.75rem;
        border-bottom: 1px solid var(--linje);
    }
    .biblioteksrad > summary {
        cursor: pointer;
        list-style: none;
        font-size: 1.05rem;
        min-height: 2.9rem;
    }
    .biblioteksrad.enkel { font-size: 1.05rem; min-height: 2.9rem; }
    .biblioteksrad + .biblioteksrad { border-top: 1px solid var(--linje); }
    .biblioteksrad.enkel .namn { text-decoration: none; }
    .biblioteksrad.enkel .namn:hover { text-decoration: underline; }
    /* Osynlig pil håller samma vänstermarginal som raderna med pil */
    .biblioteksrad.enkel .namn::before { border-color: transparent; }
    .biblioteksrad > summary::-webkit-details-marker { display: none; }
    /* Ingen svart fokusram efter klick eller omritning; tangentbordsfokus
       markeras i stället med understruket namn */
    .biblioteksrad > summary:focus,
    .biblioteksrad > summary:focus-visible { outline: none; }
    .biblioteksrad > summary:focus-visible .namn { text-decoration: underline; }
    .biblioteksrad .namn {
        font-weight: 600;
        color: #2b6cb0;
    }
    .biblioteksrad .namn::before {
        content: "";
        display: inline-block;
        width: 0.45rem;
        height: 0.45rem;
        border-right: 2px solid currentColor;
        border-bottom: 2px solid currentColor;
        transform: rotate(-45deg);
        margin: 0 0.85rem 0.12rem 0.1rem;
        transition: transform 0.15s;
    }
    .biblioteksrad[open] .namn::before { transform: rotate(45deg); }
    .biblioteksrad > summary:hover .namn { text-decoration: underline; }
    .biblioteksrad[open] > summary .etikett { display: none; }
    .enheter { padding: 0 1.25rem 0.75rem 2.6rem; }
    .enhet {
        display: flex;
        justify-content: space-between;
        padding: 0.25rem 0;
        color: #3a4555;
    }
    .enheter .katalog { padding-top: 0.35rem; }

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

    /* --- Lunds universitetsbibliotek --- */
    .lund { margin: 1.5rem 0 0 0; }
    .lund-rubrik {
        font-size: 1.15rem;
        font-weight: 600;
        color: #1a365d;
    }
    .lund-bibliotek {
        margin-top: 0.15rem;
        color: #5b6675;
    }
    .lund-status { margin: 0.25rem 0 0.25rem 0; }
    .lund-status td { font-size: 1rem !important; padding: 0.6rem 1rem !important; }
    .lund-status th { padding: 0.6rem 1rem !important; }
    .lund-status td:nth-child(2) { color: #5b6675; }
    .lund-status strong { color: #1a1a1a; font-weight: 700; }
    .lund-status.en-sigel td:nth-child(1) { color: #5b6675; }
    .lund-status.en-sigel td:nth-child(2) { color: inherit; }

    /* Utlåningsstatus som en ramlös textrad direkt under bibliotekens namn
       (gäller bara Lund-delen, inte "Om tjänsten") */
    [class*="st-key-nasta-"] { gap: 0 !important; }
    [class*="st-key-nasta-"] [data-testid="stExpander"] details {
        border: none !important;
        background: transparent !important;
    }
    [class*="st-key-nasta-"] [data-testid="stExpander"] summary {
        padding: 0.6rem 0 0.2rem 0 !important;
        background: transparent !important;
        border: none !important;
    }
    [class*="st-key-nasta-"] [data-testid="stExpander"] details[open] > summary {
        border-bottom: none !important;
    }
    [class*="st-key-nasta-"] [data-testid="stExpander"] summary p {
        font-size: 1rem !important;
        font-weight: 600;
        color: #2b6cb0;
    }
    [class*="st-key-nasta-"] [data-testid="stExpander"] summary:hover { background: transparent !important; }
    [class*="st-key-nasta-"] [data-testid="stExpanderDetails"] {
        padding: 0.25rem 0 0 0 !important;
        border: none !important;
    }
    [class*="st-key-nasta-"] [data-testid="stExpander"] { margin-top: 0.4rem; }

    /* Sortering av träfflistan: ramlösa textval med avdelare. Det valda
       alternativet färgas och fetas via etiketten (se sorteringsetikett). */
    .st-key-sortering [data-testid="stButtonGroup"] { gap: 0 !important; }
    .st-key-sortering button {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
        padding: 0.1rem 0.4rem !important;
        min-height: 0 !important;
        color: #5b6675 !important;
    }
    .st-key-sortering button:first-child { padding-left: 0 !important; }
    .st-key-sortering button:not(:last-child)::after {
        content: "|";
        margin-left: 0.8rem;
        color: #c5ccd6;
        font-weight: 400;
    }
    .st-key-sortering strong,
    .st-key-sortering strong * { color: #2b6cb0 !important; }
    .st-key-sortering button:hover { color: #2b6cb0 !important; }

    .bestallning { color: #5b6675; }

    /* Länk till Libris-posten: som biblioteksnamnen, understruken bara vid hovring */
    a.postlank { text-decoration: none; }
    a.postlank:hover { text-decoration: underline; }

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
    "bibliotekens rapporterade bestånd av tryckt material i LIBRIS.</p>",
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
    st.session_state["sok_valt_index"] = None  # återställ val vid ny sökning

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
        # Sortering av de (högst 20) träffar Libris returnerat: i Libris
        # relevansordning, eller nyast först (stabil, så att lika år
        # behåller relevansordningen).
        st.markdown("Flera poster matchar. Välj ett alternativ i listan nedan.")
        with st.container(key="sortering"):
            st.segmented_control(
                "Sortera", ["Relevans", "År"], default="Relevans",
                format_func=sorteringsetikett,
                key="sok_sortering", label_visibility="collapsed",
                on_change=behall_sortering,
            )
        sortering = "Nyast först" if st.session_state.get("sok_sortering") == "År" else "Relevans"
        ordning = list(range(len(verk_lista)))
        if sortering == "Nyast först":
            ordning.sort(key=lambda i: -utgivningsar(verk_lista[i]))
        # Förvalt alternativ: överst i aktuell ordning, tills användaren väljer
        if st.session_state.get("sok_valt_index") not in ordning or \
                st.session_state.get("sok_forra_sortering") != sortering:
            st.session_state["sok_valt_index"] = ordning[0]
        st.session_state["sok_forra_sortering"] = sortering
        st.radio(
            "Träffar", options=ordning,
            format_func=lambda i: etiketter[i],
            key="sok_valt_index", label_visibility="collapsed",
        )
        valt_verk = verk_lista[st.session_state["sok_valt_index"]]

    post = None
    med_bestand = set()

    if valt_verk is not None:
        instans = forsta_fysiska_instansen(valt_verk)
        if instans:
            post = katalogpost(valt_verk, instans, sokterm_vy)
        med_bestand = bibliotek_med_bestand(valt_verk)

    # Lånestatus för samarbetets bibliotek som har aktiverat den (just nu
    # Vellinge). Hämtas före tabellen, eftersom exemplar som bara finns i den
    # lokala katalogen (t.ex. äldre bestånd) också ska ge "Finns".
    lanestatus_per_kod = {}
    isbn_valt = ""
    if post:
        isbn_valt = re.sub(r"[^0-9Xx]", "", post["isbn"][0]) if post["isbn"] else ""
        bib_id_valt = ""
        lanestatus_bibliotek = {k: i for k, i in SIGLAR.items() if i.get("lanestatus")}
        if lanestatus_bibliotek:
            bib_id_valt = kontrollnummer_for(forsta_fysiska_instansen(valt_verk))
        if lanestatus_bibliotek and (isbn_valt or bib_id_valt):
            for kod, info in lanestatus_bibliotek.items():
                ls = info["lanestatus"]
                lanestatus_per_kod[kod] = hamta_status_for_sigler(
                    ls["url"], bib_id_valt or "0", isbn_valt, list(ls["enheter"]))
                if any(status_per_enhet for status_per_enhet in lanestatus_per_kod[kod].values()):
                    med_bestand.add(kod)

    if post:
        rubrik = md(post["titel"])
        if post["undertitel"]:
            rubrik += f" : {md(post['undertitel'])}"
        st.markdown(f"### {rubrik}")
        st.markdown(md(beskrivning_text(post)))
        if post["post_url"]:
            st.markdown(
                f'<a class="postlank" href="{html.escape(post["post_url"], quote=True)}" '
                'target="_blank">Visa posten i LIBRIS ↗</a>',
                unsafe_allow_html=True,
            )
    elif soktyp_vy == "ISBN":
        st.warning(f"ISBN \"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera siffrorna.")
    else:
        st.warning(
            f"\"{sokterm_vy}\" hittades inte i LIBRIS. Kontrollera stavning, "
            "eller pröva med ISBN."
        )

    # Huvudlistan: en rad per bibliotek (kommun). Raden fälls ut med ett klick
    # på namnet och visar då tillgänglighet per enhet (för bibliotek med
    # lånestatus) och länk till bibliotekets katalog.
    rader = []
    for kod, info in sorted(SIGLAR.items(), key=lambda kv: kv[1]["namn"]):
        if soktyp_vy == "ISBN":
            arena_fraga = sokterm_vy
        else:
            arena_fraga = bygg_arena_titelfraga(sokterm_vy)
            if forfattare_vy:
                arena_fraga = f"{bygg_arena_forfattarfraga(forfattare_vy)} AND {arena_fraga}"
        sok_lank = info["sok_url"].format(query=quote_plus(arena_fraga))

        if kod in med_bestand:
            status = '<span class="etikett finns">Finns</span>'
        else:
            status = '<span class="etikett saknas">Finns ej</span>'

        # Bara bibliotek med lånestatus (och bestånd) får pil och kan fällas ut.
        # Övriga visar namnet som länk till katalogen, som tidigare.
        if not (kod in lanestatus_per_kod and kod in med_bestand):
            rader.append(
                '<div class="biblioteksrad enkel">'
                f'<a class="namn" href="{html.escape(sok_lank, quote=True)}" target="_blank">'
                f'{html.escape(info["namn"])}</a>{status}</div>')
            continue

        innehall = []
        enheter = info["lanestatus"]["enheter"]
        svar = lanestatus_per_kod[kod]
        if all(v is None for v in svar.values()):
            innehall.append('<div class="enhet"><span>Utlåningsstatus kunde inte hämtas</span></div>')
        else:
            for sigel in sorted((s_ for s_ in svar if svar[s_]), key=lambda s_: enheter[s_]):
                innehall.append(
                    f'<div class="enhet"><span>{html.escape(enheter[sigel])}</span>'
                    f"<span>{html.escape(tillganglighet_text(svar[sigel]))}</span></div>")
        if info.get("bestall_i_katalogen"):
            lank = info["sok_url"].format(query=quote_plus(isbn_valt)) if isbn_valt else sok_lank
            lanktext = "Beställ i katalogen ↗"
        else:
            lank, lanktext = sok_lank, "Visa i katalogen ↗"
        innehall.append(
            f'<div class="katalog"><a class="postlank" href="{html.escape(lank, quote=True)}" '
            f'target="_blank">{lanktext}</a></div>')

        rader.append(
            '<details class="biblioteksrad"><summary>'
            f'<span class="namn">{html.escape(info["namn"])}</span>{status}</summary>'
            f'<div class="enheter">{"".join(innehall)}</div></details>')

    # Sammanfattningen står överst i listans ram i stället för i en egen
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
        f'<div class="bestand">{topp}'
        '<div class="lista-rubrik"><span>Bibliotek</span><span>Status</span></div>'
        f"{''.join(rader)}</div>",
        unsafe_allow_html=True,
    )

    # --- Nästa steg i fjärrlånekedjan: Lunds UB, därefter Depåbiblioteket ---
    # Vilka bibliotek som har titeln kommer från samma Libris-svar och visas
    # direkt. Utlåningsstatus hämtas därefter, medan resten av sidan redan
    # syns, och ligger klar när man fäller ut den.
    if valt_verk is not None:
        instans_vald = forsta_fysiska_instansen(valt_verk)
        isbn = re.sub(r"[^0-9Xx]", "", post["isbn"][0]) if post and post["isbn"] else ""

        for steg in NASTA_STEG:
            har_titeln = sigler_med_bestand(valt_verk, steg["sigler"])
            flera = len(steg["sigler"]) > 1
            with st.container(key=f"nasta-{steg['nyckel']}"):
                # Raden under rubriken: vilka enheter som har titeln (unika
                # kortnamn i ordning), "Finns" för en enda sigel, annars "Finns ej"
                if not har_titeln:
                    bestandsrad = "Finns ej"
                elif steg["visa_namn"]:
                    namn = list(dict.fromkeys(steg["sigler"][s] for s in har_titeln))
                    bestandsrad = " · ".join(html.escape(n) for n in namn)
                else:
                    bestandsrad = "Finns"
                namnrad = f'<div class="lund-bibliotek">{bestandsrad}</div>'
                st.markdown(
                    f'<div class="lund"><div class="lund-rubrik">{html.escape(steg["rubrik"])}</div>'
                    f"{namnrad}</div>",
                    unsafe_allow_html=True,
                )
                if not har_titeln:
                    continue
                with st.expander("Utlåningsstatus"):
                    bib_id = kontrollnummer_for(instans_vald)
                    kan_hamta = bib_id or ("{isbn}" in steg["url"] and isbn)
                    status_per_sigel = (
                        hamta_status_for_sigler(steg["url"], bib_id, isbn, har_titeln)
                        if kan_hamta else {}
                    )
                    # Länk till posten i bibliotekets egen katalog (t.ex. Finn),
                    # för hylla och övriga detaljer
                    katalog_lank = ""
                    if steg.get("katalog"):
                        katalognamn, mall_bibid, mall_isbn = steg["katalog"]
                        if bib_id:
                            katalog_url = mall_bibid.format(bib_id=quote_plus(bib_id))
                        elif isbn:
                            katalog_url = mall_isbn.format(isbn=quote_plus(isbn))
                        else:
                            katalog_url = ""
                        if katalog_url:
                            katalog_lank = (
                                f'<a class="postlank" href="{html.escape(katalog_url, quote=True)}" '
                                f'target="_blank">Visa posten i {html.escape(katalognamn)} ↗</a>'
                            )
                    if not status_per_sigel or all(v is None for v in status_per_sigel.values()):
                        st.markdown("Utlåningsstatus kunde inte hämtas.")
                        if katalog_lank:
                            st.markdown(katalog_lank, unsafe_allow_html=True)
                        continue
                    rader_status = []
                    for sigel in har_titeln:
                        bib = f"<td>{html.escape(steg['sigler'][sigel])}</td>" if flera else ""
                        exemplar = status_per_sigel[sigel]
                        if exemplar is None:
                            rader_status.append(f"<tr>{bib}<td></td><td>Kunde inte hämtas</td></tr>")
                        elif not exemplar:
                            rader_status.append(f"<tr>{bib}<td></td><td>Inga exemplar</td></tr>")
                        else:
                            for policy, kurs, tillganglighet in sammanfatta_exemplar(
                                    exemplar, steg.get("markera_kurs", False)):
                                policy_html = html.escape(policy) + (" · <strong>Kursbok</strong>" if kurs else "")
                                rader_status.append(
                                    f"<tr>{bib}<td>{policy_html}</td>"
                                    f"<td>{html.escape(tillganglighet)}</td></tr>")
                    rubrikrad = ("<th>Bibliotek</th>" if flera else "") + \
                        "<th>Lånepolicy</th><th>Tillgänglighet</th>"
                    st.markdown(
                        f'<div class="bestand lund-status{"" if flera else " en-sigel"}"><table>'
                        f"<thead><tr>{rubrikrad}</tr></thead>"
                        f"<tbody>{''.join(rader_status)}</tbody></table></div>",
                        unsafe_allow_html=True,
                    )
                    if katalog_lank:
                        st.markdown(katalog_lank, unsafe_allow_html=True)

elif sok_knapp:
    st.warning("Skriv in en sökterm.")

# ---------------------------------------------------------------------------
# OM TJÄNSTEN - kort information för användaren längst ner på sidan
# ---------------------------------------------------------------------------
st.markdown('<div style="height:1.5rem"></div>', unsafe_allow_html=True)
with st.expander("Om tjänsten"):
    st.markdown(
        "Samsök visar vilka av de nio folkbiblioteken i fjärrlånesamarbetet "
        "som har en viss titel. Den bygger på bibliotekens rapporterade bestånd "
        "av tryckt material i LIBRIS.\n\n"
        "**Varför kan ett bibliotek visas med Finns ej fast titeln står på hyllan?**\n"
        "- Äldre bestånd som aldrig förts in i LIBRIS syns inte.\n"
        "- LIBRIS håller isär olika utgåvor, till exempel pocket och inbunden. "
        "Har biblioteket en annan utgåva än den som visas kan det stå Finns ej. "
        "Bibliotekens egna kataloger slår ofta ihop utgåvorna.\n\n"
        "**Bara tryckt material.** E-böcker, ljudböcker och andra medier ingår inte.\n\n"
        "**Lånestatus** för biblioteken i samarbetet syns i respektive katalog. "
        "Klicka på bibliotekets namn. Bibliotek som har aktiverat Libris "
        "lånestatus (just nu Vellinge) visas också under Utlåningsstatus, och "
        "då räknas även exemplar som bara finns i bibliotekets egen katalog.\n\n"
        "**Lunds universitetsbibliotek** och **Sveriges depåbibliotek och "
        "lånecentral** visas under tabellen, med utlåningsstatus och lånepolicy "
        "från bibliotekens egna system när de har titeln.\n\n"
        "[Källkod och teknisk beskrivning på GitHub ↗]"
        "(https://github.com/Boojonas/sydvast-samsokning)"
    )
