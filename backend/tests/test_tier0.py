from tradeflow import scenario, tier0


def test_hero_options_match_the_plan(tf):
    scenario.add_forwarder_invoice(tf.L, scenario.SAMPLE_INVOICE, "test")
    opts = {o.key: o for o in tier0.options(tf.L)}
    assert opts["A"].pays_now == 2_42_000 and opts["A"].cash_left == -12_000 and not opts["A"].feasible
    assert opts["B"].pays_now == 2_24_000 and opts["B"].cash_left == 6_000
    assert opts["C"].pays_now == 1_92_000 and opts["C"].cash_left == 38_000


def test_forwarder_answer_reorders_options(tf):
    scenario.add_forwarder_invoice(tf.L, scenario.SAMPLE_INVOICE, "test")
    assert tier0.options(tf.L)[0].key == "C"  # release unknown: B is only a maybe
    tf.L.learn("forwarder_release", True, "test")
    assert tier0.options(tf.L)[0].key == "B"
    tf.L.learn("forwarder_release", False, "test")
    ranked = tier0.options(tf.L)
    assert ranked[0].key == "C"
    assert not next(o for o in ranked if o.key == "B").feasible


def test_buyer_weight_comes_from_record_then_from_call(tf):
    cash = tier0.cash_position(tf.L)
    assert cash.expected_in_weighted == 1_50_000  # 2.5 lakh x (1 - 2/5)
    tf.L.collections["buyer_po"].commitment = "firm_date"
    assert tier0.cash_position(tf.L).expected_in_weighted == 2_25_000


def test_scorecard_catches_every_trap_and_blocks_no_good_payment():
    r = scenario.run_scorecard()
    assert r["traps_caught"] == r["traps_total"]
    assert r["good_wrongly_blocked"] == 0
