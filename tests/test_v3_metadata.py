import unittest


class MetadataBoundaryTests(unittest.TestCase):
    def test_provider_failure_returns_unavailable_without_raising(self):
        from autoanime_v3.integrations.metadata import SafeMetadataAdapter

        def failing_provider(unused_title):
            raise TimeoutError("provider offline")

        result = SafeMetadataAdapter(failing_provider).fetch("测试番")
        self.assertFalse(result.available)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.poster_url)


if __name__ == "__main__":
    unittest.main()
