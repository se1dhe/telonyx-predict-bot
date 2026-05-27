from app.pipeline import build_ggbet_match_url
from app.schemas import AiPick
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
