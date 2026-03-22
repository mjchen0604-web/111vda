import React, { useEffect, useMemo, useState } from 'react';
import {
  Banner,
  Button,
  Col,
  Form,
  Input,
  Row,
  Select,
  Spin,
  Switch,
  Table,
  TextArea,
  Typography,
} from '@douyinfe/semi-ui';
import { API, showError, showSuccess, showWarning } from '../../../helpers';

const { Text } = Typography;

const EMPTY_SETTINGS = {
  routingStrategy: 'round-robin',
  requestRetry: 0,
  maxRetryInterval: 5,
  serviceTier: '',
  reasoningEffort: 'minimal',
  reasoningSummary: 'auto',
  reasoningCompat: 'current',
  exposeReasoningModels: false,
  verbose: false,
  verboseObfuscation: false,
  httpProxy: '',
  httpsProxy: '',
  allProxy: '',
  noProxy: '',
  uploadReplaceDefault: false,
};

const cardStyle = {
  border: '1px solid var(--semi-color-border)',
  borderRadius: 12,
  padding: 16,
  height: '100%',
  background: 'var(--semi-color-bg-1)',
};

const selectOptions = (items) =>
  items.map((item) =>
    typeof item === 'string'
      ? { label: item, value: item }
      : item,
  );

const safeText = (value, fallback = '-') => {
  if (value === null || value === undefined) {
    return fallback;
  }
  const text = String(value).trim();
  return text || fallback;
};

const formatAccountStatus = (record) => {
  if (!record || typeof record !== 'object') {
    return '-';
  }

  const status = String(record.status || '').trim();
  const lastStatus = record.last_status;
  const lastClassification = String(record.last_classification || '').trim();
  const lastError = String(record.last_error || record.error || '').trim();
  const cooldownRemaining = Number(record.cooldown_remaining || 0);
  const unlockAt = String(record.unlock_at || '').trim();

  if (
    cooldownRemaining <= 0 &&
    !unlockAt &&
    !lastError &&
    (status === 'ready' || lastClassification === 'ready' || Number(lastStatus) === 200)
  ) {
    return 'ready';
  }

  const parts = [];

  if (Number.isFinite(lastStatus) && lastStatus > 0 && Number(lastStatus) !== 200) {
    parts.push(`HTTP ${lastStatus}`);
  } else if (status) {
    parts.push(status);
  }

  if (lastClassification && lastClassification !== 'ready') {
    parts.push(lastClassification);
  }

  if (cooldownRemaining > 0) {
    parts.push(`cooldown ${cooldownRemaining}s`);
  }

  if (unlockAt) {
    parts.push(`until ${unlockAt}`);
  }

  if (lastError) {
    parts.push(lastError);
  }

  return parts.length > 0 ? parts.join(' | ') : '-';
};

const formatRuntimeCandidateStatus = (record) => {
  if (!record || typeof record !== 'object') {
    return '-';
  }

  const status = String(record.status || '').trim();
  const classification = String(record.last_classification || '').trim();
  const cooldownRemaining = Number(record.cooldown_remaining || 0);
  const stickySessions = Number(record.sticky_sessions || 0);
  const lastError = String(record.last_error || '').trim();
  const lastStatus = Number(record.last_status || 0);

  if (
    cooldownRemaining <= 0 &&
    !lastError &&
    (status === 'ready' || classification === 'ready' || lastStatus === 200)
  ) {
    return stickySessions > 0 ? `ready | sticky ${stickySessions}` : 'ready';
  }

  return safeText(
    [
      status,
      classification && classification !== status ? classification : '',
      cooldownRemaining > 0 ? `cooldown ${cooldownRemaining}s` : '',
      stickySessions > 0 ? `sticky ${stickySessions}` : '',
      lastStatus > 0 && lastStatus !== 200 ? `HTTP ${lastStatus}` : '',
      lastError,
    ]
      .filter(Boolean)
      .join(' | '),
  );
};

