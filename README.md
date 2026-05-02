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
