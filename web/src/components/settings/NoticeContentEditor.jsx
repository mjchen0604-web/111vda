import React, { useEffect, useMemo, useRef } from 'react';
import { Col, Row, TextArea, Typography } from '@douyinfe/semi-ui';
import { marked } from 'marked';
import NoticeImageUploader from './NoticeImageUploader';

const { Text } = Typography;

const escapeRegExp = (value) =>
  String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const escapeHtmlAttr = (value) =>
  String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

const parseTranslate = (styleText) => {
  const match = String(styleText || '').match(
    /translate\(\s*(-?\d+(?:\.\d+)?)px(?:,\s*|\s+)(-?\d+(?:\.\d+)?)px\s*\)/i,
  );
  if (!match) return { x: 0, y: 0 };
  return {
    x: Number(match[1] || 0),
    y: Number(match[2] || 0),
  };
};

const buildImageStyle = (styleText, x, y) => {
  const styleMap = new Map();
  String(styleText || '')
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean)
    .forEach((item) => {
      const [key, ...rest] = item.split(':');
      if (!key || rest.length === 0) return;
      styleMap.set(key.trim().toLowerCase(), rest.join(':').trim());
    });
  styleMap.set('max-width', '100%');
  styleMap.set('height', 'auto');
  styleMap.set('display', 'block');
  if (x === 0 && y === 0) {
    styleMap.delete('transform');
  } else {
    styleMap.set('transform', `translate(${Math.round(x)}px, ${Math.round(y)}px)`);
  }
  return Array.from(styleMap.entries())
    .map(([key, value]) => `${key}: ${value}`)
    .join('; ');
};

const updateImagePositionInContent = (content, src, x, y) => {
  const normalizedSrc = String(src || '').trim();
  if (!normalizedSrc) return content;
  const escapedSrc = escapeRegExp(normalizedSrc);
  const normalizedStyle = buildImageStyle('', x, y);

  const imgRegex = new RegExp(
    `<img([^>]*?)src=(["'])${escapedSrc}\\2([^>]*)>`,
    'i',
  );
  if (imgRegex.test(content)) {
    return content.replace(imgRegex, (fullMatch, before, quote, after) => {
      const attrs = `${before || ''} ${after || ''}`;
      const altMatch = attrs.match(/alt=(["'])(.*?)\1/i);
      const styleMatch = attrs.match(/style=(["'])(.*?)\1/i);
      const alt = altMatch?.[2] || 'notice image';
      const nextStyle = buildImageStyle(styleMatch?.[2] || '', x, y);
      const cleanedAttrs = attrs
        .replace(/\s*alt=(["']).*?\1/gi, '')
        .replace(/\s*style=(["']).*?\1/gi, '')
        .trim();
      const extraAttrs = cleanedAttrs ? ` ${cleanedAttrs}` : '';
      return `<img src="${escapeHtmlAttr(normalizedSrc)}" alt="${escapeHtmlAttr(alt)}"${extraAttrs} style="${escapeHtmlAttr(nextStyle)}" />`;
    });
  }

  const markdownRegex = new RegExp(
    `!\\[([^\\]]*)\\]\\((?:<)?${escapedSrc}(?:>)?(?:\\s+["'][^"']*["'])?\\)`,
    'i',
  );
  if (markdownRegex.test(content)) {
    return content.replace(markdownRegex, (_, altText) => {
      return `<img src="${escapeHtmlAttr(normalizedSrc)}" alt="${escapeHtmlAttr(
        altText || 'notice image',
      )}" style="${escapeHtmlAttr(normalizedStyle)}" />`;
    });
  }

  return `${content}\n\n<img src="${escapeHtmlAttr(normalizedSrc)}" alt="notice image" style="${escapeHtmlAttr(normalizedStyle)}" />`;
};

const NoticeContentEditor = ({
  t,
  value,
  onChange,
  placeholder,
  rows = 10,
  maxCount,
  previewTitle,
}) => {
  const previewRef = useRef(null);
  const previewHtml = useMemo(() => marked.parse(value || ''), [value]);

  useEffect(() => {
    const container = previewRef.current;
    if (!container) return undefined;

    const cleanupFns = [];
    const images = Array.from(container.querySelectorAll('img'));
    images.forEach((img) => {
      img.style.cursor = 'move';
      img.style.maxWidth = img.style.maxWidth || '100%';
      img.style.height = img.style.height || 'auto';
      img.style.display = img.style.display || 'block';

      const handleMouseDown = (event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        const startX = event.clientX;
        const startY = event.clientY;
        const { x: originX, y: originY } = parseTranslate(img.style.transform);

        const handleMouseMove = (moveEvent) => {
          const nextX = originX + (moveEvent.clientX - startX);
          const nextY = originY + (moveEvent.clientY - startY);
          img.style.transform = `translate(${Math.round(nextX)}px, ${Math.round(nextY)}px)`;
        };

        const handleMouseUp = (upEvent) => {
          const nextX = originX + (upEvent.clientX - startX);
          const nextY = originY + (upEvent.clientY - startY);
          document.removeEventListener('mousemove', handleMouseMove);
          document.removeEventListener('mouseup', handleMouseUp);
          onChange?.(updateImagePositionInContent(value || '', img.getAttribute('src'), nextX, nextY));
        };

        document.addEventListener('mousemove', handleMouseMove);
        document.addEventListener('mouseup', handleMouseUp);
      };

      img.addEventListener('mousedown', handleMouseDown);
      cleanupFns.push(() => img.removeEventListener('mousedown', handleMouseDown));
    });

    return () => {
      cleanupFns.forEach((fn) => fn());
    };
  }, [value, previewHtml, onChange]);

  const handleInsert = (markdown, uploadData) => {
    const inserted = uploadData?.html || markdown;
    onChange?.(value ? `${value}\n\n${inserted}` : inserted);
  };

  return (
    <div style={{ marginBottom: 16 }}>
      <NoticeImageUploader t={t} onInsert={handleInsert} />
      <Row gutter={16}>
        <Col xs={24} md={12}>
          <Text
            type='secondary'
            size='small'
            style={{ display: 'block', marginBottom: 8 }}
          >
            {t('支持 Markdown 和 HTML；上传后的图片可在右侧预览中直接拖动调整位置。')}
          </Text>
          <TextArea
            value={value}
            placeholder={placeholder}
            maxCount={maxCount}
            rows={rows}
            autosize={rows ? undefined : { minRows: 8, maxRows: 18 }}
            style={{ width: '100%', fontFamily: 'JetBrains Mono, Consolas' }}
            onChange={onChange}
          />
        </Col>
        <Col xs={24} md={12}>
          <Text
            type='secondary'
            size='small'
            style={{ display: 'block', marginBottom: 8 }}
          >
            {previewTitle || t('首页同款预览')}
          </Text>
          <div
            ref={previewRef}
            className='notice-editor-preview notice-content-scroll max-h-[55vh] overflow-y-auto rounded-xl border p-3'
            dangerouslySetInnerHTML={{ __html: previewHtml || '' }}
          />
        </Col>
      </Row>
    </div>
  );
};

export default NoticeContentEditor;
