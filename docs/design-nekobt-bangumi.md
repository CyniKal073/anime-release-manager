# 个人动漫媒体库 Release 管理器

> 一个面向个人本地动漫媒体库的 Release 检索与下载工具。
>
> **核心目标：帮助用户从多个 Torrent Release 中找到符合自己媒体规格要求的版本，并将其下载到本地。**
>
> 项目不以在线播放为目标，也不是新的动漫资源站。

> **2026-10-05 修订**：下载客户端由 BitComet 改为 **qBittorrent（WebAPI v2）**；作品识别主链路修正为 **nekoBT 媒体搜索优先**，Bangumi 降级为可选增强；文末新增「附录 A：实测 API 事实」记录所有已验证的接口行为与坑。

---

## 1. 项目定位

### 1.1 我们真正要解决的问题

对于普通用户：

> “我想看《葬送的芙莉莲》。”

他们通常需要的是：

```text
搜索作品
    ↓
找到可以播放的资源
    ↓
在线观看
```

而本项目面向的是另一类需求：

```text
我想看《葬送的芙莉莲》
        ↓
我想要哪个 Release？
        ↓
BD / WEB-DL？
1080p / 2160p？
HEVC / H.264？
哪个 Release Group？
是否包含简体中文字幕？
文件大小是否合理？
        ↓
选择符合自己媒体库标准的版本
        ↓
下载到本地
```

因此，本项目管理的核心对象不是单纯的 **Anime**，而是：

> **Release**

---

## 2. 核心定义

### 2.1 Anime / Work

表示一部作品，例如：

```text
葬送的芙莉莲
Sousou no Frieren
Frieren: Beyond Journey's End
```

它解决的问题是：

> **“用户说的是哪部作品？”**

---

### 2.2 Release

表示某个具体的视频发布版本，例如：

```text
[Group A] Sousou no Frieren - 01
1080p
BDRip
HEVC
Japanese Audio
Chinese Subtitle
```

它解决的问题是：

> **“我要下载哪个版本？”**

同一部作品可以存在大量不同 Release：

```text
葬送的芙莉莲
│
├── 1080p BDRip HEVC
├── 1080p BDRip H.264
├── 1080p WEB-DL HEVC
├── 1080p WEB-DL H.264
├── 2160p BDRip HEVC
└── ...
```

本项目的核心价值就在于帮助用户从这些 Release 中进行选择。

---

## 3. 项目边界

### MVP 的边界

```text
用户输入作品
    ↓
作品识别
    ↓
Release 检索
    ↓
Release 标准化
    ↓
Release 筛选 / 排序
    ↓
用户选择
    ↓
Torrent 获取
    ↓
qBittorrent
    ↓
本地文件
```

**本地文件成功进入下载流程，即完成 MVP 的核心任务。**

---

### 明确不负责

```text
本地文件
    ↓
字幕匹配
    ↓
字幕对轴
    ↓
视频播放
    ↓
媒体库刮削
    ↓
Jellyfin / Emby
```

以上均不属于 MVP。

---

# 4. 外部服务职责

项目使用多个外部服务，但每个服务只负责自己擅长的事情。

## 4.1 Bangumi（中文检索的第一入口）

定位：

> **中文作品名识别与消歧。**

它是整条链路的第一步：把用户输入的中文名解析成一个确定的条目，再交给 nekoBT 去找资源。

### 为什么必须是它

nekoBT 的中文能力是「碰巧某个作品的 AniList synonym 或 TVDB 别名里有中文」，覆盖率随机，
而且是字符级模糊匹配、没有分词。同一批查询在两边的实测对比：

| 输入 | nekoBT 媒体搜索 | Bangumi |
| --- | --- | --- |
| 日常 | s4149 (1.0)，但带不出「日常系的异能战斗」 | 116 条，含日常系的异能战斗 |
| 败犬女主太多了 | m161 Under the Dog（**错**） | 464376 败犬女主太多了！（**对**） |
| 败北女主太多了（打错字） | m161（错） | 464376（**照样对**） |
| 水星领航员 | s1267 (1.0) | 531 / 750 / 1269第二季 / 1270第三季 |
| 葬送的芙莉莲 | s462 (0.5556，繁简差异) | 400602，第二季与魔法篇分开 |

差别不在数据量，而在索引结构：Bangumi 把**中文名当独立字段维护**（`name_cn`），
别名也参与索引，还有条目间的续集/前作关系；nekoBT 只有一堆混在一起的多语言标题。

### 桥接方式（实测 6/6 命中）

```text
中文输入
    ↓  Bangumi /v0/search/subjects
subject_id + name_cn + name（日文原名）
    ↓  用「日文原名」查 nekoBT media/search
media_id
    ↓
torrents/search?media_id=…
```

Bangumi 的 `name` 是日文原名，而 nekoBT 的索引里有 AniList 的 `native` 字段，
两边天然对得上，实测 similarity 全部是 1.0：

