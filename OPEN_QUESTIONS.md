# Открытые вопросы

Эти вопросы являются очередью проверок, а не запросом очередного согласования пользователя.

1. Выбрать публичный H1/D1 provider с достаточной глубиной, понятным часовым поясом и приемлемыми условиями использования. ECB reference rate не заменяет OHLC.
2. Перепроверить неоднозначные timestamps в старом HistData и invalid OHLC Dukascopy до любого импорта. Не исправлять сезонный сдвиг без доказательств.
3. Для каждого macro series определить canonical ID, units, историческую глубину, календарь, original releases и vintages. HTTP 200 и один валидный sample не закрывают M0.
4. FRED/ALFRED API требуют ключа; проверить публичные альтернативы для bootstrap, не выдавая current revised values за исторические vintages.
5. US/DE 2Y: выбрать сопоставимые серии с доказанной частотой и временем доступности. Daily yields не подходят для измерения first-hour reaction.
6. CFTC: восстановить исторические actual publication timestamps, включая внеплановые задержки и исправления. Текущее расписание покрывает только 38 строк консервативной оценкой; 121 строка остаётся с неизвестной доступностью.
7. Consensus: проверить коммерческий доступ, глубину PIT, timestamps и права на использование. Точную цену не выдумывать. До появления данных CPI/NFP surprise experiments = unavailable.
8. M5 registry фиксирует пять published effects и их data requirements. Для перевода из unavailable нужны historical consensus для CPI/NFP, futures/OIS surprise factors для FOMC/ECB и узкие FX windows; текущий unconditional baseline не считается репликацией.
9. M4 использует NY17 и не пропускает неполную сессию. До строгого допуска и расширения истории нужно доказать исторические версии provider calendar и holidays; текущая calendar metadata не считается vintage.
10. Зафиксировать train/validation/test и purge/embargo с учётом пересекающихся labels до запуска экспериментов.
11. Настроить резервное копирование data и обновления после стабилизации ingestion. На этой стадии persistent volume не является backup.
12. При необходимости доступа вне LAN — отдельно выбрать домен и аутентификацию; сейчас внешней публикации нет.
13. FOMC: найти независимое доказательство historical availability/receipt и архив изменений страниц. До этого точный официальный release time хранится отдельно от `available_at`.
14. ECB: FOEDB и страницы релизов подтвердили 14:15 Europe/Berlin для текущего окна. Остаётся найти независимое доказательство historical receipt, архив изменений страниц и проверку исключений при расширении истории.
15. M7 дал только 31 positioning-regime feature row после as-of и minimum-history фильтров; 114 событий не имеют доступной позиции, ещё 8 имеют недостаточную историю. Решить, расширять ли historical CFTC availability до любых formal contrasts.
16. Завершить free/public continuous coverage: выбрать и документировать долгую US/EU equity proxy history и расширить EUR/USD до общего периода 2004/2005+.
17. После common D1 dataset реализовать deterministic DENN snapshots/decay kernels и зафиксировать feature definitions до первого spectral run.
18. Для futures/OIS policy surprises отдельно проверить доступность контрактной истории и intraday timestamps для FOMC и ECB; фактическое изменение ставки не заменяет surprise factor.
19. Historical consensus sample и его лицензирование отложены до завершения free/public dataset и spectral MVP; schema и null semantics остаются готовыми.
20. До расширения любого публичного источника проверить условия локального хранения и derived research use. Доступный HTTP endpoint сам по себе не доказывает эти права.
21. Для расширения Dukascopy tick history измерить пропуски, дубликаты, spread outliers и rate limits на нескольких event days; отдельно проверить условия использования и архивную неизменность.

## H1/D1 follow-up

- Investigate 13 invalid provider OHLC and 8 incomplete NY17 sessions; no guessed replacement.
- Verify Dukascopy redistribution terms, published quotas, historical calendar changes and revisions before expanding use.
- Recover BLS/ECB/Fed publication archives and CFTC exceptional release dates. Audit ALFRED access without making keys a prerequisite for other sources.
- Historical consensus remains unavailable; surprise-based experiments stay unavailable until legitimate forecast vintages are obtained.
- BLS archive pages may be reissued at the same URL. Compare future snapshots and recover correction metadata before declaring release vintages final.
- Establish evidence for historical `available_at` or use a documented conservative delay; embargo timestamps alone are not strict PIT availability.
- CFTC: решить отдельным экспериментом, нужны ли Futures + Options, continuous-contract/roll features и нормализация по open interest. Не смешивать определения с уже сохранённым Futures Only рядом.
