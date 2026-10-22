# Changelog

## 0.10.0
- Interaktywne wykresy w statystykach: po najechaniu na miesiąc podpowiedź z danymi, po kliknięciu (lub dotknięciu) panel ze szczegółami miesiąca i porównaniem do tego samego miesiąca rok wcześniej.

## 0.9.1
- Noty odsetkowe są pomijane z czytelnym komunikatem (jak korekty).
- Faktury bez pozycji energii (same opłaty) nie powodują błędu odczytu.

## 0.9.0
- Odczyt faktur Energa w każdym układzie: sprzed fotowoltaiki (2019–2022), z premią prądową (2023), z depozytem w różnych wariantach (2024–2026). Sumy sprawdzone z zestawieniem CSV.
- Faktury obejmujące kilka miesięcy rozdzielane na miesiące proporcjonalnie do dni; kilka faktur z jednego miesiąca sumuje się.
- Faktury zapisywane po numerze: duplikat tej samej faktury nadpisuje się, nie podwaja.
- Starsze lata bez paneli: podsumowanie pokazuje tylko to, co dotyczy danego okresu.
- Historia wieloletnia opiera się na CSV (uwzględnia korekty), PDF-y uzupełniają szczegóły.
- Wykresy z wieloma miesiącami: rzadsze etykiety osi.

## 0.8.1
- Wgrywanie wielu faktur naraz: postęp („3 z 140”) i zwięzłe podsumowanie (ile wczytano, ile korekt pominięto, które pliki się nie odczytały).

## 0.8.0
- Import zestawienia rozliczeń z Energa 24 (CSV): cała historia kwot faktur i rozliczonych kWh od początku umowy, wykresy miesięczne i tabela rok po roku. Faktury PDF z danymi szczegółowymi mają pierwszeństwo przed danymi z CSV.

## 0.7.0
- Osobny ekran „📊 Statystyki” (zamiast okna): filtr okresu (wszystkie, 12 / 6 miesięcy, wybrany rok), wgrywanie faktur, podsumowanie prostym językiem, wykresy (płatności, pobór i oddawanie, ceny, rok do roku), koszt urządzeń w okresie, tabela faktur i podsumowanie roczne.

## 0.6.2
- Rachunki: podsumowanie prostym językiem (co na plus, co na minus) zamiast kafelków ze skrótami.

## 0.6.1
- Rachunki: wykresy (ile płacisz miesięcznie z podziałem na depozyt, pobór i oddawanie, ceny za kWh) oraz podsumowanie roczne.

## 0.6.0
- Zakładka „Rachunki” (🧾): wgrywasz PDF faktury Energa, a dodatek odczytuje pobraną i oddaną energię, ceny, depozyt i kwotę do zapłaty. Bez ręcznego wpisywania.
- Przy trybie „Z fotowoltaiką” ceny zakupu i oddanej kWh aktualizują się same z najnowszej faktury.
- Statystyki: średni rachunek miesięcznie, zysk z depozytu, za ile procent ceny zakupu oddajesz energię, udział opłat stałych, energia skompensowana godzinowo.
- Nowe encje w HA (urządzenie „Rachunki za prąd”): ostatni rachunek, średni rachunek, cena zakupu, wartość oddanej kWh, depozyt.
- Obraz dodatku zawiera teraz poppler-utils (czytanie PDF), więc aktualizacja przebuduje dodatek.

## 0.5.0
- Koszt cyklu z fotowoltaiką (net-billing): w ⚙ → „Cena prądu” → „Z fotowoltaiką” wybierasz liczniki pobranej i oddanej energii oraz wpisujesz cenę zakupu i wartość oddanej kWh z faktury. Koszt liczony godzina po godzinie z salda licznika: energia pokryta nadwyżką z paneli kosztuje tyle, ile utracona wartość oddanej energii, reszta pełną cenę z sieci. Dotyczy panelu, encji kosztu, powiadomień i podsumowania tygodnia.

## 0.4.0
- Przycisk „Wyjęte" w powiadomieniu na telefonie, w panelu i jako encja przycisku w HA; przypomnienia (co X min, maks. N razy) aż do potwierdzenia.
- Cisza nocna: zakończenia cykli z nocy przychodzą rano jako jedno zbiorcze powiadomienie.
- Alarm o nietypowym cyklu (np. dużo dłuższym lub droższym niż zwykle).
- Rozpoznawanie programów z historii, własne nazwy (np. Eko), porównanie z poprzednim cyklem tego programu.
- Nowe encje: koszt ostatniego cyklu, program, cykle dziś / 7 dni, energia dziś, szacowany koniec, przycisk „Wyjęte".
- Placeholdery w treści: {cost} {program} {ago}; tygodniowe podsumowanie (opcjonalne).

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
