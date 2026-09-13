import assert from 'node:assert/strict';
import test from 'node:test';
import { validatedAssistantDownload } from '../../src/schemii/common/web/assets/ai-download.js';
const origin = 'https://localhost:8001';
test('assistant download receipts permit only typed model and result exports', () => {
  for (const [url, filename] of [['/api/v1/schemoo/models/model_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'semantic-model.json'], ['/api/v1/common/query-executions/cex_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/results/res_cccccccccccccccccccccccccccccccc/export.csv', 'query-result.csv']]) {
    assert.deepEqual(validatedAssistantDownload({ effect: 'browser_download', url, filename: '../unsafe' }, origin), { url, filename });
    assert.equal(validatedAssistantDownload({ effect: 'browser_download', url: 'https://evil.test'+url }, origin), null);
    assert.equal(validatedAssistantDownload({ effect: 'browser_download', url: url+'?secret=1' }, origin), null);
  }
  assert.equal(validatedAssistantDownload({ effect: 'browser_download', url: '/api/v1/settings' }, origin), null);
});
