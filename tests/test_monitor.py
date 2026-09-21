import unittest
from unittest.mock import Mock, patch

import monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        monitor._ITUNES_MATCH_MEMO.clear()
        monitor._MUSICFETCH_LAST_REQUEST = 0.0

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

    def _itunes_source(
        self,
        track_id=1761054509,
        title="Вены-реки",
        artist="Анастасия Стоцкая",
        track_url="https://music.apple.com/ru/song/example/1761054509",
    ):
        return {
            "item": {
                "trackId": track_id,
                "trackName": title,
                "artistName": artist,
                "trackViewUrl": track_url,
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
        self.assertEqual(
            result["apple_url"],
            "https://music.apple.com/ru/song/example/1761054509",
        )

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
            "🟠 Яндекс: не удалось проверить",
            monitor.display_track(track, {"status": "not_confirmed"}),
        )


    def test_select_new_without_yandex_filters_strictly(self):
        missing = {"title": "Missing", "artist": "Artist", "label": ""}
        found = {"title": "Found", "artist": "Artist", "label": ""}
        uncertain = {"title": "Uncertain", "artist": "Artist", "label": ""}
        errored = {"title": "Errored", "artist": "Artist", "label": ""}
        delta = {
            "added": [
                (10, "missing", missing),
                (20, "found", found),
                (30, "uncertain", uncertain),
                (40, "errored", errored),
            ],
            "gone": [(5, "gone", {"title": "Gone", "artist": "Artist", "label": ""})],
            "moved": [(3, 7, 10, "moved", {"title": "Moved", "artist": "Artist", "label": ""})],
        }
        yandex_info = {
            monitor.cache_key(missing): {"status": "verified_missing"},
            monitor.cache_key(found): {"status": "found"},
            monitor.cache_key(uncertain): {"status": "uncertain"},
            monitor.cache_key(errored): {"status": "error"},
        }
        selected = monitor.select_new_without_yandex(delta, yandex_info)
        self.assertEqual(selected, [(10, "missing", missing)])

    def test_added_only_delta_ignores_gone_and_moved(self):
        track = {"title": "New", "artist": "Artist", "label": ""}
        delta = {
            "added": [(12, "new", track)],
            "gone": [(2, "gone", {"title": "Gone", "artist": "Artist", "label": ""})],
            "moved": [(5, 8, 13, "moved", {"title": "Moved", "artist": "Artist", "label": ""})],
        }
        self.assertEqual(
            monitor.added_only_delta(delta),
            {"added": [(12, "new", track)], "gone": [], "moved": []},
        )

    def test_new_without_yandex_report_contains_only_selected_tracks(self):
        selected = {"title": "Missing", "artist": "Artist", "label": "Label"}
        yandex_info = {
            monitor.cache_key(selected): {"status": "verified_missing"},
        }
        message = monitor.report_new_without_yandex(
            "Shazam Top 200 Russia",
            [(17, "missing", selected)],
            "21.09.2026 15:00 МСК",
            yandex_info,
        )
        self.assertIn("Missing — Artist", message)
        self.assertIn("Яндекс: не найден", message)
        self.assertNotIn("УШЛИ", message)
        self.assertNotIn("ИЗМЕНЕНИЯ ПОЗИЦИЙ", message)

    def test_track_open_url_prefers_apple_music(self):
        info = {
            "apple_url": "https://music.apple.com/ru/song/song/123",
            "source_url": "https://song.link/i/123",
        }
        self.assertEqual(
            monitor.track_open_url(info),
            "https://music.apple.com/ru/song/song/123",
        )

    def test_track_open_url_falls_back_to_songlink(self):
        info = {
            "apple_url": "",
            "source_url": "https://song.link/i/123",
        }
        self.assertEqual(
            monitor.track_open_url(info),
            "https://song.link/i/123",
        )

    def test_new_without_yandex_report_contains_track_link(self):
        selected = {"title": "Missing", "artist": "Artist", "label": "Label"}
        yandex_info = {
            monitor.cache_key(selected): {
                "status": "verified_missing",
                "apple_url": "https://music.apple.com/ru/song/song/123",
                "source_url": "https://song.link/i/123",
            },
        }
        message = monitor.report_new_without_yandex(
            "Shazam Top 200 Russia",
            [(17, "missing", selected)],
            "21.09.2026 15:00 МСК",
            yandex_info,
        )
        self.assertIn(
            "🔗 Открыть трек: https://music.apple.com/ru/song/song/123",
            message,
        )

    def test_alert_baseline_activation_memorizes_current_charts(self):
        state = {
            "Shazam Top 200 Russia": [{"title": "Old", "artist": "A", "label": ""}],
            "_recent_events": [{"fingerprint": "old"}],
        }
        results = {
            "Shazam Top 200 Russia": [{"title": "Current", "artist": "A", "label": ""}],
            "Apple Music — Shazam Charts Russia": [{"title": "Current", "artist": "A", "label": ""}],
        }
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        self.assertTrue(monitor.needs_alert_baseline(state))
        monitor.activate_alert_baseline(
            state,
            results,
            ("Shazam Top 200 Russia", "Apple Music — Shazam Charts Russia"),
            now,
        )
        self.assertFalse(monitor.needs_alert_baseline(state))
        self.assertEqual(state["Shazam Top 200 Russia"], results["Shazam Top 200 Russia"])
        self.assertEqual(
            state["Apple Music — Shazam Charts Russia"],
            results["Apple Music — Shazam Charts Russia"],
        )
        self.assertEqual(state["_recent_events"], [])
        self.assertEqual(state["_baseline_at"], "2026-09-21T12:00:00Z")


    def test_baseline_track_is_not_eligible_for_fallback(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "Existing", "artist": "Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        monitor.baseline_source(
            state,
            "Apple Music — Shazam Charts Russia",
            [track],
            now,
        )
        delta = {
            "added": [(42, "existing", {"title": "Existing", "artist": "Artist", "label": ""})],
            "gone": [],
            "moved": [],
        }
        self.assertEqual(monitor.eligible_new_entries(state, delta), [])

    def test_alerted_apple_track_is_not_eligible_from_shazam(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "New Song", "artist": "Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        monitor.set_track_registry_status(
            state,
            track,
            "alerted",
            "Apple Music — Shazam Charts Russia",
            now,
        )
        shazam_track = {"title": "New Song", "artist": "Artist", "label": ""}
        delta = {"added": [(12, "new", shazam_track)], "gone": [], "moved": []}
        self.assertEqual(monitor.eligible_new_entries(state, delta), [])

    def test_yandex_found_apple_track_is_not_eligible_from_shazam(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "Found Song", "artist": "Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        monitor.set_track_registry_status(
            state,
            track,
            "yandex_found",
            "Apple Music — Shazam Charts Russia",
            now,
        )
        delta = {"added": [(33, "found", track)], "gone": [], "moved": []}
        self.assertEqual(monitor.eligible_new_entries(state, delta), [])

    def test_pending_apple_track_is_eligible_for_shazam_retry(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        apple_track = {"title": "Retry Me", "artist": "Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        monitor.set_track_registry_status(
            state,
            apple_track,
            "pending",
            "Apple Music — Shazam Charts Russia",
            now,
        )
        shazam_track = {"title": "Retry Me", "artist": "Artist", "label": ""}
        delta = {"added": [(88, "retry", shazam_track)], "gone": [], "moved": []}
        self.assertEqual(
            monitor.eligible_new_entries(state, delta),
            [(88, "retry", shazam_track)],
        )

    def test_registry_matches_same_track_when_apple_artist_missing(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        apple_track = {"title": "Same Song", "artist": "", "label": ""}
        shazam_track = {"title": "Same Song", "artist": "Correct Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        monitor.set_track_registry_status(
            state,
            apple_track,
            "baseline",
            "Apple Music — Shazam Charts Russia",
            now,
        )
        self.assertEqual(
            monitor.track_registry_status(state, shazam_track),
            "baseline",
        )

    def test_yandex_outcomes_create_terminal_and_pending_states(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        found = {"title": "Found", "artist": "A", "label": ""}
        missing = {"title": "Missing", "artist": "B", "label": ""}
        pending = {"title": "Pending", "artist": "C", "label": ""}
        entries = [
            (1, "found", found),
            (2, "missing", missing),
            (3, "pending", pending),
        ]
        info = {
            monitor.cache_key(found): {"status": "found"},
            monitor.cache_key(missing): {"status": "verified_missing"},
            monitor.cache_key(pending): {"status": "error"},
        }
        selected = monitor.apply_yandex_outcomes(
            state,
            "Apple Music — Shazam Charts Russia",
            entries,
            info,
            now,
        )
        self.assertEqual(selected, [(2, "missing", missing)])
        self.assertEqual(monitor.track_registry_status(state, found), "yandex_found")
        self.assertEqual(monitor.track_registry_status(state, pending), "pending")
        self.assertIsNone(monitor.track_registry_status(state, missing))

    def test_mark_alerted_blocks_future_fallback(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "Alert Me", "artist": "Artist", "label": ""}
        entries = [(1, "alert", track)]
        monitor.reset_alert_mode(state, now)
        monitor.mark_alerted(
            state,
            "Apple Music — Shazam Charts Russia",
            entries,
            now,
        )
        self.assertEqual(monitor.track_registry_status(state, track), "alerted")
        self.assertEqual(
            monitor.eligible_new_entries(
                state,
                {"added": [(9, "alert", track)], "gone": [], "moved": []},
            ),
            [],
        )


    def test_major_label_family_detects_universal_imprints(self):
        self.assertEqual(
            monitor.major_label_family("℗ 2026 Interscope Records"),
            "Universal Music Group",
        )
        self.assertEqual(
            monitor.major_label_family("Republic Records"),
            "Universal Music Group",
        )
        self.assertEqual(
            monitor.major_label_family("Virgin Music Group"),
            "Universal Music Group",
        )

    def test_major_label_family_detects_sony_imprints(self):
        self.assertEqual(
            monitor.major_label_family("Columbia Records"),
            "Sony Music Entertainment",
        )
        self.assertEqual(
            monitor.major_label_family("AWAL"),
            "Sony Music Entertainment",
        )
        self.assertEqual(
            monitor.major_label_family("Indie Label / The Orchard"),
            "Sony Music Entertainment",
        )

    def test_major_label_family_detects_warner_imprints(self):
        self.assertEqual(
            monitor.major_label_family("Atlantic Records"),
            "Warner Music Group",
        )
        self.assertEqual(
            monitor.major_label_family("Fueled By Ramen"),
            "Warner Music Group",
        )
        self.assertEqual(
            monitor.major_label_family("ADA"),
            "Warner Music Group",
        )

    def test_independent_label_is_not_classified_as_major(self):
        self.assertIsNone(
            monitor.major_label_family("Vibe Department / СЕМЬЯ")
        )

    def test_major_label_track_is_suppressed_and_terminal(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {
            "title": "Major Song",
            "artist": "Artist",
            "label": "Interscope Records",
        }
        monitor.reset_alert_mode(state, now)
        selected = monitor.filter_non_major_entries(
            state,
            "Apple Music — Shazam Charts Russia",
            [(10, "major", track)],
            now,
        )
        self.assertEqual(selected, [])
        self.assertEqual(
            monitor.track_registry_status(state, track),
            "major_label",
        )
        self.assertEqual(
            monitor.eligible_new_entries(
                state,
                {"added": [(20, "major", track)], "gone": [], "moved": []},
            ),
            [],
        )

    def test_unknown_label_passes_final_filter(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "Unknown Label", "artist": "Artist", "label": ""}
        monitor.reset_alert_mode(state, now)
        entry = (11, "unknown", track)
        selected = monitor.filter_non_major_entries(
            state,
            "Apple Music — Shazam Charts Russia",
            [entry],
            now,
        )
        self.assertEqual(selected, [entry])
        self.assertIsNone(monitor.track_registry_status(state, track))

    def test_non_major_label_passes_final_filter(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {
            "title": "Indie Song",
            "artist": "Artist",
            "label": "Vibe Department / СЕМЬЯ",
        }
        monitor.reset_alert_mode(state, now)
        entry = (7, "indie", track)
        self.assertEqual(
            monitor.filter_non_major_entries(
                state,
                "Apple Music — Shazam Charts Russia",
                [entry],
                now,
            ),
            [entry],
        )


    def test_collects_flat_apple_track_lockup(self):
        node = {
            "id": "track-lockup - pl.test - 1234567890",
            "title": "Fresh Track",
            "subtitleLinks": [{"title": "Fresh Artist"}],
            "contentDescriptor": {
                "identifiers": {"storeAdamID": "1234567890"}
            },
            "duration": 201000,
        }
        out = []
        monitor.collect_apple_songs(node, out)
        self.assertEqual(
            out,
            [{
                "title": "Fresh Track",
                "artist": "Fresh Artist",
                "label": "",
                "apple_track_id": "1234567890",
            }],
        )

    def test_decodes_serialized_server_data_meta(self):
        payload = {
            "items": [
                {
                    "id": "track-lockup - pl.test - 1111111111",
                    "title": "Song One",
                    "subtitleLinks": [{"title": "Artist One"}],
                    "contentDescriptor": {
                        "identifiers": {"storeAdamID": "1111111111"}
                    },
                    "duration": 180000,
                },
                {
                    "id": "track-lockup - pl.test - 2222222222",
                    "title": "Song Two",
                    "subtitleLinks": [{"title": "Artist Two"}],
                    "contentDescriptor": {
                        "identifiers": {"storeAdamID": "2222222222"}
                    },
                    "duration": 190000,
                },
            ]
        }
        encoded = monitor.html_lib.escape(
            monitor.json.dumps(payload, ensure_ascii=False),
            quote=True,
        )
        page = (
            '<html><head><meta name="serialized-server-data" '
            f'content="{encoded}"></head></html>'
        )
        payloads = monitor.apple_embedded_payloads(page)
        self.assertEqual(len(payloads), 1)
        out = []
        monitor.collect_apple_songs(payloads[0], out)
        merged = monitor.merge_candidates(out)
        self.assertEqual(
            merged,
            [
                {
                    "title": "Song One",
                    "artist": "Artist One",
                    "label": "",
                    "apple_track_id": "1111111111",
                },
                {
                    "title": "Song Two",
                    "artist": "Artist Two",
                    "label": "",
                    "apple_track_id": "2222222222",
                },
            ],
        )

    def test_embedded_script_track_lockups_are_parsed(self):
        payload = {
            "playlist": {
                "tracks": [
                    {
                        "id": "track-lockup - pl.test - 3333333333",
                        "title": "Script Song",
                        "artistName": "Script Artist",
                        "contentDescriptor": {
                            "identifiers": {"storeAdamID": "3333333333"}
                        },
                        "duration": 200000,
                    }
                ]
            }
        }
        page = (
            "<html><body><script>"
            + monitor.json.dumps(payload, ensure_ascii=False)
            + "</script></body></html>"
        )
        payloads = monitor.apple_embedded_payloads(page)
        self.assertEqual(len(payloads), 1)
        out = []
        monitor.collect_apple_songs(payloads[0], out)
        self.assertEqual(
            monitor.merge_candidates(out),
            [{
                "title": "Script Song",
                "artist": "Script Artist",
                "label": "",
                "apple_track_id": "3333333333",
            }],
        )


    def test_songlink_not_confirmed_is_not_alertable(self):
        track = {"title": "Known Yandex Track", "artist": "Artist", "label": ""}
        delta = {"added": [(1, "known", track)], "gone": [], "moved": []}
        info = {
            monitor.cache_key(track): {"status": "not_confirmed"},
        }
        self.assertEqual(
            monitor.select_new_without_yandex(delta, info),
            [],
        )

    def test_only_verified_missing_is_alertable(self):
        track = {"title": "Missing Track", "artist": "Artist", "label": ""}
        delta = {"added": [(1, "missing", track)], "gone": [], "moved": []}
        info = {
            monitor.cache_key(track): {"status": "verified_missing"},
        }
        self.assertEqual(
            monitor.select_new_without_yandex(delta, info),
            [(1, "missing", track)],
        )


    def test_musicfetch_url_lookup_can_confirm_yandex(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        url_result = {
            "type": "track",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "isrc": "USABC2600001",
            "label": "Indie Label",
            "distributor": "Indie Distributor",
            "services": {
                "yandex": {"link": "https://music.yandex.ru/track/12345"}
            },
        }
        with patch.object(
            monitor,
            "musicfetch_get",
            return_value=(url_result, ""),
        ) as lookup:
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["url"], "https://music.yandex.ru/track/12345")
        self.assertEqual(result["verification"], "musicfetch_url")
        self.assertEqual(result["isrc"], "USABC2600001")
        self.assertEqual(lookup.call_count, 1)

    def test_musicfetch_isrc_lookup_can_confirm_yandex(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        url_result = {
            "type": "track",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "isrc": "USABC2600001",
            "services": {},
        }
        isrc_result = {
            "type": "track",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "isrc": "USABC2600001",
            "services": {
                "yandex": {"link": "https://music.yandex.ru/album/99/track/12345"}
            },
        }
        with patch.object(
            monitor,
            "musicfetch_get",
            side_effect=[(url_result, ""), (isrc_result, "")],
        ) as lookup:
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "found")
        self.assertEqual(
            result["url"],
            "https://music.yandex.ru/album/99/track/12345",
        )
        self.assertEqual(result["verification"], "musicfetch_isrc")
        self.assertEqual(lookup.call_count, 2)

    def test_musicfetch_two_exact_misses_create_verified_missing(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        url_result = {
            "type": "track",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "isrc": "USABC2600001",
            "label": "Small Label",
            "distributor": "Small Distributor",
            "services": {},
        }
        isrc_result = {
            "type": "track",
            "name": "Song",
            "artists": [{"name": "Artist"}],
            "isrc": "USABC2600001",
            "services": {},
        }
        with patch.object(
            monitor,
            "musicfetch_get",
            side_effect=[(url_result, ""), (isrc_result, "")],
        ):
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "verified_missing")
        self.assertEqual(
            result["verification"],
            "songlink+musicfetch_url+musicfetch_isrc",
        )
        self.assertEqual(len(result["evidence"]), 3)
        self.assertEqual(result["distributor"], "Small Distributor")

    def test_musicfetch_mismatch_never_creates_missing_status(self):
        track = {"title": "Expected Song", "artist": "Expected Artist", "label": ""}
        wrong = {
            "type": "track",
            "name": "Different Song",
            "artists": [{"name": "Different Artist"}],
            "isrc": "USABC2600001",
            "services": {},
        }
        with patch.object(
            monitor,
            "musicfetch_get",
            return_value=(wrong, ""),
        ):
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "not_confirmed")

    def test_musicfetch_missing_token_never_creates_missing_status(self):
        track = {"title": "Song", "artist": "Artist", "label": ""}
        with patch.object(monitor, "musicfetch_token", return_value=""):
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "not_confirmed")
        self.assertIn("MUSICFETCH_TOKEN", result["error"])

    def test_major_distributor_blocks_track_even_with_indie_label(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {
            "title": "Song",
            "artist": "Artist",
            "label": "Tiny Vanity Label",
        }
        entry = (5, "song", track)
        info = {
            monitor.cache_key(track): {
                "status": "verified_missing",
                "distributor": "Universal Music Group",
            }
        }
        monitor.reset_alert_mode(state, now)
        selected = monitor.filter_non_major_entries(
            state,
            "Apple Music — Shazam Charts Russia",
            [entry],
            now,
            info,
        )
        self.assertEqual(selected, [])
        self.assertEqual(monitor.track_registry_status(state, track), "major_label")

    def test_musicfetch_label_can_enrich_before_major_filter(self):
        state = {}
        now = monitor.datetime(2026, 9, 21, 12, 0, tzinfo=monitor.timezone.utc)
        track = {"title": "Song", "artist": "Artist", "label": ""}
        entry = (5, "song", track)
        info = {
            monitor.cache_key(track): {
                "status": "verified_missing",
                "musicfetch_label": "Interscope Records",
            }
        }
        monitor.reset_alert_mode(state, now)
        selected = monitor.apply_yandex_outcomes(
            state,
            "Apple Music — Shazam Charts Russia",
            [entry],
            info,
            now,
        )
        self.assertEqual(selected, [entry])
        self.assertEqual(track["label"], "Interscope Records")
        filtered = monitor.filter_non_major_entries(
            state,
            "Apple Music — Shazam Charts Russia",
            selected,
            now,
            info,
        )
        self.assertEqual(filtered, [])


    def test_similarity_channels_keep_text_and_latin_separate(self):
        scores = monitor.similarity_channels("Моя Мишель", "Moya Mishel")
        self.assertLess(scores["text"], 0.50)
        self.assertGreaterEqual(scores["latin"], 0.90)
        self.assertEqual(scores["best"], scores["latin"])

    def test_musicfetch_match_accepts_cyrillic_to_latin_transliteration(self):
        track = {
            "title": "Если я буду танцевать",
            "artist": "Баста & Моя Мишель",
            "label": "",
        }
        result = {
            "type": "track",
            "name": "Esli ya budu tantsevat",
            "artists": [
                {"name": "Basta"},
                {"name": "Moya Mishel"},
            ],
        }
        quality = monitor.musicfetch_match_quality(track, result)
        self.assertTrue(quality["matches"])
        self.assertGreaterEqual(quality["title_latin"], 0.93)
        self.assertGreaterEqual(quality["artist_latin"], 0.88)

    def test_musicfetch_match_accepts_native_text(self):
        track = {
            "title": "Если я буду танцевать",
            "artist": "Баста & Моя Мишель",
            "label": "",
        }
        result = {
            "type": "track",
            "name": "Если я буду танцевать",
            "artists": [
                {"name": "Баста"},
                {"name": "Моя Мишель"},
            ],
        }
        quality = monitor.musicfetch_match_quality(track, result)
        self.assertTrue(quality["matches"])
        self.assertGreaterEqual(quality["title_text"], 0.99)
        self.assertGreaterEqual(quality["artist_text"], 0.99)

    def test_musicfetch_match_rejects_wrong_artist_even_when_title_matches(self):
        track = {
            "title": "Если я буду танцевать",
            "artist": "Баста & Моя Мишель",
            "label": "",
        }
        result = {
            "type": "track",
            "name": "Esli ya budu tantsevat",
            "artists": [{"name": "Completely Different Artist"}],
        }
        quality = monitor.musicfetch_match_quality(track, result)
        self.assertFalse(quality["matches"])
        self.assertGreaterEqual(quality["title_latin"], 0.93)
        self.assertLess(quality["artist_latin"], 0.88)

    def test_musicfetch_verified_missing_keeps_both_match_channels(self):
        track = {
            "title": "Если я буду танцевать",
            "artist": "Баста & Моя Мишель",
            "label": "",
        }
        url_result = {
            "type": "track",
            "name": "Esli ya budu tantsevat",
            "artists": [{"name": "Basta"}, {"name": "Moya Mishel"}],
            "isrc": "RUABC2600001",
            "services": {},
        }
        isrc_result = {
            "type": "track",
            "name": "Если я буду танцевать",
            "artists": [{"name": "Баста"}, {"name": "Моя Мишель"}],
            "isrc": "RUABC2600001",
            "services": {},
        }
        with patch.object(
            monitor,
            "musicfetch_get",
            side_effect=[(url_result, ""), (isrc_result, "")],
        ):
            result = monitor.musicfetch_verify_yandex(
                track,
                "https://music.apple.com/ru/album/song/1?i=2",
            )
        self.assertEqual(result["status"], "verified_missing")
        self.assertTrue(result["url_match"]["matches"])
        self.assertTrue(result["isrc_match"]["matches"])
        self.assertGreaterEqual(result["url_match"]["title_latin"], 0.93)
        self.assertGreaterEqual(result["isrc_match"]["title_text"], 0.99)


    def test_normalized_tracks_preserves_apple_source_metadata(self):
        tracks = monitor.normalized_tracks([
            {
                "title": "Song",
                "artist": "Artist",
                "label": "Label",
                "apple_track_id": "1234567890",
                "apple_url": "https://music.apple.com/ru/song/1234567890",
            }
        ])
        self.assertEqual(tracks[0]["apple_track_id"], "1234567890")
        self.assertEqual(
            tracks[0]["apple_url"],
            "https://music.apple.com/ru/song/1234567890",
        )

    def test_find_itunes_track_prefers_native_apple_id_lookup(self):
        track = {
            "title": "Exact Song",
            "artist": "Exact Artist",
            "label": "",
            "apple_track_id": "1234567890",
        }
        response = Mock()
        response.json.return_value = {
            "results": [
                {
                    "trackId": 1234567890,
                    "trackName": "Exact Song",
                    "artistName": "Exact Artist",
                    "trackViewUrl": "https://music.apple.com/ru/song/1234567890",
                    "collectionId": 987654321,
                }
            ]
        }
        with patch.object(monitor, "get", return_value=response) as getter:
            result = monitor.find_itunes_track(track)

        self.assertIsNotNone(result)
        self.assertTrue(result["quality"]["native_id"])
        self.assertEqual(result["item"]["trackId"], 1234567890)
        first_call = getter.call_args_list[0]
        self.assertEqual(first_call.args[0], monitor.ITUNES_LOOKUP)
        self.assertEqual(first_call.kwargs["params"]["id"], "1234567890")

    def test_merge_candidates_keeps_native_apple_id(self):
        merged = monitor.merge_candidates([
            {
                "title": "Song",
                "artist": "Artist",
                "label": "",
                "apple_track_id": "1234567890",
            },
            {
                "title": "Song",
                "artist": "Artist",
                "label": "Label",
            },
        ])
        self.assertEqual(merged[0]["apple_track_id"], "1234567890")
        self.assertEqual(merged[0]["label"], "Label")


    def test_yandex_match_threshold_accepts_exactly_80_percent(self):
        track = {"title": "Apple Song", "artist": "Apple Artist", "label": ""}
        result = {
            "type": "track",
            "name": "Yandex Song",
            "artists": [{"name": "Yandex Artist"}],
        }
        score = {"text": 0.80, "latin": 0.20, "best": 0.80}
        with patch.object(monitor, "similarity_channels", return_value=score):
            quality = monitor.musicfetch_match_quality(track, result)
        self.assertEqual(monitor.YANDEX_MATCH_THRESHOLD, 0.80)
        self.assertTrue(quality["matches"])

    def test_yandex_match_threshold_rejects_below_80_percent(self):
        track = {"title": "Apple Song", "artist": "Apple Artist", "label": ""}
        result = {
            "type": "track",
            "name": "Yandex Song",
            "artists": [{"name": "Yandex Artist"}],
        }
        score = {"text": 0.799, "latin": 0.20, "best": 0.799}
        with patch.object(monitor, "similarity_channels", return_value=score):
            quality = monitor.musicfetch_match_quality(track, result)
        self.assertFalse(quality["matches"])

    def test_title_95_artist_84_is_treated_as_yandex_match(self):
        track = {"title": "Apple Song", "artist": "Apple Artist", "label": ""}
        result = {
            "type": "track",
            "name": "Yandex Song",
            "artists": [{"name": "Yandex Artist"}],
        }
        scores = [
            {"text": 0.95, "latin": 0.20, "best": 0.95},
            {"text": 0.95, "latin": 0.20, "best": 0.95},
            {"text": 0.84, "latin": 0.20, "best": 0.84},
        ]
        with patch.object(monitor, "similarity_channels", side_effect=scores):
            quality = monitor.musicfetch_match_quality(track, result)
        self.assertTrue(quality["matches"])


if __name__ == "__main__":
    unittest.main()
