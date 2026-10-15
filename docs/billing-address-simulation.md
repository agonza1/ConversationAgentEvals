# Built-in billing address simulation

The built-in generalist **voice** target enables a per-call billing sandbox for
`call-center-voice-ai/billing-address-change`. Other scenarios and external
targets are unchanged. No production account, order, payment or vendor system is
connected. The synthetic customer brief is given only to the caller-side tester.

The selected small model explicitly requests four allowlisted operations:
`verify_customer`, `prepare_address`, `commit_address`, and `read_address`.
The application executes those requests against in-memory state. Ordinary speech
does not generate tool evidence. A bounded JSON control envelope works with the
existing provider bridge; it is not dependent on provider-native function calling.
Only final speech is sent to TTS. There are at most five tool calls per turn,
thirty per session, one JSON correction per turn, and no new services or models.

An outcome is **Verified** when the customer supplied two correct fictional
identifiers, the supplied address was read back in full, a later caller turn
unambiguously confirmed it, the mock update committed, the stored address was
read back through the backend tool and matched the requested destination, and the
agent explained the next-invoice effect. **Simulation** is a separate context
label, not a lesser verification status. Missing evidence remains unverified;
failed run/evaluation verdicts take precedence. Conversation/policy evaluation
and ASSERT review remain separate: a backend receipt cannot prove every
natural-language behavior or policy requirement.

The authenticated worker returns a bounded event log. CAE correlates its run and
session identity and replays it before producing operational verification. Tools,
results, final state, the simulation marker and replay log are retained in the
execution evidence/vCon export. Imported success flags and historical analysis
cannot opt into this internal verification path. This is local-runtime evidence,
not independent remote attestation or proof of a production account change.

Address/identifier normalization intentionally covers this small fictional
fixture, not a general postal validator. Ambiguous ASR or caller confirmation
must not silently pass. The account resets for each session.
