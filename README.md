<div align="center">
  <img src="./assets/images/logo.svg" alt="StreamCap" />
</div>
<p align="center">
  <img alt="Python version" src="https://img.shields.io/badge/python-3.10%2B-blue.svg">
  <a href="https://github.com/ihmily/StreamCap">
      <img alt="Supported Platforms" src="https://img.shields.io/badge/Platforms-Docker%20%7C%20Web-6B5BFF.svg"></a>
    <a href="https://hub.docker.com/r/ihmily/streamcap/tags">
      <img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/ihmily/streamcap?label=Docker%20Pulls&color=2496ED&logo=docker&v=0"></a>
  <a href="https://github.com/ihmily/StreamCap/releases/latest">
      <img alt="Latest Release" src="https://img.shields.io/github/v/release/ihmily/StreamCap"></a>
  <a href="https://github.com/ihmily/StreamCap/releases/latest">
      <img alt="Downloads" src="https://img.shields.io/github/downloads/ihmily/StreamCap/total?v=0"></a>
</p>
<div align="center">
  简体中文 / <a href="./README_EN.md">English</a>
</div><br>




StreamCap 是一个基于 FFmpeg 和 StreamGet 的多平台直播流录制与媒体管理平台，覆盖 40+ 国内外主流直播平台，支持批量录制、循环监控、定时监控、自动转码、视频库管理与基于 YOLOv8 人体识别的自动剪辑等功能。采用 React + FastAPI 前后端分离架构，Docker 一键部署。

## ✨功能特性

- **Web 管理**：React + FastAPI 前后端分离，Docker 单容器部署
- **循环监控**：实时监控直播间状态，开播即录。
- **定时任务**：根据设定时间范围检查直播间状态。
- **多种输出格式**：支持 ts、flv、mkv、mov、mp4、mp3、m4a 等格式。
- **自动转码**：录制完成后自动转码为 mp4 格式。
- **视频管理**：录制视频在线浏览、流式播放、删除、批量清理。
- **人体识别剪辑**：基于 YOLOv8 检测+姿态识别，自动剪辑出仅含人物的片段（可录制后自动处理）。
- **消息推送**：支持直播状态推送，及时获取开播通知。

## 📸录制界面

![StreamCap Interface](./assets/images/example01.png)

## 🛠️快速开始

### 1.**Docker 运行（推荐）**

