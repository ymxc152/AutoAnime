import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from autoanime_v3.models import Evidence, MediaFile, Resolution
from autoanime_v3.planner import build_plan


class PlannerIncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.output_root = self.root / "library"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def resolution(self, relative_path, release_tag="", episode=3, content=None):
        path = self.root / "input" / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            str(relative_path).encode("utf-8") if content is None else content
        )
        stat = path.stat()
        media = MediaFile(
            path,
            self.root / "input",
            "bundle",
            Path(relative_path).as_posix(),
            stat.st_size,
            stat.st_mtime_ns,
        )
        return Resolution(media, "测试番剧", 1, episode, False, 0.99, True, release_tag)

    @staticmethod
    def video_entry(plan):
        return next(entry for entry in plan if not entry.companion_of)

    def test_incremental_platform_versions_keep_distinct_stable_destinations(self):
        baha = self.resolution("baha/Show.S01E03.Baha.mkv", "Baha")
        friday = self.resolution("friday/Show.S01E03.friDay.mkv", "friDay")

        first = self.video_entry(build_plan([baha], self.output_root))
        self.assertIn("[Baha]", first.destination.name)
        first.destination.parent.mkdir(parents=True, exist_ok=True)
        first.destination.write_bytes(b"already organized Baha release")

        second = self.video_entry(build_plan([friday], self.output_root))
        self.assertEqual("organize", second.action)
        self.assertIn("[friDay]", second.destination.name)
        self.assertNotEqual(first.destination, second.destination)

    def test_single_file_intrinsic_metadata_gets_a_stable_version_label(self):
        cases = (
            ("[BeanSub] Show.S01E03.mkv", "", "BeanSub"),
            ("[ANi] Show.S01E03.mkv", "", "ANi"),
            ("[Studio GreenTea] Show.S01E03.mkv", "", "Studio GreenTea"),
            ("[BeanSub&LoliHouse] Show.S01E03.mkv", "", "BeanSub&LoliHouse"),
            ("Show.S01E03.uncensored.mkv", "", "Uncensored"),
            ("Show.S01E03.Mandarin.mkv", "", "zh-dub"),
        )
        for index, (name, release_tag, expected_label) in enumerate(cases):
            with self.subTest(name=name):
                resolution = self.resolution("case%d/%s" % (index, name), release_tag)
                entry = self.video_entry(build_plan([resolution], self.output_root))
                self.assertIn("[%s]" % expected_label, entry.destination.name)

    def test_version_label_uses_the_actual_v_number(self):
        for version in (3, 4):
            with self.subTest(version=version):
                resolution = self.resolution("v%d/Show.S01E03.V%d.mkv" % (version, version))
                entry = self.video_entry(build_plan([resolution], self.output_root))
                self.assertIn("[V%d]" % version, entry.destination.name)
                self.assertNotIn("[V2]", entry.destination.name)

    def test_equal_intrinsic_labels_use_order_independent_relative_path_digests(self):
        left = self.resolution("left/Show.S01E03.Baha.mkv", "Baha")
        right = self.resolution("right/Show.S01E03.Baha.mkv", "Baha")

        forward_plan = [entry for entry in build_plan([left, right], self.output_root) if not entry.companion_of]
        reverse_plan = [entry for entry in build_plan([right, left], self.output_root) if not entry.companion_of]
        forward = {entry.source: (entry.destination, entry.action, entry.reason) for entry in forward_plan}
        reverse = {entry.source: (entry.destination, entry.action, entry.reason) for entry in reverse_plan}

        self.assertEqual(
            {source: values[0] for source, values in forward.items()},
            {source: values[0] for source, values in reverse.items()},
        )
        self.assertEqual(2, len({values[0] for values in forward.values()}))
        self.assertEqual(1, sum(values[1] == "organize" for values in forward.values()))
        self.assertEqual(1, sum(values[1] == "skip" for values in forward.values()))
        self.assertEqual(
            {"not_preferred_release"},
            {values[2] for values in forward.values() if values[1] == "skip"},
        )
        for destination, unused_action, unused_reason in forward.values():
            self.assertRegex(destination.name, r"\[Baha-[0-9a-f]{8}\]")

    def test_preferred_release_uses_resolution_rank_before_size(self):
        high = self.resolution("high/Show.S01E03.1080p.mkv", content=b"high")
        low = self.resolution("low/Show.S01E03.720p.mkv", content=b"x" * 4096)

        entries = {
            entry.source: entry
            for entry in build_plan([low, high], self.output_root)
            if not entry.companion_of
        }

        self.assertEqual(entries[high.media.path].action, "organize")
        self.assertEqual(entries[low.media.path].action, "skip")
        self.assertEqual(entries[low.media.path].reason, "not_preferred_release")

    def test_preferred_release_uses_larger_size_for_equal_rank(self):
        small = self.resolution("small/Show.S01E03.1080p.mkv", content=b"small")
        large = self.resolution(
            "large/Show.S01E03.1080p.mkv", content=b"large" * 1024
        )

        entries = {
            entry.source: entry
            for entry in build_plan([small, large], self.output_root)
            if not entry.companion_of
        }

        self.assertEqual(entries[large.media.path].action, "organize")
        self.assertEqual(entries[small.media.path].action, "skip")
        self.assertEqual(entries[small.media.path].reason, "not_preferred_release")

    def test_incremental_plain_versions_get_distinct_stable_path_keys(self):
        first_resolution = self.resolution("plain-a/Show.S01E03.mkv")
        second_resolution = self.resolution("plain-b/Show.S01E03.mkv")

        first = self.video_entry(build_plan([first_resolution], self.output_root))
        self.assertRegex(first.destination.name, r"\[version-[0-9a-f]{8}\]")
        first.destination.parent.mkdir(parents=True, exist_ok=True)
        first.destination.write_bytes(b"already organized plain release")

        second = self.video_entry(build_plan([second_resolution], self.output_root))
        self.assertEqual("organize", second.action)
        self.assertRegex(second.destination.name, r"\[version-[0-9a-f]{8}\]")
        self.assertNotEqual(first.destination, second.destination)

    def test_plain_versions_with_same_relative_path_in_different_roots_get_distinct_keys(self):
        resolutions = []
        for root_name in ("input-a", "input-b"):
            input_root = self.root / root_name
            path = input_root / "Show.S01E03.mkv"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(root_name.encode("utf-8"))
            stat = path.stat()
            media = MediaFile(
                path,
                input_root,
                "bundle",
                "Show.S01E03.mkv",
                stat.st_size,
                stat.st_mtime_ns,
            )
            resolutions.append(Resolution(media, "测试番剧", 1, 3, False, 0.99, True))

        destinations = [
            self.video_entry(build_plan([resolution], self.output_root)).destination
            for resolution in resolutions
        ]

        self.assertNotEqual(destinations[0], destinations[1])

    def test_stable_version_key_does_not_require_path_resolution_access(self):
        resolution = self.resolution("restricted/Show.S01E03.mkv")

        with mock.patch.object(Path, "resolve", side_effect=PermissionError("denied")):
            entry = self.video_entry(build_plan([resolution], self.output_root))

        self.assertRegex(entry.destination.name, r"\[version-[0-9a-f]{8}\]")

    def test_title_bracket_is_not_misclassified_as_release_group(self):
        resolution = self.resolution("title/[测试番剧] Show.S01E03.mkv")
        entry = self.video_entry(build_plan([resolution], self.output_root))
        self.assertNotIn("[测试番剧]", entry.destination.name)

    def test_catalog_alias_bracket_is_not_misclassified_as_release_group(self):
        resolution = self.resolution("title-alias/[Grand Blue] Show.S01E03.mkv")
        resolution.canonical_title = "碧蓝之海"
        resolution.evidence.append(Evidence("catalog", "碧蓝之海", 0.99, "alias=Grand Blue"))

        entry = self.video_entry(build_plan([resolution], self.output_root))

        self.assertNotIn("[Grand Blue]", entry.destination.name)
        self.assertRegex(entry.destination.name, r"\[version-[0-9a-f]{8}\]")

    def test_already_linked_video_still_plans_a_new_matching_subtitle(self):
        resolution = self.resolution("linked/Show.S01E03.mkv")
        initial = self.video_entry(build_plan([resolution], self.output_root))
        initial.destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(str(resolution.media.path), str(initial.destination))
        subtitle = resolution.media.path.with_name("Show.S01E03.CHS.ass")
        subtitle.write_text("new subtitle", encoding="utf-8")

        plan = build_plan([resolution], self.output_root)
        video = self.video_entry(plan)
        subtitle_entries = [entry for entry in plan if entry.companion_of]

        self.assertEqual("skip", video.action)
        self.assertEqual("already_linked", video.reason)
        self.assertEqual(1, len(subtitle_entries))
        self.assertEqual("organize", subtitle_entries[0].action)
        self.assertEqual(subtitle, subtitle_entries[0].source)
        self.assertTrue(subtitle_entries[0].destination.name.endswith(".CHS.ass"))

    def test_already_linked_video_keeps_subtitle_destination_collision_safe(self):
        resolution = self.resolution("collision/Show.S01E04.mkv", episode=4)
        initial = self.video_entry(build_plan([resolution], self.output_root))
        initial.destination.parent.mkdir(parents=True, exist_ok=True)
        os.link(str(resolution.media.path), str(initial.destination))
        subtitle = resolution.media.path.with_name("Show.S01E04.CHS.ass")
        subtitle.write_text("new subtitle", encoding="utf-8")
        subtitle_destination = initial.destination.with_suffix("").with_name(
            initial.destination.stem + ".CHS.ass"
        )
        subtitle_destination.write_text("unrelated existing subtitle", encoding="utf-8")

        plan = build_plan([resolution], self.output_root)
        subtitle_entries = [entry for entry in plan if entry.companion_of]

        self.assertEqual(1, len(subtitle_entries))
        self.assertEqual("conflict", subtitle_entries[0].action)
        self.assertEqual("subtitle_destination_exists", subtitle_entries[0].reason)


if __name__ == "__main__":
    unittest.main()
