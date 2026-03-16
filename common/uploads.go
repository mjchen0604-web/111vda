package common

import (
	"os"
	"path/filepath"
)

const (
	uploadsDirName      = "uploads"
	noticeUploadsSubdir = "notices"
)

func GetUploadsRootDir() string {
	return filepath.Join(".", uploadsDirName)
}

func GetNoticeUploadsDir() string {
	return filepath.Join(GetUploadsRootDir(), noticeUploadsSubdir)
}

func EnsureNoticeUploadsDir() error {
	return os.MkdirAll(GetNoticeUploadsDir(), 0o755)
}