本机无需 Python 环境运行，在运行命令之前，请确保您的机器上安装了 [Docker](https://docs.docker.com/get-docker/) 和 [Docker Compose](https://docs.docker.com/compose/install/)

进入项目根目录后执行（确保已经存在 `.env` 文件）：

```bash
cp .env.example .env   # 首次运行
docker compose up -d
```

启动成功后，通过 `http://127.0.0.1:6006` 访问。

停止容器实例：

```bash
docker compose stop
```

数据持久化：`./config`（配置）、`./downloads`（录制视频）、`./logs`（日志）通过卷挂载到宿主机。

### 2.从源代码运行

确保已安装 **Python 3.10+**、**FFmpeg** 和 **bun/node**（构建前端）。

```bash
# 克隆项目
git clone https://github.com/ihmily/StreamCap.git
cd StreamCap

# 安装后端依赖
pip install -r requirements.txt
pip install -r requirements-pose.txt   # 人体识别功能（可选）

# 构建前端
cd frontend && bun install && bun run build && cd ..

# 启动
python main.py --host 0.0.0.0 --port 6006
```

通过 `http://127.0.0.1:6006` 访问。

如果程序提示缺少 FFmpeg，请访问 FFmpeg 官方下载页面 [Download FFmpeg](https://ffmpeg.org/download.html)，下载预编译的 FFmpeg 可执行文件，并配置环境变量。

## 😺已支持平台

**国内平台（30+）**：

抖音、快手、虎牙、斗鱼、B站、小红书、YY、映客、Acfun、Blued、京东、淘宝...

**海外平台（10+）**：

TikTok、Twitch、PandTV、Soop、Twitcasting、CHZZK、Shopee、Youtube、LiveMe、Flextv(TTingLive)、Popkontv、Bigo...

**示例地址：**

如未特殊备注，默认使用直播间地址录制

```
抖音:
https://live.douyin.com/745964462470 (网页端主播直播间地址)
https://v.douyin.com/iQFeBnt/ (app端主播直播间地址)
https://live.douyin.com/yall1102 (拼接“https://live.douyin.com/”+抖音号, 可支持录制VR直播)
https://v.douyin.com/CeiU5cbX (app端主播主页地址)
https://www.douyin.com/user/MS4wLjABAAAA3kr2yA4aRD-sjf9cx8xkOH8Di3RjktpKcAvqIetpsF0 (网页端主播主页地址)

TikTok:
https://www.tiktok.com/@pearlgaga88/live

快手:
https://live.kuaishou.com/u/yall1102

虎牙:
https://www.huya.com/52333

斗鱼:
https://www.douyu.com/3637778?dyshid=
https://www.douyu.com/topic/wzDBLS6?rid=4921614&dyshid=

YY:
https://www.yy.com/22490906/22490906

B站:
https://live.bilibili.com/320

小红书:
http://xhslink.com/xpJpfM  (一次性地址，暂不支持循环监控)

bigo直播:
https://www.bigo.tv/cn/716418802

buled直播:
https://app.blued.cn/live?id=Mp6G2R

SOOP:
https://play.sooplive.com/yoomingseo/293945591

网易cc:
https://cc.163.com/583946984

千度热播:
https://qiandurebo.com/web/video.php?roomnumber=33333

PandaTV:
https://www.pandalive.co.kr/live/play/bara0109

猫耳FM:
https://fm.missevan.com/live/868895007

Look直播:
https://look.163.com/live?id=65108820&position=3

WinkTV:
https://www.winktv.co.kr/live/play/anjer1004

FlexTV/TTinglive:
https://www.flextv.co.kr/channels/593127/live
https://www.ttinglive.com/channels/593127/live

PopkonTV:
https://www.popkontv.com/live/view?castId=wjfal007&partnerCode=P-00117
https://www.popkontv.com/channel/notices?mcid=wjfal007&mcPartnerCode=P-00117

TwitCasting:
https://twitcasting.tv/c:uonq

百度直播:
https://live.baidu.com/m/media/pclive/pchome/live.html?room_id=9175031377&tab_category

微博直播:
https://weibo.com/l/wblive/p/show/1022:2321325026370190442592

酷狗直播:
https://fanxing2.kugou.com/50428671?refer=2177&sourceFrom=

TwitchTV:
https://www.twitch.tv/gamerbee

LiveMe:
https://www.liveme.com/zh/v/17141543493018047815/index.html

花椒直播:
https://www.huajiao.com/l/345096174  (一次性地址，暂不支持循环监控)

ShowRoom:
https://www.showroom-live.com/room/profile?room_id=480206  (主播主页地址)

Acfun:
https://live.acfun.cn/live/179922

映客直播:
https://www.inke.cn/liveroom/index.html?uid=22954469&id=1720860391070904

音播直播:
https://live.ybw1666.com/800002949

知乎直播:
https://www.zhihu.com/theater/10687?drama_id=2037464603865642996  (直播间地址)

CHZZK:
https://chzzk.naver.com/live/458f6ec20b034f49e0fc6d03921646d2

嗨秀直播:
https://www.haixiutv.com/6095106

VV星球直播:
https://h5webcdn-pro.vvxqiu.com//activity/videoShare/videoShare.html?h5Server=https://h5p.vvxqiu.com&roomId=LP115924473&platformId=vvstar

17Live:
https://17.live/en/live/6302408

浪Live:
https://www.lang.live/en-US/room/3349463

畅聊直播:
https://live.tlclw.com/106188

飘飘直播:
https://m.pp.weimipopo.com/live/preview.html?uid=91648673&anchorUid=91625862&app=plpl

六间房直播:
https://v.6.cn/634435

乐嗨直播:
https://www.lehaitv.com/8059096

花猫直播:
https://h.catshow168.com/live/preview.html?uid=19066357&anchorUid=18895331

Shopee:
https://sg.shp.ee/GmpXeuf?uid=1006401066&session=802458

Youtube(需配置cookie):
https://www.youtube.com/watch?v=cS6zS5hi1w0

淘宝:
https://tbzb.taobao.com/live?liveSource=pc_live.discovery&liveId=563820798126  (一次性地址，暂不支持循环监控)

京东:
https://3.cn/28MLBy-E

Faceit:
https://www.faceit.com/zh/players/Compl1/stream

连接直播:
https://show.lailianjie.com/10000258

咪咕直播:
https://www.miguvideo.com/p/live/120000541321

来秀直播:
https://www.imkktv.com/h5/share/video.html?uid=1845195&roomId=1710496

Picarto:
https://www.picarto.tv/cuteavalanche

心动热播：
https://xcqrkj.com/web/video.php?roomnumber=10394232
```

## 📖文档

如需完整文档和高级用法，请访问官方文档 [Wiki](https://github.com/ihmily/StreamCap/wiki/%E4%B8%BB%E9%A1%B5)

## ❤️贡献者

<a href="https://github.com/ihmily/StreamCap/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=ihmily/StreamCap" />
</a>

## 📜许可证

StreamCap在Apache License 2.0下发布。有关详情，请参阅[LICENSE](./LICENSE)文件。

## 🙏特别感谢

特别感谢以下开源项目和技术的支持：

- [flet](https://github.com/flet-dev/flet)
- [FFmpeg](https://ffmpeg.org)
- [streamget](https://github.com/ihmily/streamget)

如果您有任何问题或建议，请随时通过GitHub Issues与我们联系。
