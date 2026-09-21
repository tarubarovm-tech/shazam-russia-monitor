import unittest

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


if __name__ == "__main__":
    unittest.main()