```text
葬送のフリーレン              → s462
負けヒロインが多すぎる！      → s2951
異能バトルは日常系のなかで    → s277
ARIA The ANIMATION           → s1267
```

所以 Bangumi 只负责「中文名 → 条目」这一步，Release 检索完全仍然走 nekoBT。

### 降级要求（硬性）

Bangumi 依赖网络可达性（实测：不开 VPN 时 `api.bgm.tv` 直接超时），因此必须有降级路径：

```text
本地映射缓存命中 → 直接用，不打网络
      ↓ 未命中
Bangumi 可用   → 中文检索 + 桥接
      ↓ 不可用 / 桥接失败
nekoBT 模糊搜索（附带置信度分档与查询变体）
```

降级不是「写在文档里」就算，实现上必须真的能在 Bangumi 超时时继续跑完流程。

### 接口与约束

* 搜索：`POST https://api.bgm.tv/v0/search/subjects`，body `{keyword, filter:{type:[2]}}`
* 详情：`GET https://api.bgm.tv/v0/subjects/{id}`
* **必须携带合规 `User-Agent`**（带项目地址），否则 403
* 有速率限制 → 本地映射缓存是必需项，不是优化项
* Bangumi 不负责寻找 Torrent

---

## 4.2 nekoBT

定位：

> **作品检索 + Release / Torrent 检索源。**

nekoBT 的 API 能力比最初预想的更强，且**公开检索接口无需 API Key**：

* 媒体（作品）搜索
* Torrent 搜索
* 按字幕语言、视频编码、视频类型、Batch、升级版本过滤
* 返回文件大小、做种数、字幕语言、编码、Magnet、InfoHash
* 提供 `.torrent` 文件下载接口
* 额外提供 Torznab 接口（`/api/torznab/api`），可被标准索引器客户端复用

### 关键实测结论：中文要搜「媒体」，不是搜「Torrent」

nekoBT 有两级搜索，行为完全不同：

```text
GET /api/v1/media/search?query=葬送的芙莉莲      ← 中文可命中 ✅
GET /api/v1/torrents/search?query=葬送的芙莉莲   ← 返回 0 条 ❌
GET /api/v1/torrents/search?query=Frieren        ← 可命中 ✅
```

原因：Torrent 标题是罗马字/英文，中文根本不参与索引；而**媒体库索引了 AniList 的 synonym，所以中文能命中**。

```text
葬送的芙莉莲
    ↓  media/search
s462 / Frieren: Beyond Journey's End
    ↓  torrents/search?media_id=s462
Release 列表
```

因此正确链路是：

> **中文输入 → 媒体搜索拿 media_id → 用 media_id 搜 Release**

这条链路中 Bangumi 完全不是必需环节。

三个实测坑，必须写进实现：

1. **参数名是 `query`，不是 `q`**。用 `q=` 会被静默忽略，接口照样返回数据，只是搜索条件没生效——这是最容易踩的坑。
2. **AniList 官方 API 反而不认中文**。`{Page{media(search:"葬送的芙莉莲")}}` 返回空数组，因为 AniList 的 `search` 不覆盖 synonym。中文必须交给 nekoBT 自己的媒体库解析。
3. **带季数后缀的中文查询会失败**。`无职转生 第二季` 命中错误作品（`Biaoren`），而 `无职转生` 命中正确（`s153`）。搜索前应剥离「第 N 季 / Season N」等后缀，季数在拿到 Release 之后再用 `SxxExx` / `media_episode_ids` 区分。

### 媒体搜索的相似度字段

媒体搜索结果带 `similarity` 字段，可直接用来过滤噪声：

```text
葬送的芙莉莲  → s462  0.5556   ← 命中
             → m641  0        ← 噪声

孤独摇滚      → s6    1.0      ← 命中
             → m779  0        ← 噪声
```

策略：取 `similarity` 最高者作为默认候选，其余按分数排序交给用户确认。

### 降级方案

```text
用户中文名
    ↓
nekoBT 媒体搜索（中文）
    ↓
候选作品（按 similarity 排序）
    ↓
用户人工确认
    ↓
媒体搜索失败时，退回罗马字 / 日文原名查询
```

---

## 4.3 qBittorrent

定位：

> **本地 Torrent 下载执行器。**

MVP 使用 **qBittorrent WebUI API（WebAPI v2）**，不再使用 BitComet。原因：

* 官方提供完整 HTTP API，可查询任务状态、进度、保存路径
* 支持 `category` / `tags`，便于后续做媒体库整理
* 添加任务时可直接指定 `savepath`，不依赖未公开的命令行参数
* 添加成功/失败可明确返回，整条链路可验证

本机环境（已确认）：

```text
F:\qBittorrentEE\qbittorrent_x64.exe   版本 v4.6.5.10（运行时实际使用的就是它）
F:\qBittorrentEE\qbittorrent.exe       版本 v5.1.0.11（同目录的另一个主程序，未被使用）
WebAPI 2.9.3 / WebUI 端口 8081
```

