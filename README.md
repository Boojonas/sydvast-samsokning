# Samsök – Nätverket Sydväst

En liten webbapp som visar vilka av de nio folkbiblioteken i fjärrlånesamarbetet som har en viss tryckt titel. Tänkt som stöd vid fjärrlån mellan biblioteken.

Appen körs på Streamlit Community Cloud: https://sydvast-samsokning.streamlit.app/

## Hur den fungerar

1. Användaren skriver en titel eller ett ISBN i samma sökfält, valfritt med författare. Appen känner själv igen ISBN (10 eller 13 siffror).
2. Appen gör **ett** anrop mot LIBRIS öppna sök-API (`https://libris.kb.se/find`), begränsat till tryckta böcker (`instanceCategory: saobf/Print`).
3. Svaret är strukturerat som *verk → instans → exemplar*. Appen går igenom exemplaren lokalt och kontrollerar vilka bibliotek (sigler) som har titeln. Inga fler nätverksanrop görs.
4. Ger sökningen flera olika verk får användaren välja rätt bok i en lista.
5. Resultatet visas som en bibliografisk beskrivning av utgåvan (titel, upphov, upplaga, utgivning, ISBN) med länk till posten i LIBRIS, följd av en tabell över biblioteken. Varje biblioteksnamn är en djuplänk till samma sökning i bibliotekets egen Arena-katalog, där lånestatus syns.

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
| Vellinge | Vell |

Siglerna och länkarna till bibliotekens kataloger finns i `SIGLAR` i `app.py`. Exemplar med sigler som börjar på `7` (skolbibliotek) räknas inte, eftersom de inte fjärrlånar.

## Begränsningar

- Appen ser bara det bestånd biblioteken rapporterat till LIBRIS. Äldre bestånd som aldrig konverterats syns inte och måste sökas i respektive katalog.
- Bara tryckta böcker, inte e-böcker, ljudböcker eller andra medier.
- Beståndet räknas för den första tryckta utgåvan av det valda verket. Har ett bibliotek bara en annan utgåva kan det visas utan bestånd. Folkbibliotekens egna kataloger slår ofta ihop utgåvor (t.ex. pocket och inbunden) i en post, vilket LIBRIS inte gör.
- Resultatet säger inget om lånestatus. Den syns i bibliotekets egen katalog.
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
