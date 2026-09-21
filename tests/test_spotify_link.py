"""Тесты добычи ссылок на Spotify.

Главное, что стерегут: ссылка ставится только на ТОТ трек, а отсутствие
ключей или сбой API не ломают алерт — просто ссылки не будет.
"""
import unittest
from unittest.mock import patch

import monitor
import spotify_link as sl


def _item(name, artist="Artist", url="https://open.spotify.com/track/abc123"):
    return {
        "name": name,
        "artists": [{"name": artist}],
        "external_urls": {"spotify": url},
    }


class NormalizationTests(unittest.TestCase):
    def test_version_suffixes_are_stripped(self):
        self.assertEqual(sl._norm("Song (Remix)"), "song")
        self.assertEqual(sl._norm("Song [Remastered 2024]"), "song")

    def test_yo_is_folded(self):
        self.assertEqual(sl._norm("Ёлка"), sl._norm("Елка"))

    def test_similar_matches_same_title(self):
        self.assertGreaterEqual(sl._similar("Море", "Море"), sl.MATCH_THRESHOLD)

    def test_similar_rejects_other_title(self):
        self.assertLess(sl._similar("Believer", "Thunder"), sl.MATCH_THRESHOLD)


class FindTrackUrlTests(unittest.TestCase):
    def setUp(self):
        sl._memo.clear()
        self._avail = patch.object(sl, "available", return_value=True)
        self._avail.start()
        self.addCleanup(self._avail.stop)

    def test_exact_title_returns_url(self):
        with patch.object(sl, "_search", return_value=[_item("Море")]):
            url = sl.find_track_url("Женя Трофимов", "Море")
        self.assertEqual(url, "https://open.spotify.com/track/abc123")

    def test_wrong_title_is_rejected(self):
        """Spotify вернул другой трек — ссылку не ставим."""
        with patch.object(sl, "_search", return_value=[_item("Совершенно другой трек")]):
            url = sl.find_track_url("Artist", "Ожидаемый трек")
        self.assertEqual(url, "")

    def test_version_matches_base_track(self):
        with patch.object(sl, "_search", return_value=[_item("Blinding Lights")]):
            url = sl.find_track_url("The Weeknd", "Blinding Lights (Remix)")
        self.assertTrue(url)

    def test_empty_result_returns_empty(self):
        with patch.object(sl, "_search", return_value=[]):
            self.assertEqual(sl.find_track_url("A", "B"), "")

    def test_api_failure_never_raises(self):
        """Сбой Spotify не должен ронять прогон монитора."""
        with patch.object(sl, "_search", side_effect=RuntimeError("500")):
            self.assertEqual(sl.find_track_url("A", "B"), "")

    def test_missing_credentials_returns_empty(self):
        self._avail.stop()
        with patch.object(sl, "available", return_value=False):
            url = sl.find_track_url("A", "B")
        self._avail.start()
        self.assertEqual(url, "")

    def test_result_is_memoized(self):
        with patch.object(sl, "_search", return_value=[_item("Море")]) as search:
            sl.find_track_url("Женя Трофимов", "Море")
            sl.find_track_url("Женя Трофимов", "Море")
        self.assertEqual(search.call_count, 1)


class MonitorIntegrationTests(unittest.TestCase):
    def setUp(self):
        sl._memo.clear()

    def test_api_link_is_preferred_over_songlink_parsing(self):
        """Ссылка из Spotify API должна браться раньше парсинга страницы."""
        track = {"title": "Море", "artist": "Женя Трофимов", "label": ""}
        with patch.object(
            monitor, "_spotify_url_via_api",
            return_value="https://open.spotify.com/track/xyz",
        ):
            info = monitor.fetch_spotify_link_for_track(track, {})
        self.assertEqual(info["spotify_url"], "https://open.spotify.com/track/xyz")

    def test_alert_uses_spotify_link_first(self):
        info = {
            "spotify_url": "https://open.spotify.com/track/xyz",
            "apple_url": "https://music.apple.com/ru/album/x",
            "source_url": "https://song.link/i/1",
        }
        self.assertEqual(monitor.track_open_url(info), "https://open.spotify.com/track/xyz")

    def test_alert_falls_back_to_apple(self):
        """Без Spotify-ссылки алерт всё равно даёт рабочую ссылку."""
        info = {
            "apple_url": "https://music.apple.com/ru/album/x",
            "source_url": "https://song.link/i/1",
        }
        self.assertEqual(monitor.track_open_url(info), "https://music.apple.com/ru/album/x")

    def test_spotify_failure_does_not_break_alert(self):
        """Spotify недоступен — ссылка на Apple остаётся, алерт не падает."""
        track = {"title": "Море", "artist": "Женя Трофимов", "label": ""}
        base = {"apple_url": "https://music.apple.com/ru/album/x",
                "source_url": "https://song.link/i/1"}
        with patch.object(monitor, "_spotify_url_via_api", return_value=""), \
             patch.object(monitor, "get", side_effect=monitor.requests.RequestException("нет сети")):
            info = monitor.fetch_spotify_link_for_track(track, base)
        self.assertEqual(monitor.track_open_url(info), "https://music.apple.com/ru/album/x")


if __name__ == "__main__":
    unittest.main()
