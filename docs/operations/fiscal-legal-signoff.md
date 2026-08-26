# Fiscal and legal sign-off

Software tests cannot approve legal or contractual assumptions. Production stays blocked until
an authorized reviewer records evidence for every key in `/api/v1/admin/signoffs/`:

- entity and tax regime;
- wallet/top-up fiscalization under the selected scheme;
- payment and refund receipt flow;
- privacy notice, retention and data-flow map;
- commercial/API terms for every enabled AI provider;
- published offer, privacy policy and refund rules;
- enforced MFA for administrative identities.

An `approved` sign-off requires a durable evidence reference. Credentials, passport data and
full contracts must not be stored in the notes field. Enabling live payments while fiscalization
is `disabled` remains blocked by application logic and the strict prelaunch command.
