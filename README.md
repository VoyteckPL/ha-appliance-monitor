# HA Appliance Monitor

Aplikacja (add-on) do Home Assistant OS. Wykrywa start i koniec cyklu "głupich" urządzeń
(pralka, suszarka, zmywarka…) na podstawie poboru mocy z gniazdka, wysyła powiadomienia
na telefon i tworzy w HA encje do automatyzacji. Całą konfigurację robisz w panelu,
bez YAML-a.

## Instalacja

1. W HA: **Ustawienia → Aplikacje → Sklep aplikacji → ⋮ → Repozytoria**,
   dodaj `https://github.com/VoyteckPL/ha-appliance-monitor`
   (przy repo prywatnym HA nie pobierze go bez tokenu, więc najprościej ustawić je jako publiczne).
2. Zainstaluj **Mosquitto broker** (jeśli jeszcze go nie masz) i dodaj integrację MQTT,
   którą HA sam zaproponuje.
3. Zainstaluj **Appliance Monitor**, włącz „Pokaż na pasku bocznym" i uruchom.
4. Otwórz panel **Urządzenia** i kliknij **+ Dodaj**.

## Aktualizacje

Po każdej zmianie wersji w `appliance_monitor/config.yaml` HA pokaże przycisk „Aktualizuj".

## Jak to działa

- Co 5 s odczytuje moc z wybranego czujnika.
- **Start**: moc powyżej progu startu przez zadany czas.
- **Koniec**: moc poniżej progu końca przez zadany czas (dłuższy niż najdłuższa przerwa w cyklu).
- Cykle krótsze niż „min. czas cyklu" są odrzucane jako zakłócenia.
- Wykres w panelu pokazuje moc z ostatnich 3 godzin wraz z progami, co ułatwia kalibrację.
- **Szczegóły** każdego urządzenia: statystyki, koszt (cena prądu w ⚙), wykres energii dziennej i wykres mocy każdego cyklu.
- **Wczytaj historię z HA** uzupełnia historię cykli z rejestratora Home Assistanta (do 14 dni wstecz).
- **Sprawdź ustawienia** w formularzu urządzenia odtwarza historię przy podanych progach i pokazuje, ile cykli by wykryto.
- Na karcie pracującego urządzenia widać szacowany czas końca.

Dla każdego urządzenia w HA powstają encje: Stan (`idle`/`running`/`finished`), Pracuje,
Energia cyklu, Czas cyklu, Ostatnie zakończenie.

## Testy

```
python tests/test_engine.py
```
