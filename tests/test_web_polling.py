import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from app import create_app
from karting import Runtime
from karting.modules.files import normalize_race


class WebPollingTests(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime()
        self.app = create_app(self.runtime)
        self.app.config.update(TESTING=True, UPDATE_TIME=1234, LONG_POLL_TIMEOUT=0)
        self.client = self.app.test_client()
        self.previous_view = self.client.get('/state').json['view']
        self.client.put('/state', json={'view': 'style1'})
        self.addCleanup(self.client.put, '/state', json={'view': self.previous_view})

    def race(self, style='laps'):
        return self.runtime.store.set('current_race', normalize_race(
            {'style': style, 'laps': {'7': [32.0, 31.0]}}, 'laps.yaml'))

    def config(self, html):
        match = re.search(r'<script id="karting-poll-config" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def check(self, revision, category='current_race'):
        return self.client.get('/check-updates', query_string={
            'revision': revision, 'category': category})

    def test_bootstrap_uses_revision_of_rendered_race_not_clock(self):
        item = self.race()
        self.runtime.store.set('current_time', '15:00:00')
        config = self.config(self.client.get('/').get_data(as_text=True))
        self.assertEqual(item.revision, config['revision'])
        self.assertEqual('style1', config['view'])
        self.assertEqual(1234, config['intervalMs'])

    def test_snapshot_change_during_render_is_not_hidden(self):
        item = self.race()
        def render(template, **context):
            self.runtime.store.set('current_race', {'new': True})
            return json.dumps(context['web_poll'])
        with patch('app.routes.render_template', side_effect=render):
            config = json.loads(self.client.get('/').get_data(as_text=True))
        self.assertEqual(item.revision, config['revision'])
        self.assertTrue(self.check(config['revision']).json['updated'])

    def test_empty_page_bootstraps_zero_and_detects_first_race(self):
        config = self.config(self.client.get('/').get_data(as_text=True))
        self.assertEqual(0, config['revision'])
        self.runtime.store.set('temperature', 20)
        self.assertFalse(self.check(0).json['updated'])
        self.race()
        self.assertTrue(self.check(0).json['updated'])

    def test_unchanged_race_and_sensor_updates_do_not_trigger_reload(self):
        item = self.race()
        for _ in range(2):
            self.runtime.store.set('current_time', '15:00:00')
            self.runtime.store.set('temperature', 23)
            self.assertEqual({'updated': False, 'revision': item.revision}, self.check(item.revision).json)

    def test_new_race_revision_is_detected(self):
        first = self.race()
        second = self.race()
        self.assertEqual({'updated': True, 'revision': second.revision}, self.check(first.revision).json)

    def test_all_data_polling_remains_backwards_compatible(self):
        item = self.runtime.store.set('temperature', 24)
        self.assertEqual({'updated': True, 'revision': item.revision},
                         self.client.get('/check-updates?revision=0').json)

    def test_unknown_polling_category_is_rejected(self):
        self.assertEqual(400, self.check(0, 'unknown').status_code)

    def test_supported_views_only(self):
        for value in ('race', '', '<script>', None, ['style1'], {'style1': True}):
            with self.subTest(value=value):
                self.assertEqual(400, self.client.put('/state', json={'view': value}).status_code)
        self.assertEqual(200, self.client.put('/state', json={'view': 'style2'}).status_code)

    def test_every_page_includes_exactly_one_shared_controller(self):
        for style, view in (('laps', 'style1'), ('laps', 'style2'), ('race', 'style1')):
            with self.subTest(style=style, view=view):
                self.race(style)
                self.client.put('/state', json={'view': view})
                html = self.client.get('/').get_data(as_text=True)
                self.assertEqual(view, self.config(html)['view'])
                self.assertEqual(1, html.count('/static/js/polling.js'))
                self.assertNotIn('сurrent_race.UPD_TIME', html)
                self.assertNotIn('data.timestamp', html)
        self.runtime.store.set('current_race', [])
        html = self.client.get('/').get_data(as_text=True)
        self.assertEqual(1, html.count('/static/js/polling.js'))
        self.config(html)

    def test_controller_is_served_by_flask(self):
        response = self.client.get('/static/js/polling.js')
        self.addCleanup(response.close)
        self.assertEqual(200, response.status_code)
        self.assertIn('category=current_race', response.get_data(as_text=True))

    @unittest.skipUnless(shutil.which('node'), 'Node.js is required to execute the JavaScript regressions')
    def test_javascript_controller(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which('node'), '--test', str(root / 'tests/js/test_polling.cjs')],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
