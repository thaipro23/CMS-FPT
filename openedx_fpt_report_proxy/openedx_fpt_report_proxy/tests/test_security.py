import unittest

from openedx_fpt_report_proxy.security import course_hash, normalize_report_key, report_course_hash


class SecurityHelpersTest(unittest.TestCase):
    def test_normalize_accepts_report_key(self):
        key = "0123456789abcdef0123456789abcdef01234567/grades.csv"
        self.assertEqual(normalize_report_key(key), key)

    def test_normalize_rejects_traversal(self):
        for key in ("../grades.csv", "abc/../grades.csv", "/absolute.csv", "abc\\..\\grades.csv", ""):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    normalize_report_key(key)

    def test_report_course_hash_requires_sha1_directory(self):
        key = "0123456789abcdef0123456789abcdef01234567/grades.csv"
        self.assertEqual(report_course_hash(key), key.split("/", 1)[0])
        with self.assertRaises(ValueError):
            report_course_hash("not-a-course-hash/grades.csv")

    def test_course_hash_matches_report_store_algorithm(self):
        self.assertEqual(
            course_hash("course-v1:FPS+COM1091+FA26"),
            "e6a877439f7a55f64cd98259df8805d369069b1d",
        )


if __name__ == "__main__":
    unittest.main()
