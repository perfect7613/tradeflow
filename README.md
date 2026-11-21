# TradeFlow

**A money control room for MSME exporters. The owner approves payments with a nod of his AirPods.**

An exporter in Tiruppur gets a freight bill ₹18,000 over quote, has a supplier due tomorrow, and a buyer who often pays late. TradeFlow reads the bill, phones the forwarder in Tamil to ask why, works out what he can safely pay, and tells him in his ear. He nods and Cashfree pays. If someone tries to redirect a payment, TradeFlow calls the real owner and refuses. The AI can check, look up and collect. Only the owner's nod moves money out.

Built for the Sarvam × Cashfree hackathon.

```
 invoice ──▶ Tier 0 rules + arithmetic ──▶ Prism (50 ms) ──▶ GLM 5.3 ──▶ owner's nod ──▶ Cashfree Payouts ──▶ webhook
                 │ broken rule: refuse           │ unknown? ──▶ voice agent phones someone ──▶ facts ──┘
```

## What's in the box

| Path | What |
| --- | --- |
| `backend/` | Python 3.12, FastAPI, managed with **uv**. Decision engine, Cashfree rails, voice loop, live translation. |
| `web/` | Next.js 16 PWA (static export, served by the backend). Control room, Ears, Phone, Translate, Why?, Traps. |
| `ios/` | **TradeFlow Ears**: thin SwiftUI shell around the PWA that adds AirPods head tracking and stem press. |
| `CONTEXT.md` | Domain glossary. `docs/adr/` records the decisions not to re-litigate. |

### Screens

- **Room** (`/`): spendable cash with confirmed / pending / estimated labels, the demo script as a stepper, options A/B/C with arithmetic, payouts with stamps, audit trail.
- **Ears** (`/ears`): the owner's view. Plays the brief in Tamil, opens a 10 s nod window, mirrors your head live. Nod = approve, shake = refuse, tilt right = "why?", squeeze stem = speak a new amount.
- **Phone** (`/phone?as=forwarder|truck_owner|buyer`): the judge's phone. Rings when TradeFlow calls; a voice agent (Saaras → Sarvam 105B → Bulbul) talks in the chosen language.
- **Translate** (`/translate`): live two-way interpreter. Auto-detects who is speaking; the owner hears the other side in Tamil in his AirPods; his words show (and optionally play) in their language.
- **Why?** (`/trail?id=…`): every rule, model answer, call line and Cashfree status behind one payout.
- **Traps** (`/scorecard`): 13 planted traps + 3 good payments run through the Tier 0 rules.

## Models (all on one Sarvam key)

| Job | Model |
| --- | --- |
| Fast yes/no, choice, score, routing | `sarvam-prism` via `POST /v2/systemone` |
| Trade-offs and the spoken brief | `glm5.3` (backup `deepseekv4.1-flash-1017`; Kimi K3.1 Pro is not enabled on our key) |
| Voice agent turns | `sarvam-105b-conversations` (~0.4 s) |
| Field extraction | `deepseekv4.1-flash-1017` |
| Invoices / receipts | Sarvam Vision (Doc AI extract) |
| Speech in / out, translation | `saaras:v3` streaming (VAD, auto language detect), `bulbul:v3`, `mayura:v1` code-mixed |

Measured on our key: Prism ~50–150 ms, a voice turn ~2 s end to end (STT endpoint + 0.4 s LLM + ~1.2 s TTS).

## Cashfree

| Product | Used for | Code |
| --- | --- | --- |
| Secure ID: Bank Account Verification (sync V2) | Every payee; every changed account | `rails.py` `verify_account` |
| Payouts V2 (beneficiary + standard transfer, `x-api-version: 2024-01-01`) | Truck, forwarder, supplier payouts with a fixed `transfer_id` per obligation attempt | `rails.py` `pay` |
| Payment Links (`x-api-version: 2025-01-01`, partial payments) | Buyer pays during the call | `rails.py` `create_link` |
| Webhooks (HMAC-SHA256 of timestamp + raw body) | The only source of payout / link status | `api.py` `/webhooks/cashfree/*` |

A payout counts as **paid** only on `SUCCESS` + `COMPLETED` (or `TRANSFER_ACKNOWLEDGED`). `REVERSED` reopens the obligation and needs a fresh approval. A timeout is polled, never retried.

