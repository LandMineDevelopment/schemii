import test from 'node:test';
import assert from 'node:assert/strict';
import { declareCoverage, coverageIDs, validCoverage, captureSupports, inspectionCoverage, inspectionSupports, assertCoverageActivity } from './evidence.mjs';

function fixture() {
  const context={generation:1,agent:'agent-1',source:'a'.repeat(64),product:'schemii',expectedState:'product',url:'https://localhost:8001/?workspace=owned',viewport:{width:1280,height:800},resources:{workspace:'owned'},phase:'scenario'};
  const coverage=declareCoverage([{...context,scenario:'case-a',id:'attempt-a'},{...context,scenario:'case-b',id:'attempt-b'}],{lane:'lane-1',session:'session-1',activity:{schema:1,epoch:4,pending:0},at:'2026-11-11T12:00:00Z'});
  const capture={...context,scenario:'case-a',scenarioAttempt:'attempt-a',coverage,nativeSession:'session-1',sha256:'b'.repeat(64)};
  return {coverage,capture,context};
}
test('literal bounded declaration and image inspection cover only explicit case attempts',()=>{
  const {coverage,capture}=fixture();assert.equal(validCoverage(coverage),true);
  assert.equal(captureSupports(capture,'case-b','attempt-b'),true);
  for(const [id,attempt] of [['case-c','attempt-b'],['case-b','attempt-old'],['case-a','attempt-b']])assert.equal(captureSupports(capture,id,attempt),false);
  assert.throws(()=>inspectionCoverage(capture),/explicitly name/);
  const inspection={sha256:capture.sha256,...inspectionCoverage(capture,['case-b'])};
  assert.equal(inspectionSupports(capture,inspection,'case-b','attempt-b'),true);
  assert.equal(inspectionSupports(capture,inspection,'case-a','attempt-a'),false);
  for(const ids of [[],['case-b','case-b'],['case-c'],[['case-a']],['case-a',{}]])assert.throws(()=>inspectionCoverage(capture,ids),/explicitly name/);
});
test('changed declaration context or activity cannot retain shared eligibility',()=>{
  const {coverage,capture}=fixture();
  assertCoverageActivity(coverage,'session-1',{schema:1,epoch:4,pending:0});
  for(const [session,activity] of [['session-2',{schema:1,epoch:4,pending:0}],['session-1',{schema:1,epoch:5,pending:0}],['session-1',{schema:1,epoch:4,pending:1}],['session-1',undefined]])assert.throws(()=>assertCoverageActivity(coverage,session,activity));
  for(const key of ['source','generation','agent','product','expectedState','url','viewport','resources','phase']) {
    const changed=structuredClone(capture);changed[key]='changed';assert.equal(captureSupports(changed,'case-b','attempt-b'),false,key);
  }
  const wrongSession=structuredClone(capture);wrongSession.browserSession='session-1';wrongSession.nativeSession='peer-session';
  assert.equal(captureSupports(wrongSession,'case-b','attempt-b'),false);
  assert.equal(captureSupports(capture,'case-b','attempt-b','peer-lane'),false);
  const changed=structuredClone(capture);changed.coverage.declaration.members.push({scenario:'case-c',attempt:'attempt-c'});
  assert.equal(captureSupports(changed,'case-c','attempt-c'),false);
});
test('duplicate nested excessive unknown shape and mismatched state declarations reject',()=>{
  for(const ids of [['case-a'],['case-a','case-a'],['case-b','case-a'],['case-a',{}],['case-a',['case-b']],['a','b','c','d','e']])assert.throws(()=>coverageIDs(ids,'case-a'));
  const {context}=fixture();
  for(const key of ['generation','agent','source','product','expectedState','url','viewport','resources','phase']) {
    assert.throws(()=>declareCoverage([{...context,scenario:'case-a',id:'attempt-a'},{...context,[key]:'changed',scenario:'case-b',id:'attempt-b'}],{lane:'lane-1',session:'session-1',activity:{schema:1,epoch:0,pending:0}}),/exactly/);
  }
});
test('historical single-case captures stay readable and cannot gain retroactive coverage',()=>{
  const capture={scenario:'case-a',scenarioAttempt:'attempt-a',sha256:'b'.repeat(64)};
  assert.equal(captureSupports(capture,'case-a','attempt-a'),true);
  assert.equal(captureSupports(capture,'case-b','attempt-a'),false);
  assert.deepEqual(inspectionCoverage(capture),{});
  assert.throws(()=>inspectionCoverage(capture,['case-a','case-b']),/after capture/);
  assert.equal(inspectionSupports(capture,{sha256:capture.sha256},'case-a','attempt-a'),true);
});
