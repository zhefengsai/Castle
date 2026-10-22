"""Local checks for the pricing cap and correlation-label policy."""
import unittest

from rq4_nonamp_pricing import NOTIONAL, VaultSim, race_coupled


class PricingTest(unittest.TestCase):
    def test_choice_respects_collateral_cap(self):
        for pi_L in (0.0, 0.3, 0.7, 1.0):
            for p in (0.0, 0.0225, 0.035, 0.05, 1.0):
                with self.subTest(p=p, pi_L=pi_L):
                    choice = race_coupled(p, pi_L)["x_hat_over_C"]
                    self.assertGreaterEqual(choice, 0.0)
                    self.assertLessEqual(choice, 1.0)

    def test_late_evidence_reduces_interior_choice(self):
        low = race_coupled(0.035, 0.3)
        high = race_coupled(0.035, 0.7)
        self.assertLess(high["x_hat_over_C"], low["x_hat_over_C"])
        self.assertLess(high["p_switch"], low["p_switch"])

    def test_zero_response_when_expected_direct_loss_exceeds_revenue(self):
        self.assertEqual(race_coupled(0.05, 0.3)["x_hat_over_C"], 0.0)


class LabelPolicyTest(unittest.TestCase):
    def make_vault(self, from_az, to_az):
        vault = VaultSim()
        vault.set_collateral(b"origin", 10 * NOTIONAL, az=from_az)
        vault.set_collateral(b"recipient", 10 * NOTIONAL, az=to_az)
        vault.admit(b"intent", b"origin", NOTIONAL)
        return vault

    def test_unknown_and_shared_labels_are_rejected(self):
        for from_az, to_az, message in ((0, 2, "unknown az"), (1, 0, "unknown az"), (1, 1, "chi!=0")):
            with self.subTest(from_az=from_az, to_az=to_az):
                vault = self.make_vault(from_az, to_az)
                with self.assertRaisesRegex(RuntimeError, message):
                    vault.reroute(b"intent", b"recipient")
                self.assertEqual(vault.R[b"origin"], NOTIONAL)
                self.assertEqual(vault.R[b"recipient"], 0)

    def test_distinct_known_label_passes(self):
        vault = self.make_vault(1, 3)
        vault.reroute(b"intent", b"recipient")
        self.assertEqual(vault.R[b"origin"], 0)
        self.assertEqual(vault.R[b"recipient"], NOTIONAL)


if __name__ == "__main__":
    unittest.main()
