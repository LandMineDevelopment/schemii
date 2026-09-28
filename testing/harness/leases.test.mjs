import test from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtemp, rm, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { reserveAccounts, releaseAccounts, withAvailableAccounts } from './leases.mjs';

test('fixture cleanup holds the reservation lock and refuses an account owned by a run', async () => {
  const root=await mkdtemp(join(tmpdir(),'schemii-qa-lease-'));
  const runDir=join(root,'run');
  await mkdir(runDir);
  execFileSync('git',['init','--quiet',root]);
  const account='qa_report_author_001';
  const runId='qa-test-reservation';
  try {
    await reserveAccounts({runId,runDir,accounts:[account],root});
    let called=false;
    await assert.rejects(withAvailableAccounts([account],async()=>{called=true;},{root}),
      error=>error.code==='QA_ACCOUNT_BUSY');
    assert.equal(called,false);

    await releaseAccounts({runId,accounts:[account],root});
    assert.equal(await withAvailableAccounts([account],async()=>true,{root}),true);
  } finally {
    await releaseAccounts({runId,accounts:[account],root});
    await rm(root,{recursive:true,force:true});
  }
});
