import unittest
from meme_harvester import Images, distance


class HarvesterTests(unittest.TestCase):
    def test_parse_image_sources(self):
        parser = Images("https://example.org/post")
        parser.feed('<meta property="og:image" content="/meme.jpg"><img data-src="https://cdn.example.org/photo.png">')
        self.assertIn("https://example.org/meme.jpg", parser.urls)
        self.assertIn("https://cdn.example.org/photo.png", parser.urls)

    def test_ignore_icons_and_insecure_images(self):
        parser = Images("https://example.org/post")
        parser.feed('<img src="https://example.org/logo.png"><img src="http://elsewhere.org/meme.jpg">')
        self.assertEqual(parser.urls, [])

    def test_perceptual_distance(self):
        self.assertEqual(distance("0000000000000000", "0000000000000000"), 0)
        self.assertEqual(distance("0000000000000000", "ffffffffffffffff"), 64)


if __name__ == "__main__":
    unittest.main()