**Without keys the app runs on `SimRails`**, which emits the same webhook payloads Cashfree sends, and uses Cashfree's published sandbox test accounts. Every simulated amount is labelled "simulated" in the UI.

### Keys you need for live sandbox

From the [Merchant Dashboard](https://merchant.cashfree.com), switched to **Test**:

1. **Payouts** → Developers → API Keys → client ID + secret. Then Developers → Two-Factor Authentication: whitelist your IP **or** download the public key (set `CASHFREE_PAYOUTS_PUBLIC_KEY_PATH`; we sign `X-Cf-Signature` for you).
2. **Secure ID** → Developers → API Keys (Bank Account Verification must be enabled).
3. **Payment Gateway** → Developers → API Keys (for Payment Links).
4. Webhooks: Payouts → Developers → Webhooks → `https://<your-tunnel>/webhooks/cashfree/payouts`. Payment links send to `PUBLIC_URL/webhooks/cashfree/links` automatically.
5. Add test balance to the Payouts sandbox account.

Put them in `backend/.env` (see `backend/.env.example`).

## Run it

```bash
# 1. backend (uv)
cd backend
cp .env.example .env            # add SARVAM_API_KEY (and Cashfree keys if you have them)
uv sync
uv run pytest -q                # 12 tests
uv run tradeflow                # http://localhost:8000

# 2. web (build once; the backend serves web/out)
cd ../web
npm install
NEXT_PUBLIC_API= npm run build
npx vitest run                  # gesture detector tests

# 3. HTTPS tunnel (the iPhone mic and Cashfree webhooks need it)
ngrok http 8000                 # then set PUBLIC_URL in backend/.env and restart
```

Open `https://<tunnel>/` on a laptop (control room) and `https://<tunnel>/phone` on a judge's phone. For development with hot reload: `cd web && npm run dev` (uses `NEXT_PUBLIC_API=http://localhost:8000`).

### The iPhone app (AirPods gestures)

Needs a real iPhone and AirPods Pro / Max / 3rd gen or later (no simulator; head motion does not exist there).

```bash
brew install xcodegen
cd ios
# set DEVELOPMENT_TEAM in project.yml to your team ID
xcodegen generate
open TradeFlowEars.xcodeproj    # pick your iPhone, Run
```

or from the terminal:

```bash
xcodebuild -project TradeFlowEars.xcodeproj -scheme TradeFlowEars \
  -destination 'id=<your iPhone UDID>' -derivedDataPath build -allowProvisioningUpdates build
xcrun devicectl device install app --device <UDID> build/Build/Products/Debug-iphoneos/TradeFlowEars.app
```

First launch: on the iPhone, trust the developer (Settings → General → VPN & Device Management) and turn on Developer Mode if asked. Paste your tunnel URL; the app opens `/ears`. Long-press to change the URL. With a free Apple ID the install lasts 7 days; to share with other iPhones use TestFlight from a paid team.

In a plain browser the Ears screen still works: on-screen buttons, or keys **N** (nod), **S** (shake), **T** (tilt), **M** (stem).

## Demo script (≈3 min)

1. **Room** → *Use sample invoice*. ₹50,000 vs ₹32,000 quote; options A (short ₹12,000), B (₹6,000 left, needs the forwarder to agree), C (₹38,000 left, cargo held).
2. **Ring forwarder**. The judge answers on `/phone?as=forwarder`, explains detention, agrees to release on ₹32,000. Prism marks release agreed; option B moves to the top.
3. **Ears**: Selvam hears the brief in Tamil, nods. ₹32,000 is paid; the webhook confirms; stamp turns green.
4. **Driver voice note** asks to pay his brother's account. Secure ID says the account is real. That isn't proof, so TradeFlow phones the truck owner on his saved number (`/phone?as=truck_owner`). He denies. Refused, spoken in Tamil. No money moves.
5. **Ring buyer**. The agent confirms the date and sends a payment link during the call; pay ₹1; cash updates.
6. Open **Why?** on a payout, then **Traps**.

## Honest limits

- Balance is a demo figure (labelled *simulated*) until live Payouts keys are added.
- Real outbound phone calls need a Sarvam Voice Agents deployment (`SARVAM_AGENTS_*`); otherwise the judge talks to the agent in a browser.
- Head-gesture thresholds are tuned on synthetic data and a few real nods; the roll sign for "tilt right" may need flipping per wearer (`rollRightSign`).
- Port, customs and carrier events are simulated; no international payouts.
