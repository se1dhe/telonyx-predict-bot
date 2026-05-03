# Football Gold Hybrid Predictor Bot

Telegram-бот для Railway с переключаемым источником данных.

## Источники

```env
DATA_PROVIDER=LOCAL
```

Бесплатный режим: football-data.co.uk + ClubElo + SerpAPI.

```env
DATA_PROVIDER=API_FOOTBALL
```

Текущий режим через API-FOOTBALL / API-Sports.

## Railway

1. Запушить проект в GitHub.
2. Railway → New Project → Deploy from GitHub.
3. Перенести переменные из `.env` в Railway Variables.
4. Start command уже задан: `python -m app.main`.

## Важно

`.env` добавлен по запросу для быстрого деплоя, но `.gitignore` запрещает пушить его в GitHub.
После тестов лучше перевыпустить ключи, потому что они были переданы в чат.


## V2: fallback для LOCAL

Если `football-data.co.uk/fixtures.csv` возвращает 0 матчей на дату, LOCAL режим автоматически пробует взять ближайшие матчи через TheSportsDB:

```env
THESPORTSDB_ENABLED=true
THESPORTSDB_API_KEY=1
THESPORTSDB_LEAGUE_IDS=4328,4335,4332,4331,4334,4337
```

TheSportsDB нужен только для расписания. Форма, H2H, таблица и исторические odds всё равно считаются по football-data.co.uk.


## V3: ESPN fallback

Если `football-data.co.uk/fixtures.csv` и TheSportsDB вернули 0 матчей, бот пробует бесплатный ESPN scoreboard:

```env
ESPN_ENABLED=true
ESPN_LEAGUES=eng.1,esp.1,ita.1,ger.1,fra.1,ned.1,por.1,sco.1
```

Также в диагностике теперь видно, сколько матчей нашёл каждый источник:
- football-data fixtures
- TheSportsDB fixtures
- ESPN fixtures
- Использованный fallback


## V4: защита от зависаний

Добавлены:
- подробные логи по этапам pipeline;
- короткие HTTP timeout для бесплатных источников;
- SerpAPI timeout;
- общий pipeline timeout;
- новости ищутся только для финальных кандидатов, а не для всех матчей.

Рекомендуемые тестовые переменные:
```env
MATCHES_PER_DAY=3
MIN_AI_CONFIDENCE=25
MAX_RAW_EVENTS=8
MAX_CANDIDATES_FOR_AI=4
LOCAL_LOOKAHEAD_DAYS=1
PIPELINE_TIMEOUT_SECONDS=180
HTTP_TIMEOUT_SECONDS=12
NEWS_TIMEOUT_SECONDS=8
```


## V5: фикс OpenAI temperature

Исправлена ошибка:

```text
Unsupported parameter: 'temperature' is not supported with this model.
```

Теперь для моделей `gpt-5*`, `o1*`, `o3*`, `o4*` параметр `temperature` не передаётся.
Для старых моделей он остаётся.


## V6: бесплатный rule-based fallback без OpenAI

Добавлены переменные:

```env
AI_ENABLED=true
AI_FALLBACK_ON_ERROR=true
```

Если OpenAI возвращает `insufficient_quota`, `rate_limit`, billing error или другую ошибку, бот не падает.
Он автоматически выбирает матчи локальным алгоритмом по:
- форме команд;
- голам за/против;
- BTTS;
- Over 1.5 / Over 2.5;
- очным встречам;
- таблице;
- ClubElo, если доступен.

Чтобы полностью не использовать OpenAI:

```env
AI_ENABLED=false
AI_FALLBACK_ON_ERROR=true
```


## V7: потребительский формат Telegram-постов

Добавлены переменные:

```env
SHOW_TECH_DIAGNOSTICS=false
SHOW_DETAILED_PICKS=true
```

Что изменилось:
- в сводке теперь есть дата/время матча;
- явно написано, что ставить;
- объяснено, какой рынок искать у букмекера;
- добавлен риск, уверенность и ожидаемый счёт;
- техническая диагностика скрыта от пользователя по умолчанию;
- подробные прогнозы стали понятнее и короче;
- добавлены короткие правила банкролла.


## V8: чистый пользовательский вывод + точные ссылки

Из Telegram-сообщений убрана техническая диагностика:
- дата запуска;
- количество матчей от источника;
- отсеянные по score;
- внутренние score/data_quality.

Диагностика теперь остаётся только в Railway Logs.

Также:
- дата/время берутся из источника матча и не заменяются фразой «уточнить в линии букмекера»;
- лига/турнир берётся из маппинга источника, без фразы «лига уточняется»;
- для TheSportsDB используется точная ссылка `https://www.thesportsdb.com/event/{idEvent}`;
- для ESPN используется точная ссылка ESPN на конкретный матч;
- SofaScore search остаётся только как fallback, если точной ссылки источник не дал.


## V9: статистика winrate

Бот ведёт статистику по каждому прогнозу:
- сохраняет прогнозы в таблицу `predictions`;
- каждый час проверяет открытые прогнозы;
- закрывает прогноз как плюс/минус/возврат;
- в конце игрового дня отправляет отчёт winrate за день;
- в этом же отчёте показывает winrate за всё время.

Новые переменные:

```env
STATS_REPORT_ENABLED=true
DAILY_STATS_HOUR=23
DAILY_STATS_MINUTE=55
SHOW_TECH_DIAGNOSTICS=false
SHOW_DETAILED_PICKS=true
```

Winrate считается так:

```text
плюсы / (плюсы + минусы)
```

Возвраты по форе 0 / DNB не считаются ни плюсом, ни минусом.


## V10: фикс rule-based selector

Исправлена ошибка:

```text
AttributeError: 'TeamMetrics' object has no attribute 'points'
```

Причина:
`TeamMetrics` хранит `wins/draws/losses`, но не хранит поле `points`.

Исправление:
локальный алгоритм теперь считает очки сам:

```text
points = wins * 3 + draws
```

Также исправлена логика fallback:
если `AI_ENABLED=false`, ошибка локального алгоритма больше не маркируется как ошибка OpenAI и не запускается повторно.
