import unittest
from unittest.mock import Mock, patch

import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        monitor._ITUNES_MATCH_MEMO.clear()

    def test_repairs_mojibake(self):
        self.assertEqual(monitor.repair_text("ÐÐµÐ½Ñ-ÑÐµÐºÐ¸"), "Вены-реки")
        self.assertEqual(monitor.repair_text("Tutu TurÃº"), "Tutu Turú")
        self.assertEqual(monitor.repair_text("NO BATIDÃ\x83O"), "NO BATIDÃO")

    def test_metadata_enrichment_does_not_create_chart_change(self):
        old = monitor.normalized_tracks(
            [
                {"title": "ÐÐµÐ½Ñ-ÑÐµÐºÐ¸", "artist": ""},
                {"title": "Starburster", "artist": ""},
            ]
        )
        new = [
            {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": "Example Label"},
            {"title": "Starburster", "artist": "Fontaines D.C.", "label": "Example Label 2"},
        ]
        self.assertFalse(monitor.has_chart_change(monitor.make_delta(old, new)))

    def test_event_fingerprint_ignores_metadata_representation(self):
        old_a = [{"title": "A", "artist": "", "label": ""}, {"title": "B", "artist": "", "label": ""}]
        new_a = [{"title": "B", "artist": "", "label": ""}, {"title": "C", "artist": "", "label": ""}]
        old_b = [
            {"title": "A", "artist": "Artist A", "label": "Label A"},
            {"title": "B", "artist": "Artist B", "label": "Label B"},
        ]
        new_b = [
            {"title": "B", "artist": "Artist B", "label": "Label B"},
            {"title": "C", "artist": "Artist C", "label": "Label C"},
        ]
        self.assertEqual(
            monitor.event_fingerprint(monitor.make_delta(old_a, new_a)),
            monitor.event_fingerprint(monitor.make_delta(old_b, new_b)),
        )

    def test_merge_candidates_prefers_artist_and_label(self):
        merged = monitor.merge_candidates(
            [
                {"title": "Song", "artist": "", "label": ""},
                {"title": "Song", "artist": "Artist", "label": "Label"},
            ]
        )
        self.assertEqual(
            merged,
            [{"title": "Song", "artist": "Artist", "label": "Label"}],
        )

    def test_label_is_rendered(self):
        track = {"title": "Song", "artist": "Artist", "label": "Label"}
        self.assertIn("🏷 Label", monitor.display_track(track))

    def test_label_fallback_is_explicit(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        self.assertIn("🏷 не найден", monitor.display_track(track))

    def test_parse_label_copyright(self):
        self.assertEqual(
            monitor.parse_label_copyright("℗ 2026 Warner Music"),
            "Warner Music",
        )

    def test_transliteration_similarity(self):
        self.assertGreaterEqual(monitor.similarity("Basta", "Баста"), 0.99)
        self.assertGreaterEqual(monitor.similarity("Moya Mishel", "Моя Мишель"), 0.90)

    def test_itunes_exact_match_is_found(self):
        track = {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""}
        item = {"trackName": "Вены-реки", "artistName": "Анастасия Стоцкая"}
        quality = monitor.itunes_candidate_quality(track, item)
        self.assertEqual(quality["status"], "found")
        self.assertGreaterEqual(quality["title_score"], 0.99)
        self.assertGreaterEqual(quality["artist_score"], 0.99)

    def test_itunes_wrong_artist_is_rejected(self):
        track = {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""}
        item = {"trackName": "Вены-реки", "artistName": "Совсем другой артист"}
        quality = monitor.itunes_candidate_quality(track, item)
        self.assertEqual(quality["status"], "reject")

    def test_itunes_multi_artist_requires_coverage(self):
        track = {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""}
        one_artist = {"trackName": "Die With A Smile", "artistName": "Lady Gaga"}
        both_artists = {"trackName": "Die With A Smile", "artistName": "Lady Gaga & Bruno Mars"}
        self.assertNotEqual(
            monitor.itunes_candidate_quality(track, one_artist)["status"],
            "found",
        )
        self.assertEqual(
            monitor.itunes_candidate_quality(track, both_artists)["status"],
            "found",
        )

    def test_extract_yandex_urls_from_songlink_html(self):
        page = (
            '<script>{"url":"https:\\/\\/music.yandex.ru\\/track\\/129617880"}</script>'
            '<a href="https://music.yandex.ru/album/1/track/2?utm_source=test">Yandex</a>'
        )
        urls = monitor.extract_yandex_urls(page)
        self.assertIn("https://music.yandex.ru/track/129617880", urls)
        self.assertIn("https://music.yandex.ru/album/1/track/2?utm_source=test", urls)

    def _itunes_source(self, track_id=1761054509, title="Вены-реки", artist="Анастасия Стоцкая"):
        return {
            "item": {
                "trackId": track_id,
                "trackName": title,
                "artistName": artist,
            },
            "country": "ru",
            "quality": {
                "status": "found",
                "score": 1.0,
                "title_score": 1.0,
                "artist_score": 1.0,
            },
        }

    def test_yandex_found_only_with_direct_songlink_url(self):
        track = {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""}
        response = Mock()
        response.text = (
            "<html>Вены-реки Анастасия Стоцкая "
            "https:\\/\\/music.yandex.ru\\/track\\/129617880</html>"
        )
        with patch.object(monitor, "find_itunes_track", return_value=self._itunes_source()):
            with patch.object(monitor, "get", return_value=response):
                result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["url"], "https://music.yandex.ru/track/129617880")
        self.assertEqual(result["itunes_track_id"], "1761054509")

    def test_yandex_without_direct_link_is_not_confirmed(self):
        track = {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""}
        response = Mock()
        response.text = "<html>Die With A Smile</html>"
        source = self._itunes_source(
            track_id=1777878890,
            title="Die With A Smile",
            artist="Lady Gaga & Bruno Mars",
        )
        with patch.object(monitor, "find_itunes_track", return_value=source):
            with patch.object(monitor, "get", return_value=response):
                result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "not_confirmed")
        self.assertEqual(result["url"], "")

    def test_yandex_songlink_title_mismatch_is_uncertain(self):
        track = {"title": "Expected Song", "artist": "Artist", "label": ""}
        response = Mock()
        response.text = "<html>Completely Different Song https://music.yandex.ru/track/123</html>"
        source = self._itunes_source(track_id=1, title="Expected Song", artist="Artist")
        with patch.object(monitor, "find_itunes_track", return_value=source):
            with patch.object(monitor, "get", return_value=response):
                result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["url"], "")

    def test_yandex_without_verified_itunes_source_is_uncertain(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        with patch.object(monitor, "find_itunes_track", return_value=None):
            result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "uncertain")

    def test_yandex_network_failure_is_error(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        source = self._itunes_source(track_id=1, title="Song", artist="Artist")
        with patch.object(monitor, "find_itunes_track", return_value=source):
            with patch.object(monitor, "get", side_effect=monitor.requests.Timeout("timeout")):
                with patch.object(monitor.time, "sleep", return_value=None):
                    result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "error")

    def test_yandex_status_is_rendered(self):
        track = {"title": "Song", "artist": "Artist", "label": "Label"}
        self.assertIn(
            "🟡 Яндекс: есть",
            monitor.display_track(track, {"status": "found"}),
        )
        self.assertIn(
            "⚪ Яндекс: не подтверждён",
            monitor.display_track(track, {"status": "not_confirmed"}),
        )


if __name__ == "__main__":
    unittest.main()
