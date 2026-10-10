import argparse
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from sidecarwatch import config
from tests.helpers import Sandbox, FakeClock


class ConfigTests(unittest.TestCase):
    def test_defaults_and_limits(self):
        self.assertEqual(config.merge(config.DEFAULTS, {})['auto_reply']['max_turns'], 5)
        for val in ({'never': {'disable': True}}, {'never': {'patterns': []}}, {'auto_reply': {'max_turns': 51}}, {'auto_reply': {'min_gap_seconds': 29}}, {'delivery': {'claude_resume': 'yes'}}, {'scrub': {'extra_patterns': ['[']}}, {'watch': {'only': [1]}}):
            with self.subTest(val=val), self.assertRaises(config.ConfigError):
                config.merge(config.DEFAULTS, val)
        for word in config.FORBIDDEN_ARGS:
            with self.assertRaises(config.ConfigError):
                config.merge(config.DEFAULTS, {'reviewer': {'claude': {'extra_args': [word]}}})

    def test_home_overrides(self):
        with Sandbox() as sb:
            args = argparse.Namespace(config=None, claude_home=None, codex_home=None)
            for agent, variable in [('claude', 'CLAUDE_CONFIG_DIR'), ('codex', 'CODEX_HOME')]:
                override = 'SIDECARWATCH_' + agent.upper() + '_HOME'
                with self.subTest(agent=agent), patch.dict(os.environ, {variable: str(sb.root / 'custom')}):
                    self.assertEqual(config.directories(args)[3][agent], getattr(sb, agent))
                    setattr(args, agent + '_home', str(sb.root / 'flag'))
                    self.assertEqual(config.directories(args)[3][agent], sb.root / 'flag')
                    setattr(args, agent + '_home', None)
                    os.environ.pop(override)
                    self.assertEqual(config.directories(args)[3][agent], sb.root / 'custom')
                    os.environ.pop(variable)
                    self.assertEqual(config.directories(args)[3][agent], sb.root / ('.' + agent))

    def test_every_config_limit(self):
        for key, (floor, ceiling) in config.LIMITS.items():
            for value in [floor - 1] + ([] if ceiling is None else [ceiling + 1]):
                group, name = key.split('.')
                with self.subTest(key=key, value=value), self.assertRaises(config.ConfigError):
                    config.merge(config.DEFAULTS, {group: {name: value}})

    def test_times(self):
        now = FakeClock().now()
        self.assertEqual(config.duration('2h'), 7200)
        self.assertEqual((now - config.when('30m', now)).total_seconds(), 1800)
        self.assertGreater(config.when('00:00', now, True), now)
        for value in ['-1h', '1d', 'banana']:
            with self.assertRaises(config.ConfigError):
                config.duration(value)
