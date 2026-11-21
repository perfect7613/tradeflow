# 1. No model moves money

Status: accepted

Models may verify, look up, classify, explain and collect (create a payment link). They never
call the payout API, never do arithmetic that decides a payment, and never set a payment status.

- `PaymentDesk.execute()` is the only caller of `Rails.pay()`. It runs only after Tier 0 rules pass
  and either the owner approved (one-time token) or the auto-pay conditions all hold.
- Prism and GLM outputs are inputs to the decision; a disagreement between them, or a model
  leaning towards refusal, sends the case to the owner. A model alone cannot refuse or pay.
- Payout status changes only in `PaymentDesk._apply()`, fed by the Cashfree webhook or status poll.

Consequence: the Cashfree MCP server and Agent Toolkit are kept to read/verify/collect use.
