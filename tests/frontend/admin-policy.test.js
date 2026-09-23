import test from 'node:test';
import assert from 'node:assert/strict';
import { policyChanges, policyRequest, scopeIdentity } from '../../src/schemii/common/web/assets/admin-policy.js';
const policy = { path: 'shared-codex', product: 'schemii', connectionOwnerId: 'owner', connectionId: 'east', modelId: 'gpt-6-luna', reasoningEffort: 'default' };
test('policy diffs preserve sibling model, reasoning and database access', () => {
  const sibling = {...policy,reasoningEffort:'high'};
  const otherDb = {...policy,connectionId:'west'};
  assert.deepEqual(policyChanges([policy,sibling,otherDb],[sibling,otherDb]), {removals:[policy],additions:[]});
  assert.notEqual(scopeIdentity(policy),scopeIdentity({...policy,connectionOwnerId:'another-owner'}));
});
test('direct model additions append and removals target one exact policy', () => {
  assert.equal(policyRequest(policy,'user','user').options.method,'POST');
  const removal = policyRequest({...policy, active:true, revision:4},'user','user',true);
  assert.equal(removal.url,'/api/v1/admin/ai/shared-codex/model-grants');
  assert.deepEqual(removal.options.body,{userId:'user',product:'schemii',connectionOwnerId:'owner',connectionId:'east',modelId:'gpt-6-luna',reasoningEffort:'default'});
});
test('retry diffs omit already saved policies after partial failure', () => {
  const second = {...policy,reasoningEffort:'high'};
  const desired = [policy,second];
  const persisted = [];
  assert.equal(policyChanges(persisted,desired).additions.length,2);
  persisted.push(policy); // first API write succeeded, second failed
  assert.deepEqual(policyChanges(persisted,desired),{removals:[],additions:[second]});
});
test('role and Zen policy requests retain separate ownership and routes', () => {
  assert.equal(policyRequest(policy,'role','reporting').url,'/api/v1/admin/ai/shared-codex/role-grants');
  assert.equal(policyRequest(policy,'role','reporting').options.body.roleId,'reporting');
  const zen = {path:'zen',product:'schemer',connectionOwnerId:'owner',connectionId:'east'};
  assert.equal(policyRequest(zen,'user','viewer').options.method,'PUT');
  assert.equal(policyRequest(zen,'user','viewer',true).url,'/api/v1/admin/ai/zen/grants');
});
