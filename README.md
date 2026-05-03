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


## V11: чистка пользовательского текста

Из Telegram-сообщений убраны:
- строка `Источник данных: LOCAL`;
- блок `Что важно учитывать`;
- предупреждение `LOCAL режим: нет гарантированно свежих составов и травм...`.

Техническая информация остаётся только в Railway Logs.


## V12: AI reasoning logs + bookmaker link

Добавлено:
- `LOG_AI_REASONING=true` — в Railway Logs пишутся:
  - матч;
  - ставка;
  - confidence;
  - risk;
  - expected score;
  - почему матч выбран;
  - reasoning OpenAI/локального алгоритма;
  - отклонённые AI матчи.
- `BOOKMAKER_LINK_ENABLED=true` — в прогнозе появляется ссылка на линию букмекера.
- По умолчанию используется поиск Pinnacle:
  `https://www.pinnacle.com/en/search?s={query}`

Важно:
точный deeplink Pinnacle на конкретное событие не гарантируется без официальной настройки deeplinks.
Поэтому ссылка букмекера сделана через настраиваемый template.

Новые переменные:

```env
LOG_AI_REASONING=true
BOOKMAKER_LINK_ENABLED=true
BOOKMAKER_NAME=Pinnacle
BOOKMAKER_SEARCH_URL_TEMPLATE=https://www.pinnacle.com/en/search?s={query}
```


## V13: замена Pinnacle на рабочие ссылки букмекеров с тоталами

Pinnacle search заменён, потому что ссылка не открывалась стабильно.

Новая схема:
- основной букмекер: DraftKings Soccer;
- резерв 1: BetMGM Soccer;
- резерв 2: Favbet Sport;
- в сообщении добавлена подсказка: искать `Total Goals / Over-Under / Тотал голов`.

Новые/обновлённые переменные:

```env
BOOKMAKER_LINK_ENABLED=true
BOOKMAKER_NAME=DraftKings
BOOKMAKER_SEARCH_URL_TEMPLATE=https://sportsbook.draftkings.com/sports/soccer
BOOKMAKER_BACKUP_LINKS_ENABLED=true
BOOKMAKER_BACKUP_LINKS=BetMGM|https://www.betmgm.com/en/sports/soccer-4/betting;Favbet|https://www.favbet.ua/uk/sports
BOOKMAKER_MARKET_HINT=Ищи рынок: Total Goals / Over-Under / Тотал голов
```


## V14: API-FOOTBALL free plan fix

Исправлена ошибка:

```text
Free plans do not have access to the Last parameter.
```

В free plan нельзя использовать:

```text
/fixtures?team=TEAM_ID&last=8
```

Теперь при:

```env
APIFOOTBALL_FREE_PLAN=true
```

бот использует:

```text
/fixtures?team=TEAM_ID&season=2025
```

и берёт последние завершённые матчи локально.

Новые переменные:

```env
APIFOOTBALL_FREE_PLAN=true
APIFOOTBALL_SEASON=2025
```


## V15: fix RawFixture import

Исправлена ошибка запуска:

```text
ImportError: cannot import name 'RawFixture' from 'app.schemas'
```

Причина:
в v14 `api_football.py` импортировал `RawFixture`, но модель не была добавлена в `schemas.py`.

Также исправлено:
- `TeamMetrics` теперь можно создавать из API-FOOTBALL без обязательных `team_id/name`;
- API_FOOTBALL фильтр теперь работает с `RawFixture` объектами, а не только со старыми dict.


## V16: fix detect_rejection_risks import

Исправлена ошибка старта контейнера:

```text
ImportError: cannot import name 'detect_rejection_risks' from 'app.services.api_football'
```

Причина:
в `free_data_provider.py` остался импорт старой функции `detect_rejection_risks`,
а в `api_football.py` после v14 функция называлась `detect_risks`.

Исправление:
добавлен совместимый alias:

```python
def detect_rejection_risks(ctx):
    return detect_risks(ctx)
```


## V17: fix LOCAL risks + DraftKings-only links

