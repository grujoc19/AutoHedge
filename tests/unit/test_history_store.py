import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autohedge.data.history import HistorySnapshot, HistoryStore
from autohedge.errors import DataQualityError
from history_fixtures import NOW, snapshot


class HistoryStoreTests(unittest.TestCase):
    def test_round_trip_has_stable_identity_and_does_not_overwrite(self):
        record = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history")
            digest = store.save(record)
            path = store.directory / f"{digest}.json"
            original = path.read_bytes()
            modified_at = path.stat().st_mtime_ns
            self.assertEqual(hashlib.sha256(original).hexdigest(), digest)
            self.assertEqual(store.save(record), digest)
            self.assertEqual(path.stat().st_mtime_ns, modified_at)
            self.assertEqual(store.load(digest), record)
            self.assertEqual(len(list(store.directory.iterdir())), 1)
            self.assertNotIn(b"history-fixture-secret", original)
            self.assertEqual(json.loads(original)["schema_version"], 1)

    def test_later_acquisition_and_revisions_create_distinct_preserved_versions(self):
        first = snapshot()
        later = snapshot(received_at=NOW + timedelta(hours=1))
        revised = snapshot(received_at=NOW + timedelta(hours=2), close=100)
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            ids = [store.save(record) for record in (first, later, revised)]
            self.assertEqual(len(set(ids)), 3)
            self.assertEqual(store.load(ids[0]), first)
            self.assertEqual(store.load(ids[1]), later)
            self.assertEqual(store.load(ids[2]), revised)
            self.assertEqual(len(list(Path(directory).iterdir())), 3)

    def test_concurrent_identical_saves_publish_one_complete_file(self):
        record = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            with ThreadPoolExecutor(max_workers=4) as executor:
                ids = list(executor.map(lambda _: store.save(record), range(8)))
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(store.load(ids[0]), record)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_replay_uses_receipt_time_instead_of_historical_bar_time(self):
        record = snapshot(as_of=NOW - timedelta(days=1))
        self.assertEqual(record.request.as_of, NOW - timedelta(days=1))
        self.assertEqual(record.series.received_at, NOW)
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            digest = store.save(record)
            with self.assertRaisesRegex(DataQualityError, "history_not_yet_available"):
                store.replay(digest, available_at=record.request.as_of)
            with self.assertRaisesRegex(DataQualityError, "history_not_yet_available"):
                store.replay(digest, available_at=NOW - timedelta(microseconds=1))
            self.assertEqual(store.replay(digest, available_at=NOW), record.series)
            with self.assertRaisesRegex(DataQualityError, "invalid_clock"):
                store.replay(digest, available_at=NOW.replace(tzinfo=None))

    def test_corruption_is_detected_on_read_and_repeat_save_without_repair(self):
        record = snapshot()
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            digest = store.save(record)
            path = Path(directory) / f"{digest}.json"
            path.write_text("corrupted fixture")
            with self.assertRaisesRegex(DataQualityError, "history_integrity"):
                store.load(digest)
            with self.assertRaisesRegex(DataQualityError, "history_integrity"):
                store.save(record)
            self.assertEqual(path.read_text(), "corrupted fixture")
            self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_valid_hash_does_not_replace_schema_and_provenance_checks(self):
        valid = snapshot().model_dump(mode="json")
        malformed = {**valid, "schema_version": 2}
        mismatched = json.loads(json.dumps(valid))
        mismatched["series"]["candles"][0]["volume_quote"] = "1"
        for wire in (
            b"not-json",
            json.dumps(malformed).encode(),
            json.dumps(mismatched).encode(),
        ):
            with (
                self.subTest(wire=wire[:40]),
                tempfile.TemporaryDirectory() as directory,
            ):
                store = HistoryStore(directory)
                digest = hashlib.sha256(wire).hexdigest()
                (Path(directory) / f"{digest}.json").write_bytes(wire)
                with self.assertRaisesRegex(DataQualityError, "invalid_history"):
                    store.load(digest)

    def test_nonvalidating_copies_cannot_change_request_pages_or_selected_series(self):
        record = snapshot()
        page = record.pages[0]
        invalid = (
            record.model_copy(
                update={
                    "pages": (
                        page.model_copy(update={"endpoint": "http://history.example"}),
                    )
                }
            ),
            record.model_copy(
                update={
                    "pages": (
                        page.model_copy(
                            update={"endpoint": "https://user:secret@history.example"}
                        ),
                    )
                }
            ),
            record.model_copy(
                update={"request": record.request.model_copy(update={"count": 2})}
            ),
            record.model_copy(update={"pages": ()}),
            record.model_copy(
                update={"pages": (page.model_copy(update={"before_timestamp": 0}),)}
            ),
            record.model_copy(
                update={
                    "pages": (
                        page.model_copy(
                            update={
                                "series": page.series.model_copy(
                                    update={"source": "other"}
                                )
                            }
                        ),
                    )
                }
            ),
            record.model_copy(
                update={
                    "series": record.series.model_copy(
                        update={"received_at": NOW + timedelta(seconds=1)}
                    )
                }
            ),
            record.model_copy(
                update={
                    "pages": (
                        page.model_copy(
                            update={
                                "series": page.series.model_copy(
                                    update={"received_at": NOW - timedelta(seconds=1)}
                                )
                            }
                        ),
                    )
                }
            ),
        )
        for item in invalid:
            with self.subTest(item=item), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(DataQualityError, "invalid_history"):
                    HistoryStore(directory).save(item)
                self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_ids_path_traversal_and_io_errors_have_safe_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(directory)
            for digest in ("../outside", "/tmp/example", "a" * 63, "A" * 64, None):
                with (
                    self.subTest(digest=digest),
                    self.assertRaisesRegex(DataQualityError, "invalid_history_id"),
                ):
                    store.load(digest)
            with self.assertRaisesRegex(DataQualityError, "history_not_found"):
                store.load("a" * 64)
            with patch(
                "pathlib.Path.open", side_effect=OSError("private filesystem detail")
            ):
                with self.assertRaisesRegex(DataQualityError, "history_io") as error:
                    store.load("a" * 64)
            self.assertNotIn("private", str(error.exception))
            with patch(
                "autohedge.data.history.os.link",
                side_effect=OSError("private filesystem detail"),
            ):
                with self.assertRaisesRegex(DataQualityError, "history_io"):
                    store.save(snapshot())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_history_json_round_trip_revalidates_the_entire_record(self):
        record = snapshot()
        self.assertEqual(
            HistorySnapshot.model_validate_json(record.model_dump_json()), record
        )


if __name__ == "__main__":
    unittest.main()
