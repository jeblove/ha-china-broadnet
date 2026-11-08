const { test } = require('node:test');
const assert = require('node:assert/strict');
const { snapshot } = require('./readings.cjs');

const now = new Date('2026-11-09T00:00:00+08:00');
const plan = {
  discntName: '示例本月套餐通用流量',
  busiType: '5',
  startTime: '20261101000000',
  endTime: '20261130235959',
  highFee: '1048576',
  balance: '524288',
};
const read = (rows, name = plan.discntName) =>
  snapshot({ balance: '1201', spayFee: '0' }, { userResList: rows }, name, now);

test('convert fen and KiB, selecting one explicitly approved resource', () => {
  const directed = { ...plan, discntName: '示例定向流量' };
  assert.deepEqual(read([directed, plan]), {
    observed: String(now.getTime()),
    balance: '12.01',
    traffic: {
      data_total: '1.073741824',
      data_used: '0.536870912',
      data_remaining: '0.536870912',
    },
  });
});

test('only use unambiguous packages in the current month', () => {
  assert.deepEqual(read([plan, plan]).traffic, {});
  assert.deepEqual(read([{ ...plan, endTime: '2026-12-01 00:00:00' }]).traffic,
    read([plan]).traffic);
  assert.deepEqual(read([{ ...plan, endTime: '20261201235959' }]).traffic, {});
  assert.deepEqual(read([{ ...plan, balance: '9999999' }]).traffic, {});
  const carryover = { ...plan, discntName: '结转通用流量' };
  assert.deepEqual(read([carryover], carryover.discntName).traffic, {});
});

test('distinguish zero and overdue balances from invalid results', () => {
  assert.equal(snapshot({ balance: '0' }, {}, '', now).balance, '0.00');
  assert.equal(snapshot({ balance: '0', spayFee: '180' }, {}, '', now).balance, '-1.80');
  assert.throws(() => snapshot({ balance: '' }, {}, '', now));
});
