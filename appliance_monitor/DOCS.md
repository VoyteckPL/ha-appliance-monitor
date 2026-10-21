# Appliance Monitor

Aplikacja nie ma opcji w zakładce Konfiguracja. Wszystko ustawiasz w panelu **Urządzenia**
na pasku bocznym.

## Wskazówki do kalibracji

- Uruchom jeden normalny cykl i obejrzyj wykres mocy w panelu.
- **Próg startu** ustaw wyraźnie powyżej mocy jałowej gniazdka i urządzenia w stanie czuwania.
- **Próg końca** ustaw tuż powyżej mocy w stanie czuwania.
- **Czas poniżej progu** ustaw dłuższy niż najdłuższa przerwa w środku cyklu
  (pralka podczas moczenia, zmywarka podczas suszenia).
- Wartości z listy „Rodzaj" to tylko punkt startowy.

## Powiadomienia

W treści możesz użyć `{name}`, `{duration}` i `{energy}`.
Na liście celów pojawiają się usługi `notify.*`, w tym telefony z aplikacją HA Companion.
