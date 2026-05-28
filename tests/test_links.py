from datetime import date
from types import SimpleNamespace

from app.pipeline import build_ggbet_match_url, find_pick_market_odds, find_pick_market_offer
from app.pipeline import DailyPipeline
from app.schemas import RawFixture
from app.services.api_football import odds_row_has_min_allowed_market, match_ggbet_event_to_fixture, simplify_odds
from app.services.bookmaker_resolver import GGBetResolver, ggbet_market_matches_bet, ggbet_odd_matches_bet, ggbet_slug_candidates
from app.services.ggbet_scraper import GGBetEvent, ggbet_event_to_odds, parse_match_page
from app.schemas import AiPick, CandidateContext, TeamMetrics
from app.services.render import render_daily_summary, render_pick_detail


def test_build_ggbet_match_url_matches_known_format() -> None:
    assert (
        build_ggbet_match_url(
            "Vvsb",
            "Excelsior Maassluis",
            "2026-05-27T17:00:00+00:00",
        )
        == "https://ggbet.ua/uk-ua/sports/match/vvsb-vs-excelsior-maassluis-27-05"
    )


def test_build_ggbet_match_url_preserves_club_abbreviations_and_aliases() -> None:
    assert (
        build_ggbet_match_url(
            "Sparta Nijkerk",
            "Ijsselmeervogels",
            "2026-05-27T17:00:00+00:00",
        )
        == "https://ggbet.ua/uk-ua/sports/match/sparta-nijkerk-vs-vv-ijsselmeervogels-27-05"
    )
    assert (
        build_ggbet_match_url(
            "Velez Sarsfield Res.",
            "Instituto Res.",
            "2026-05-27T18:00:00+00:00",
        )
        == "https://ggbet.ua/uk-ua/sports/match/velez-sarsfield-res-vs-instituto-res-27-05"
    )


def test_ggbet_generated_fallback_candidates_use_en_slug_rules() -> None:
    assert ggbet_slug_candidates(
        "Crystal Palace",
        "Rayo Vallecano",
        "2026-05-27T19:00:00+00:00",
        "Europe/Kiev",
    )[0] == "crystal-palace-vs-rayo-vallecano-27-05"
    assert ggbet_slug_candidates(
        "Sparta Nijkerk",
        "Ijsselmeervogels",
        "2026-05-27T18:00:00+00:00",
        "Europe/Kiev",
    )[0] == "sparta-nijkerk-vs-vv-ijsselmeervogels-27-05"


def test_ggbet_total_over_15_market_mapping() -> None:
    market = {
        "id": "398t1_5",
        "name": "Total",
        "typeId": 398,
        "odds": [{"name": "over 1.5", "value": "1.44"}],
    }

    assert ggbet_market_matches_bet(market, "OVER_1_5")
    assert ggbet_odd_matches_bet(market["odds"][0], "OVER_1_5")


def test_ggbet_bootstrap_can_use_env_graphql_settings() -> None:
    resolver = object.__new__(GGBetResolver)
    resolver.settings = SimpleNamespace(
        ggbet_graphql_endpoint="//gg-b-gql.ggbet.ua",
        ggbet_graphql_token="token",
        ggbet_graphql_app_id="22",
        ggbet_graphql_access_token="access",
        ggbet_locale="en",
    )

    assert resolver._load_bootstrap_from_env() == {
        "endpoint": "https://gg-b-gql.ggbet.ua/graphql",
        "token": "token",
        "app_id": "22",
        "access_token": "access",
        "locale": "en",
    }


def test_ggbet_bootstrap_can_join_split_env_token() -> None:
    resolver = object.__new__(GGBetResolver)
    resolver.settings = SimpleNamespace(
        ggbet_graphql_endpoint="https://gg-b-gql.ggbet.ua/graphql",
        ggbet_graphql_token="",
        ggbet_graphql_token_part_1="abc.",
        ggbet_graphql_token_part_2="def",
        ggbet_graphql_app_id="22",
        ggbet_graphql_access_token="access",
        ggbet_locale="en",
    )

    assert resolver._load_bootstrap_from_env()["token"] == "abc.def"