export default function SettingsChatCoreRuntime() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  const [health, setHealth] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [runtimeCandidates, setRuntimeCandidates] = useState({ count: 0, rawCount: 0, candidates: [], excluded: [] });
  const [models, setModels] = useState([]);
  const [configText, setConfigText] = useState('');
  const [settingsPath, setSettingsPath] = useState('');
  const [uploadFileList, setUploadFileList] = useState([]);

  const accountColumns = useMemo(
    () => [
      {
        title: '账号标签',
        dataIndex: 'label',
        render: (_, record) => safeText(record.label),
      },
      {
        title: '来源',
        dataIndex: 'source',
        render: (_, record) => safeText(record.source),
      },
      {
        title: '账号 ID',
        dataIndex: 'account_id',
        render: (_, record) => safeText(record.account_id),
      },
      {
        title: '空间 / Workspace',
        dataIndex: 'workspace_display',
        render: (_, record) =>
          safeText(
            record.workspace_display ||
              [record.org_id, record.project_id].filter(Boolean).join(' / '),
          ),
      },
      {
        title: '状态',
        dataIndex: 'last_status',
        render: (_, record) => formatAccountStatus(record),
      },
    ],
    [],
  );

  const runtimeCandidateColumns = useMemo(
    () => [
      { title: '标签', dataIndex: 'label', render: (_, record) => safeText(record.label) },
      { title: '账号 ID', dataIndex: 'account_id', render: (_, record) => safeText(record.account_id) },
      { title: 'Plan', dataIndex: 'plan', render: (_, record) => safeText(record.plan) },
      { title: '空间', dataIndex: 'workspace_display', render: (_, record) => safeText(record.workspace_display) },
      {
        title: '状态',
        dataIndex: 'status',
        render: (_, record) => formatRuntimeCandidateStatus(record),
      },
      { title: '来源文件', dataIndex: 'source', render: (_, record) => safeText(record.source) },
    ],
    [],
  );

  const excludedCandidateColumns = useMemo(
    () => [
      { title: '标签', dataIndex: 'label', render: (_, record) => safeText(record.label) },
      { title: '账号 ID', dataIndex: 'account_id', render: (_, record) => safeText(record.account_id) },
      { title: '排除原因', dataIndex: 'excluded_reason', render: (_, record) => safeText(record.excluded_reason) },
      {
        title: '状态',
        dataIndex: 'status',
        render: (_, record) =>
          safeText(
            [
              record.status,
              record.last_classification,
              record.last_raw_code,
              Number(record.cooldown_remaining || 0) > 0
                ? `cooldown ${record.cooldown_remaining}s`
                : '',
            ]
              .filter(Boolean)
              .join(' | '),
          ),
      },
      { title: '来源文件', dataIndex: 'source', render: (_, record) => safeText(record.source) },
    ],
    [],
  );

  const getErrorMessage = (error, fallback) =>
    error?.response?.data?.message ||
    error?.response?.data?.error ||
    error?.message ||
    fallback;

  const fetchRuntimeState = async () => {
    setLoading(true);
    try {
      const [healthRes, settingsRes, accountsRes, runtimeRes, modelsRes, configRes] =
        await Promise.all([
          API.get('/api/chatcore/admin/health', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/settings', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/accounts', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/runtime_candidates', {
            skipErrorHandler: true,
          }),
          API.get('/api/chatcore/admin/models', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/config', { skipErrorHandler: true }),
        ]);

      setHealth(healthRes.data || null);
      setSettings({
        ...EMPTY_SETTINGS,
        ...(settingsRes.data?.settings || {}),
      });
      setSettingsPath(settingsRes.data?.settingsPath || '');
      setAccounts(
        Array.isArray(accountsRes.data?.accounts) ? accountsRes.data.accounts : [],
      );
      setRuntimeCandidates({
        count: Number(runtimeRes.data?.count || 0),
        rawCount: Number(runtimeRes.data?.rawCount || 0),
        candidates: Array.isArray(runtimeRes.data?.candidates)
          ? runtimeRes.data.candidates
          : [],
        excluded: Array.isArray(runtimeRes.data?.excluded)
          ? runtimeRes.data.excluded
          : [],
      });
      setModels(Array.isArray(modelsRes.data?.ids) ? modelsRes.data.ids : []);
      setConfigText(configRes.data?.activeConfig || configRes.data?.localConfig || '');
    } catch (error) {
      showError(getErrorMessage(error, '读取内嵌 chat 状态失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuntimeState();
  }, []);

  const handleSettingChange = (key, value) => {
    setSettings((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    try {
      const res = await API.post('/api/chatcore/admin/settings', settings, {
        skipErrorHandler: true,
      });
      setSettings({
        ...EMPTY_SETTINGS,
        ...(res.data?.settings || settings),
      });
      setSettingsPath(res.data?.settingsPath || settingsPath);
      showSuccess('ChatCore 参数已保存');
      await fetchRuntimeState();
    } catch (error) {
      showError(getErrorMessage(error, 'ChatCore 参数保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const handleUploadAuths = async () => {
    if (!uploadFileList.length) {
      showWarning('请先选择一个或多个 auth.json');
      return;
    }

    const formData = new FormData();
    formData.append('replace', settings.uploadReplaceDefault ? '1' : '0');

    uploadFileList.forEach((item, index) => {
      const fileObj = item.fileInstance;
      if (fileObj) {
        formData.append(
          'files',
          fileObj,
          fileObj.name || item.name || `auth-${index + 1}.json`,
        );
      }
    });

    setUploading(true);
    try {
      const res = await API.post('/api/chatcore/admin/upload_auths', formData, {
        skipErrorHandler: true,
      });
      setUploadFileList([]);
      showSuccess(`已上传 ${res.data?.uploaded || 0} 个 auth.json`);
      await fetchRuntimeState();
    } catch (error) {
      showError(getErrorMessage(error, 'auth.json 上传失败'));
    } finally {
      setUploading(false);
    }
  };

  const handleSweepInvalidAuths = async () => {
    setSweeping(true);
    try {
      const res = await API.post(
        '/api/chatcore/admin/sweep_invalid_auths',
        {},
        { skipErrorHandler: true },
      );
      showSuccess(`已扫描 ${res.data?.scanned || 0} 个凭证，移除 ${res.data?.removed || 0} 个无效凭证`);
      await fetchRuntimeState();
    } catch (error) {
      showError(getErrorMessage(error, '无效账号清理失败'));
    } finally {
      setSweeping(false);
    }
  };

  const metric = (title, value, subtext) => (
    <div style={cardStyle}>
      <Text strong>{title}</Text>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 8 }}>
        {safeText(value)}
      </div>
      {subtext ? (
        <Text type='tertiary' size='small'>
          {subtext}
        </Text>
      ) : null}
    </div>
  );

  const controlBlock = (label, node) => (
    <div style={{ marginBottom: 12 }}>
      <Text>{label}</Text>
      <div style={{ marginTop: 8 }}>{node}</div>
    </div>
  );

  return (
    <Spin spinning={loading}>
      <Form>
        <Form.Section text='ChatCore 单服务管理'>
          <Banner
            type='info'
            closeIcon={null}
            description='这里管理容器内嵌 chat 的账号池和运行参数。外部客户端继续只连接 II.fy，对内统一转到 ChatGPT backend。'
            style={{ marginBottom: 16 }}
          />

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col xs={24} sm={12} md={6}>
              {metric(
                '服务状态',
                health?.service?.status || 'unknown',
                health?.service?.raw || '等待检测',
              )}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric(
                '账号数量',
                runtimeCandidates.count || 0,
                `当前有效账号池 / 原始 ${runtimeCandidates.rawCount || 0}`,
              )}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric('模型数量', health?.models?.count || 0, '完整 chat 暴露模型')}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric('设置文件', settingsPath || '-', 'dashboard settings path')}
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} lg={16}>
              <div style={cardStyle}>
                <Text strong>运行参数</Text>
                <Row gutter={16} style={{ marginTop: 12 }}>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '轮询策略',
                      <Select
                        value={settings.routingStrategy || 'round-robin'}
                        optionList={selectOptions(['round-robin', 'random', 'first'])}
                        onChange={(value) => handleSettingChange('routingStrategy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '请求重试',
                      <Input
                        value={String(settings.requestRetry ?? 0)}
                        onChange={(value) => handleSettingChange('requestRetry', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '最大重试间隔（秒）',
                      <Input
                        value={String(settings.maxRetryInterval ?? 5)}
                        onChange={(value) => handleSettingChange('maxRetryInterval', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Performance Mode',
                      <Select
                        value={settings.serviceTier ?? ''}
                        optionList={selectOptions([
                          { label: '默认 / 不透传', value: '' },
                          { label: 'priority', value: 'priority' },
                          { label: 'flex', value: 'flex' },
                        ])}
                        onChange={(value) => handleSettingChange('serviceTier', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Effort',
                      <Select
                        value={settings.reasoningEffort || 'minimal'}
                        optionList={selectOptions(['minimal', 'low', 'medium', 'high', 'xhigh'])}
                        onChange={(value) => handleSettingChange('reasoningEffort', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Summary',
                      <Select
                        value={settings.reasoningSummary || 'auto'}
                        optionList={selectOptions(['auto', 'concise', 'detailed', 'none'])}
                        onChange={(value) => handleSettingChange('reasoningSummary', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Compat',
                      <Select
                        value={settings.reasoningCompat || 'current'}
                        optionList={selectOptions(['legacy', 'o3', 'current'])}
                        onChange={(value) => handleSettingChange('reasoningCompat', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'HTTP_PROXY',
                      <Input
                        value={settings.httpProxy || ''}
                        onChange={(value) => handleSettingChange('httpProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'HTTPS_PROXY',
                      <Input
                        value={settings.httpsProxy || ''}
                        onChange={(value) => handleSettingChange('httpsProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'ALL_PROXY',
                      <Input
                        value={settings.allProxy || ''}
                        onChange={(value) => handleSettingChange('allProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'NO_PROXY',
                      <Input
                        value={settings.noProxy || ''}
                        onChange={(value) => handleSettingChange('noProxy', value)}
                      />,
                    )}
                  </Col>
                </Row>

                <Row gutter={16} style={{ marginTop: 8 }}>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>暴露推理模型</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.exposeReasoningModels)}
                          onChange={(value) =>
                            handleSettingChange('exposeReasoningModels', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>Verbose</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.verbose)}
                          onChange={(value) => handleSettingChange('verbose', value)}
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>Verbose Obfuscation</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.verboseObfuscation)}
                          onChange={(value) =>
                            handleSettingChange('verboseObfuscation', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>上传时替换现有账号池</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.uploadReplaceDefault)}
                          onChange={(value) =>
                            handleSettingChange('uploadReplaceDefault', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                </Row>

                <Button type='primary' onClick={handleSaveSettings} loading={saving}>
                  保存 chat 参数
                </Button>
              </div>
            </Col>

            <Col xs={24} lg={8}>
              <div style={cardStyle}>
                <Text strong>上传 auth.json</Text>
                <Form.Upload
                  field='chatmock_auth_files'
                  accept='.json'
                  draggable
                  multiple
                  uploadTrigger='custom'
                  beforeUpload={() => false}
                  fileList={uploadFileList}
                  onChange={({ fileList }) => setUploadFileList(fileList || [])}
                  dragMainText='点击或拖拽 auth.json 到这里'
                  dragSubText='支持一次上传多个账号文件'
                  style={{ marginTop: 12 }}
                />
                <Text type='tertiary' size='small'>
                  当前模式：
                  {settings.uploadReplaceDefault ? '替换现有账号池' : '追加到现有账号池'}
                </Text>
                <div style={{ marginTop: 12 }}>
                  <Button type='primary' onClick={handleUploadAuths} loading={uploading}>
                    上传 auth.json
                  </Button>
                  <Button
                    style={{ marginLeft: 8 }}
                    onClick={handleSweepInvalidAuths}
                    loading={sweeping}
                  >
                    清理已判定无效账号
                  </Button>
                </div>
              </div>
            </Col>
          </Row>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>账号列表</Text>
            <Table
              style={{ marginTop: 12 }}
              rowKey={(record, index) => `${record.label || 'acc'}-${index}`}
              dataSource={accounts}
              columns={accountColumns}
              pagination={false}
              empty='暂无账号'
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>当前运行候选账号</Text>
            <Text type='tertiary' size='small'>
              当前真正会被强成功链路尝试的账号：{runtimeCandidates.count || 0} / {runtimeCandidates.rawCount || 0}
            </Text>
            <Table
              style={{ marginTop: 12 }}
              rowKey={(record, index) => `${record.label || 'runtime'}-${index}`}
              dataSource={runtimeCandidates.candidates}
              columns={runtimeCandidateColumns}
              pagination={false}
              empty='暂无运行候选账号'
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>被排除账号</Text>
            <Text type='tertiary' size='small'>
              这些账号当前不在 runtime 候选池里。
            </Text>
            <Table
              style={{ marginTop: 12 }}
              rowKey={(record, index) => `${record.label || 'excluded'}-${index}`}
              dataSource={runtimeCandidates.excluded}
              columns={excludedCandidateColumns}
              pagination={false}
              empty='暂无被排除账号'
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>当前暴露模型</Text>
            <TextArea
              autosize={{ minRows: 4, maxRows: 10 }}
              value={models.join(', ')}
              readOnly
              style={{ marginTop: 12 }}
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>当前生效配置</Text>
            <TextArea
              autosize={{ minRows: 10, maxRows: 18 }}
              value={configText}
              readOnly
              style={{ marginTop: 12 }}
            />
          </div>
        </Form.Section>
      </Form>
    </Spin>
  );
}
