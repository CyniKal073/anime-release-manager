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

已验证（端到端跑通）：中文检索、media_id 解析、Release 检索与解析、筛选排序、
`.torrent` 获取、提交 qBittorrent、指定保存目录、真实下载。

当前未验证：nekoBT 非公开种子需要 API Key 的路径；Bangumi 在当前网络不可达。

## V1 本地 Web UI

```powershell
.\run_web.ps1                      # 默认 http://127.0.0.1:8765
.\run_web.ps1 --port 9000          # 换端口
.\run_web.ps1 --no-access-log      # 只保留启动日志
```

也可以直接 `python -m src.api.app`（用脚本是为了固定 UTF-8 日志编码）。

界面提供：搜索框、作品候选（带相似度）、筛选栏（分辨率/来源/编码/字幕/合集/最少做种）、
排序切换、符合条件 / 字段未知 / 不符合 三态分页、逐条下载按钮（可选「添加后暂停」）、下载历史。

首页还会显示**本季连载**（来自 Bangumi 每日放送，按星期分组，点任意一部即可搜索），
作品候选与连载条目都带封面图。Bangumi 不可达时首页会给出提示并继续工作，不会报错。

筛选逻辑与 CLI 完全共用 `src/service.py`，唯一区别是偏好可以「存为默认偏好」写进 SQLite。

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
├── api/app.py              V1 Web API（FastAPI）
├── web/                    前端（原生 HTML/CSS/JS，无构建步骤）
├── service.py              CLI 与 Web 共用的业务编排层
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
tests/                      57 个离线用例
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
9. **8080 端口被 NI Application Web Server 占用**，它对 `/api/v2/*` 一律返回 404，所以 qBittorrent WebUI 改用了 8081。
10. **qBittorrent 的暂停/恢复接口名随版本变化**：4.6 只有 `pause`/`resume`，5.0+ 才是 `stop`/`start`；客户端做的是「新名优先、404 回退」。
11. **`nekobt.to` 的 A 记录可能被 DNS 污染**（实测被指向 `45.67.223.32`，该地址出具的证书不匹配主机名，报 `CertificateError: hostname ... doesn't match ...`），同一时刻 AAAA 记录正常。客户端已默认 **IPv6 优先**（`src/net.py`），主机名仍用于 SNI 与证书校验，安全性不变；连不上 IPv6 时自动回落到 IPv4。顺带一提，开启 VPN 后更容易触发这个现象——如果报这个错，先检查 VPN 的 DNS 设置。

## 待办

- [x] 启用 qBittorrent WebUI，`doctor` 自检通过（WebAPI 2.9.3 / 应用 v4.6.5.10）
- [x] 真实种子验证「获取 `.torrent` → 提交 qBittorrent → 任务出现在客户端 → 正常下载」
- [ ] nekoBT 非公开种子（`private_magnet`）的 API Key 形式尚未验证
- [ ] Bangumi 在当前网络不可达，相关代码路径未实测
