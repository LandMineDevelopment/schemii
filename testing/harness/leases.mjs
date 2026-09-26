import { mkdir, open, readFile, unlink, access, readdir, link } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { readFileSync, unlinkSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { deploymentLockPath } from './deployment.mjs';
import { root as defaultRoot } from './store.mjs';

function names(accounts) {
  const result=accounts.map(account=>typeof account==='string'?account:account.username);
  if(result.some(name=>! /^[a-zA-Z0-9_.@-]{1,64}$/.test(name||''))||new Set(result.map(name=>name?.toLowerCase())).size!==result.length)throw new Error('Account leases require unique valid usernames.');
  return result.map(name=>name.toLowerCase());
}
function directory(root){return join(dirname(deploymentLockPath(root)),'qa-account-leases');}
function file(dir,username){return join(dir,`${username}.json`);}

// flock belongs to this open file description, so the parent retains it after
// the helper exits. Async acquisition also permits concurrent callers in one
// Node process without blocking the holder's event loop.
export async function withFileLock(path, task) {
  const handle=await open(path,'a',0o600);
  try {
    await new Promise((resolve,reject)=>{
      const child=spawn('flock',['--exclusive','3'],{stdio:['ignore','ignore','pipe',handle.fd]});
      child.once('error',reject);
      child.once('close',code=>code===0?resolve():reject(new Error(`Cannot acquire QA lifecycle lock (flock exit ${code}).`)));
    });
    return await task();
  }finally {await handle.close();}
}
function withReservations(root, task) {
  return withFileLock(join(dirname(deploymentLockPath(root)),'qa-account-reservations.lock'),task);
}

// Publish a complete, durable record without ever exposing a partial JSON file.
// The hard link is exclusive: concurrent coordinators cannot replace its owner.
async function publishReservation(dir, record) {
  const temporary=join(dir, `.pending-${randomUUID()}.tmp`);
  const handle=await open(temporary,'wx',0o600);
  try {
    try { await handle.writeFile(`${JSON.stringify(record)}\n`);await handle.sync(); }
    finally { await handle.close(); }
    try { await link(temporary,file(dir,record.username));return true; }
    catch(error) {
      if(error.code!=='EEXIST')throw error;
      let existing;
      try{existing=JSON.parse(await readFile(file(dir,record.username),'utf8'));}catch{}
      if(existing?.runId===record.runId&&existing?.runDir===record.runDir)return false;
      const busy=new Error(`Account ${record.username} is reserved by another run; choose an available account or clean up its stopped owner.`);
      busy.code='QA_ACCOUNT_BUSY';throw busy;
    }
  }finally { await unlink(temporary); }
}

// A crashed controller deliberately leaves its reservations behind. Only a
// coordinator that has verified its recorded processes are stopped may release.
async function reserveAccountsUnlocked({runId,runDir,accounts,root=defaultRoot}) {
  if(!runId||!runDir)throw new Error('Account reservation requires run identity.');
  const dir=directory(root), created=[];
  await mkdir(dir,{recursive:true,mode:0o700});
  try {
    for(const username of names(accounts)) {
      if(await publishReservation(dir,{runId,runDir:resolve(runDir),username}))created.push(username);
    }
  }catch(error){
    await releaseAccountsUnlocked({runId,accounts:created,root});
    throw error;
  }
  return {runId,accounts:names(accounts),created};
}

async function releaseAccountsUnlocked({runId,accounts,root=defaultRoot}) {
  const dir=directory(root);
  if(accounts===undefined) {
    let files;
    try{files=await readdir(dir);}catch(error){if(error.code==='ENOENT')return;throw error;}
    accounts=[];
    for(const name of files) {
      if(!name.endsWith('.json'))continue;
      let record;
      try{record=JSON.parse(await readFile(join(dir,name),'utf8'));}catch(error){if(error.code==='ENOENT')continue;throw error;}
      if(record.runId===runId)accounts.push(record.username);
    }
  }
  for(const username of names(accounts)) {
    let record;
    try{record=JSON.parse(readFileSync(file(dir,username),'utf8'));}
    catch(error){if(error.code==='ENOENT')continue;throw error;}
    if(record.runId===runId)unlinkSync(file(dir,username));
  }
}

export async function availableAccounts(accounts,{root=defaultRoot}={}) {
  const dir=directory(root), usernames=names(accounts), result=[];
  for(let i=0;i<usernames.length;i++) {
    try{await access(file(dir,usernames[i]));}
    catch(error){if(error.code!=='ENOENT')throw error;result.push(accounts[i]);}
  }
  return result;
}

// Selection itself acquires each reservation; an availability listing is never
// treated as permission to use an account. Partial batches roll back on failure.
async function reserveAvailableUnlocked({runId,runDir,candidates,count,root=defaultRoot}) {
  names(candidates);
  if(!Number.isInteger(count)||count<1)throw new Error('Reservation count must be a positive integer.');
  const selected=[],created=[];
  try {
    for(const candidate of candidates) {
      try {
        const result=await reserveAccountsUnlocked({runId,runDir,accounts:[candidate],root});
        selected.push(candidate);created.push(...result.created);
        if(selected.length===count)return selected;
      }catch(error){if(error.code!=='QA_ACCOUNT_BUSY')throw error;}
    }
    throw new Error(`Only ${selected.length} of ${count} requested persona accounts are available. Finish another run or provision a larger pool.`);
  }catch(error){await releaseAccountsUnlocked({runId,accounts:created,root});throw error;}
}

export function reserveAccounts(options) {
  return withReservations(options.root || defaultRoot,()=>reserveAccountsUnlocked(options));
}
export function reserveAvailable(options) {
  return withReservations(options.root || defaultRoot,()=>reserveAvailableUnlocked(options));
}
export function releaseAccounts(options) {
  return withReservations(options.root || defaultRoot,()=>releaseAccountsUnlocked(options));
}