注意两个坑：

* 注册表卸载信息里写的是 5.0.4.10，那是残留记录，不要信；版本以二进制的 FileVersion 为准。
* 8080 端口被 `NIApplicationWebServer`（National Instruments 的 Embedded-http 服务）占用，
  它对 `/api/v2/*` 一律返回 404，因此 WebUI 必须改用 8081 等空闲端口。

调用链路：

```text
nekoBT
    ↓
获取 .torrent
    ↓
qBittorrent WebAPI
    ↓
指定 savepath
    ↓
开始下载
```

MVP 需要的最小接口集：

| 用途 | 接口 |
| --- | --- |
| 登录拿 `SID` | `POST /api/v2/auth/login` |
| 添加任务 | `POST /api/v2/torrents/add` |
| 查询任务 | `GET /api/v2/torrents/info` |
| 暂停 / 恢复 | `POST /api/v2/torrents/stop` / `POST /api/v2/torrents/start` |
| 删除任务 | `POST /api/v2/torrents/delete` |
| 版本自检 | `GET /api/v2/app/webapiVersion` |

`torrents/add` 使用 `multipart/form-data`，关键字段：

```text
torrents=<.torrent 文件二进制>     # 或 urls=<magnet / .torrent URL>
savepath=D:\Anime\...
category=anime
tags=nekobt
paused=false
```

注意事项：

* 必须先在 qBittorrent 里启用 WebUI，并设置端口 / 用户名 / 密码
* 登录成功后服务端返回 `SID` Cookie，后续请求都要带上
* 登录请求需要带 `Referer` 头（CSRF 保护），否则可能被拒
* WebUI 默认只监听本机，MVP 也只需要本机访问
* 5.0 起暂停/恢复的接口名是 `stop` / `start`（旧的 `pause` / `resume` 已更名）

---

# 5. MVP 核心工作流

## Step 1：输入作品

```text
请输入动漫名称：

> 葬送的芙莉莲
```

---

## Step 2：作品识别

```text
用户输入中文名
    ↓
本地映射缓存命中？ ── 是 ─→ 直接拿到 media_id
    ↓ 否
Bangumi 搜索（中文强项）
    ↓
用条目原名桥接到 nekoBT media_id
    ↓ 不可用 / 桥接失败
nekoBT 模糊搜索（带置信度分档）
    ↓
候选作品（标注来源与置信度）
```

例如：

```text
识别来源：Bangumi

[1] 败犬女主太多了！ / 2024
    media_id=s2951  桥接=1.0000  置信度=high
    原名=負けヒロインが多すぎる！

[2] 败犬女主太多了！第二季
    media_id=s2951  桥接=1.0000  置信度=high
```

用户确认：

```text
> 1
```

说明：

* 用户确认后把「输入名 → media_id」写入 `media_mapping`，下次同一输入直接命中缓存、不再打网络。
* 走 nekoBT 兜底时，候选按 similarity 分成高/低置信度两档展示——噪声和真候选不能混排。
* 无论走哪条路，都必须由用户确认，不能静默取第一条。

---

## Step 3：建立作品身份

从 nekoBT 媒体详情 `GET /api/v1/media/{media_id}` 获取（AniList 数据已内嵌）：

```text
nekoBT media_id      （如 s462）
anilist_id           （如 154587）
mal_id               （如 52991）
tvdbId / tmdbId / imdbId
中文名 / 原名 / Romaji / 英文名
aliases              （anilist.synonyms）
年份
类型
集数
```

例如：

```text
AnimeMetadata
├── nekobt_media_id
├── anilist_id
├── bgm_id            （可选，Bangumi 可用时才有）
├── title_cn
├── title_original
├── title_romaji
├── title_en
├── aliases
├── year
├── type
└── episode_count
```

---

## Step 4：建立 nekoBT 检索条件

主路径直接用 `media_id`，不依赖标题检索：

```text
media_id=s462
```

只有在 media_id 解析失败时，才退回关键字检索：

```text
Sousou no Frieren
Frieren
```

退回关键字时只用**罗马字 / 英文**，不要用中文——Torrent 索引不含中文。

---

# 6. Release 检索

nekoBT 按 `media_id` 返回多个 Torrent。

程序不直接展示原始 JSON，而是转换为统一的 `Release` 数据模型。

其中 `sub_lang` / `fsub_lang` / `video_codec` / `video_type` / `batch` / `seeders` 等字段由 API 直接给出，`resolution` / `source` / `group` 需要从标题解析。

典型结果：

```text
[1]
Group:       Group A
Episode:     01
Source:      BDRip
Resolution:  1080p
Video:       HEVC
Subtitle:    zh-hans
Size:        1.82 GB
Seeders:     35

[2]
Group:       Group B
Episode:     01
Source:      WEB-DL
Resolution:  1080p
Video:       H.264
Subtitle:    zh-hans
Size:        1.41 GB
Seeders:     82
```

