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


## V26: бот отвечает во время сбора матчей

Исправлена проблема:
при `RUN_ON_START=true` приложение делало:

```python
await send_daily_gold_matches(bot)
await dp.start_polling(bot)
```

Из-за этого polling не стартовал, пока бот собирал/анализировал матчи.
Пользователь видел, что контейнер работает, но `/start` и кнопки не отвечали.

Теперь:
```python
asyncio.create_task(send_daily_gold_matches(bot))
await dp.start_polling(bot)
```

То есть:
- сбор прогнозов идёт в фоне;
- Telegram polling стартует сразу;
- бот отвечает на команды и кнопки во время анализа;
- добавлен `pipeline_lock`, чтобы тяжёлый сбор не запускался параллельно два раза.


## V27: PayKassa 403 diagnostics + official TRON_TRC20 system

По логам PayKassa отвечает:
```text
PayKassa HTTP 403: nginx
```

Это ответ не от нашего домена, а от `https://paykassa.pro/sci/0.3/index.php`.
Для `sci_create_order` официальный пример PayKassa использует `system=TRON_TRC20`, а не `tron_trc20`,
поэтому дефолт изменён на:

```env
PAYKASSA_SYSTEM=TRON_TRC20
PAYKASSA_ENDPOINT=https://paykassa.pro/sci/0.3/index.php
```

Также:
- добавлен `PAYKASSA_ENDPOINT`;
- headers сделаны ближе к обычному form/curl запросу;
- при 403 бот пишет более полезную диагностику в Railway Logs.


## V28: зелёная CTA-кнопка под постами в открытом канале

Добавлено:
- под каждым постом в `TELEGRAM_PUBLIC_CHANNEL` появляется зелёная кнопка:
  `🔒 Получить VIP доступ / Get VIP access`;
- кнопка ведёт в Telegram-бота:
  `https://t.me/{TELEGRAM_BOT_USERNAME}?start=vip`;
- приватный канал кнопки не получает — там остаются полные VIP-прогнозы.

Новые переменные:
```env
TELEGRAM_BOT_USERNAME=telonyx_predict_bot
PUBLIC_CHANNEL_CTA_ENABLED=true
```


## V29: повністю український інтерфейс

Багатомовність тимчасово вимкнено.

Змінено:
- `/start` одразу відкриває українське меню без вибору мови;
- усі кнопки бота українською;
- повідомлення про підписку українською;
- платіжні повідомлення українською;
- сторінки `/payment/success` і `/payment/fail` українською;
- пости у відкритому та приватному каналах українською;
- AI prompt просить модель повертати всі текстові поля українською;
- fallback-прогнози та статистика winrate українською.



## v30: многоязычные каналы

Бот поддерживает три языка: `uk`, `en`, `ru`.

Главное правило: если канал для языка не заполнен в `.env`, бот его не обрабатывает.

Переменные:

```env
SUPPORTED_LANGUAGES="uk,en,ru"
DEFAULT_LANGUAGE="uk"
TELEGRAM_PRIVATE_CHANNEL_UK="-1003952952921"
TELEGRAM_PRIVATE_CHANNEL_EN=""
TELEGRAM_PRIVATE_CHANNEL_RU=""
TELEGRAM_PUBLIC_CHANNEL_UK="@telonyx_predict"
TELEGRAM_PUBLIC_CHANNEL_EN=""
TELEGRAM_PUBLIC_CHANNEL_RU=""
```

Что изменено:

- при `/start` пользователь может выбрать язык;
- открытый канал получает только пост своего языка;
- приватный канал получает VIP-посты своего языка;
- если EN/RU-каналы пустые, бот их пропускает;
- при оплате пользователь получает invite именно в приватный канал выбранного языка;
- после окончания подписки пользователь удаляется из приватного канала своего языка;
- уведомления об окончании подписки приходят с кнопками продления через Stars и PayKassa;
- статистика winrate после завершения матчей и в конце дня отправляется во все заполненные приватные языковые каналы.


