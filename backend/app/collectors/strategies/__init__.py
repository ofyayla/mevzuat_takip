"""Strateji kayıt defteri: sources.yaml'daki ``strategy`` adı → sınıf."""
from __future__ import annotations

from app.collectors.base import Strategy
from app.collectors.strategies.css_list import CssListStrategy
from app.collectors.strategies.feed import FeedStrategy
from app.collectors.strategies.link_pattern import LinkPatternStrategy
from app.collectors.strategies.resmi_gazete import ResmiGazeteStrategy
from app.collectors.strategies.wordpress import WordPressApiStrategy

STRATEGIES: dict[str, type] = {
    s.name: s
    for s in (FeedStrategy, CssListStrategy, LinkPatternStrategy, WordPressApiStrategy, ResmiGazeteStrategy)
}


def get_strategy(name: str) -> Strategy:
    try:
        return STRATEGIES[name]()
    except KeyError:
        raise KeyError(f"Bilinmeyen strateji: {name}. Geçerli: {', '.join(sorted(STRATEGIES))}") from None