---

# 7. Release 标准化

MVP 至少尝试解析以下字段：

| 字段            | 含义                   |
| ------------- | -------------------- |
| `title`       | 原始 Release 标题        |
| `group`       | Release Group        |
| `season`      | 季数                   |
| `episode`     | 集数                   |
| `source`      | BDRip / WEB-DL 等     |
| `resolution`  | 720p / 1080p / 2160p |
| `video_codec` | HEVC / H.264 / AV1 等 |
| `audio_codec` | 音频编码                 |
| `sub_lang`    | 字幕语言                 |
| `fsub_lang`   | 硬字幕语言                |
| `batch`       | 是否 Batch             |
| `filesize`    | 文件大小                 |
| `seeders`     | 做种数                  |
| `leechers`    | 下载数                  |
| `magnet`      | Magnet               |
| `torrent_id`  | nekoBT Torrent ID    |
| `infohash`    | Info Hash            |

---

## 7.1 解析原则

不要追求：

> 100% 正确解析所有 Release 标题。

而应该：

```text
能够可靠解析 → 保存
无法确定 → None / Unknown
```

例如：

```text
Resolution = 1080p
Codec = HEVC
Source = Unknown
```

也比错误判断成：

```text
Source = BDRip
```

更好。

---

# 8. Release 筛选

用户可以定义自己的媒体偏好。

MVP 只需要提供最基本的筛选：

### 分辨率

```text
720p
1080p
2160p
```

### 来源

```text
BDRip
WEB-DL
WEBRip
```

### 视频编码

```text
HEVC
H.264
AV1
```

### 字幕语言

```text
简体中文
繁体中文
英文
无字幕
```

nekoBT 已经提供 `zh-hans`、`zh-hant` 等语言字段（搜索参数名 `sub_lang`），因此字幕语言筛选可以直接建立在 API 数据上，而不需要自行猜测标题。

---

# 9. Release 排序

MVP 不实现复杂 AI 推荐。

只使用明确、可解释的规则。

例如：

```text
用户偏好：

1080p
BD > WEB-DL
HEVC > H.264
简体中文
```

排序：

```text
① 1080p / BDRip / HEVC / 简中
② 1080p / BDRip / H.264 / 简中
③ 1080p / WEB-DL / HEVC / 简中
④ 1080p / WEB-DL / H.264 / 简中
```

同时可以参考：

```text
Seeders
Filesize
Subtitle Level
```

但必须保留原始信息，让用户能够自己判断。

---

# 10. 用户确认

MVP 不自动替用户决定下载哪个 Release。

流程必须是：

```text
搜索
 ↓
展示
 ↓
筛选
 ↓
排序
 ↓
用户选择
 ↓
用户确认下载
```

例如：

```text
请选择 Release：

[1] [Group A]
    1080p / BDRip / HEVC / 简中
    1.82 GB / Seed 35

[2] [Group B]
    1080p / WEB-DL / HEVC / 简中
    1.41 GB / Seed 82

[3] [Group C]
    2160p / BDRip / HEVC / 简中
    7.82 GB / Seed 18

> 1
```

---

# 11. Torrent 获取

用户确认后：

```text
Release
    ↓
nekoBT
    ↓
.torrent
```

优先使用 `.torrent` 文件，而不是直接依赖 Magnet。

原因：

1. qBittorrent WebAPI 可以直接接收 `.torrent` 文件或 URL。
2. 可以明确指定 `savepath`。
3. nekoBT API key 获取的 Torrent 可以包含私有 announce 信息。
4. 对下载客户端更加稳定。

实测接口：

```text
GET /api/v1/torrents/{torrent_id}/download?public=true
→ Content-Type: application/x-bittorrent
→ Content-Disposition: attachment; filename="....torrent"
```

`public=true` 用于公开种子；非公开种子需要 nekoBT API Key，否则会失败。

---

# 12. qBittorrent 下载

最终：

```text
Torrent 文件
    ↓
qBittorrent WebAPI
    ↓
指定 savepath
```

例如：

```text
D:\Anime\
```

执行（概念示意）：

```http
POST /api/v2/torrents/add
Content-Type: multipart/form-data

torrents   = <frieren.torrent 二进制>
savepath   = D:\Anime\Frieren
category   = anime
tags       = nekobt
paused     = false
```

MVP 至此完成。

---

# 13. 核心数据模型

MVP 不需要复杂数据库设计，但领域对象必须明确。

## 13.1 AnimeMetadata

```python
class AnimeMetadata:
    bgm_id: int

    title_cn: str
    title_original: str
    title_romaji: str | None
    title_en: str | None

    aliases: list[str]

    year: int | None
    type: str | None
    episode_count: int | None

    anilist_id: int | None
```

---

## 13.2 MediaMapping

表示：

