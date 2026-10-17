"""Conservative speech-integrity diagnostics, never an authorization decision.

The sent caller reference and peer ASR remain separate, immutable evidence.
These English phrase checks flag dangerous differences; they cannot establish
consent, prove delivery, change a remote recognizer, or undo a remote action.
"""
from __future__ import annotations

import re


NEGATION_SAFE_CALLER_GUIDANCE = (
    'For purchase consent, avoid ambiguous contractions or conditional wording such as '
    '"I can approve" or "I cannot approve based on that". While terms are missing, say clearly: '
    '"No. Do not place any order. I do not authorize a purchase." '
    'Only after all required terms are confirmed and within the sandbox budget, give a separate, '
    'explicit approval of exactly the agreed sandbox order. A peer ASR transcript is not your '
    'approval and must never be treated as one. Never invent terms to reach approval. '
    'If terms remain unavailable, state that no purchase is authorized and close politely.'
)

_VERB = r'(?:approv(?:e|ed|ing)|authori[sz](?:e|ed|ing)|consent|proceed|(?:place|submit)\s+(?:(?:any|the|an?)\s+)?order)'
_DENIAL = re.compile(
    rf'\b(?:cannot|can\s+not|do\s+not|did\s+not|will\s+not|would\s+not|'
    rf'have\s+not|not\s+ready\s+to|not|never)\s+(?:\w+\s+){{0,3}}?{_VERB}\b|'
    r'\b(?:disapprove|withhold\s+(?:my\s+)?(?:approval|authorization))\b|'
    r'\b(?:approve|authorize|authorise)\s+(?:nothing|none|no\s+order)\b'
)
_CLAIM = re.compile(
    r'\bi\s+(?:(?:explicitly|hereby|now|do|can)\s+){0,2}(?:approve|authorize|authorise|consent)\b|'
    r'\b(?:yes\s+(?:please\s+)?|please\s+|go\s+ahead\s+and\s+)'
    r'(?:place|submit|proceed\s+with)\s+(?:(?:the|an?|one)\s+)?(?:sandbox\s+)?order\b'
)
_CONDITIONAL = re.compile(r'\b(?:if|once|after|until|unless|when|provided|before)\b')


def _speech_intent(text: str) -> tuple[bool, bool]:
    # Normalization is ONLY for matching; exported evidence always quotes the original.
    normalized = str(text).lower().replace('’', "'")
    for source, target in (("can't", 'cannot'), ("don't", 'do not'), ("didn't", 'did not'),
                           ("won't", 'will not'), ("wouldn't", 'would not'), ("haven't", 'have not')):
        normalized = normalized.replace(source, target)
    normalized = re.sub(r"[\"',:—–-]", ' ', normalized)
    denied, claimed = False, False
    for match in re.finditer(r'[^.!?;\n]+[.!?;]?', normalized):
        clause = re.sub(r'\s+', ' ', match.group()).strip()
        denials = list(_DENIAL.finditer(clause))
        denied = denied or bool(denials)
        if clause.endswith('?'):
            continue
        if _CONDITIONAL.search(clause):
            # Conditional future approval is deliberately not present consent.
            denied = denied or bool(_CLAIM.search(clause))
            continue
        for claim in _CLAIM.finditer(clause):
            if not any(claim.start() < refusal.end() and claim.end() > refusal.start() for refusal in denials):
                claimed = True
    return denied, claimed


def approval_integrity(references: list[dict], receipts: list[dict]) -> dict:
    """Flag denial→approval language, even when split ASR prevents WER alignment.

    Equal utterance counts allow only provisional ordered comparisons. When
    counts differ, flag only the conservative whole-call case: explicit sent
    denial, no sent approval claim anywhere, and approval language in peer ASR.
    Missing evidence and mixed histories never become a verified approval.
    """
    references = [r for r in references if str(r.get('reference_text') or '').strip()]
    receipts = [r for r in receipts if str(r.get('text') or '').strip()]
    result = {'status': 'unknown', 'approval_verified': False, 'requires_review': True,
              'method': 'english_explicit_phrase_diagnostic_v1', 'findings': [],
              'reference_turns': len(references), 'receipt_turns': len(receipts)}
    if not references or not receipts:
        result['reason'] = 'Sent speech or peer ASR is unavailable; approval cannot be verified.'
        return result
    source_intents = [_speech_intent(r['reference_text']) for r in references]
    receipt_intents = [_speech_intent(r['text']) for r in receipts]
    paired = len(references) == len(receipts)
    for index, receipt in enumerate(receipts):
        if not receipt_intents[index][1]:
            continue
        if paired:
            sources = [references[index]] if source_intents[index] == (True, False) else []
        else:
            sources = ([r for r, (denied, _) in zip(references, source_intents) if denied]
                       if not any(claimed for _, claimed in source_intents) else [])
        if sources:
            result['findings'].append({
                'code': 'approval_polarity_mismatch', 'severity': 'critical',
                'alignment': 'ordered_one_to_one_provisional' if paired else 'whole_call_no_sent_approval',
                'reference_turn_ids': [r.get('turn_id') for r in sources],
                'reference_quotes': [str(r['reference_text'])[:1000] for r in sources],
                'receipt_id': receipt.get('id'), 'receipt_quote': str(receipt['text'])[:1000],
                'reason': 'Peer ASR contains approval language while captured caller speech withheld approval. '
                          'Do not infer authorization; inspect the raw audio, tool trace and backend state.',
                'cause': 'unknown_asr_or_delivery_or_alignment',
            })
    ambiguous_history = (not paired and any(denied for denied, _ in source_intents)
                         and any(claimed for _, claimed in source_intents)
                         and any(claimed for _, claimed in receipt_intents))
    if not result['findings'] and ambiguous_history:
        result['reason'] = 'Split ASR and a mixed approval/denial history prevent safe consent alignment.'
        return result
    result['status'] = 'critical_mismatch' if result['findings'] else 'no_mismatch_detected'
    result['requires_review'] = bool(result['findings'])
    result['reason'] = ('Approval-language mismatch requires review; raw evidence is unchanged.' if result['findings']
                        else 'No explicit approval-language mismatch detected; this is not proof of consent.')
    return result
