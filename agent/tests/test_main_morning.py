import inspect
from pathlib import Path

from src.main import run_daemon, run_morning_routine


AGENT_ROOT = Path(__file__).resolve().parents[1]


class MorningDatabase:
    def query(self, *_args, **_kwargs):
        return []


class MorningAnalyzer:
    def __init__(self):
        self.prospects_updated = 0

    def run_technical_analysis(self):
        return []

    def generate_morning_briefing(self):
        return "briefing"

    def update_prospects(self):
        self.prospects_updated += 1


def test_morning_routine_never_calls_legacy_public_scrapers(monkeypatch):
    network_calls = []

    def forbidden_network(url, *_args, **_kwargs):
        network_calls.append(str(url))
        raise AssertionError("legacy public scraper must remain disabled")

    monkeypatch.setattr("urllib.request.urlopen", forbidden_network)
    monkeypatch.setattr("requests.get", forbidden_network)
    analyzer = MorningAnalyzer()

    result = run_morning_routine(MorningDatabase(), analyzer)

    assert result == "briefing"
    assert analyzer.prospects_updated == 1
    assert network_calls == []


def test_daemon_has_no_legacy_news_fetcher_consumer():
    source = inspect.getsource(run_daemon)

    assert "NewsFetcher" not in source
    assert "update_news" not in source


def test_legacy_public_scrapers_and_dependencies_are_removed():
    requirements = (AGENT_ROOT / "requirements.txt").read_text()

    assert not (AGENT_ROOT / "src/data/news.py").exists()
    assert not (AGENT_ROOT / "src/data/reports.py").exists()
    assert "feedparser" not in requirements
    assert "beautifulsoup4" not in requirements