> 用户输入的中文名与 nekoBT / AniList 媒体的对应关系。

```python
class MediaMapping:
    input_title: str            # 用户原始输入
    nekobt_media_id: str
    anilist_id: int | None
    bgm_id: int | None          # 可选

    confidence: float
    confirmed: bool
```

这是一个非常重要的长期资产。

第一次：

```text
中文名
 ↓
nekoBT 媒体搜索
 ↓
用户确认
 ↓
media_id
```

确认映射。

以后：

```text
中文名
 ↓
直接得到 nekoBT media_id
```

无需重复猜测。

---

## 13.3 Release

```python
class Release:
    torrent_id: str
    title: str

    group: str | None
    season: int | None
    episode: str | None

    source: str | None
    resolution: str | None

    video_codec: str | None
    audio_codec: str | None

    sub_lang: list[str]
    fsub_lang: list[str]

    batch: bool | None
    upgraded: bool | None

    filesize: int | None
    seeders: int | None
    leechers: int | None

    infohash: str | None
    magnet: str | None
```

---

## 13.4 DownloadTask

```python
class DownloadTask:
    release_id: str
    client: str                    # "qbittorrent"

    torrent_path: str
    save_path: str
    torrent_hash: str | None       # qBittorrent 侧的任务哈希，用于查询状态

    status: str
```

MVP 状态只需要：

```text
queued
started
failed
```

实现提示：本地 Python 为 3.9，`str | None` 这类写法需要 `from __future__ import annotations`，否则应改用 `Optional[str]`；若后续升级到 3.10+ 则无此限制。

---

# 14. MVP 的最小项目结构

第一版不要直接上 FastAPI、PySide6、Tauri。

先用 CLI 验证核心链路。

```text
anime-release-manager/
│
├── src/
│   ├── bangumi/            # 可选，网络可用时才启用
│   │   └── client.py
│   │
│   ├── nekobt/
│   │   ├── client.py
│   │   └── media.py       # 媒体搜索 / media_id 解析
│   │
│   ├── release/
│   │   ├── models.py      # AnimeMetadata / MediaMapping / Release
│   │   ├── parser.py      # 标题解析（resolution / source 等）
│   │   └── matcher.py     # 筛选与排序
│   │
│   ├── qbittorrent/
│   │   └── client.py
│   │
│   ├── config.py          # .env 读取与校验
│   ├── storage.py         # SQLite 缓存
│   └── main.py
│
├── tests/
│
├── data/
│
├── config/
│
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```

---

# 15. 模块职责

## BangumiClient（可选）

只负责：

```text
搜索 Subject
获取 Subject
解析元数据
```

不负责 Torrent。

定位：可选增强。Bangumi 不可达时必须能被跳过，不能阻塞主流程。

---

## NekoBTClient

只负责：

```text
媒体搜索
媒体解析
Torrent 搜索
Torrent 详情
Torrent 文件获取
```

不负责用户界面。

---

## ReleaseParser

只负责：

```text
原始数据
 ↓
标准 Release
```

---

## ReleaseMatcher

只负责：

```text
Release
+
用户偏好
 ↓
筛选
 ↓
排序
```

---

## QBittorrentClient

只负责：

```text
Torrent
 ↓
qBittorrent WebAPI
```

职责：

```text
登录并维持 SID
添加任务（savepath / category / tags）
查询任务状态
暂停 / 恢复 / 删除
```

不参与作品搜索，也不解析 Release 标题。

---

# 16. MVP 严格不做的功能

## ❌ 在线播放

不做：

* Web Player
* HLS
* M3U8
* 视频流
* 在线转码
* CDN

---

## ❌ 自建资源站

不做：

* 视频托管
* Torrent 托管
* 用户上传
* 公共 Torrent 数据库
* Tracker

nekoBT 只是外部资源检索源。

---

## ❌ 字幕系统

MVP 不做：

* 自动下载字幕
* 字幕翻译
* OCR
* 字幕对轴
* 自动修复时间轴
* 字幕与 Release 自动匹配

这些未来可以独立成为：

```text
SubtitleManager
```

---

## ❌ 媒体库整理

不做：

* 自动重命名
* 自动移动文件
* Season 文件夹生成
* NFO
* 海报
* Fanart
* 媒体库刮削

---

## ❌ 播放器

不做：

* MPV
* VLC
* MPC-HC
* 自研播放器

用户下载完成后使用自己喜欢的播放器。

---

## ❌ Jellyfin / Emby

不集成媒体服务器。

未来可以让本项目产生的本地媒体库被 Jellyfin / Emby 等外部工具管理。

---

## ❌ 自动追番

不做：

```text
订阅作品
 ↓
定期查询
 ↓
自动下载新集
```

这是未来功能。

---

## ❌ 自动下载

默认不允许：

```text
搜索到资源
 ↓
自动下载
```

必须经过：

```text
用户查看
 ↓
用户选择
 ↓
用户确认
```

---

