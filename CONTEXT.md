# TradeFlow domain language

Use these words in code, UI and docs. One concept, one name.

| Term | Meaning |
| --- | --- |
| **Owner** | The exporter who decides (Selvam in the demo). Hears briefs, approves by nod. |
| **Party** | Anyone money moves to or from: truck owner, driver, forwarder, supplier, buyer. Has a saved phone and (for payees) a saved account. |
| **Obligation** | Money the owner owes a party, with a due date. May carry a **quote** (what was agreed before the invoice). The amount above the quote is **disputed**. |
| **Collection** | Money a party (the buyer) owes the owner. Weighted by the buyer's record or by what the buyer said on a call. |
| **Cash position** | Balance minus payouts not yet confirmed, plus weighted collections. Lines are labelled **confirmed**, **pending**, **estimated** or **simulated** and never mixed. **Spendable** excludes estimated money. |
| **Option** | One way to pay what is due (A pay everything, B disputed invoice at quote, C hold it). Built and ranked by code. |
| **Payout** | One attempt to pay one obligation. Has exactly one **transfer id**. Status comes only from Cashfree (webhook or status poll). |
| **Attempt** | Counter on an obligation. Bumps only when a reversal reopens it; a new attempt needs a new approval. |
| **Rule** | A Tier 0 safety check. Each fails to one of: **refuse**, **call** (someone must be phoned), **ask_owner**. |
| **Fact** | Something learned that changes a decision (e.g. `forwarder_release = true`), with its source and confidence. |
| **Call** | A voice agent conversation with one **purpose** (forwarder_release, account_change, buyer_collection, driver_photo). Ends in **findings**, read by Prism. |
| **Brief** | What the owner hears: GLM (or a template) in English, translated to the owner's language, spoken by Bulbul. |
| **Approval** | One brief awaiting the owner. Carries a one-time **token**. **Armed** when the brief finishes; the **window** (10 s) starts then. |
| **Ears** | The owner's AirPods. A nod counts only while armed, only with the approval's token, only with AirPods in. |
| **Trail** | The "Why?" view: every audit entry behind one payout. |
| **Rails** | The seam to Cashfree. Adapters: `CashfreeRails` (real) and `SimRails` (stand-in, same webhook shapes). |

## Tiers

0. Plain code: rules, arithmetic, payment state.
1. Sarvam Prism: probabilities over fixed options.
2. GLM 5.3 (DeepSeek V4.1 Flash as backup): trade-offs and the brief.
3. The owner.
