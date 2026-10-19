import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync import parse_deadline, normalize, google_id, google_body, fetch_source

class SyncTests(unittest.TestCase):
    def test_timezones(self):
        aoe = parse_deadline('2027-01-01 23:59:59', 'AoE')
        self.assertEqual(aoe.utcoffset(), timedelta(hours=-12))
        self.assertEqual(parse_deadline('2027-01-01 23:59:59', 'UTC+8').utcoffset(), timedelta(hours=8))
        self.assertEqual(parse_deadline('2027-01-01 23:59:59', 'PT').utcoffset(), timedelta(hours=-8))
        self.assertEqual(parse_deadline('2027-06-01 23:59:59', 'PT').utcoffset(), timedelta(hours=-7))
        self.assertIsNone(parse_deadline('TBD', 'AoE'))
        with self.assertRaises(ValueError):
            parse_deadline('2027-01-01 23:59:59', 'UNKNOWN-XYZ')
    def test_ranks_and_rounds(self):
        fixture = Path(__file__).resolve().parent / 'fixture.yml'
        rows = normalize(fetch_source(str(fixture)), now=datetime(2026, 10, 19, tzinfo=timezone.utc), lookahead_days=550)
        self.assertEqual(len(rows), 3)  # ICRA abstract + paper, CVPR paper. B excluded
        self.assertEqual({x.field for x in rows}, {'AI', 'Robotics'})
        self.assertEqual(len(set(x.source_id for x in rows)), 3)
        self.assertEqual(len(set(google_id(x) for x in rows)), 3)
        for row in rows:
            self.assertLessEqual(max(x['minutes'] for x in google_body(row)['reminders']['overrides']), 40320)

if __name__ == '__main__':
    unittest.main()
