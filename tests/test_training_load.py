import math
from datetime import date, datetime, timedelta
import unittest

from app import compute_daily_tss_series, compute_atl_ctl_from_daily_tss


class TrainingLoadComputationTests(unittest.TestCase):
    def test_compute_daily_tss_series_fills_gaps(self):
        base_date = date(2024, 1, 1)
        activities = [
            {
                "start_date_local": datetime.combine(base_date, datetime.min.time()).isoformat(),
                "moving_time": 3600,
            },
            {
                "start_date_local": datetime.combine(base_date + timedelta(days=2), datetime.min.time()).isoformat(),
                "suffer_score": 25.0,
                "moving_time": 1800,
            },
        ]

        series = compute_daily_tss_series(activities, end_date=base_date + timedelta(days=2))
        self.assertEqual(3, len(series))
        tss_map = {entry["date"]: entry["tss"] for entry in series}
        self.assertGreater(tss_map[base_date], 0)
        self.assertEqual(0.0, tss_map[base_date + timedelta(days=1)])
        self.assertEqual(25.0, tss_map[base_date + timedelta(days=2)])

    def test_compute_atl_ctl_from_daily_tss_matches_ema(self):
        series = [
            {"date": date(2024, 1, 1), "tss": 100.0},
            {"date": date(2024, 1, 2), "tss": 50.0},
            {"date": date(2024, 1, 3), "tss": 0.0},
        ]
        results = compute_atl_ctl_from_daily_tss(series)
        self.assertEqual(3, len(results))

        k_atl = 1 - math.exp(-1 / 7)
        k_ctl = 1 - math.exp(-1 / 42)

        day1 = results[0]
        self.assertAlmostEqual(100.0, day1["atl"], places=4)
        self.assertAlmostEqual(100.0, day1["ctl"], places=4)
        self.assertAlmostEqual(0.0, day1["tsb"], places=4)

        expected_atl_day2 = 100 + k_atl * (50 - 100)
        expected_ctl_day2 = 100 + k_ctl * (50 - 100)
        expected_tsb_day2 = 100.0 - 100.0

        day2 = results[1]
        self.assertAlmostEqual(expected_atl_day2, day2["atl"], places=4)
        self.assertAlmostEqual(expected_ctl_day2, day2["ctl"], places=4)
        self.assertAlmostEqual(expected_tsb_day2, day2["tsb"], places=4)

        expected_atl_day3 = expected_atl_day2 + k_atl * (0 - expected_atl_day2)
        expected_ctl_day3 = expected_ctl_day2 + k_ctl * (0 - expected_ctl_day2)
        expected_tsb_day3 = expected_ctl_day2 - expected_atl_day2

        day3 = results[2]
        self.assertAlmostEqual(expected_atl_day3, day3["atl"], places=4)
        self.assertAlmostEqual(expected_ctl_day3, day3["ctl"], places=4)
        self.assertAlmostEqual(expected_tsb_day3, day3["tsb"], places=4)


if __name__ == "__main__":
    unittest.main()
