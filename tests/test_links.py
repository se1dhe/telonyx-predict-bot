from datetime import date
from types import SimpleNamespace

from app.pipeline import build_ggbet_match_url, find_pick_market_odds
from app.pipeline import DailyPipeline
from app.schemas import RawFixture
from app.services.bookmaker_resolver import ggbet_slug_candidates
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
                "values": [{"value": "Over 1.5", "odd": "1.42"}],
            },
        ],
    )

    assert find_pick_market_odds(pick, ctx) == 1.42
