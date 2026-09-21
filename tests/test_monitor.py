import unittest

import monitor


class MonitorTests(unittest.TestCase):
    def test_repairs_mojibake(self):
        self.assertEqual(monitor.repair_text("ÐÐµÐ½Ñ-ÑÐµÐºÐ¸"), "Вены-реки")
        self.assertEqual(monitor.repair_text("Tutu TurÃº"), "Tutu Turú")
        self.assertEqual(monitor.repair_text("NO BATIDÃ\x83O"), "NO BATIDÃO")

    def test_artist_enrichment_does_not_create_chart_change(self):
        old = monitor.normalized_tracks(
            [
                {"title": "ÐÐµÐ½Ñ-ÑÐµÐºÐ¸", "artist": ""},
                {"title": "Starburster", "artist": ""},
            ]
        )
        new = [
            {"title": "Вены-реки", "artist": "Анастасия Стоцкая"},
            {"title": "Starburster", "artist": "Fontaines D.C."},
        ]
        self.assertFalse(monitor.has_chart_change(monitor.make_delta(old, new)))

    def test_event_fingerprint_ignores_artist_representation(self):
        old_a = [{"title": "A", "artist": ""}, {"title": "B", "artist": ""}]
        new_a = [{"title": "B", "artist": ""}, {"title": "C", "artist": ""}]
        old_b = [
            {"title": "A", "artist": "Artist A"},
            {"title": "B", "artist": "Artist B"},
        ]
        new_b = [
            {"title": "B", "artist": "Artist B"},
            {"title": "C", "artist": "Artist C"},
        ]
        self.assertEqual(
            monitor.event_fingerprint(monitor.make_delta(old_a, new_a)),
            monitor.event_fingerprint(monitor.make_delta(old_b, new_b)),
        )

    def test_merge_candidates_prefers_artist(self):
        merged = monitor.merge_candidates(
            [
                {"title": "Song", "artist": ""},
                {"title": "Song", "artist": "Artist"},
            ]
        )
        self.assertEqual(merged, [{"title": "Song", "artist": "Artist"}])


if __name__ == "__main__":
    unittest.main()
