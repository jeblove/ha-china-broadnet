const ORIGIN = 'https://www.10099.com.cn';
const ACCOUNT = ORIGIN + '/personal-center-number-order.html';

function integer(value, signed = false) {
  const pattern = signed ? /^-?\d{1,12}$/ : /^\d{1,12}$/;
  if (typeof value !== 'string' || !pattern.test(value)) return null;
  return Number(value);
}

function localDate(value) {
  if (typeof value !== 'string') return null;
  const compact = value.match(/^(\d{4})(\d{2})(\d{2})(?:\d{6})?$/);
  const iso = value.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{2}:\d{2}:\d{2})?$/);
  return (compact || iso)?.slice(1, 4).join('-') ?? null;
}

function trafficFor(rows, name, now) {
  if (!name || !Array.isArray(rows) || !name.includes('通用') || /定向|结转|赠送/.test(name)) {
    return {};
  }
  const matches = rows.filter(row => row.busiType === '5' && row.discntName === name);
  if (matches.length !== 1) return {};
  const { startTime, endTime, highFee, balance } = matches[0];
  const today = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(now);
  const start = localDate(startTime);
  const end = localDate(endTime);
  const month = today.slice(0, 7);
  const [year, monthNumber] = month.split('-').map(Number);
  const nextMonth = new Date(Date.UTC(year, monthNumber, 1)).toISOString().slice(0, 10);
  const atNextBoundary = end === nextMonth && /(?:[ T]00:00:00|^\d{8}000000)$/.test(endTime);
  if (!start?.startsWith(month) || (!end?.startsWith(month) && !atNextBoundary)
      || start > today || end < today) {
    return {};
  }
  const total = integer(highFee);
  const remaining = integer(balance);
  if (total === null || remaining === null || remaining > total) return {};
  const gb = kib => (kib * 1024 / 1e9).toFixed(9);
  return {
    data_total: gb(total),
    data_used: gb(total - remaining),
    data_remaining: gb(remaining),
  };
}

function snapshot(balance, resources, name, now) {
  const fee = integer(balance?.balance, true);
  const overdue = integer(balance?.spayFee);
  if (fee === null || (balance.spayFee && overdue === null)) {
    throw new Error('The carrier did not return a valid balance');
  }
  return {
    observed: String(now.getTime()),
    balance: ((overdue ? -overdue : fee) / 100).toFixed(2),
    traffic: trafficFor(resources?.userResList, name, now),
  };
}

async function observe(page, packageName) {
  await page.goto(ACCOUNT, { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.getByText('[退出]', { exact: false }).first().waitFor({ timeout: 10000 });
  const waitFor = name => page.waitForResponse(response => {
    const url = new URL(response.url());
    return url.origin === ORIGIN && url.pathname === '/contact-web/api/busi/' + name
      && response.request().method() === 'POST';
  }, { timeout: 20000 });
  const [balance, resources] = await Promise.all([
    waitFor('qryBalanceFee'),
    waitFor('qryUserRes'),
    page.getByText('我的账户', { exact: true }).first().click({ timeout: 10000 }),
  ]);
  if (!balance.ok() || !resources.ok()) throw new Error('The carrier query failed');
  const [b, r] = await Promise.all([balance.json(), resources.json()]);
  if (!b.ok || !r.ok || !b.data?.intfResultBean || !r.data?.intfResultBean) {
    throw new Error('The carrier rejected the query');
  }
  return snapshot(b.data.intfResultBean, r.data.intfResultBean, packageName, new Date());
}

module.exports = { ORIGIN, ACCOUNT, observe, snapshot };
