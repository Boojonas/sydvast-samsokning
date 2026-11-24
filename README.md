# Samsök – Nätverket Sydväst

En liten webbapp som visar vilka av de nio folkbiblioteken i fjärrlånesamarbetet som har en viss titel, utifrån bibliotekens bestånd av tryckt material i LIBRIS. Tänkt som stöd vid fjärrlån mellan biblioteken.

Appen körs på Streamlit Community Cloud: https://sydvast-samsokning.streamlit.app/

## Hur den fungerar

1. Användaren skriver en titel eller ett ISBN i samma sökfält, valfritt med författare. Appen känner själv igen ISBN (10 eller 13 siffror).
2. Appen gör **ett** anrop mot LIBRIS öppna sök-API (`https://libris.kb.se/find`), begränsat till tryckt material (`instanceCategory: saobf/Print`).
3. Svaret är strukturerat som *verk → instans → exemplar*. Appen går igenom exemplaren lokalt och kontrollerar vilka bibliotek (sigler) som har titeln. Inga fler nätverksanrop görs.
4. Ger sökningen flera olika verk får användaren välja rätt bok i en lista, sorterad efter relevans eller nyast först. Sorteringen gäller de 20 mest relevanta träffarna från Libris.
5. Resultatet visas som en bibliografisk beskrivning av utgåvan (titel, upphov, upplaga, utgivning, ISBN) med länk till posten i LIBRIS, följd av en lista över biblioteken i bokstavsordning. Varje biblioteksnamn är en djuplänk till samma sökning i bibliotekets egen Arena-katalog. Bibliotek med lånestatus (se nedan) har i stället en pil framför namnet; klickar man på det fälls raden ut och visar tillgänglighet per enhet.
6. Har någon av Lunds universitetsbibliotek titeln visas det under listan (samma Libris-svar). Utlåningsstatus och lånepolicy hämtas samtidigt från Lunds lånestatustjänst – se nedan – och ligger klar när man fäller ut *Utlåningsstatus*.

## Biblioteken och deras sigler

| Bibliotek | Sigel i LIBRIS |
|---|---|
| Burlöv | Arlo |
| Eslöv | ESLO |
| Höör | Hoor |
| Kävlinge | Kavl |
| Lomma/Bjärred | LOBJ |
| Staffanstorp | Staf |
| Svedala | Sved |
| Trelleborg | Trel |
| Vellinge | Vell, Holl, Skan, Vib (Vellinge, Höllviken, Skanör, Västra Ingelstad) |

Siglerna och länkarna till bibliotekens kataloger finns i `SIGLAR` i `app.py`. Exemplar med sigler som börjar på `7` (skolbibliotek) räknas inte, eftersom de inte fjärrlånar.

## Lånestatus för samarbetets bibliotek

Bibliotek som har aktiverat Libris lånestatus i sitt bibliotekssystem får en `lanestatus`-post i `SIGLAR` (adressmall och enheter med sigel). Just nu gäller det Vellinge (BOOK-IT, schema `bookit33`):

```
https://vellingebibliotek.axiell.com/services/rest/libris/holding?ds=jdbc/bookit33&sigel=<sigel>&isbn=<ISBN>&bib_id=<kontrollnummer>
```

Appen frågar varje enhet (sigel) samtidigt, med kontrollnummer och ISBN. BOOK-IT kräver `bib_id` men hittar också exemplar via ISBN, så exemplar som bara finns i den lokala katalogen ger "Finns" i listan. När man fäller ut biblioteket visas tillgängligheten per enhet i bokstavsordning (utan lånepolicy), med länk för beställning i katalogen när biblioteket vill det (`bestall_i_katalogen`). "Ej utlånad" och "Tillgänglig" räknas som inne; passerat återlämningsdatum visas som "försenad".

För BOOK-IT aktiveras tjänsten med kryssrutan *Visa lånestatus* och fältet *Sigel* under *Librisinställningar* på varje enhet.

## Lunds universitetsbibliotek och Depåbiblioteket

Lunds sigler står i `LUND_SIGLER` i `app.py` (digitala resurser, Ldig/Ldix, är utelämnade). Status hämtas från Lunds tjänst för [Libris lånestatus](https://kb.se/download/18.5c4746ff197581d1a74a5e/1749808286539/Libris%20l%C3%A5nestatus%202025.pdf), samma som Libris själv använder:

```
https://lubcat.lub.lu.se/cgi-bin/koha/lutil/libris_item_search.pl?Bib_ID=<kontrollnummer>&BIBL=<sigel>
```

Varje sigel returnerar bara sina egna exemplar, så appen gör ett anrop per Lund-bibliotek som har titeln, samtidigt och med fem sekunders tidsgräns. Svaren sparas i 10 minuter. Exemplaren sammanfattas per lånepolicy: antal tillgängliga, eller första återlämningsdatum om inget är tillgängligt. Referensmaterial visas utan tillgänglighet.

Därefter visas Sveriges depåbibliotek och lånecentral (sigel Umdp) på samma sätt. Dess lånestatustjänst (Axiell Quria) tar både ISBN och kontrollnummer; adressen står i `DEPA_LANESTATUS_URL`. Ordningen och vilka bibliotek som ingår styrs av `NASTA_STEG` i `app.py`.

## Begränsningar

- Appen ser bara det bestånd biblioteken rapporterat till LIBRIS. Äldre bestånd som aldrig konverterats syns inte och måste sökas i respektive katalog.
- Bara tryckt material, inte e-böcker, ljudböcker eller andra medier.
- Beståndet räknas för den första tryckta utgåvan av det valda verket. Har ett bibliotek bara en annan utgåva kan det visas utan bestånd. Folkbibliotekens egna kataloger slår ofta ihop utgåvor (t.ex. pocket och inbunden) i en post, vilket LIBRIS inte gör.
- För bibliotek utan aktiverad lånestatus säger resultatet inget om tillgänglighet. Den syns i bibliotekets egen katalog.
- Varför appen inte skrapar Arena direkt: flera av bibliotekens Arena-installationer har bot-skydd, och LIBRIS ger en enhetlig källa för alla nio.

## Köra lokalt

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Lösenordsskydd (valfritt)

Appen är öppen för alla. Vill man lösenordsskydda den lägger man in `app_losenord = "..."` i Streamlits *Settings → Secrets*, eller lokalt i `.streamlit/secrets.toml`. Den filen ska **inte** checkas in i repot och står därför i `.gitignore`.

## Teknik

- Python, [Streamlit](https://streamlit.io/), `requests`
- Sökningar cachas i 10 minuter (`st.cache_data`).
- Fast ljust tema och accentfärg i `.streamlit/config.toml`. Övrig formgivning ligger som CSS i `app.py`.
- Anropen tvingas över IPv4, eftersom IPv6 är opålitligt i vissa molnmiljöer.

## Hålla appen vaken

Streamlit stänger av appar som inte haft besök på cirka 12 timmar. Ett schemalagt arbetsflöde i det separata repot [keepalive-samsok](https://github.com/Boojonas/keepalive-samsok) öppnar därför appen var fjärde timme.

## Egen kopia

Kör du en egen kopia av appen: byt kontaktadressen i `HEADERS` (`User-Agent`) i `app.py` till din egen, så att LIBRIS kan se vem som står bakom trafiken. Arbetsflödet i keepalive-samsok pekar på den här appens adress, så en egen kopia behöver ett eget sådant.

## Ikonen i webbläsarfliken

Ikonen i `bilder/bocker.png` kommer från Googles [Noto Emoji](https://github.com/googlefonts/noto-emoji) och används under Apache-licensen 2.0.