Исправлено:
- `detect_rejection_risks() takes 1 positional argument but 2 were given`.
  Теперь функция принимает `*args, **kwargs`, поэтому LOCAL provider снова собирает контексты.
- BetMGM и Favbet убраны.
- Bookmaker link теперь строится под DraftKings event slug:
  `https://sportsbook.draftkings.com/event/{slug}`

Пример:
```text
Auxerre — Angers -> https://sportsbook.draftkings.com/event/auxerre-vs-angers
```

Важно:
у DraftKings точная ссылка часто имеет вид:
```text
/event/auxerre-vs-angers/34020984
```
Где `34020984` — внутренний event id DraftKings. Его нельзя надёжно получить без отдельного odds/deeplink API.
Поэтому v17 формирует максимально близкий URL по slug. Если позже подключить источник event_id,
шаблон можно заменить на:
```env
BOOKMAKER_SEARCH_URL_TEMPLATE=https://sportsbook.draftkings.com/event/{slug}/{event_id}
```


## V18: точные DraftKings ссылки через SerpAPI

Проблема:
ссылка вида:

```text
https://sportsbook.draftkings.com/event/auxerre-vs-angers
```

невалидна, потому что DraftKings требует внутренний event_id:

```text
https://sportsbook.draftkings.com/event/auxerre-vs-angers/34020984
```

Решение:
бот теперь после выбора матчей делает SerpAPI Google search:

```text
site:sportsbook.draftkings.com/event/{slug} DraftKings
```

и берёт только URL, которые соответствуют формату:

```text
https://sportsbook.draftkings.com/event/.../{digits}
```

Если точная ссылка не найдена — бот НЕ подставляет фейковый slug URL,
а пишет:

```text
DraftKings: точная ссылка на матч пока не найдена
```

Новые переменные:

```env
DRAFTKINGS_RESOLVER_ENABLED=true
DRAFTKINGS_RESOLVER_MAX_RESULTS=5
BOOKMAKER_SEARCH_URL_TEMPLATE=
```


## V19: дневной winrate после каждого завершённого матча

Добавлено:
- когда очередной прогноз закрывается после окончания матча, бот сразу отправляет:
  - результат матча;
  - зашла ставка или нет;
  - дневной winrate на текущий момент;
  - количество плюсов/минусов/возвратов/ожидающих за день.
- в конце дня всё равно отправляется финальный отчёт:
  - дневной winrate;
  - общий winrate за всё время.

Новая переменная:

```env
STATS_AFTER_EACH_FINISHED_MATCH_ENABLED=true
```


## V20: монетизация, два канала, кабинет и платежи

Добавлено:
- два канала:
  - `TELEGRAM_PRIVATE_CHANNEL_ID` — приватный канал со всеми прогнозами;
  - `TELEGRAM_PUBLIC_CHANNEL` — бесплатный канал, куда постится 1 лучший матч дня.
- бот-меню без текстовых команд после старта:
  - выбор языка при первом запуске;
  - RU/EN интерфейс;
  - тарифы: 1 день, 3 дня, 1 месяц;
  - личный кабинет;
  - история транзакций.
- Telegram Stars:
  - цены считаются от USDT через `STARS_PER_USDT`;
  - после успешной оплаты бот создаёт одноразовую ссылку в приватный канал.
- PayKassa:
  - endpoint `/paykassa/ipn`;
  - домен проекта: `https://predict.telonyx.app`;
  - после IPN бот подтверждает платёж и выдаёт invite link.
- канальные посты стали bilingual-friendly: VIP/public заголовки RU/EN.

### PayKassa endpoint

В кабинете PayKassa нужно будет указать IPN/notification URL:

```text
https://predict.telonyx.app/paykassa/ipn
```

Healthcheck:

```text
https://predict.telonyx.app/health
```

### Важно

Для выдачи доступа бот должен быть администратором приватного канала и иметь право создавать invite links.


## V21: bilingual predictions, styled buttons, subscription guard

