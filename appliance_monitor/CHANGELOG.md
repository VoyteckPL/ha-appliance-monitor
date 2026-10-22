# Changelog

## 0.3.0
- Naprawa animacji: ikony ruszają się tylko, gdy urządzenie pracuje (wcześniej kręciły się zawsze).
- Nowe nazwy stanów w panelu: Wyłączone / Pracuje / Gotowe (zamiast „Czeka").
- Przycisk „Szczegóły": statystyki z 30 dni (cykle, energia, koszt, średnie), wykres energii z 14 dni, wykres mocy każdego cyklu z kursorem, lista cykli z możliwością usunięcia fałszywych.
- „Wczytaj historię z HA": odtwarza cykle z rejestratora Home Assistanta (do 14 dni wstecz), więc historia jest od razu pełna.
- „Sprawdź ustawienia" w formularzu: pokazuje, ile cykli wykryłyby podane progi na historii z HA, z ostrzeżeniem o podejrzanie krótkich cyklach.
- Szacowany czas końca na karcie pracującego urządzenia (mediana ostatnich cykli).
- Ustawienia ogólne (⚙): cena prądu do wyliczania kosztów.
- Historia do 200 cykli na urządzenie, razem z wykresem mocy każdego cyklu.
- Stan zapisywany co minutę w trakcie cyklu, więc restart aplikacji nie gubi energii ani początku wykresu.

## 0.2.0
- Ikona i logo aplikacji.
- Animowane ikony urządzeń w panelu (pralka, suszarka, zmywarka, inne): ruszają się, gdy urządzenie pracuje.
- Encja „Pracuje" pokazuje teraz zwykłe On/Off (wcześniej Działa/Nie działa).

## 0.1.1
- Preset zmywarki dopasowany do rzeczywistych pomiarów (21 dni, 17 cykli): koniec po 3 min poniżej 4 W zamiast 15 min, powiadomienie ok. 8 min po zakończeniu grzania zamiast ok. 30 min.

## 0.1.0
- Pierwsza wersja: panel, wykrywanie cyklu, powiadomienia, encje przez MQTT discovery.