## ❌ BitComet / 多下载器

MVP 不同时支持多个下载客户端。

第一版只支持：

> **qBittorrent（WebAPI v2）**

BitComet 不在 MVP 范围内。未来如果需要，再抽象：

```text
Downloader
├── QBittorrent
└── BitComet
```

---

# 17. 数据持久化

SQLite 可以加入 MVP，但只保存真正有价值的数据。

核心数据：

```text
works
media_mapping
search_cache
downloads
settings
```

其中最有价值的是：

```text
works
    ↓
nekoBT Media ID / AniList ID

media_mapping
    ↓
中文名 ↔ nekoBT Media ID

search_cache
    ↓
减少重复 API 请求

downloads
    ↓
记录下载历史
```

尤其是 `media_mapping`：

```text
中文名
    ↕
nekoBT Media ID
```

一旦用户确认过，后续就可以直接使用。

修订后 `works` 的主键不再是 Bangumi Subject，而是 `nekobt_media_id`；`bgm_id` 只作为可选外键。

---

# 18. 缓存与 API 限制

nekoBT API 存在请求限制，因此不能设计成：

```text
每次点击
    ↓
重新请求所有 API
```

应该：

```text
API
 ↓
本地缓存
 ↓
用户操作
```

需要考虑：

* HTTP 429
* `Retry-After`
* 5xx
* Cloudflare
* 网络超时
* API key 无效
* 返回字段缺失

nekoBT 参数层面的坑（实测）：

* 搜索参数是 `query`；写成 `q` 不会报错，只会静默失效
* `limit` / `offset` 控制分页，`per_page` 无效
* `video_codec` 是数字枚举（1 = H.264，2 = HEVC，3 = AV1），传字符串会返回 500
* 字幕语言参数是 `sub_lang`，不是 `subtitle_language`

Bangumi 同样需要：

* 合规 User-Agent
* 请求失败重试
* 本地缓存
* 宽松解析

但如上所述，Bangumi 不可用时不应阻塞主流程。

qBittorrent 是本机服务，失败模式不同：

* WebUI 未启用 / 端口未监听
* 登录失败（用户名密码错误）
* 缺少 `Referer` 头导致 CSRF 拒绝
* `savepath` 所在磁盘不存在或不可写
* WebAPI 版本与 5.0 不一致时接口名差异（`pause` vs `stop`）

这些都应该在启动时做一次自检（`GET /api/v2/app/webapiVersion`），失败时给出明确提示，而不是等到下载阶段才报错。

---

# 19. 凭证与 API Key 安全

nekoBT API key 与 qBittorrent WebUI 账号都是用户自己的凭证。

因此：

```text
.env
```

或本地配置存储：

```text
NEKOBT_API_KEY=...

QBIT_URL=http://127.0.0.1:8080
QBIT_USERNAME=...
QBIT_PASSWORD=...

QBIT_SAVEPATH=D:\Anime
QBIT_CATEGORY=anime
```

禁止：

```text
写死在源码
提交 Git
写入日志
上传 GitHub
打印到终端回显
```

`.gitignore` 必须包含：

```text
.env
data/
```

---

# 20. MVP 验收标准

MVP 不以“代码写完”为验收，而以完整用户流程是否跑通为准。

## 验收场景

输入：

```text
葬送的芙莉莲
```

必须能够完成：

```text
① 搜索作品
      ↓
② 用户确认作品
      ↓
③ 获取作品元数据
      ↓
④ 建立 / 获取 nekoBT Media ID
      ↓
⑤ 搜索 Release
      ↓
⑥ 显示标准化 Release
      ↓
⑦ 按用户偏好筛选
      ↓
⑧ 用户选择 Release
      ↓
⑨ 获取 .torrent
      ↓
⑩ 调用 qBittorrent WebAPI
      ↓
⑪ 指定本地目录
      ↓
⑫ qBittorrent 成功创建下载任务
```

---

# 21. MVP 必须通过的具体测试

### Test 1：中文作品搜索

输入：

```text
葬送的芙莉莲
```

必须命中：

```text
s462 / Frieren: Beyond Journey's End
```

且 `similarity` 最高者为该作品（实测 0.5556）。

---

### Test 2：存在多部相似作品

输入：

```text
无职转生
```

必须让用户区分：

```text
第一季
第二季
OVA
其他相关作品
```

不能静默选择错误作品。

同时验证带季数后缀的情况：

```text
无职转生 第二季
```

程序必须先剥离「第 N 季 / Season N」后缀再检索。实测把整串丢给 nekoBT 会命中错误作品（`Biaoren`），而 `无职转生` 能正确命中 `s153`。

---

### Test 3：中文资源筛选

对于支持中文字幕的 Release：

```text
sub_lang = zh-hans
```

应该能够筛选出来。

注意参数名是 `sub_lang`；用 `subtitle_language` 不会报错，但筛选条件会被忽略。

---

### Test 4：媒体规格筛选

