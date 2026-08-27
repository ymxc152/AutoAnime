import tempfile
import unittest
from pathlib import Path


def bgm_hit():
    return {
        "list": [
            {
                "id": 7,
                "name_cn": "葬送的芙莉莲",
                "name": "葬送のフリーレン",
                "images": {"common": "http://img.bgm.tv/poster.jpg"},
                "summary": "一个魔法使的故事。",
                "air_date": "2023-09-29",
            }
        ]
    }


def tmdb_tv_hit():
    return {
        "results": [
            {
                "id": 101,
                "name": "葬送的芙莉莲",
                "original_name": "Frieren: Beyond Journey's End",
                "overview": "A mage's journey.",
                "poster_path": "/frieren.jpg",
                "first_air_date": "2023-09-29",
            }
        ]
    }


def fake_get(payload):
    def get(url, headers=None, timeout=12.0):
        return payload

    return get


class MetadataSearchTests(unittest.TestCase):
    def test_bangumi_hit_uses_chinese_name(self):
        from autoanime_v3.metadata import MetadataSearch

        search = MetadataSearch(True, False, "", 12, get=fake_get(bgm_hit()))
        hit = search.search("Frieren")
        self.assertEqual(hit["provider"], "bgm")
        self.assertEqual(hit["name"], "葬送的芙莉莲")
        self.assertEqual(hit["confidence"], 0.9)
        self.assertEqual(hit["provider_id"], "7")

    def test_tmdb_hit_when_bangumi_disabled(self):
        from autoanime_v3.metadata import MetadataSearch

        search = MetadataSearch(False, True, "tmdb-key", 12, get=fake_get(tmdb_tv_hit()))
        hit = search.search("Frieren")
        self.assertEqual(hit["provider"], "tmdb")
        self.assertEqual(hit["name"], "葬送的芙莉莲")
        self.assertIn("image.tmdb.org", hit["poster_url"])

    def test_movie_search_prefers_title_field(self):
        from autoanime_v3.metadata import MetadataSearch

        payload = {
            "results": [{"id": 9, "title": "千与千寻", "original_title": "Spirited Away", "release_date": "2001-07-20"}]
        }
        search = MetadataSearch(False, True, "key", 12, get=fake_get(payload))
        hit = search.search("Spirited Away", movie=True)
        self.assertEqual(hit["provider"], "tmdb")
        self.assertEqual(hit["name"], "千与千寻")

    def test_both_disabled_returns_none(self):
        from autoanime_v3.metadata import MetadataSearch

        search = MetadataSearch(False, False, "", 12)
        self.assertIsNone(search.search("Frieren"))

    def test_network_failure_returns_none_without_raising(self):
        from autoanime_v3.metadata import MetadataSearch

        def get(url, headers=None, timeout=12.0):
            raise TimeoutError("offline")

        search = MetadataSearch(True, False, "", 12, get=get)
        self.assertIsNone(search.search("Frieren"))

    def test_tmdb_prefers_anime_over_live_action(self):
        from autoanime_v3.metadata import MetadataSearch

        payload = {
            "results": [
                {
                    "id": 1,
                    "name": "葬送的芙莉莲（真人版）",
                    "original_name": "Live Action",
                    "genre_ids": [18, 10765],
                    "original_language": "ja",
                    "first_air_date": "2023-01-01",
                    "overview": "",
                },
                {
                    "id": 2,
                    "name": "葬送的芙莉莲",
                    "original_name": "Frieren",
                    "genre_ids": [16, 10759],
                    "original_language": "ja",
                    "first_air_date": "2023-09-29",
                    "overview": "",
                },
            ]
        }
        search = MetadataSearch(False, True, "key", 12, get=fake_get(payload))
        hit = search.search("葬送的芙莉莲")
        self.assertEqual(hit["provider_id"], "2")  # 选中动画版而非第一条真人版
        self.assertTrue(hit["is_anime"])

    def test_tmdb_falls_back_to_first_when_no_anime(self):
        from autoanime_v3.metadata import MetadataSearch

        payload = {
            "results": [
                {
                    "id": 1,
                    "name": "真人剧集",
                    "original_name": "Live",
                    "genre_ids": [18],
                    "original_language": "ja",
                    "first_air_date": "2023-01-01",
                    "overview": "",
                }
            ]
        }
        search = MetadataSearch(False, True, "key", 12, get=fake_get(payload))
        hit = search.search("某番")
        self.assertEqual(hit["provider_id"], "1")
        self.assertFalse(hit["is_anime"])

    def test_bangumi_hit_marks_anime(self):
        from autoanime_v3.metadata import MetadataSearch

        search = MetadataSearch(True, False, "", 12, get=fake_get(bgm_hit()))
        hit = search.search("Frieren")
        self.assertTrue(hit["is_anime"])


