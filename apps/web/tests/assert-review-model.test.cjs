const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');
const filename = path.resolve('apps/web/lib/assertReview.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename,'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const moduleInstance = new Module(filename, module); moduleInstance._compile(compiled, filename);
const { normalizeAssertReview, sortedAssertReviews, buildAssertReportView }=moduleInstance.exports;
const fixture=JSON.parse(fs.readFileSync('apps/api/tests/fixtures/assert-review-native-run.json','utf8'));
const reviews=fixture.conversations[0].judge_reviews;

test('native nodes group flagged, clear and irrelevant without invented confidence',()=>{
 const review=normalizeAssertReview(reviews[1]);
 assert.deepEqual(review.behaviors.map(n=>n.outcome),['Flagged','Clear','Not relevant','Unavailable']);
 assert.equal(review.behaviors[0].confidence,'high');
 assert.deepEqual(review.behaviors[0].references,['Judge turn 1','Judge turn 3']);
 assert.equal(review.behaviors[3].confidence,0);
 assert.equal(review.behaviors[3].references[0],'Unsupported judge-turn reference: 2');
});
test('ordinal match preserves type, missing/null applicability, zero and fractions',()=>{
 const dimensions=normalizeAssertReview(reviews[1]).dimensions;
 assert.equal(dimensions.find(d=>d.name==='string_ordinal').ordinalLabel,'String two recorded');
 assert.equal(dimensions.find(d=>d.name==='zero_dimension').value,0);
 assert.equal(dimensions.find(d=>d.name==='fractional_dimension').value,0.25);
 assert.equal(dimensions.find(d=>d.name==='nullable_dimension').outcome,'Unavailable');
 assert.equal(dimensions.find(d=>d.name==='not_applicable_dimension').outcome,'Not applicable');
});
test('timestamp ordering is stable and legacy optional fields do not break details',()=>{
 assert.equal(sortedAssertReviews(reviews)[0].id,'judge-review-native-newest');
 const legacy=structuredClone(reviews[1]);legacy.created_at='bad';legacy.status='oops';legacy.judge_result.provenance.node_judgments=[null,{},'bad'];
 const view=normalizeAssertReview(legacy);assert.equal(view.dateLabel,'Recorded time unavailable');
 assert.equal(view.statusLabel,'Status unknown');assert.equal(view.behaviors[0].outcome,'Unavailable');
});
test('missing/invalid dates and equal timestamps prefer newest appended review',()=>{
 for (const dates of [[undefined, undefined], ['invalid', 'invalid'], ['2026-10-08T12:00:00Z', '2026-10-08T12:00:00Z']]) {
   const older=structuredClone(reviews[2]);older.review_id='appended-first';older.created_at=dates[0];
   const newest=structuredClone(reviews[1]);newest.review_id='appended-last';newest.created_at=dates[1];
   assert.deepEqual(sortedAssertReviews([older,newest]).map(review=>review.id),['appended-last','appended-first']);
 }
});
test('technical metadata allowlist never exposes paths/secret maps/raw output',()=>{
 const raw=structuredClone(reviews[1]);raw.model='/opt/private/internal-model';raw.judge_result.raw_output='NEVER-EXPOSE';raw.judge_result.provenance.artifacts={path:'/workspace/hidden'};
 const view=normalizeAssertReview(raw);assert.equal(view.model,null);assert.ok(!JSON.stringify(view.technical).includes('NEVER-EXPOSE'));
 assert.ok(!JSON.stringify(view.technical).includes('/workspace'));
});

test('metadata model rejects relative artifact/traversal and Windows drive paths',()=>{
 for (const model of ['../artifacts/score.json','artifacts/assert/runs.json','C:/Work/InternalModel','client_secret:NEVER-EXPOSE','aws_secret_access_key:NEVER-EXPOSE']) {
   const raw=structuredClone(reviews[1]);raw.model=model;assert.equal(normalizeAssertReview(raw).model,null);
 }
 const raw=structuredClone(reviews[1]);raw.model='openai/gpt-4.1-mini';assert.equal(normalizeAssertReview(raw).model,raw.model);
});

function evidenceReview() {
 const raw=structuredClone(reviews[1]);
 raw.latency_ms=0;raw.judge_result.agrees=false;raw.judge_result.next_action='Attach the booking receipt.';
 Object.assign(raw.judge_result.provenance,{
  transcript_adapter_version:'cae-assert-evidence-v4',evidence_details_status:'complete',
  evidence_coverage:{transcript:true,tool_trace:false,final_state:false},
  evidence_messages:[
   {id:'event:0',role:'user',text:'Afternoon instead of morning.'},
   // IDs are original ASSERT event offsets, not a contiguous message counter.
   {id:'event:3',role:'assistant',text:'The corrected afternoon booking is confirmed.'},
  ],
  evidence_references:[{index:0,description:'Booking claim',parts:[{
   quote:'The corrected afternoon booking is confirmed.',status:'matched',message_id:'event:3',detail:'Exact quote',
  }]}],
 });
 return raw;
}

test('structured evidence preserves exact saved event IDs and explicit false/zero values',()=>{
 const view=normalizeAssertReview(evidenceReview());
 assert.deepEqual(view.evidenceMessages.map(message=>[message.id,message.speakerLabel]),[['event:0','Caller'],['event:3','Agent']]);
 assert.equal(view.evidenceReferences[0].index,0);
 assert.equal(view.evidenceReferences[0].parts[0].status,'matched');
 assert.equal(view.evidenceReferences[0].parts[0].messageId,'event:3');
 assert.deepEqual(view.coverage,{transcript:true,toolTrace:false,finalState:false});
 assert.equal(view.agrees,false);assert.equal(view.latencyMs,0);assert.equal(view.legacyEvidence,false);
 assert.equal(view.nextAction,'Attach the booking receipt.');assert.equal(view.evidenceDetailsStatus,'complete');
});

test('live and saved ASSERT responses share the evidence model without requiring fake saved metadata',()=>{
 const saved=evidenceReview(),live=structuredClone(saved);live.status='ready';delete live.review_id;delete live.created_at;
 assert.equal(normalizeAssertReview(live),null);
 const view=buildAssertReportView(live),savedView=normalizeAssertReview(saved);
 assert.equal(view.statusLabel,'Review complete');assert.equal(view.dateLabel,'Recorded time unavailable');
 assert.deepEqual(view.evidenceReferences,savedView.evidenceReferences);
 assert.deepEqual(view.evidenceMessages,savedView.evidenceMessages);
 live.review_id='judge-review-saved-now';assert.equal(buildAssertReportView(live).statusLabel,'Pending confirmation');
 assert.equal(buildAssertReportView({status:'ready',provider:'unrelated'}),null);
});

test('native ambiguous and unresolved references are never upgraded to a jump link',()=>{
 for(const status of ['ambiguous','unresolved']) {
  const raw=evidenceReview();raw.judge_result.provenance.evidence_references[0].parts[0].status=status;
  const part=normalizeAssertReview(raw).evidenceReferences[0].parts[0];
  assert.equal(part.status,status);assert.equal(part.messageId,null);
 }
});

test('matched reference must identify the message containing the exact case-sensitive quote',()=>{
 for(const change of [part=>part.message_id='event:0',part=>part.message_id='event:99',part=>part.quote=part.quote.toUpperCase(),part=>part.quote='']) {
  const raw=evidenceReview();change(raw.judge_result.provenance.evidence_references[0].parts[0]);
  const part=normalizeAssertReview(raw).evidenceReferences[0].parts[0];
  assert.equal(part.status,'unresolved');assert.equal(part.messageId,null);
 }
});

test('quotes repeated across messages or within one message remain ambiguous',()=>{
 for(const sameMessage of [false,true]) {
  const raw=evidenceReview(),p=raw.judge_result.provenance,quote=p.evidence_references[0].parts[0].quote;
  if(sameMessage)p.evidence_messages[1].text+=' '+quote;
  else p.evidence_messages.push({id:'event:5',role:'assistant',text:quote});
  const part=normalizeAssertReview(raw).evidenceReferences[0].parts[0];
  assert.equal(part.status,'ambiguous');assert.equal(part.messageId,null);
 }
});

test('malicious, noncanonical, missing and duplicated message IDs cannot create anchors',()=>{
 for(const id of ['../event:3','event:03','event:-1','event:3#unsafe','javascript:alert(1)','event:9007199254740992',undefined]) {
  const raw=evidenceReview(),p=raw.judge_result.provenance;p.evidence_messages[1].id=id;p.evidence_references[0].parts[0].message_id=id;
  const view=normalizeAssertReview(raw);assert.equal(view.evidenceReferences[0].parts[0].messageId,null);
  assert.equal(view.evidenceSnapshotAvailable,false);assert.equal(view.evidenceDetailsStatus,'invalid');
 }
 const raw=evidenceReview(),p=raw.judge_result.provenance;p.evidence_messages.push({...p.evidence_messages[1]});
 assert.equal(normalizeAssertReview(raw).evidenceReferences[0].parts[0].status,'unresolved');
});

test('malformed extra snapshot rows do not hide duplicate quotes and falsely establish uniqueness',()=>{
 const raw=evidenceReview(),p=raw.judge_result.provenance;
 p.evidence_messages.push({id:'unsafe-id',role:'assistant',text:p.evidence_messages[1].text});
 const view=normalizeAssertReview(raw);assert.equal(view.evidenceSnapshotAvailable,false);
 assert.equal(view.evidenceReferences[0].parts[0].messageId,null);
});

test('missing snapshots do not borrow a transcript or resolve plausible-looking reference IDs',()=>{
 const raw=evidenceReview();delete raw.judge_result.provenance.evidence_messages;
 raw.transcript='Agent: The corrected afternoon booking is confirmed.';
 const view=normalizeAssertReview(raw);assert.equal(view.legacyEvidence,true);
 assert.equal(view.evidenceMessages.length,0);assert.equal(view.evidenceReferences[0].parts[0].status,'unresolved');
 assert.equal(view.evidenceDetailsStatus,'legacy');
});

test('an unversioned snapshot remains historical and cannot authorize a transcript jump',()=>{
 const raw=evidenceReview();delete raw.judge_result.provenance.transcript_adapter_version;
 const view=normalizeAssertReview(raw);assert.equal(view.legacyEvidence,true);
 assert.equal(view.evidenceSnapshotAvailable,false);assert.equal(view.evidenceReferences[0].parts[0].messageId,null);
});

test('snapshot limits are explicit and not misrepresented as historical metadata',()=>{
 const raw=evidenceReview(),p=raw.judge_result.provenance;
 p.evidence_details_status='limited';p.evidence_details_message='Evidence details exceed the display budget.';
 delete p.evidence_messages;delete p.evidence_references;
 const view=normalizeAssertReview(raw);assert.equal(view.legacyEvidence,false);
 assert.equal(view.evidenceSnapshotAvailable,false);assert.equal(view.evidenceDetailsStatus,'limited');
 assert.equal(view.evidenceDetailsMessage,p.evidence_details_message);
});

test('unknown evidence coverage is not converted into missing telemetry or inferred from transcript length',()=>{
 const raw=evidenceReview(),p=raw.judge_result.provenance;
 p.evidence_coverage={transcript:'true',tool_trace:0,final_state:null};
 raw.latency_ms=-1;raw.judge_result.agrees='false';
 const view=normalizeAssertReview(raw);assert.deepEqual(view.coverage,{transcript:null,toolTrace:null,finalState:null});
 assert.equal(view.latencyMs,null);assert.equal(view.agrees,null);
});

test('unknown roles are labeled unavailable rather than invented and cannot establish verified anchors',()=>{
 const raw=evidenceReview();raw.judge_result.provenance.evidence_messages[1].role='made-up';
 const view=normalizeAssertReview(raw);assert.equal(view.evidenceMessages[1].speakerLabel,'Unknown speaker');
 assert.equal(view.evidenceReferences[0].parts[0].messageId,null);
});

test('legacy truncated JSON citations show readable recorded fields without trusting claimed anchors',()=>{
 const raw=evidenceReview();delete raw.judge_result.provenance.evidence_references;delete raw.judge_result.provenance.evidence_messages;
 raw.evidence_citations=['{"description":"Claims booking confirmed", "parts":[{"anchor":{"exact":"The booking is confirmed."}, "message_id":"event:3", "resolution":{"detail":"Still incom\n[... truncated, 1197 chars total ...]'];
 const view=normalizeAssertReview(raw),reference=view.evidenceReferences[0];
 assert.equal(reference.description,'Claims booking confirmed');assert.equal(reference.parts[0].quote,'The booking is confirmed.');
 assert.equal(reference.parts[0].status,'unresolved');assert.equal(reference.parts[0].messageId,null);
 assert.deepEqual(view.citations,raw.evidence_citations);assert.equal(view.legacyEvidence,true);
});

test('legacy complete JSON and plain-text references have readable fallbacks, never fabricated jumps',()=>{
 const raw=evidenceReview();delete raw.judge_result.provenance.evidence_references;
 raw.evidence_citations=[JSON.stringify({description:'A recorded quote',parts:[{quoted_text:'He said "confirmed".',message_id:'event:3'}]}),'No tool receipt was supplied.','{"description":"broken'];
 const references=normalizeAssertReview(raw).evidenceReferences;
 assert.equal(references[0].parts[0].quote,'He said "confirmed".');assert.equal(references[1].parts[0].quote,'No tool receipt was supplied.');
 assert.equal(references[2].description,'Historical evidence reference');
 assert.ok(references.every(reference=>reference.parts.every(part=>part.messageId===null)));
});

test('evidence metadata never expands the technical allowlist or supplies freshness/apply authority',()=>{
 const raw=evidenceReview();raw.judge_output='NEVER-EXPOSE';raw.prompt_preview='NEVER-EXPOSE';raw.freshness={status:'current'};
 raw.judge_result.provenance.configuration={api_key:'NEVER-EXPOSE'};raw.judge_result.provenance.evidence_details_message='Details are limited.';
 const view=normalizeAssertReview(raw);assert.ok(!JSON.stringify(view.technical).includes('NEVER-EXPOSE'));
 assert.equal(view.freshness,undefined);assert.equal(view.canApply,undefined);
 assert.equal(view.status,'pending_confirmation');
});
