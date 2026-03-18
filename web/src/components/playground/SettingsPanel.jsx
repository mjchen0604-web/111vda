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
import { renderGroupOption, selectFilter } from '../../helpers';
import ConfigManager from './ConfigManager';
import CustomRequestEditor from './CustomRequestEditor';
import ImageUrlInput from './ImageUrlInput';
import ParameterControl from './ParameterControl';

const VISIBILITY_OPTIONS = [
  { label: '鍏抽棴', value: 'off' },
  { label: '浠呯鐞嗗憳', value: 'admin' },
  { label: '鍏ㄥ眬寮€鏀?, value: 'global' },
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
  applyPromptToRealAPI,
  onApplyPromptToRealAPIChange,
  applyModelConfigToRealAPI,
  onApplyModelConfigToRealAPIChange,
  onPromptModeChange,
}) => {
  const currentConfig = {
    inputs,
    parameterEnabled,
  };

  const promptInputLocked =
    inputs.promptMode !== 'native' || applyPromptToRealAPI;

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
            妯″瀷閰嶇疆
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
              鍒嗙粍
            </Typography.Text>
          </div>
          <Select
            placeholder='璇烽€夋嫨鍒嗙粍'
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
              鎻愮ず璇嶆ā寮?            </Typography.Text>
            <Typography.Text className='text-xs text-gray-500 block mt-1'>
              榛樿妯″紡浣跨敤鍐呯疆鑱婂ぉ鍩虹嚎鎻愮ず璇嶏紱鍘熺敓鏍煎紡鍙娇鐢ㄧ┖鎻愮ず璇嶆垨浣犳墜鍔ㄥ～鍐欑殑鎻愮ず璇嶃€?            </Typography.Text>
          </div>

          <div className='grid grid-cols-2 gap-2'>
            <Button
              theme={inputs.promptMode === 'default' ? 'solid' : 'light'}
              type={inputs.promptMode === 'default' ? 'primary' : 'tertiary'}
              onClick={() => onPromptModeChange('default')}
              className='!rounded-lg'
            >
              榛樿
            </Button>
            <Button
              theme={inputs.promptMode === 'native' ? 'solid' : 'light'}
              type={inputs.promptMode === 'native' ? 'primary' : 'tertiary'}
              onClick={() => onPromptModeChange('native')}
              className='!rounded-lg'
            >
              鍘熺敓鏍煎紡
            </Button>
          </div>

          <div className='flex items-center justify-between gap-3'>
            <div>
              <Typography.Text strong className='text-sm'>
                鏄惁鐪熷疄搴旂敤
              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mt-1'>
                寮€鍚悗锛屽綋鍓嶆彁绀鸿瘝妯″紡鍜岃嚜瀹氫箟鎻愮ず璇嶄細甯﹀埌鐪熷疄璇锋眰锛涘叧闂悗鍙湪鎿嶇粌鍦烘湰鍦扮敓鏁堛€?              </Typography.Text>
            </div>
            <Switch
              checked={applyPromptToRealAPI}
              onChange={onApplyPromptToRealAPIChange}
              checkedText='On'
              uncheckedText='Off'
              size='small'
            />
          </div>

          <TextArea
            value={inputs.systemPrompt || ''}
            onChange={(value) => onInputChange('systemPrompt', value)}
            disabled={promptInputLocked}
            autosize={{ minRows: 4, maxRows: 10 }}
            placeholder='鍘熺敓鏍煎紡涓嬪彲濉啓鑷畾涔夋彁绀鸿瘝锛涚暀绌哄垯浣跨敤绌烘彁绀鸿瘝銆傚紑鍚湡瀹炲簲鐢ㄥ悗杩欓噷浼氶攣瀹氾紝鍏抽棴鍚庢墠鑳界户缁紪杈戙€?
            className='!rounded-lg'
          />

          {adminControls?.enabled && (
            <div className='rounded-lg bg-[var(--semi-color-fill-0)] p-3'>
              <Typography.Text strong className='text-sm block mb-2'>
                绠＄悊鍛樺叏灞€鎻愮ず璇嶅揩鎹峰垏鎹?              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mb-3'>
                鐐瑰嚮鍝竟锛屽氨鎶婂叏灞€榛樿鎻愮ず璇嶅垏鍒板摢杈癸紝骞跺悓姝ヤ笂鏂瑰綋鍓嶉€変腑鐨勭姸鎬併€?              </Typography.Text>
              <div className='grid grid-cols-2 gap-2'>
                <Button
                  theme='outline'
                  type='secondary'
                  onClick={() => adminControls.onSaveGlobalPromptPreset?.('default')}
                  className='!rounded-lg'
                >
                  鍏ㄥ眬榛樿鎻愮ず璇?                </Button>
                <Button
                  theme='outline'
                  type='warning'
                  onClick={() => adminControls.onSaveGlobalPromptPreset?.('native-empty')}
                  className='!rounded-lg'
                >
                  鍏ㄥ眬绌烘彁绀鸿瘝
                </Button>
              </div>
            </div>
          )}
        </div>

        <div>
          <div className='flex items-center gap-2 mb-2'>
            <Sparkles size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              妯″瀷
            </Typography.Text>
          </div>
          <Select
            placeholder='璇烽€夋嫨妯″瀷'
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
        </div>

        <div className='space-y-3 rounded-xl border border-[var(--semi-color-border)] p-3'>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <Typography.Text strong className='text-sm'>
                寮哄埗鏄剧ず鎬濊€冭繃绋?              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mt-1'>
                榛樿鍏抽棴銆傚紑鍚悗锛屼細鍚戞搷缁冨満璇锋眰鑷姩娉ㄥ叆鍙 {'<think>'} 鎸囦护锛屽敖閲忚妯″瀷鎶婂綋鍓嶆€濊€冭繃绋嬬洿鎺ュ啓鍑烘潵銆?              </Typography.Text>
            </div>
            <Switch
              checked={Boolean(inputs.forceReasoningOutput)}
              onChange={(checked) => onInputChange('forceReasoningOutput', checked)}
              checkedText='On'
              uncheckedText='Off'
              size='small'
            />
          </div>
        </div>

        <ImageUrlInput
          imageUrls={inputs.imageUrls}
          imageEnabled={inputs.imageEnabled}
          onImageUrlsChange={(urls) => onInputChange('imageUrls', urls)}
          onImageEnabledChange={(enabled) => onInputChange('imageEnabled', enabled)}
        />

        <div className='space-y-3 rounded-xl border border-[var(--semi-color-border)] p-3'>
          <Typography.Text strong className='text-sm'>
            妯″瀷閰嶇疆
          </Typography.Text>
          <ParameterControl
            inputs={inputs}
            parameterEnabled={parameterEnabled}
            onInputChange={onInputChange}
            onParameterToggle={onParameterToggle}
          />
          <div className='flex items-center justify-between gap-3 pt-1'>
            <div>
              <Typography.Text strong className='text-sm'>
                妯″瀷閰嶇疆杩欎簲涓弬鏁板甫鍒扮湡瀹炶姹備綋
              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mt-1'>
                寮€鍚悗锛孴emperature銆乀op P銆丗requency Penalty銆丳resence Penalty銆丼eed 浼氭敞鍏ョ湡瀹炶姹傦紱鍏抽棴鍚庡彧鍦ㄦ搷缁冨満鏈湴鐢熸晥銆?              </Typography.Text>
            </div>
            <Switch
              checked={applyModelConfigToRealAPI}
              onChange={onApplyModelConfigToRealAPIChange}
              checkedText='On'
              uncheckedText='Off'
              size='small'
            />
          </div>
        </div>

        {adminControls?.enabled && (
          <div className='space-y-4 rounded-xl border border-[var(--semi-color-border)] p-3'>
            <div className='flex items-center gap-2'>
              <Bug size={16} className='text-gray-500' />
              <Typography.Text strong className='text-sm'>
                瀹為獙鍔熻兘鍙鎬?              </Typography.Text>
            </div>
            <div>
              <Typography.Text className='text-xs text-gray-500 mb-2 block'>
                璋冭瘯淇℃伅
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
                鑷畾涔夎姹備綋妯″紡
              </Typography.Text>
              <Select
                value={adminControls.customRequestVisibility}
                optionList={VISIBILITY_OPTIONS}
                onChange={(value) =>
                  adminControls.onSaveVisibility?.('custom_request', value)
                }
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
                娴佸紡杈撳嚭
              </Typography.Text>
            </div>
            <Switch
              checked={inputs.stream}
              onChange={(checked) => onInputChange('stream', checked)}
              checkedText='On'
              uncheckedText='Off'
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
