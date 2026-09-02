# Review notes — options thesis plan manager foundation

Questions for review before merge:

1. Does one repeated upstream Signa state stay one event with repeat telemetry?
2. Can any Signa field change actionability or conviction? It must not.
3. Are targets still selected by the existing `find_targets` authority?
4. Does the adapter prevent wrong-kind levels and unverified gamma labels from becoming targets?
5. Is `HIGH_CONVICTION_CANDIDATE` impossible unless an explicit threshold is supplied?
6. Does the label remain display/evidence only, with no sizing or execution effect?
7. Does an ACTIVE thesis remain active across ordinary polling until explicit exit, invalidation, or expiry?
8. Are terminal theses immutable so a later setup starts a new history record?
9. Is a Signa-only change telemetry-only instead of a notification-worthy trade update?
10. Are all broker/network/alert/execution imports absent?

Known intentional gap: the current canonical market-context validator elsewhere in
`options_manager` still gives Signa more authority than the completed
retrospective audit supports. This foundation does not reuse that contaminated
aggregate status for conviction and does not wire itself into alerts yet. That
policy cleanup remains a required prerequisite before integration.
