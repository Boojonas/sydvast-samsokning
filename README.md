# Samsökning – Nätverket Sydväst

En liten webbapp som visar vilka av de nio folkbiblioteken i fjärrlånesamarbetet som har en viss tryckt titel. Tänkt som stöd vid fjärrlån mellan biblioteken.

Appen körs på Streamlit Community Cloud: https://sydvast-samsokning.streamlit.app/ (lösenordsskyddad).

## Hur den fungerar

1. Användaren skriver en titel eller ett ISBN i samma sökfält, valfritt med författare. Appen känner själv igen ISBN (10 eller 13 siffror).
2. Appen gör **ett** anrop mot LIBRIS öppna sök-API (`https://libris.kb.se/find`), begränsat till tryckta böcker (`instanceCategory: saobf/Print`).
3. Svaret är strukturerat som *verk → instans → exemplar*. Appen går igenom exemplaren lokalt och räknar hur många som hör till respektive biblioteks sigel. Inga fler nätverksanrop görs.
4. Ger sökningen flera olika verk får användaren välja rätt bok i en lista.
5. Resultatet visas som en tabell. Varje biblioteksnamn är en djuplänk till samma sökning i bibliotekets egen Arena-katalog, där lånestatus syns.

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
- Antal "poster" är antal exemplarposter i LIBRIS, inte nödvändigtvis antal fysiska exemplar på hyllan, och säger inget om lånestatus.
- Varför appen inte skrapar Arena direkt: flera av bibliotekens Arena-installationer har bot-skydd, och LIBRIS ger en enhetlig källa för alla nio.

## Köra lokalt

```bash
pip install -r requirements.txt
mkdir -p .streamlit
echo 'app_losenord = "välj-ett-lösenord"' > .streamlit/secrets.toml
streamlit run app.py
```

`.streamlit/secrets.toml` ska **inte** checkas in i repot.

## Teknik

- Python, [Streamlit](https://streamlit.io/), `requests`
- Sökningar cachas i 10 minuter (`st.cache_data`).
- Anropen tvingas över IPv4, eftersom IPv6 är opålitligt i vissa molnmiljöer.

**Kör du en egen kopia av appen:** byt kontaktadressen i `HEADERS` (`User-Agent`) i `app.py` till din egen, så att LIBRIS kan se vem som står bakom trafiken.
