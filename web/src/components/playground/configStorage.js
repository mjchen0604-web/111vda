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

import {
  STORAGE_KEYS,
  DEFAULT_CONFIG,
  PLAYGROUND_USER_INPUT_KEYS,
  PLAYGROUND_USER_PARAMETER_KEYS,
} from '../../constants/playground.constants';

const SESSION_STORAGE_KEY = 'playground_session_id';

const pick = (source, keys) => {
  const out = {};
  keys.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(source || {}, key)) {
      out[key] = source[key];
    }
  });
  return out;
};

const normalizeUserScopedConfig = (config = {}) => {
  const inputs = {
    ...pick(DEFAULT_CONFIG.inputs, PLAYGROUND_USER_INPUT_KEYS),
    ...pick(config.inputs || {}, PLAYGROUND_USER_INPUT_KEYS),
  };
  const parameterEnabled = {
    ...pick(DEFAULT_CONFIG.parameterEnabled, PLAYGROUND_USER_PARAMETER_KEYS),
    ...pick(config.parameterEnabled || {}, PLAYGROUND_USER_PARAMETER_KEYS),
  };
  return {
    inputs,
    parameterEnabled,
  };
};

export const saveConfig = (config) => {
  try {
    const normalized = normalizeUserScopedConfig(config);
    const configToSave = {
      ...normalized,
      timestamp: new Date().toISOString(),
    };
    localStorage.setItem(STORAGE_KEYS.CONFIG, JSON.stringify(configToSave));
  } catch (error) {
    console.error('保存配置失败:', error);
  }
};

export const saveMessages = (messages) => {
  try {
    const messagesToSave = {
      messages,
      timestamp: new Date().toISOString(),
    };
    localStorage.setItem(STORAGE_KEYS.MESSAGES, JSON.stringify(messagesToSave));
  } catch (error) {
    console.error('保存消息失败:', error);
  }
};

export const generateConversationSessionId = () => {
  try {
    if (
      typeof window !== 'undefined' &&
      window.crypto &&
      typeof window.crypto.randomUUID === 'function'
    ) {
      return window.crypto.randomUUID();
    }
  } catch (error) {
    console.error('生成会话 ID 失败:', error);
  }
  return `pgsess_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
};

export const saveConversationSessionId = (sessionId) => {
  try {
    if (sessionId && typeof sessionId === 'string') {
      localStorage.setItem(SESSION_STORAGE_KEY, sessionId);
    }
  } catch (error) {
    console.error('保存会话 ID 失败:', error);
  }
};

export const loadConversationSessionId = () => {
  try {
    const sessionId = localStorage.getItem(SESSION_STORAGE_KEY);
    return sessionId || null;
  } catch (error) {
    console.error('加载会话 ID 失败:', error);
  }
  return null;
};

export const clearConversationSessionId = () => {
  try {
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (error) {
    console.error('清除会话 ID 失败:', error);
  }
};

export const loadConfig = () => {
  try {
    const savedConfig = localStorage.getItem(STORAGE_KEYS.CONFIG);
    if (savedConfig) {
      const parsedConfig = JSON.parse(savedConfig);
      const normalized = normalizeUserScopedConfig(parsedConfig);

      return {
        inputs: {
          ...DEFAULT_CONFIG.inputs,
          ...normalized.inputs,
        },
        parameterEnabled: {
          ...DEFAULT_CONFIG.parameterEnabled,
          ...normalized.parameterEnabled,
        },
      };
    }
  } catch (error) {
    console.error('加载配置失败:', error);
  }

  return DEFAULT_CONFIG;
};

export const loadRawConfig = () => {
  try {
    const savedConfig = localStorage.getItem(STORAGE_KEYS.CONFIG);
    if (savedConfig) {
      const parsedConfig = JSON.parse(savedConfig);
      if (parsedConfig && typeof parsedConfig === 'object') {
        return normalizeUserScopedConfig(parsedConfig);
      }
    }
  } catch (error) {
    console.error('加载原始配置失败:', error);
  }
  return null;
};

export const loadMessages = () => {
  try {
    const savedMessages = localStorage.getItem(STORAGE_KEYS.MESSAGES);
    if (savedMessages) {
      const parsedMessages = JSON.parse(savedMessages);
      return parsedMessages.messages || null;
    }
  } catch (error) {
    console.error('加载消息失败:', error);
  }

  return null;
};

export const clearConfig = () => {
  try {
    localStorage.removeItem(STORAGE_KEYS.CONFIG);
    localStorage.removeItem(STORAGE_KEYS.MESSAGES);
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (error) {
    console.error('清除配置失败:', error);
  }
};

export const clearMessages = () => {
  try {
    localStorage.removeItem(STORAGE_KEYS.MESSAGES);
    localStorage.removeItem(SESSION_STORAGE_KEY);
  } catch (error) {
    console.error('清除消息失败:', error);
  }
};

export const hasStoredConfig = () => {
  try {
    return localStorage.getItem(STORAGE_KEYS.CONFIG) !== null;
  } catch (error) {
    console.error('检查配置失败:', error);
    return false;
  }
};

export const getConfigTimestamp = () => {
  try {
    const savedConfig = localStorage.getItem(STORAGE_KEYS.CONFIG);
    if (savedConfig) {
      const parsedConfig = JSON.parse(savedConfig);
      return parsedConfig.timestamp || null;
    }
  } catch (error) {
    console.error('获取配置时间戳失败:', error);
  }
  return null;
};

export const exportConfig = (config, messages = null) => {
  try {
    const configToExport = {
      ...config,
      messages: messages || loadMessages(),
      sessionId: loadConversationSessionId(),
      exportTime: new Date().toISOString(),
      version: '1.0',
    };

    const dataStr = JSON.stringify(configToExport, null, 2);
    const dataBlob = new Blob([dataStr], { type: 'application/json' });

    const link = document.createElement('a');
    link.href = URL.createObjectURL(dataBlob);
    link.download = `playground-config-${new Date().toISOString().split('T')[0]}.json`;
    link.click();

    URL.revokeObjectURL(link.href);
  } catch (error) {
    console.error('导出配置失败:', error);
  }
};

export const importConfig = (file) => {
  return new Promise((resolve, reject) => {
    try {
      const reader = new FileReader();
      reader.onload = (event) => {
        try {
          const importedConfig = JSON.parse(event.target.result);

          if (importedConfig.inputs && importedConfig.parameterEnabled) {
            if (
              importedConfig.messages &&
              Array.isArray(importedConfig.messages)
            ) {
              saveMessages(importedConfig.messages);
            }
            if (
              importedConfig.sessionId &&
              typeof importedConfig.sessionId === 'string'
            ) {
              saveConversationSessionId(importedConfig.sessionId);
            }

            resolve(importedConfig);
          } else {
            reject(new Error('配置文件格式无效'));
          }
        } catch (parseError) {
          reject(new Error(`解析配置文件失败: ${parseError.message}`));
        }
      };
      reader.onerror = () => reject(new Error('读取文件失败'));
      reader.readAsText(file);
    } catch (error) {
      reject(new Error(`导入配置失败: ${error.message}`));
    }
  });
};
