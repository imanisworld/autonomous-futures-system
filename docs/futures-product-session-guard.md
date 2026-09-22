# Futures product-session guard (prep, not wired)

`context/futures_product_session.py` answers one question: is the Globex
session for a CME equity-index product open at a given instant?

It returns a `ProductSessionStatus` (`instrument`, `status`, `calendar`,
`exchange_time`, `detail`, `is_open`). Only `market_open` counts as open.

| status | when |
|---|---|
| `market_open` | normal Globex hours |
| `maintenance_halt` | 17:00–18:00 ET, Mon–Thu |
| `weekend_closed` | Fri 17:00 ET → Sun 18:00 ET |
| `holiday_closed` | 00:00–18:00 ET on a CME non-trade date (`context/cme_trading_day.py`) |
| `special_session_closed` | from 13:00 ET on the day after Thanksgiving, Dec 24 and Jul 3 |
| `unsupported_instrument` | anything other than `MNQ` / `MES` |
| `calendar_unknown_fail_closed` | naive or non-datetime input, year outside `SUPPORTED_YEARS`, Good Friday, the evening before a holiday |

## Fail-closed choices

- The repo holiday data carries dates, not hours. Holidays block the whole
  day to 18:00 ET, although CME runs an abbreviated morning on some of them.
- Globex reopens the evening before some holidays but not others. The dates
  cannot tell which, so that evening is unknown and closed.
- Good Friday is not in the repo calendar. It is unknown and closed.
- The early close uses 13:00 ET. CME's is 12:15 CT (13:15 ET).
- The CME 16:15–16:30 ET equity halt that `product_session_active` encodes is
  not modeled here. Confirm it against the CME before a wiring PR relies on it.

## Not product-calendar proof

`futures_session_active` (feed-health label, no holidays, fails open) and
`alert_ranker.session_calendar` (NYSE RTH) are not used as inputs. The tests
pin cases where they disagree with this guard.

## Scope

Nothing imports this module at runtime; a test enforces that. Any wiring
(alerts, Discord suppression, collectors) is a separate PR under the change
rule.
