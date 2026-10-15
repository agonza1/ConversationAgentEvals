# Caller speech and scenario directions

Quick cases keep `simulated_user_prompt` (persona and test directions), goal,
required/forbidden behaviors, and `spoken_opener` separate. The quick-case form
requires a natural opening line; the API accepts it as an optional field for
existing clients. It is stored and returned without replacing the persona.

Fixed `caller_steps` and controlled audio fixtures remain exact. An explicit
`spoken_opener` takes precedence over sample text. Built-in caller-facing sample
openers remain available. Legacy direct first-person requests can still be used
as speech; descriptive persona/instruction prompts receive a neutral greeting
instead of being read aloud or sent to an LLM to invent an opening. Supply an
explicit opener to exercise a particular request. No existing saved records are
rewritten, and a one-exchange smoke test still requires no tester LLM.

Example information-only Steven fixture:

- Persona/directions: synthetic caller requests electronics information; do not
  order, create customer records, submit payment, or alter real accounts.
- Spoken opening: “Hi, can you tell me what wireless headphones you have? I only
  need information today.”
- Outcome checks: answer supported by catalog results; transaction side effects
  require independently captured backend evidence, otherwise remain unknown.

LiveKit may report one final utterance through both its legacy transcription
event and modern text stream using different segment IDs. Display text pairs
exact matching finals across the two sources only within the same observation
turn and a one-second receipt window. Each segment accepts at most one receipt
per source; later repeated utterances and corrected finals remain distinct.
Native partial/final events are retained unchanged, with the redundant final's
canonical segment reference added. This is observer-side deduplication, not a
rewrite of Waylo's authoritative final transcript.
