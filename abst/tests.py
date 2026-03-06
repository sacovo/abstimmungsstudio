import numpy as np
from django.test import TestCase
from abst.predict import predict_missing_results


class PredictTests(TestCase):
    def test_predict_missing_results(self):
        projection = np.array([
            [1.0, 2.0],
            [1.0, 3.0],
            [1.0, 4.0],
            [1.0, 5.0]
        ])
        # Suppose coefficients should be [0.5, 0.5]
        # Then values:
        # row 0: 0.5*1 + 0.5*2 = 1.5
        # row 1: 0.5*1 + 0.5*3 = 2.0
        # row 2: 0.5*1 + 0.5*4 = 2.5
        # row 3: 0.5*1 + 0.5*5 = 3.0
        results = [1.5, 2.0, 0.0, 0.0]
        mask = [False, False, True, True]

        y_final = predict_missing_results(projection, results, mask)

        self.assertAlmostEqual(y_final[0], 1.5)
        self.assertAlmostEqual(y_final[1], 2.0)
        self.assertAlmostEqual(y_final[2], 2.5)
        self.assertAlmostEqual(y_final[3], 3.0)