## v32: PayKassa SCI fix + безопасная отправка в Telegram

Что изменено:

- PayKassa SCI endpoint обновлён до `https://paykassa.pro/sci/0.4/index.php`.
- Добавлен fallback endpoint `https://paykassa.app/sci/0.4/index.php`.
- `PAYKASSA_SYSTEM` теперь может быть числом. Для USDT TRC20 используется `30`.
- `PAYKASSA_SYSTEM_NAME="TRON_TRC20"` оставлен как человекочитаемое название.
- `sci_confirm_order` используется для подтверждения IPN по `private_hash`.
- Если Telegram канал указан неверно или бот не админ, daily job больше не падает из-за повторной отправки fallback-сообщения в тот же недоступный chat_id. Ошибка пишется в Railway Logs.

Рекомендуемые PayKassa переменные:

```env
PAYKASSA_ENABLED="true"
PAYKASSA_SCI_ID="29914"
PAYKASSA_SCI_KEY="..."
PAYKASSA_SYSTEM="30"
PAYKASSA_SYSTEM_NAME="TRON_TRC20"
PAYKASSA_CURRENCY="USDT"
PAYKASSA_TEST_MODE="true"
PAYKASSA_ENDPOINT="https://paykassa.pro/sci/0.4/index.php"
PAYKASSA_FALLBACK_ENDPOINTS="https://paykassa.app/sci/0.4/index.php"
```


## v33: Kyiv time + Ukrainian text normalization

Что изменено:

- Время матчей в постах теперь принудительно выводится в таймзоне `TZ` из `.env`.
- Если источник отдаёт время без timezone, бот считает его UTC и переводит в `Europe/Kiev`.
- Метка времени в постах стала явной: `Дата/час (Київ)`.
- Усилена нормализация украинского текста: исправлены смешанные RU/UA фразы вроде `ближе к победе`, `должен забить`, `безопаснее`, `Уверенность`, `размер ставки`.
- Расширен детектор смешанного языка: если AI/fallback вернул русский фрагмент в украинский пост, бот заменяет его на украинский структурированный текст.


## v34: future-only predictions + faster after-match winrate

Что изменено:

- Прогнозы больше не должны попадать на уже начавшиеся или завершённые матчи.
- Добавлен фильтр `MIN_MATCH_START_LEAD_MINUTES`: матч должен стартовать минимум через указанное количество минут.
- LOCAL-источник теперь тоже проходит future-only фильтр; раньше LOCAL мог вернуть ближайшие события без проверки времени старта.
- Проверка результатов теперь запускается не раз в час, а по `RESULT_CHECK_INTERVAL_MINUTES`.
- После каждого найденного финального счёта бот закрывает прогноз и публикует дневной winrate на текущий момент.
- Для LOCAL добавлены дополнительные источники результатов: TheSportsDB past events и ESPN scoreboard.
- В Railway Logs теперь видно, почему результат ещё не закрыт или когда прогноз закрыт.

Новые переменные:

```env
MIN_MATCH_START_LEAD_MINUTES="20"
RESULT_CHECK_INTERVAL_MINUTES="15"
```


## v35: fix fixture_sort_key crash

Исправлено:

- Убрана ошибка `name 'fixture_sort_key' is not defined` в LOCAL pipeline.
- Добавлен совместимый alias `fixture_sort_key -> fixture_sort_timestamp`, чтобы старые вызовы не ломали сбор прогнозов.



## v36: definitive LOCAL fixture_sort_key fix

Исправлено:

- Ошибка `name 'fixture_sort_key' is not defined` была в `app/services/free_data_provider.py`, а не только в `pipeline.py`.
- Добавлена локальная функция `fixture_sort_key()` прямо в `free_data_provider.py`, где она используется при сортировке LOCAL fixtures.



## v37: fix football_data_season_code crash

Исправлено:

- Убрана ошибка `NameError: name 'football_data_season_code' is not defined`.
- Добавлена функция расчёта сезона football-data.co.uk в формате `2526`, `2627` и т.д.
- После этого LOCAL pipeline снова может собирать историю матчей для контекста и не должен получать `0 прогнозов` из-за падения контекстов.