def test_ggbet_dom_parser_extracts_over_15_match_url_and_time() -> None:
    from zoneinfo import ZoneInfo

    event = parse_match_page(
        "https://ggbet.ua/en/sports/match/bosnia-and-herzegovina-vs-north-macedonia-29-05",
        "21:30\nMay 29\nBosnia and Herzegovina\nNorth Macedonia\n1x2\nBosnia\n1.97\n"
        "Total\nOver 1.5\n1.37\nUnder 1.5\n2.83\nHandicap\n-1\n2.1",
        ZoneInfo("Europe/Kiev"),
    )

    assert event is not None
    assert event.home_team == "Bosnia And Herzegovina"
    assert event.away_team == "North Macedonia"
    assert event.over15_odds == 1.37
    assert event.start_time is not None
    assert event.start_time.hour == 21


def test_ggbet_event_matches_api_fixture_and_carries_odds() -> None:
    event = GGBetEvent(
        home_team="Fluminense FC RJ",
        away_team="Deportivo La Guaira",
        start_time=None,
        url="https://ggbet.ua/en/sports/match/fluminense-fc-rj-vs-deportivo-la-guaira-28-05",
        over15_odds=1.42,
    )
    fixture = RawFixture(
        fixture_id="fixture-1",
        date="2026-05-28T22:00:00+00:00",
        league_name="CONMEBOL Sudamericana",
        country="World",
        home_team="Fluminense",
        away_team="Deportivo La Guaira",
    )

    assert match_ggbet_event_to_fixture(event, [fixture], set()) == fixture
    assert ggbet_event_to_odds(event) == [
        {
            "bookmaker": "GGBET",
            "market": "Total",
            "values": [{"value": "Over 1.5", "odd": "1.42"}],
        }
    ]


def test_raw_fixture_filter_keeps_only_target_kyiv_date() -> None:
    pipeline = object.__new__(DailyPipeline)
    pipeline.settings = SimpleNamespace(
        allowed_countries=set(),
        preferred_league_ids=[],
        max_raw_events=20,
        min_match_start_lead_minutes=0,
        tz="Europe/Kiev",
    )
    fixtures = [
        RawFixture(
            fixture_id="today",
            date="2099-05-27T18:00:00+00:00",
            league_name="Test",
            country="World",
            home_team="Home",
            away_team="Away",
        ),
        RawFixture(
            fixture_id="tomorrow-local",
            date="2099-05-27T23:00:00+00:00",
            league_name="Test",
            country="World",
            home_team="Late",
            away_team="Away",
        ),
    ]

    filtered, stats = pipeline._filter_raw_fixtures(fixtures, date(2099, 5, 27))

    assert [fixture.fixture_id for fixture in filtered] == ["today"]
    assert stats.skipped_wrong_local_date == 1


def test_render_uses_exact_ggbet_link_and_hides_sofascore() -> None:
    pick = AiPick(
        fixture_id="1",
        match_title="Vvsb — Excelsior Maassluis",
        ai_rank_score=80,
        predicted_winner="ринок безпечніший",
        who_should_score="через тотал",
        main_bet_code="OVER_1_5",
        main_bet_label="Over 1.5",
        safe_bet_label="Over 1.5",
        risky_bet_label="Over 2.5",
        risk_level="низький",
        confidence=70,
        why_this_match_is_gold="ok",
        reasoning="ok",
        tracking_url="https://www.sofascore.com/search?q=Vvsb+Excelsior+Maassluis",
        bookmaker_url="https://ggbet.ua/uk-ua/sports/match/vvsb-vs-excelsior-maassluis-27-05",
        bookmaker_name="GGBET",
        bookmaker_odds=1.45,
    )

    text = render_daily_summary([pick], [], lang="uk") + "\n" + render_pick_detail(pick, lang="uk")

    assert "https://ggbet.ua/uk-ua/sports/match/vvsb-vs-excelsior-maassluis-27-05" in text
    assert "https://ggbet.ua/uk/sports" not in text
    assert "sofascore.com" not in text.lower()


def test_render_hides_ggbet_link_when_pick_url_is_empty() -> None:
    pick = AiPick(
        fixture_id="1",
        match_title="Vvsb — Excelsior Maassluis",
        ai_rank_score=80,
        predicted_winner="ринок безпечніший",
        who_should_score="через тотал",
        main_bet_code="OVER_1_5",
        main_bet_label="Over 1.5",
        safe_bet_label="Over 1.5",
        risky_bet_label="Over 2.5",
        risk_level="низький",
        confidence=70,
        why_this_match_is_gold="ok",
        reasoning="ok",
        tracking_url="",
        bookmaker_url="",
        bookmaker_name="GGBET",
        bookmaker_odds=1.45,
    )
    ctx = CandidateContext(
        fixture_id="1",
        start_time="2026-05-27T17:00:00+00:00",
        home_team="Vvsb",
        away_team="Excelsior Maassluis",
        home_team_id="vvsb",
        away_team_id="excelsior-maassluis",
        league_name="Test League",
        country="Netherlands",
        home_metrics=TeamMetrics(matches=1),
        away_metrics=TeamMetrics(matches=1),
    )

    text = render_daily_summary([pick], [], contexts_by_id={"1": ctx}, lang="uk")

    assert "ggbet.ua" not in text
    assert "Кеф / лінія:</b> GGBET — 1.45" in text