Добавлено:
- прогнозы теперь RU/EN: основные поля, инструкция по рынку, риск, confidence, expected score, key numbers;
- inline-кнопки формируются как raw JSON с `style`: `primary`, `success`, `danger` where needed;
- добавлен переключатель `STYLED_BUTTONS_ENABLED`;
- подписочный guard:
  - уведомление за 24 часа;
  - уведомление за 5 часов;
  - уведомление за 1 час;
  - удаление пользователя из приватного канала после окончания доступа;
  - после продления уведомления сбрасываются.

Новые переменные:
```env
SUBSCRIPTION_CHECK_INTERVAL_MINUTES=15
SUBSCRIPTION_KICK_ENABLED=true
SUBSCRIPTION_NOTIFY_ENABLED=true
STYLED_BUTTONS_ENABLED=true
```


## V22: TelOnyx payment pages + PayKassa test setup

Добавлены страницы:
```text
https://predict.telonyx.app/payment/success
https://predict.telonyx.app/payment/fail
```

Они оформлены в тёмном TelOnyx-стиле и подходят для redirect URL в PayKassa.

### Данные для PayKassa

В кабинете PayKassa:
```text
URL мерчанта:
https://predict.telonyx.app

URL уведомлений об оплате инвойса [sci_confirm_order]:
https://predict.telonyx.app/paykassa/ipn

URL успешной оплаты [redirect]:
https://predict.telonyx.app/payment/success

URL сбоя при оплате [redirect]:
https://predict.telonyx.app/payment/fail

URL обработчика транзакций криптовалют [sci_confirm_transaction_notification]:
оставить пустым

Принимать любую сумму:
Нет

Тестовый режим (SCI/API):
Включён на время отладки
```

Railway:
```env
PAYKASSA_ENABLED=true
PAYKASSA_TEST_MODE=true
PROJECT_PUBLIC_URL=https://predict.telonyx.app
```


## V23: TELEGRAM_TARGET_CHAT_ID удалён

Теперь используется только один закрытый канал:

```env
TELEGRAM_PRIVATE_CHANNEL_ID=-1003952952921
```

`TELEGRAM_TARGET_CHAT_ID` полностью удалён из настроек и больше не нужен.
Все технические сообщения, ошибки, приватные прогнозы, результаты матчей и VIP-отчёты отправляются в `TELEGRAM_PRIVATE_CHANNEL_ID`.
Публичный бесплатный прогноз отправляется в `TELEGRAM_PUBLIC_CHANNEL`.


## V24: кнопки оплаты в уведомлениях об окончании подписки

Добавлено:
- уведомление за 24 часа до окончания подписки приходит сразу с кнопками продления;
- уведомление за 5 часов приходит сразу с кнопками продления;
- уведомление за 1 час приходит сразу с кнопками продления;
- сообщение после окончания подписки тоже приходит с кнопками продления.

Для каждого срока доступны быстрые кнопки:
- Stars;
- PayKassa USDT.

Кнопки используют существующие callback:
```text
pay:stars:1d
pay:paykassa:1d
pay:stars:3d
pay:paykassa:3d
pay:stars:30d
pay:paykassa:30d
```


## V25: PayKassa no-JSON response fix

Исправлена ошибка:
```text
JSONDecodeError: Expecting value: line 1 column 1 (char 0)
```

Причина:
PayKassa вернула не JSON, а HTML/текст. Такое обычно происходит при неверном `PAYKASSA_SCI_ID`,
неподтверждённом домене, неактивном магазине, неверном `PAYKASSA_SYSTEM` или несовпадении test mode.

Что изменено:
- PayKassa ответ теперь сначала читается как raw text;
- raw response логируется в Railway Logs;
- поддержаны JSON, URL-encoded и plain URL ответы;
- пользователь больше не видит падение обработчика;
- если счёт не создан, бот показывает нормальное сообщение и оставляет кнопки оплаты.

Для диагностики в Railway Logs теперь ищи строки:
```text
PayKassa create_order request payload
PayKassa create_order HTTP response
```
