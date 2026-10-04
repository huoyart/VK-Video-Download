import unittest
from download_utils import build_output_template, format_size, parse_video_urls, sanitize_filename, validate_proxy


class UtilsTests(unittest.TestCase):
    def test_url_split_dedupe_and_validation(self):
        self.assertEqual(parse_video_urls('https://example.com/a，https://example.com/b\nhttps://example.com/a'),
                         ['https://example.com/a', 'https://example.com/b'])
        for text in ('', 'file:///video.mp4', 'https://example.com:abc/x'):
            with self.assertRaises(ValueError):
                parse_video_urls(text)

    def test_proxy_modes(self):
        self.assertEqual(validate_proxy(''), '')
        self.assertEqual(validate_proxy('socks5://127.0.0.1:1080'), 'socks5://127.0.0.1:1080')
        with self.assertRaises(ValueError):
            validate_proxy('file:///etc/passwd')

    def test_windows_filename_and_size(self):
        self.assertNotEqual(sanitize_filename('CON').upper(), 'CON')
        self.assertFalse(sanitize_filename('bad<>. ').endswith('.'))
        self.assertEqual(format_size(1024), '1.0 KB')
        self.assertEqual(format_size(-1), '-')

    def test_template_uses_fixed_token(self):
        template = build_output_template(r'C:\Video%20', token='abc123', use_folder=True)
        self.assertIn('abc123', template)
        self.assertIn('video.%(ext)s', template)
        self.assertIn('%%20', template)


if __name__ == '__main__':
    unittest.main()
