import { getCurrencyConfig } from './render';

export const getQuotaPerUnit = () => {
  const raw = parseFloat(localStorage.getItem('quota_per_unit') || '1');
  return Number.isFinite(raw) && raw > 0 ? raw : 1;
};

export const quotaToDisplayAmount = (quota) => {
  const q = Number(quota || 0);
  if (!Number.isFinite(q) || q <= 0) return 0;
  const { type, rate } = getCurrencyConfig();
  if (type === 'TOKENS') return q;
  const usd = q / getQuotaPerUnit();
  if (type === 'USD') return usd;
  return usd * (rate || 1);
};

export const quotaToCurrencyAmount = (quota, currency = 'USD') => {
  const q = Number(quota || 0);
  if (!Number.isFinite(q) || q <= 0) return 0;
  const usd = q / getQuotaPerUnit();
  if (String(currency || 'USD').toUpperCase() === 'CNY') {
    const statusStr = localStorage.getItem('status');
    let usdRate = 1;
    try {
      if (statusStr) {
        const s = JSON.parse(statusStr);
        usdRate = s?.usd_exchange_rate || 1;
      }
    } catch (e) {}
    return usd * usdRate;
  }
  return usd;
};

export const displayAmountToQuota = (amount) => {
  const val = Number(amount || 0);
  if (!Number.isFinite(val) || val <= 0) return 0;
  const { type, rate } = getCurrencyConfig();
  if (type === 'TOKENS') return Math.round(val);
  const usd = type === 'USD' ? val : val / (rate || 1);
  return Math.round(usd * getQuotaPerUnit());
};

export const currencyAmountToQuota = (amount, currency = 'USD') => {
  const val = Number(amount || 0);
  if (!Number.isFinite(val) || val <= 0) return 0;
  const normalizedCurrency = String(currency || 'USD').toUpperCase();
  let usd = val;
  if (normalizedCurrency === 'CNY') {
    const statusStr = localStorage.getItem('status');
    let usdRate = 1;
    try {
      if (statusStr) {
        const s = JSON.parse(statusStr);
        usdRate = s?.usd_exchange_rate || 1;
      }
    } catch (e) {}
    usd = usdRate > 0 ? val / usdRate : val;
  }
  return Math.round(usd * getQuotaPerUnit());
};