## v38: OpenAI/Gemini provider switch

Добавлено:

- Переключение AI-провайдера через `AI_PROVIDER`.
- Поддерживаются значения:
  - `AI_PROVIDER="openai"`
  - `AI_PROVIDER="gemini"`
- Добавлены переменные:
  - `GEMINI_API_KEY`
  - `GEMINI_MODEL`
- Gemini вызывается через Google Generative Language REST API без новой Python-зависимости.
- Для Gemini включён `responseMimeType=application/json`, чтобы модель возвращала валидный JSON.

Пример Gemini-режима:

```env
AI_ENABLED="true"
AI_PROVIDER="gemini"
GEMINI_API_KEY="your_google_ai_studio_key"
GEMINI_MODEL="gemini-1.5-pro"
```

Fallback остаётся прежним:

```env
AI_FALLBACK_ON_ERROR="true"
```

Если Gemini/OpenAI даст ошибку, бот сможет перейти на локальный rule-based отбор.



## v40: today only + more match sources

Изменено:

- Откат идеи lookahead: бот снова ищет только матчи на текущую дату запуска.
- LOCAL provider теперь объединяет источники `football-data + TheSportsDB + ESPN`, а не останавливается на первом успешном источнике.
- ESPN расширен дополнительными лигами: MLS, Brazil, Argentina, Mexico, Turkey, Belgium, а также вторые дивизионы England/Spain/Italy/Germany/France.
- Исправлен ESPN fixture_id.
- Для ESPN-only лиг history из football-data не валит pipeline, а возвращает пустую историю.
- Добавлены настройки мягкого контекстного фильтра:
  - `MIN_CONTEXT_DATA_QUALITY`
  - `MIN_CONTEXT_PRE_AI_SCORE`
- Рекомендуется увеличить `MAX_RAW_EVENTS` и `MAX_CANDIDATES_FOR_AI`, чтобы AI/локальный селектор видел больше матчей сегодняшнего дня.



## v41: PostgreSQL on Railway

Изменено:

- Добавлена зависимость `asyncpg`.
- `DATABASE_URL` теперь автоматически нормализуется:
  - `postgres://...` -> `postgresql+asyncpg://...`
  - `postgresql://...` -> `postgresql+asyncpg://...`
  - `postgresql+asyncpg://...` остаётся как есть.
- SQLite локально всё ещё поддерживается.
- Для PostgreSQL включён `pool_pre_ping`, небольшой pool и `pool_recycle`.
- `.env` и `.env.example` переведены на Railway reference variable:

```env
DATABASE_URL="${{Postgres.DATABASE_URL}}"
```

Важно: в Railway нужно добавить PostgreSQL service и поставить эту reference variable в сервис бота.



## v42: ESPN helper fix + PostgreSQL init diagnostics

Исправлено:

- Добавлены отсутствующие функции `parse_espn_event_time()` и `espn_event_url()`.
- ESPN provider больше не должен падать с ошибкой `name 'parse_espn_event_time' is not defined`.
- В `init_db()` добавлено логирование драйвера БД и списка созданных PostgreSQL таблиц.
- После старта в Railway Logs должна появиться строка вида:
  `DB init complete. PostgreSQL public tables: [...]`.



## v43: PostgreSQL DATABASE_URL Railway fallback

Исправлено:

- Если `DATABASE_URL` задан как буквальный `${{Postgres.DATABASE_URL}}` и Railway reference не развернулся, бот больше не падает с непонятной SQLAlchemy ошибкой.
- Добавлено понятное сообщение, что reference variable указывает на неправильное имя Postgres service.
- Добавлен fallback на `DATABASE_PUBLIC_URL`, `POSTGRES_URL`, `POSTGRES_DATABASE_URL`.
- Добавлен fallback на отдельные переменные `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE`.

Правильный вариант для Railway:

```env
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

Важно: `Postgres` должен быть точным названием PostgreSQL service в Railway.

