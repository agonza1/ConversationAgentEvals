const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const Module = require('node:module');
const filename = path.resolve('apps/web/lib/assertReview.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename,'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const moduleInstance = new Module(filename, module); moduleInstance._compile(compiled, filename);
const { normalizeAssertReview, sortedAssertReviews }=moduleInstance.exports;
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
