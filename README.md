# 个人动漫媒体库 Release 管理器

从多个 Torrent Release 中找到符合自己媒体规格要求的版本，并提交到 qBittorrent 下载。

设计文档：[docs/design-nekobt-bangumi.md](docs/design-nekobt-bangumi.md)

---

## 当前状态

MVP 已跑通的主链路：

```text
中文作品名
    ↓
nekoBT 媒体搜索（中文可命中）
    ↓
media_id
    ↓
Release 检索 → 标准化 → 筛选 → 排序
    ↓
用户选择
    ↓
获取 .torrent
    ↓
qBittorrent WebAPI
```

已验证：中文检索、media_id 解析、Release 检索与解析、筛选排序、`.torrent` 获取。

未验证：qBittorrent 的实际提交（需要先启用 WebUI），见「待办」。

---

## 安装

```powershell
pip install -r requirements.txt
```

Python 3.9+ 可用（代码已用 `from __future__ import annotations` 兼容 3.9）。

## 配置

复制 `.env.example` 为 `.env` 并填写：

```text
QBIT_URL=http://127.0.0.1:8080
QBIT_USERNAME=admin
QBIT_PASSWORD=你的密码
QBIT_SAVEPATH=D:\Anime\library
QBIT_CATEGORY=anime
QBIT_TAGS=nekobt
```

nekoBT 的公开检索**不需要 API Key**；只有非公开种子才需要 `NEKOBT_API_KEY`。

`python-dotenv` 是可选依赖：装了就读 `.env`，没装也有内置的最小解析器兜底。

## 使用

```powershell
# 自检：nekoBT 连通性 + qBittorrent WebUI
python -m src.main doctor

# 完整流程（交互式选择作品与 Release，最后确认下载）
python -m src.main search "葬送的芙莉莲"

# 只检索不下载
python -m src.main search "葬送的芙莉莲" --no-download

# 带上媒体规格偏好（可重复传，顺序即优先级）
python -m src.main search "葬送的芙莉莲" `
    --resolution 1080p --source BDRip --codec HEVC --sub-lang zh-hans

# 只看合集
python -m src.main search "葬送的芙莉莲" --batch

# 服务端直接按字幕语言过滤（对应 nekoBT 的 sub_lang 参数）
python -m src.main search "葬送的芙莉莲" --sub-lang-filter zh-hans
```

## 测试

```powershell
python -m pytest
```

全部测试离线运行，用假会话替代 HTTP，不依赖网络。

---

## 目录结构

```text
src/
├── main.py                 CLI 入口
├── config.py               .env 读取与校验
├── storage.py              SQLite 映射表 / 下载历史 / JSON 缓存
├── nekobt/
│   ├── client.py           HTTP 客户端（媒体检索、Torrent 检索、.torrent 下载）
│   └── media.py            中文名 → media_id 解析（含季数后缀剥离）
├── release/
│   ├── models.py           AnimeMetadata / MediaMapping / Release / DownloadTask
│   ├── parser.py           标题解析（resolution / source / codec / episode / batch）
│   └── matcher.py          筛选（matched / unknown / excluded）与可解释排序
├── qbittorrent/
│   └── client.py           WebAPI v2（登录 / 添加 / 查询 / 停止 / 删除）
└── bangumi/
    └── client.py           可选增强，不可用时自动跳过
tests/                      46 个离线用例
```

---

## 实现时踩到的坑

这些都在设计文档附录 A 里，代码里也做了防护，改代码时别绕过去：

1. **nekoBT 搜索参数是 `query`，不是 `q`。** 写错不会报错，只会静默忽略搜索条件，返回最新 50 条。测试 `test_test7_search_param_is_query_not_q` 守着这条。
2. **中文只能搜媒体，不能搜 Torrent。** Torrent 标题是罗马字，中文必须走 `media/search` 拿 `media_id`。
3. **带季数后缀的中文查询会命中错误作品。** `无职转生 第二季` 会命中 `Biaoren`，所以 `normalize_query()` 会先剥离「第 N 季 / Season N / SN」后缀再检索。
4. **`video_codec` 是数字枚举**（1=H.264，2=HEVC，3=AV1），传字符串会拿到 500。
5. **`sub_lang` 才是字幕语言参数名**，不是 `subtitle_language`。
6. **`video_type` 语义未明**（实测 3/7/8/9/12/13/15 混杂，且是精确匹配而非位运算），所以来源筛选一律走标题解析后的客户端过滤，API 字段只作原始信息保留。
7. **`resolution` / `source` 不在 API 返回里**，必须从标题解析。
8. **本机 pytest 会被 anyio 插件卡死**（anyio 3.5 + pytest 7.1.2 不兼容），`pytest.ini` 里已用 `-p no:anyio` 禁掉。

## 待办

- [ ] **启用 qBittorrent WebUI**（工具 → 选项 → Web UI），然后跑 `python -m src.main doctor` 验证 `webapiVersion` 能取到
- [ ] 用真实种子验证「获取 `.torrent` → 提交 qBittorrent → 任务出现在客户端」
- [ ] nekoBT 非公开种子（`private_magnet`）的 API Key 形式尚未验证
- [ ] Bangumi 在当前网络不可达，相关代码路径未实测