def test_render_hides_missing_bookmaker_odds() -> None:
    pick = AiPick(
        fixture_id="1",
        match_title="Vvsb — Excelsior Maassluis",
        ai_rank_score=80,
        predicted_winner="ринок безпечніший",
        who_should_score="через тотал",
        main_bet_code="OVER_1_5",
        main_bet_label="Over 1.5",
        safe_bet_label="Over 1.5",
        risky_bet_label="Over 2.5",
        risk_level="низький",
        confidence=70,
        why_this_match_is_gold="ok",
        reasoning="ok",
        tracking_url="",
        bookmaker_url="https://ggbet.ua/en/sports/match/vvsb-vs-excelsior-maassluis-27-05",
        bookmaker_name="GGBET",
        bookmaker_odds=None,
    )

    text = render_daily_summary([pick], [], lang="uk")

    assert "Кеф / лінія" not in text
    assert "ggbet.ua/en/sports/match" in text


def test_find_pick_market_odds_uses_exact_over_15_and_best_price() -> None:
    pick = AiPick(
        fixture_id="1",
        match_title="Home — Away",
        ai_rank_score=80,
        predicted_winner="ринок безпечніший",
        who_should_score="через тотал",
        main_bet_code="OVER_1_5",
        main_bet_label="Over 1.5",
        safe_bet_label="Over 1.5",
        risky_bet_label="Over 2.5",
        risk_level="низький",
        confidence=70,
        why_this_match_is_gold="ok",
        reasoning="ok",
        tracking_url="",
    )
    ctx = CandidateContext(
        fixture_id="1",
        start_time="2026-05-27T17:00:00+00:00",
        home_team="Home",
        away_team="Away",
        home_team_id="home",
        away_team_id="away",
        league_name="Test League",
        country="World",
        home_metrics=TeamMetrics(matches=1),
        away_metrics=TeamMetrics(matches=1),
        odds=[
            {
                "bookmaker": "A",
                "market": "Goals Over/Under",
                "values": [{"value": "Over 2.5", "odd": "1.90"}, {"value": "Over 1.5", "odd": "1.31"}],
            },
            {
                "bookmaker": "B",
                "market": "Goals Over/Under",
                "values": [{"value": "Under 1.5", "odd": "3.20"}, {"value": "Over 1.5", "odd": "1.42"}],
            },
        ],
    )

    assert find_pick_market_odds(pick, ctx) == 1.42
    assert find_pick_market_offer(pick, ctx) == {
        "bookmaker_id": None,
        "bookmaker": "B",
        "market": "Goals Over/Under",
        "value": "Over 1.5",
        "odd": 1.42,
    }


def test_api_football_odds_whitelist_filters_bookmakers() -> None:
    row = {
        "bookmakers": [
            {
                "id": 16,
                "name": "Unibet",
                "bets": [{"id": 5, "name": "Goals Over/Under", "values": [{"value": "Over 1.5", "odd": "1.55"}]}],
            },
            {
                "id": 32,
                "name": "Betano",
                "bets": [{"id": 5, "name": "Goals Over/Under", "values": [{"value": "Over 1.5", "odd": "1.42"}]}],
            },
        ]
    }

    assert odds_row_has_min_allowed_market(row, 1.3, {"OVER_1_5"}, ["32"])
    assert not odds_row_has_min_allowed_market(row, 1.5, {"OVER_1_5"}, ["32"])
    row["bookmakers"][1]["bets"][0]["values"] = [{"value": "Under 1.5", "odd": "1.80"}]
    assert not odds_row_has_min_allowed_market(row, 1.3, {"OVER_1_5"}, ["32"])
    row["bookmakers"][1]["bets"][0]["values"] = [{"value": "Over 1.5", "odd": "1.42"}]
    assert simplify_odds([row], bookmaker_ids=["32"]) == [
        {
            "bookmaker_id": 32,
            "bookmaker": "Betano",
            "market": "Goals Over/Under",
            "values": [{"value": "Over 1.5", "odd": "1.42"}],
        }
    ]
