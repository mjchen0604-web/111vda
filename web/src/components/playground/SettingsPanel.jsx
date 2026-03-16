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

import React from 'react';
import { Button, Card, Select, Switch, TextArea, Typography } from '@douyinfe/semi-ui';
import { Bug, Settings, Sparkles, ToggleLeft, Users, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { renderGroupOption, selectFilter } from '../../helpers';
import ConfigManager from './ConfigManager';
import CustomRequestEditor from './CustomRequestEditor';
import ImageUrlInput from './ImageUrlInput';
import ParameterControl from './ParameterControl';

const VISIBILITY_OPTIONS = [
  { label: '关闭', value: 'off' },
  { label: '仅管理员', value: 'admin' },
  { label: '全局开放', value: 'global' },
];

const SettingsPanel = ({
  inputs,
  parameterEnabled,
  models,
  groups,
  styleState,
  onInputChange,
  onParameterToggle,
  onCloseSettings,
  onConfigImport,
  onConfigReset,
  adminControls,
  messages,
  canUseCustomRequest,
  customRequestMode,
  customRequestBody,
  onCustomRequestModeChange,
  onCustomRequestBodyChange,
  previewPayload,
  effectHint,
  applyPromptToRealAPI,
  onApplyPromptToRealAPIChange,
  applyModelConfigToRealAPI,
  onApplyModelConfigToRealAPIChange,
  onPromptModeChange,
}) => {
  const { t } = useTranslation();

  const currentConfig = {
    inputs,
    parameterEnabled,
  };

  return (
    <Card
      className='h-full flex flex-col'
      bordered={false}
      bodyStyle={{
        padding: styleState.isMobile ? '16px' : '24px',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div className='flex items-center justify-between mb-6 flex-shrink-0'>
        <div className='flex items-center'>
          <div className='w-10 h-10 rounded-full bg-gradient-to-r from-purple-500 to-pink-500 flex items-center justify-center mr-3'>
            <Settings size={20} className='text-white' />
          </div>
          <Typography.Title heading={5} className='mb-0'>
            {t('模型配置')}
          </Typography.Title>
        </div>

        {styleState.isMobile && onCloseSettings && (
          <Button
            icon={<X size={16} />}
            onClick={onCloseSettings}
            theme='borderless'
            type='tertiary'
            size='small'
            className='!rounded-lg'
          />
        )}
      </div>

      {styleState.isMobile && (
        <div className='mb-4 flex-shrink-0'>
          <ConfigManager
            currentConfig={currentConfig}
            onConfigImport={onConfigImport}
            onConfigReset={onConfigReset}
            styleState={{ ...styleState, isMobile: false }}
            messages={messages}
          />
        </div>
      )}

      <div className='space-y-6 overflow-y-auto flex-1 pr-2 model-settings-scroll'>
        <div>
          <div className='flex items-center gap-2 mb-2'>
            <Users size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              {t('分组')}
            </Typography.Text>
          </div>
          <Select
            placeholder={t('请选择分组')}
            name='group'
            required
            selection
            filter={selectFilter}
            autoClearSearchValue={false}
            onChange={(value) => onInputChange('group', value)}
            value={inputs.group}
            autoComplete='new-password'
            optionList={groups}
            renderOptionItem={renderGroupOption}
            style={{ width: '100%' }}
            dropdownStyle={{ width: '100%', maxWidth: '100%' }}
            className='!rounded-lg'
          />
        </div>

        <div className='space-y-3 rounded-xl border border-[var(--semi-color-border)] p-3'>
          <div>
            <Typography.Text strong className='text-sm'>
              {t('提示词模式')}
            </Typography.Text>
            <Typography.Text className='text-xs text-gray-500 block mt-1'>
              {t('默认模式使用内置基线提示词；原生格式使用空提示词或你自己填写的提示词。')}
            </Typography.Text>
          </div>

          <div className='flex items-center justify-between gap-3'>
            <Typography.Text strong className='text-sm'>
              {t('是否真实应用')}
            </Typography.Text>
            <Switch
              checked={applyPromptToRealAPI}
              onChange={onApplyPromptToRealAPIChange}
              checkedText={t('开')}
              uncheckedText={t('关')}
              size='small'
            />
          </div>

          <div className='flex items-center justify-between gap-3'>
            <Typography.Text className='text-xs text-gray-500'>
              {applyModelConfigToRealAPI
                ? t('模型配置这五个参数会带到真实请求体')
                : t('模型配置这五个参数不会带到真实请求体')}
            </Typography.Text>
            <Switch
              checked={applyModelConfigToRealAPI}
              onChange={onApplyModelConfigToRealAPIChange}
              checkedText={t('开')}
              uncheckedText={t('关')}
              size='small'
            />
          </div>

          <div className='grid grid-cols-2 gap-2'>
            <Button
              theme={inputs.promptMode === 'default' ? 'solid' : 'light'}
              type={inputs.promptMode === 'default' ? 'primary' : 'tertiary'}
              onClick={() => onPromptModeChange('default')}
              className='!rounded-lg'
            >
              {t('默认')}
            </Button>
            <Button
              theme={inputs.promptMode === 'native' ? 'solid' : 'light'}
              type={inputs.promptMode === 'native' ? 'primary' : 'tertiary'}
              onClick={() => onPromptModeChange('native')}
              className='!rounded-lg'
            >
              {t('原生格式')}
            </Button>
          </div>

          <TextArea
            value={inputs.systemPrompt || ''}
            onChange={(value) => onInputChange('systemPrompt', value)}
            disabled={inputs.promptMode !== 'native' || applyPromptToRealAPI}
            autosize={{ minRows: 4, maxRows: 10 }}
            placeholder={t('原生格式下可填写自定义提示词；留空则使用空提示词。')}
            className='!rounded-lg'
          />

          {adminControls?.enabled && (
            <div className='rounded-lg bg-[var(--semi-color-fill-0)] p-3'>
              <Typography.Text strong className='text-sm block mb-2'>
                {t('管理员全局提示词快捷切换')}
              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mb-3'>
                {t('点击哪边就把全局默认提示词保存到哪边，并同步当前上方显示。')}
              </Typography.Text>
              <div className='grid grid-cols-2 gap-2'>
                <Button
                  theme='outline'
                  type='secondary'
                  onClick={() => adminControls.onSaveGlobalPromptPreset?.('default')}
                  className='!rounded-lg'
                >
                  {t('全局默认提示词')}
                </Button>
                <Button
                  theme='outline'
                  type='warning'
                  onClick={() => adminControls.onSaveGlobalPromptPreset?.('native-empty')}
                  className='!rounded-lg'
                >
                  {t('全局空提示词')}
                </Button>
              </div>
            </div>
          )}
        </div>

        <div>
          <div className='flex items-center gap-2 mb-2'>
            <Sparkles size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              {t('模型')}
            </Typography.Text>
          </div>
          <Select
            placeholder={t('请选择模型')}
            name='model'
            required
            selection
            filter={selectFilter}
            autoClearSearchValue={false}
            onChange={(value) => onInputChange('model', value)}
            value={inputs.model}
            autoComplete='new-password'
            optionList={models}
            style={{ width: '100%' }}
            dropdownStyle={{ width: '100%', maxWidth: '100%' }}
            className='!rounded-lg'
          />
          {effectHint && (
            <Typography.Text className='text-xs text-gray-500 mt-2 block'>
              {effectHint}
            </Typography.Text>
          )}
        </div>

        <div>
          <ImageUrlInput
            imageUrls={inputs.imageUrls}
            imageEnabled={inputs.imageEnabled}
            onImageUrlsChange={(urls) => onInputChange('imageUrls', urls)}
            onImageEnabledChange={(enabled) => onInputChange('imageEnabled', enabled)}
          />
        </div>

        <div>
          <ParameterControl
            inputs={inputs}
            parameterEnabled={parameterEnabled}
            onInputChange={onInputChange}
            onParameterToggle={onParameterToggle}
          />
        </div>

        {adminControls?.enabled && (
          <div className='space-y-4 rounded-xl border border-[var(--semi-color-border)] p-3'>
            <div className='flex items-center gap-2'>
              <Bug size={16} className='text-gray-500' />
              <Typography.Text strong className='text-sm'>
                {t('实验功能可见性')}
              </Typography.Text>
            </div>
            <div>
              <Typography.Text className='text-xs text-gray-500 mb-2 block'>
                {t('调试信息')}
              </Typography.Text>
              <Select
                value={adminControls.debugVisibility}
                optionList={VISIBILITY_OPTIONS}
                onChange={(value) => adminControls.onSaveVisibility?.('debug', value)}
                className='!rounded-lg'
              />
            </div>
            <div>
              <Typography.Text className='text-xs text-gray-500 mb-2 block'>
                {t('自定义请求体模式')}
              </Typography.Text>
              <Select
                value={adminControls.customRequestVisibility}
                optionList={VISIBILITY_OPTIONS}
                onChange={(value) => adminControls.onSaveVisibility?.('custom_request', value)}
                className='!rounded-lg'
              />
            </div>
          </div>
        )}

        {canUseCustomRequest && (
          <CustomRequestEditor
            customRequestMode={customRequestMode}
            customRequestBody={customRequestBody}
            onCustomRequestModeChange={onCustomRequestModeChange}
            onCustomRequestBodyChange={onCustomRequestBodyChange}
            defaultPayload={previewPayload}
          />
        )}

        <div>
          <div className='flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <ToggleLeft size={16} className='text-gray-500' />
              <Typography.Text strong className='text-sm'>
                {t('流式输出')}
              </Typography.Text>
            </div>
            <Switch
              checked={inputs.stream}
              onChange={(checked) => onInputChange('stream', checked)}
              checkedText={t('开')}
              uncheckedText={t('关')}
              size='small'
            />
          </div>
        </div>
      </div>

      {!styleState.isMobile && (
        <div className='flex-shrink-0 pt-3'>
          <ConfigManager
            currentConfig={currentConfig}
            onConfigImport={onConfigImport}
            onConfigReset={onConfigReset}
            styleState={styleState}
            messages={messages}
          />
        </div>
      )}
    </Card>
  );
};

export default SettingsPanel;
