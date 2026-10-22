# Appliance Monitor

Aplikacja nie ma opcji w zakładce Konfiguracja. Wszystko ustawiasz w panelu **Urządzenia**
na pasku bocznym.

## Wskazówki do kalibracji

- Najszybciej: w ustawieniach urządzenia kliknij **Sprawdź ustawienia**. Aplikacja odtworzy ostatnie 14 dni
  poboru i pokaże, ile cykli by wykryła. Podejrzanie krótkie cykle oznaczają fałszywe wykrycia
  albo koniec wykryty za wcześnie.
- Uruchom jeden normalny cykl i obejrzyj wykres mocy w panelu.
- **Próg startu** ustaw wyraźnie powyżej mocy jałowej gniazdka i urządzenia w stanie czuwania.
- **Próg końca** ustaw tuż powyżej mocy w stanie czuwania.
- **Czas poniżej progu** ustaw dłuższy niż najdłuższa przerwa w środku cyklu
  (pralka podczas moczenia, zmywarka podczas suszenia).
- Wartości z listy „Rodzaj" to tylko punkt startowy.

## Powiadomienia

W treści możesz użyć `{name}`, `{duration}` i `{energy}`.
Na liście celów pojawiają się usługi `notify.*`, w tym telefony z aplikacją HA Companion.

## Historia i koszty

- **Szczegóły** pokazują ostatnie 30 dni. Koszt liczony jest po cenie z ustawień ogólnych (⚙).
- **Wczytaj historię z HA** pobiera dane z rejestratora HA (domyślnie przechowuje ok. 10 dni)
  i wykrywa cykle przy aktualnych ustawieniach urządzenia. Powtórne wczytanie nie dubluje cykli.
- Fałszywy cykl usuniesz przyciskiem ✕ na liście.
