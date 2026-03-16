package controller

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/QuantumNous/new-api/common"
	"github.com/QuantumNous/new-api/setting/system_setting"
	"github.com/gin-gonic/gin"
)

var noticeImageMimeTypes = map[string]string{
	"image/jpeg": ".jpg",
	"image/png":  ".png",
	"image/gif":  ".gif",
	"image/webp": ".webp",
}

func UploadNoticeImage(c *gin.Context) {
	fileHeader, err := c.FormFile("file")
	if err != nil {
		common.ApiErrorMsg(c, "请先选择图片")
		return
	}
	if fileHeader.Size <= 0 {
		common.ApiErrorMsg(c, "图片为空")
		return
	}
	if fileHeader.Size > 10*1024*1024 {
		common.ApiErrorMsg(c, "图片不能超过 10MB")
		return
	}

	src, err := fileHeader.Open()
	if err != nil {
		common.ApiErrorMsg(c, "读取图片失败")
		return
	}
	defer src.Close()

	sniff := make([]byte, 512)
	n, readErr := src.Read(sniff)
	if readErr != nil && readErr != io.EOF {
		common.ApiErrorMsg(c, "识别图片类型失败")
		return
	}

	ext, ok := noticeImageMimeTypes[http.DetectContentType(sniff[:n])]
	if !ok {
		common.ApiErrorMsg(c, "仅支持 JPG、PNG、GIF、WebP 图片")
		return
	}

	if err := common.EnsureNoticeUploadsDir(); err != nil {
		common.ApiErrorMsg(c, "创建图片目录失败")
		return
	}

	if _, err := src.Seek(0, io.SeekStart); err != nil {
		common.ApiErrorMsg(c, "重置图片流失败")
		return
	}

	filename := fmt.Sprintf("%s-%s%s", time.Now().Format("20060102150405"), common.GetRandomString(8), ext)
	dstPath := filepath.Join(common.GetNoticeUploadsDir(), filename)
	dst, err := os.Create(dstPath)
	if err != nil {
		common.ApiErrorMsg(c, "保存图片失败")
		return
	}
	defer dst.Close()

	if _, err := io.Copy(dst, src); err != nil {
		common.ApiErrorMsg(c, "写入图片失败")
		return
	}

	relativeURL := "/uploads/notices/" + filename
	absoluteURL := relativeURL
	if base := strings.TrimSpace(system_setting.ServerAddress); base != "" {
		absoluteURL = strings.TrimRight(base, "/") + relativeURL
	}

	common.ApiSuccess(c, gin.H{
		"url":          relativeURL,
		"absolute_url": absoluteURL,
		"markdown":     fmt.Sprintf("![](%s)", relativeURL),
		"html":         fmt.Sprintf(`<img src="%s" alt="notice image" />`, relativeURL),
	})
}
