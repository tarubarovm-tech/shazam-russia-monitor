import unittest
from unittest.mock import patch

import monitor


class MonitorTests(unittest.TestCase):
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


    def test_yandex_exact_match_is_found(self):
        track = {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""}
        item = {
            "id": "1",
            "title": "Вены-реки",
            "artists": [{"name": "Анастасия Стоцкая"}],
            "albums": [{"id": "10", "title": "Album"}],
        }
        quality = monitor.yandex_candidate_quality(track, item)
        self.assertEqual(quality["status"], "found")
        self.assertGreaterEqual(quality["title_score"], 0.99)
        self.assertGreaterEqual(quality["artist_score"], 0.99)

    def test_yandex_wrong_artist_is_not_found(self):
        track = {"title": "Вены-реки", "artist": "Анастасия Стоцкая", "label": ""}
        item = {
            "id": "2",
            "title": "Вены-реки",
            "artists": [{"name": "Совсем другой артист"}],
            "albums": [{"id": "11", "title": "Album"}],
        }
        quality = monitor.yandex_candidate_quality(track, item)
        self.assertEqual(quality["status"], "reject")

    def test_yandex_transliteration_matches_artist(self):
        self.assertGreaterEqual(
            monitor.similarity("Basta", "Баста"),
            0.99,
        )
        self.assertGreaterEqual(
            monitor.similarity("Moya Mishel", "Моя Мишель"),
            0.90,
        )

    def test_yandex_multi_artist_requires_good_coverage(self):
        track = {"title": "Die With A Smile", "artist": "Lady Gaga & Bruno Mars", "label": ""}
        one_artist = {
            "id": "3",
            "title": "Die With A Smile",
            "artists": [{"name": "Lady Gaga"}],
            "albums": [{"id": "12", "title": "Album"}],
        }
        both_artists = {
            "id": "4",
            "title": "Die With A Smile",
            "artists": [{"name": "Lady Gaga"}, {"name": "Bruno Mars"}],
            "albums": [{"id": "13", "title": "Album"}],
        }
        self.assertNotEqual(
            monitor.yandex_candidate_quality(track, one_artist)["status"],
            "found",
        )
        self.assertEqual(
            monitor.yandex_candidate_quality(track, both_artists)["status"],
            "found",
        )

    def test_yandex_missing_artist_is_uncertain_not_found(self):
        track = {"title": "Song", "artist": "", "label": ""}
        item = {
            "id": "5",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "albums": [{"id": "14", "title": "Album"}],
        }
        self.assertEqual(
            monitor.yandex_candidate_quality(track, item)["status"],
            "uncertain",
        )

    def test_yandex_not_found_requires_all_queries_to_succeed(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}

        def partial_failure(query):
            if query == monitor.yandex_queries(track)[0]:
                raise RuntimeError("temporary")
            return []

        with patch.object(monitor, "yandex_search_results", side_effect=partial_failure):
            result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "error")

    def test_yandex_returns_not_found_only_after_complete_empty_search(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        with patch.object(monitor, "yandex_search_results", return_value=[]):
            result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "not_found")

    def test_yandex_found_short_circuits_on_verified_candidate(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        item = {
            "id": "6",
            "title": "Song",
            "artists": [{"name": "Artist"}],
            "albums": [{"id": "15", "title": "Album"}],
        }
        with patch.object(monitor, "yandex_search_results", return_value=[item]) as mocked:
            result = monitor.check_yandex_track(track)
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["url"], "https://music.yandex.ru/album/15/track/6")
        self.assertEqual(mocked.call_count, 1)

    def test_yandex_status_is_rendered(self):
        track = {"title": "Song", "artist": "Artist", "label": "Label"}
        rendered = monitor.display_track(track, {"status": "found"})
        self.assertIn("🟡 Яндекс: есть", rendered)


if __name__ == "__main__":
    unittest.main()
