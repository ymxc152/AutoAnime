from unittest import TestCase

import AutoAnimeMv as aam


class TestSubtitleMatching(TestCase):
    def test_ideass_matches_current_episode_subtitle(self):
        ass_list = [
            "Frieren.S01.=03=.chs.ass",
            "Frieren.S01.=04=.chs.ass",
            "OtherAnime.S01.=03=.chs.ass",
        ]

        matched = aam.Auxiliary_IDEASS("Frieren", "S01", "03", ass_list)
        self.assertEqual(matched, ["Frieren.S01.=03=.chs.ass"])