设置：

```text
1080p
BDRip
HEVC
```

能够排除明显不符合条件的 Release。

---

### Test 5：无法解析字段

如果 Release 无法确定：

```text
Source = Unknown
```

程序不能因为缺失字段崩溃。

---

### Test 6：下载

选择一个 Release：

```text
Download
```

最终必须能够：

```text
qBittorrent 出现任务
```

并开始下载，且任务保存路径等于配置的 `QBIT_SAVEPATH`。

---

### Test 7：检索参数回归

nekoBT 对未知参数是静默忽略的，把 `query` 写成 `q` 不会报错，只会让搜索条件失效（表现为返回最新 50 条，而不是搜索结果）。

必须有一个回归测试断言：

```text
media/search?query=葬送的芙莉莲  → 首位是 s462
torrents/search?media_id=s462     → 结果中 media_id 全为 s462
```

---

### Test 8：下载器连通性自检

启动时必须先探测：

```text
GET /api/v2/app/webapiVersion
```

WebUI 未启用、端口错误、账号密码错误时，应立刻报错并给出可执行的修复提示，而不是等用户选完 Release 才失败。

---

# 22. MVP 完成的最终定义

当以下流程可以稳定运行：

```text
中文作品名
     ↓
作品确认
     ↓
nekoBT 元数据（Bangumi 可选）
     ↓
Release 搜索
     ↓
Release 筛选
     ↓
用户选择
     ↓
Torrent
     ↓
qBittorrent
     ↓
本地下载
```

则：

> **MVP 完成。**

不要求：

* GUI 漂亮
* 自动字幕
* 自动整理
* 在线播放
* 自动追番
* 多下载器
* AI 推荐

---

# 23. 后续版本路线

MVP 完成后，再按照实际使用价值扩展。

## V1：本地 Web UI

```text
CLI
 ↓
FastAPI
 ↓
本地 Web UI
```

提供：

* 搜索框
* 作品候选
* Release 表格
* 筛选栏
* 排序
* 下载按钮

---

## V1.5：媒体库辅助

增加：

* 下载历史
* 文件识别
* 自动重命名
* Season / Episode 识别
* 本地文件与 Release 关联

---

## V2：字幕管理

独立模块：

```text
Release
   ↕
Subtitle
```

处理：

* 字幕检索
* Release 匹配
* 版本判断
* 时间轴检查

---

## V3：自动追番

```text
订阅作品
     ↓
定期检查新 Release
     ↓
按照个人规则评分
     ↓
通知用户
```

是否自动下载，应当由用户单独开启。

---

# 24. 最终产品定义

本项目不是：

> ❌ 动漫在线观看网站

不是：

> ❌ Torrent 资源站

不是：

> ❌ 播放器

不是：

> ❌ Jellyfin 替代品

不是：

> ❌ 自动追番机器人

而是：

> ## **个人动漫媒体库 Release 管理器**

它解决的核心问题是：

> **“我知道自己想看的动漫，但我不想随便找一个能看的版本；我想找到符合自己媒体库标准的那个 Release，并可靠地把它下载到本地。”**

因此项目的核心链路最终保持为：

```text
                Personal Anime Media Library
                            │
                            ▼
                    ┌───────────────┐
                    │  Work Resolve │
                    │nekoBT Media   │
                    │   Search      │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │    Release    │
                    │     Search    │
                    │    nekoBT     │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ Filter / Rank │
                    │  User Rules   │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ User Selects  │
                    │    Release    │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   Download    │
                    │  qBittorrent  │
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │ Local Media   │
                    │     File      │
                    └───────────────┘
```

**MVP 的终点就是 `Local Media File`。**

从这里开始，播放器、字幕、媒体库整理和自动追番全部属于后续产品，而不是 MVP 的责任范围。

---

# 附录 A：实测 API 事实

> 记录时间：2026-10-05。以下均为真实调用结果，不是推测。

## A.1 nekoBT

```text
站点        https://nekobt.to
公开检索    无需 API Key
API Root    /api/v1
```

已验证接口：

| 接口 | 结果 |
| --- | --- |
| `GET /api/v1/media/search?query=...` | 200，中文可命中 |
| `GET /api/v1/media/{media_id}` | 200，含 AniList / TVDB / TMDB / IMDB |
| `GET /api/v1/torrents/search?...` | 200 |
| `GET /api/v1/torrents/{id}` | 200，含 description |
| `GET /api/v1/torrents/{id}/download?public=true` | 200，`application/x-bittorrent` |
| `GET /api/torznab/api?t=search` | 200，RSS 格式 |

媒体搜索已验证结果：