class MetadataResolverAgentTests(unittest.TestCase):
    def test_disabled_returns_none(self):
        from autoanime_v3.config import AppConfig
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.models import MediaFile, ParsedName

        config = AppConfig(Path("x.sqlite3"), Path("aliases.json"))
        agent = MetadataResolverAgent(config)
        media = MediaFile(Path("x.mkv"), Path("."), "", "x.mkv", 1, 1)
        parsed = ParsedName(raw_title="Frieren", season=1, episode=1)
        self.assertIsNone(agent.resolve(media, parsed))

    def test_bangumi_hit_returns_openai_contract_dict(self):
        from autoanime_v3.config import AppConfig
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.models import MediaFile, ParsedName

        config = AppConfig(
            Path("x.sqlite3"),
            Path("aliases.json"),
            metadata_bangumi_enabled=True,
            metadata_tmdb_enabled=False,
            metadata_timeout=12,
        )
        agent = MetadataResolverAgent(config, get=fake_get(bgm_hit()))
        media = MediaFile(Path("x.mkv"), Path("."), "", "x.mkv", 1, 1)
        parsed = ParsedName(raw_title="Frieren", season=1, episode=1, title_candidates=("Frieren",))
        result = agent.resolve(media, parsed)
        self.assertEqual(result["title"], "葬送的芙莉莲")
        self.assertEqual(result["provider"], "bgm")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["season"], 1)
        self.assertEqual(result["episode"], 1)
        self.assertIn("bgm:subject=7", result["reason"])


class MetadataBoundaryTests(unittest.TestCase):
    def test_provider_failure_returns_unavailable_without_raising(self):
        from autoanime_v3.integrations.metadata import SafeMetadataAdapter

        def failing_provider(unused_title):
            raise TimeoutError("provider offline")

        result = SafeMetadataAdapter(failing_provider).fetch("测试番")
        self.assertFalse(result.available)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.poster_url)


class MetadataResolverIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def media(self, name):
        path = self.root / name
        path.write_bytes(b"video")
        stat = path.stat()
        return __import__("autoanime_v3.models", fromlist=["MediaFile"]).MediaFile(
            path, self.root, "下载", name, stat.st_size, stat.st_mtime_ns
        )

    def config(self, **overrides):
        from autoanime_v3.config import AppConfig

        kwargs = {
            "metadata_bangumi_enabled": True,
            "metadata_tmdb_enabled": False,
            "metadata_timeout": 12,
        }
        kwargs.update(overrides)
        return AppConfig(self.root / "library.sqlite3", self.root / "aliases.json", **kwargs)

    def test_metadata_hit_applies_bgm_title_with_bgm_evidence(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config()
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get(bgm_hit()))
            result = resolver.resolve(self.media("Frieren Beyond Journey's End - S01E01.mkv"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.canonical_title, "葬送的芙莉莲")
        agents = [item.agent for item in result.evidence]
        self.assertIn("bgm", agents)

    def test_metadata_miss_falls_through_to_openai(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config()
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get({"list": []}))
            resolver.remote = _FakeOpenAI()
            result = resolver.resolve(self.media("Unknown English Show S02E03.mkv"))
        self.assertEqual(result.canonical_title, "虚构中文名")
        agents = [item.agent for item in result.evidence]
        self.assertIn("openai", agents)

    def test_review_enabled_uses_review_verdict_with_review_evidence(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config(
            review_enabled=True,
            openai_enabled=True,
            openai_api_key="fake-key",
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get({"list": []}))
            resolver.review = _FakeReviewAgent()
            resolver.remote = _ExplodingOpenAI()  # review 开启时不应再触发 openai resolve
            result = resolver.resolve(self.media("Unknown English Show S02E03.mkv"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.canonical_title, "复核中文名")
        agents = [item.agent for item in result.evidence]
        self.assertIn("review", agents)

    def test_aiparse_enriches_candidates_and_review_arbitrates(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config(
            parse_agent_mode="uncertain",
            openai_enabled=True,
            openai_api_key="fake-key",
            review_enabled=True,
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get(bgm_hit()))
            resolver.aiparse = _FakeAIParse()
            resolver.review = _FakeReviewAgent()
            resolver.remote = _ExplodingOpenAI()
            result = resolver.resolve(self.media("Unknown English Show S02E03.mkv"))
        self.assertTrue(result.accepted)
        self.assertEqual(result.canonical_title, "复核中文名")
        agents = [item.agent for item in result.evidence]
        self.assertIn("aiparse", agents)
        self.assertIn("review", agents)

    def test_all_mode_aiparse_overrides_machine_result(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config(
            parse_agent_mode="all",
            openai_enabled=True,
            openai_api_key="fake-key",
            review_enabled=True,
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get({"list": []}))
            resolver.aiparse = _FakeAIParse()
            resolver.review = _FakeReviewAgent()
            result = resolver.resolve(self.media("葬送的芙莉莲 - S01E01.mkv"))
        # 机器已自信接受,但 all 模式下 AI 复核覆盖
        self.assertEqual(result.canonical_title, "复核中文名")
        self.assertTrue(result.accepted)

    def test_all_mode_uncertain_review_routes_to_pending(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config(
            parse_agent_mode="all",
            openai_enabled=True,
            openai_api_key="fake-key",
            review_enabled=True,
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get({"list": []}))
            resolver.aiparse = _FakeAIParse()
            resolver.review = _FakeLowConfidenceReview()
            result = resolver.resolve(self.media("葬送的芙莉莲 - S01E01.mkv"))
        # AI 拿不准 → 即使机器自信也压低置信 → 进待处理
        self.assertFalse(result.accepted)
        self.assertIn("ai_uncertain", result.warnings)

    def test_resolution_candidates_collected_for_review(self):
        from autoanime_v3.cache import ResolutionCache
        from autoanime_v3.catalog import TitleCatalog
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.resolver import Resolver

        config = self.config(
            review_enabled=True,
            openai_enabled=True,
            openai_api_key="fake-key",
        )
        with ResolutionCache(config.cache_path) as cache:
            resolver = Resolver(TitleCatalog({}, {}), config, cache)
            resolver.metadata = MetadataResolverAgent(config, get=fake_get(bgm_hit()))
            resolver.aiparse = _FakeAIParse()
            resolver.review = _FakeReviewAgent()
            result = resolver.resolve(self.media("Unknown English Show S02E03.mkv"))
        sources = {candidate.get("source") for candidate in result.candidates}
        self.assertIn("aiparse", sources)  # AI 语言候选
        self.assertIn("filename", sources)  # 机器候选
        self.assertIn("metadata", sources)  # 外部命中
        titles = {candidate.get("title") for candidate in result.candidates}
        self.assertIn("葬送的芙莉莲", titles)


class _FakeOpenAI:
    def resolve(self, media, parsed):
        return {
            "title": "虚构中文名",
            "season": parsed.season,
            "episode": parsed.episode,
            "is_movie": False,
            "confidence": 0.95,
            "reason": "fake llm",
        }


class _FakeReviewAgent:
    def enabled(self):
        return True

    def review(self, media, parsed, hits, prior):
        return {
            "title": "复核中文名",
            "season": parsed.season,
            "episode": parsed.episode,
            "is_movie": bool(parsed.is_movie),
            "confidence": 0.95,
            "reason": "fake review adjudicated",
            "provider": "review",
        }

    def review_unit(self, folder_name, files, parsed, hits, prior):
        return self.review(None, parsed, hits, prior)


class _ExplodingOpenAI:
    def resolve(self, media, parsed):
        raise AssertionError("OpenAI resolve should not be called when review is enabled")


class _FakeAIParse:
    def __init__(self, enabled=True, candidates=None):
        self._enabled = enabled
        self._candidates = candidates or [("zh-cn", "葬送的芙莉莲"), ("romaji", "Frieren")]

    def enabled(self):
        return self._enabled

    def parse(self, media, parsed):
        return {"candidates": self._candidates, "reason": "fake aiparse"}


class _FakeLowConfidenceReview:
    def enabled(self):
        return True

    def review(self, media, parsed, hits, prior):
        return {
            "title": "另一个名字",
            "season": parsed.season,
            "episode": parsed.episode,
            "is_movie": bool(parsed.is_movie),
            "confidence": 0.5,
            "reason": "ambiguous",
            "provider": "review",
        }

    def review_unit(self, folder_name, files, parsed, hits, prior):
        return self.review(None, parsed, hits, prior)


class MetadataResolveAllTests(unittest.TestCase):
    def media(self):
        from autoanime_v3.models import MediaFile

        return MediaFile(Path("x.mkv"), Path("."), "下载", "x.mkv", 1, 1)

    def parsed(self, *candidates):
        from autoanime_v3.models import ParsedName

        return ParsedName(
            raw_title=candidates[0] if candidates else "X",
            season=1,
            episode=2,
            title_candidates=tuple(candidates) if candidates else ("X",),
        )

    def config(self, **overrides):
        from autoanime_v3.config import AppConfig

        kwargs = {
            "metadata_bangumi_enabled": True,
            "metadata_tmdb_enabled": False,
            "metadata_timeout": 12,
        }
        kwargs.update(overrides)
        return AppConfig(Path("x.sqlite3"), Path("aliases.json"), **kwargs)

    def test_first_candidate_miss_second_candidate_hits(self):
        from autoanime_v3.metadata import MetadataResolverAgent

        def get(url, headers=None, timeout=12.0):
            return {"list": []} if "NoSuchAnime" in url else bgm_hit()

        agent = MetadataResolverAgent(self.config(), get=get)
        best, hits = agent.resolve_all(self.media(), self.parsed("NoSuchAnime", "Frieren"))
        self.assertIsNotNone(best)
        self.assertEqual(best["title"], "葬送的芙莉莲")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["provider"], "bgm")
        self.assertIn("bgm:subject=7", best["reason"])

    def test_same_provider_hit_deduplicated(self):
        from autoanime_v3.metadata import MetadataResolverAgent

        agent = MetadataResolverAgent(self.config(), get=fake_get(bgm_hit()))
        best, hits = agent.resolve_all(self.media(), self.parsed("Frieren", "葬送のフリーレン"))
        self.assertIsNotNone(best)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["provider_id"], "7")

    def test_probe_capped_by_max_candidates(self):
        from autoanime_v3.metadata import MAX_CANDIDATE_PROBES, MetadataResolverAgent

        calls = []

        def get(url, headers=None, timeout=12.0):
            calls.append(url)
            return {"list": []}

        agent = MetadataResolverAgent(self.config(), get=get)
        many = tuple("Candidate %d" % index for index in range(MAX_CANDIDATE_PROBES + 3))
        best, hits = agent.resolve_all(self.media(), self.parsed(*many))
        self.assertIsNone(best)
        self.assertEqual(hits, [])
        self.assertEqual(len(calls), MAX_CANDIDATE_PROBES)

    def test_disabled_returns_empty(self):
        from autoanime_v3.metadata import MetadataResolverAgent

        agent = MetadataResolverAgent(self.config(metadata_bangumi_enabled=False))
        best, hits = agent.resolve_all(self.media(), self.parsed("Frieren"))
        self.assertIsNone(best)
        self.assertEqual(hits, [])

    def test_resolve_returns_best_hit(self):
        from autoanime_v3.metadata import MetadataResolverAgent

        agent = MetadataResolverAgent(self.config(), get=fake_get(bgm_hit()))
        result = agent.resolve(self.media(), self.parsed("Frieren"))
        self.assertEqual(result["title"], "葬送的芙莉莲")
        self.assertIn("bgm:subject=7", result["reason"])

    def test_probe_pool_prefers_ai_candidates(self):
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.models import ParsedName

        calls = []

        def get(url, headers=None, timeout=12.0):
            calls.append(url)
            return {"list": []} if "NoSuchAnime" in url else bgm_hit()

        parsed = ParsedName(
            raw_title="NoSuchAnime",
            season=1,
            episode=1,
            title_candidates=("NoSuchAnime",),
            ai_candidates=(("zh-cn", "葬送的芙莉莲"), ("romaji", "Frieren")),
        )
        agent = MetadataResolverAgent(self.config(), get=get)
        best, hits = agent.resolve_all(self.media(), parsed)
        self.assertIsNotNone(best)
        self.assertEqual(best["title"], "葬送的芙莉莲")
        # bgm URL 会把中文 URL 编码(%E8%91%AC%E9%80%81 = 葬送)
        self.assertIn("%E8%91%AC%E9%80%81", calls[0])  # AI 候选(zh-cn)优先探测
        self.assertIn("Frieren", calls[1])  # 第二个 AI 候选
        self.assertTrue(all("NoSuchAnime" not in url for url in calls[:2]))  # 机器候选排后面

    def test_resolve_all_best_prefers_anime_over_higher_confidence_live(self):
        from autoanime_v3.config import AppConfig
        from autoanime_v3.metadata import MetadataResolverAgent
        from autoanime_v3.models import ParsedName

        def get(url, headers=None, timeout=12.0):
            if "themoviedb" in url:
                # 真人版:日源但非动画,中文名 → 置信 0.9
                return {
                    "results": [
                        {
                            "id": 101,
                            "name": "真人剧集",
                            "original_name": "Live",
                            "genre_ids": [18],
                            "original_language": "ja",
                            "first_air_date": "2024-01-01",
                            "overview": "",
                        }
                    ]
                }
            if "AnimeShow" in url:
                # 动画版:非中文名 → 置信 0.8
                return {
                    "list": [
                        {"id": 7, "name": "Frieren", "name_cn": "", "images": {}, "summary": "", "air_date": ""}
                    ]
                }
            return {"list": []}

        config = AppConfig(
            Path("x.sqlite3"),
            Path("aliases.json"),
            metadata_bangumi_enabled=True,
            metadata_tmdb_enabled=True,
            metadata_tmdb_api_key="k",
            metadata_timeout=12,
        )
        agent = MetadataResolverAgent(config, get=get)
        parsed = ParsedName(
            raw_title="AnimeShow",
            season=1,
            episode=1,
            title_candidates=("AnimeShow", "LiveShow"),
        )
        best, hits = agent.resolve_all(self.media(), parsed)
        self.assertEqual(len(hits), 2)
        # 动漫命中置信更低(0.8 vs 0.9),仍应被优先选中
        self.assertEqual(best["provider"], "bgm")
        self.assertLessEqual(best["confidence"], 0.9)


class MetadataEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_enrich_shows_writes_metadata_records(self):
        from autoanime_v3.metadata import MetadataSearch
        from autoanime_v3.services.metadata import MetadataEnrichmentService
        from autoanime_v3.services.settings import SettingsService

        database = self.root / "web.sqlite3"
        settings = SettingsService(database)
        item = next(i for i in settings.list() if i["key"] == "metadata.bangumi_enabled")
        settings.update("metadata.bangumi_enabled", True, item["revision"])
        search = MetadataSearch(True, False, "", 12, get=fake_get(bgm_hit()))

        from autoanime_v3.db.uow import SqliteUnitOfWork

        with SqliteUnitOfWork(database) as uow:
            cursor = uow.connection.execute(
                "INSERT INTO shows(canonical_title, normalized_key, status) VALUES (?, ?, 'active')",
                ("葬送的芙莉莲", "frieren"),
            )
            show_id = int(cursor.lastrowid)
            uow.commit()

        stored = MetadataEnrichmentService(database, search=search).enrich_shows([(show_id, "葬送的芙莉莲")])
        self.assertEqual(stored, 1)
        import sqlite3

        connection = sqlite3.connect(str(database))
        connection.row_factory = sqlite3.Row
        try:
            record = connection.execute(
                "SELECT * FROM metadata_records WHERE show_id = ?", (show_id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(record["provider"], "bgm")
        self.assertEqual(record["provider_id"], "7")
        self.assertEqual(record["poster_url"], "http://img.bgm.tv/poster.jpg")

    def test_enrich_skips_when_provider_disabled(self):
        from autoanime_v3.services.metadata import MetadataEnrichmentService
        from autoanime_v3.services.settings import SettingsService

        database = self.root / "web.sqlite3"
        stored = MetadataEnrichmentService(database).enrich_shows([(1, "某番")])
        self.assertEqual(stored, 0)


if __name__ == "__main__":
    unittest.main()
