import React, { useRef, useState } from 'react';
import { Button, Space, Typography } from '@douyinfe/semi-ui';
import { API, copy, showError, showSuccess } from '../../helpers';

const { Text } = Typography;

const NoticeImageUploader = ({ t, onInsert }) => {
  const fileInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [lastUpload, setLastUpload] = useState(null);

  const handlePickFile = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const formData = new FormData();
    formData.append('file', file);
    setUploading(true);
    try {
      const res = await API.post('/api/option/notice_image', formData, {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });
      const { success, message, data } = res.data;
      if (!success) {
        showError(message || t('图片上传失败'));
        return;
      }

      setLastUpload(data);
      onInsert?.(data.markdown, data);
      showSuccess(t('图片已上传并插入'));
    } catch (error) {
      showError(
        error?.response?.data?.message || error.message || t('图片上传失败'),
      );
    } finally {
      setUploading(false);
      event.target.value = '';
    }
  };

  const handleCopyUrl = async () => {
    const text = lastUpload?.absolute_url || lastUpload?.url;
    if (!text) return;
    const ok = await copy(text);
    if (ok) {
      showSuccess(t('图片链接已复制'));
    }
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <input
        ref={fileInputRef}
        type='file'
        accept='image/png,image/jpeg,image/gif,image/webp'
        hidden
        onChange={handleFileChange}
      />
      <Space wrap>
        <Button onClick={handlePickFile} loading={uploading}>
          {t('上传公告图片')}
        </Button>
        {lastUpload?.url ? (
          <Button type='tertiary' theme='light' onClick={handleCopyUrl}>
            {t('复制图片链接')}
          </Button>
        ) : null}
      </Space>
      <Text
        type='secondary'
        size='small'
        style={{ display: 'block', marginTop: 8 }}
      >
        {t('支持 JPG、PNG、GIF、WebP；上传后会自动插入 Markdown 图片链接。')}
      </Text>
      {lastUpload?.url ? (
        <Text
          type='tertiary'
          size='small'
          style={{ display: 'block', marginTop: 4, wordBreak: 'break-all' }}
        >
          {lastUpload.url}
        </Text>
      ) : null}
    </div>
  );
};

export default NoticeImageUploader;