| 输入 | 命中 | similarity |
| --- | --- | --- |
| 葬送的芙莉莲 | s462 Frieren: Beyond Journey's End | 0.5556 |
| 无职转生 | s153 Mushoku Tensei | 正确 |
| 水星领航员 | s1267 ARIA The ANIMATION | 正确 |
| 咒术回战 | s2335 JUJUTSU KAISEN | 正确 |
| 孤独摇滚 | s6 BOCCHI THE ROCK! | 1.0 |
| 药师少女的独语 | s1477 The Apothecary Diaries | 0.1429 |
| 无职转生 第二季 | ✗ 命中 Biaoren（错误） | 0.3333 |

Torrent 搜索参数：

| 参数 | 说明 | 备注 |
| --- | --- | --- |
| `query` | 关键字 | 写 `q` 静默失效；不索引中文 |
| `media_id` | 作品 ID | 主路径 |
| `limit` / `offset` | 分页 | `per_page` 无效 |
| `sort_by` | 排序 | 实测可用 `best` / `seeders`；`size` 会报错 |
| `sub_lang` | 字幕语言 | 如 `zh-hans` |
| `video_codec` | 视频编码 | 数字：1 = H.264，2 = HEVC，3 = AV1 |
| `video_type` | 视频类型 | 数字枚举 |
| `batch` | 是否合集 | 布尔 |
| `upgraded` | 是否升级版 | 布尔 |

Torrent 返回字段（42 个）：

```text
animetosho / anonymous / audio_lang / auto_title / batch / category /
comment_count / completed / deleted / description / filesize / fsub_lang /
groups / hardsub / has_mediainfo / has_screenshots / hidden / id / imported /
infohash / leechers / level / magnet / media_episode_ids / media_id / mtl /
nyaa_upload_time / otl / private_magnet / rss_time / seeders / sub_lang /
title / upgraded / uploaded_at / uploader / user_download_count /
user_is_leeching / user_is_seeding / video_codec / video_type / waiting_approve
```

注意：**没有 `resolution` 和 `source` 字段**，这两项必须从标题解析——这正是 `ReleaseParser` 存在的原因。

## A.2 Bangumi

```text
https://bgm.tv/           连接失败（超时）
https://api.bgm.tv/       连接失败（超时）
```

结论：开发机当前网络不可达，不能作为 MVP 硬依赖。

## A.3 AniList

```text
https://graphql.anilist.co    可达
```

关键限制：`search` 只匹配 romaji / english / native，**不匹配 synonym**，因此中文查询返回空数组。

```graphql
{Page{media(search:"葬送的芙莉莲",type:ANIME){id title{romaji native}}}}
→ {"data":{"Page":{"media":[]}}}
```

但 nekoBT 媒体详情里内嵌的 synonyms 是完整的，可以直接复用：

```text
s462 → anilist 154587 / Sousou no Frieren / 葬送のフリーレン
synonyms 含：葬送的芙莉蓮、장송의 프리렌、Frieren at the Funeral ...
```

## A.4 qBittorrent

```text
F:\qBittorrentEE\qbittorrent_x64.exe   版本 v4.6.5.10（实际运行）
F:\qBittorrentEE\qbittorrent.exe       版本 v5.1.0.11（未使用）
WebAPI 版本 2.9.3
WebUI 端口 8081（8080 被 NI Application Web Server 占用）
```

WebAPI v2 关键约定：

* 登录：`POST /api/v2/auth/login`，form 参数 `username` / `password`，需带 `Referer` 头
* 登录成功后所有请求携带 `SID` Cookie
* 添加：`POST /api/v2/torrents/add`，`multipart/form-data`
  * 种子来源：`urls`（URL / magnet）或 `torrents`（文件二进制）
  * 其他字段：`savepath` / `category` / `tags` / `paused` / `skip_checking` / `root_folder`
* 暂停/恢复的接口名随版本变化，必须做兼容：
  * 4.6（WebAPI 2.9.3）：只有 `torrents/pause` / `torrents/resume`
  * 5.0+（WebAPI 2.11+）：改名为 `torrents/stop` / `torrents/start`
  * 实测 4.6 上调用 `torrents/stop` 返回 404，所以客户端采用「新名优先、404 回退旧名」
* 添加任务的暂停字段同样变过：4.x / 5.0 用 `paused`，5.1+ 用 `stopped`；两个都发最省事

实测已验证（本机 v4.6.5.10）：

```text
POST /api/v2/torrents/add  → 任务出现，分类/标签正确，savepath = D:\Anime\library
POST /api/v2/torrents/delete (deleteFiles=true) → 任务与磁盘文件一并清除
真实下载可正常进行，做种数充足
```

## A.5 本机环境

```text
Python       3.9.13 (D:\Anaconda\python.exe)
已有依赖      requests / beautifulsoup4 / tenacity / SQLAlchemy / click / pytest
需要新增      python-dotenv（读 .env）
下载目录      D:\Anime
```

注意：Python 3.9 不支持 `str | None` 运行时语法，需 `from __future__ import annotations` 或改用 `Optional`。

## A.6 尚未验证

* nekoBT 非公开种子（`private_magnet` 非空）需要 API Key，尚未验证
