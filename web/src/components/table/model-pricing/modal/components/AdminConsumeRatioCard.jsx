/*
Copyright (C) 2025 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/

import React, { useEffect, useMemo, useState } from 'react';
import { Card, Avatar, Typography, Input, Button } from '@douyinfe/semi-ui';
import { IconSave } from '@douyinfe/semi-icons';
import { SlidersHorizontal } from 'lucide-react';
import { API, showError, showSuccess } from '../../../../../helpers';

const { Text } = Typography;

const NUMERIC_INPUT_REGEX = /^(\d+(\.\d*)?|\.\d*)?$/;

const AdminConsumeRatioCard = ({ modelName, refreshPricing, t }) => {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rawMap, setRawMap] = useState({});
  const [value, setValue] = useState('');

  const currentValue = useMemo(() => {
    if (!modelName) return '';
    const current = rawMap?.[modelName];
    if (current === undefined || current === null) return '';
    return String(current);
  }, [modelName, rawMap]);

  useEffect(() => {
    setValue(currentValue);
  }, [currentValue]);

  useEffect(() => {
    if (!modelName) return;
    let cancelled = false;

    const load = async () => {
      setLoading(true);
      try {
        const res = await API.get('/api/option/');
        const list = res?.data?.data || [];
        const item = list.find((x) => x.key === 'ModelConsumeRatio');
        let parsed = {};
        if (item?.value) {
          try {
            parsed = JSON.parse(item.value);
          } catch {
            parsed = {};
          }
        }
        if (!cancelled) {
          setRawMap(parsed);
        }
      } catch (error) {
        if (!cancelled) {
          showError(error.message || t('获取实际消耗倍率失败'));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, [modelName, t]);

  const handleSave = async () => {
    if (!modelName) return;
    if (!NUMERIC_INPUT_REGEX.test(value)) {
      showError(t('请输入合法的倍率'));
      return;
    }

    const nextMap = { ...rawMap };
    const trimmed = String(value || '').trim();
    if (!trimmed || trimmed === '1') {
      delete nextMap[modelName];
    } else {
      nextMap[modelName] = Number(trimmed);
    }

    setSaving(true);
    try {
      const res = await API.put('/api/option/', {
        key: 'ModelConsumeRatio',
        value: JSON.stringify(nextMap, null, 2),
      });
      if (!res?.data?.success) {
        throw new Error(res?.data?.message || t('保存失败'));
      }
      setRawMap(nextMap);
      showSuccess(t('实际消耗倍率已保存'));
      if (typeof refreshPricing === 'function') {
        await refreshPricing();
      }
    } catch (error) {
      showError(error.message || t('保存失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className='!rounded-2xl shadow-sm border-0 mb-6'>
      <div className='flex items-center mb-4'>
        <Avatar size='small' color='purple' className='mr-2 shadow-md'>
          <SlidersHorizontal size={16} />
        </Avatar>
        <div>
          <Text className='text-lg font-medium'>{t('实际消耗控制')}</Text>
          <div className='text-xs text-gray-600'>
            {t('只影响真实扣费和消耗日志，不影响模型广场展示价格')}
          </div>
        </div>
      </div>

      <div className='space-y-3'>
        <div>
          <Text strong>{t('实际消耗倍率')}</Text>
          <div className='mt-2'>
            <Input
              value={value}
              placeholder={t('留空或 1 表示不额外放大')}
              suffix='x'
              disabled={loading || saving}
              onChange={(next) => {
                if (NUMERIC_INPUT_REGEX.test(next)) {
                  setValue(next);
                }
              }}
            />
          </div>
          <div className='mt-1 text-xs text-gray-500'>
            {t('例如 1.2 表示实际扣费提升 20%，模型对外展示价格保持不变。')}
          </div>
        </div>

        <Button
          type='primary'
          icon={<IconSave />}
          loading={saving}
          disabled={loading}
          onClick={handleSave}
        >
          {t('保存实际消耗倍率')}
        </Button>
      </div>
    </Card>
  );
};

export default AdminConsumeRatioCard;

