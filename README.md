
<div align="center">

![:name](https://count.getloli.com/@astrbot_plugin_qzone?name=astrbot_plugin_qzone&theme=minecraft&padding=6&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

# astrbot_plugin_qzone

_✨ [astrbot](https://github.com/AstrBotDevs/AstrBot) QQ空间对接插件 ✨_  

[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![AstrBot](https://img.shields.io/badge/AstrBot-3.4%2B-orange.svg)](https://github.com/Soulter/AstrBot)
[![GitHub](https://img.shields.io/badge/作者-Zhalslar-blue)](https://github.com/Zhalslar)

</div>

> **警告**：V2.3.0 更改了部分配置结构， 更新前务必备份配置文件，否则得重写配置

## 🤝 介绍

QQ空间对接插件, 可自动发说说、表白墙投稿审核、查看说说、点赞、评论等

## 📦 安装

- 直接在astrbot的插件市场搜索astrbot_plugin_qzone，点击安装，等待完成即可

- 也可以克隆源码到插件文件夹：

```bash
# 克隆仓库到插件目录
cd /AstrBot/data/plugins
git clone https://github.com/Zhalslar/astrbot_plugin_qzone

# 控制台重启AstrBot
```

## ⌨️ 配置

请前往插件配置面板进行配置

## 🐔 使用说明

### 命令表

| 命令   | 别名       | 权限      | 参数              | 功能说明                      |
| ---- | -------- | ------- | --------------- | ------------------------- |
| 查看访客 | -        | ADMIN   | -               | 获取并展示 QQ 空间最近访客列表（图片渲染）   |
| 看说说  | 查看说说     | ALL     | `[@群友] [序号]`    | 查看空间说说；`@群友` 看 ta 的，缺省看最新 |
| 读说说  | 评论说说、评说说 | ALL     | `[@群友] [序号/范围]` | 给指定说说点赞+评论；缺省对最新操作        |
| 发说说  | -        | ADMIN   | `<文本> [图片]`     | 立即发表一条用户自定义说说             |
| 写说说  | 写稿、写草稿   | ALL     | `<主题> [图片]`     | AI 按主题生成草稿，需「过稿」后才会发布     |
| 投稿   | -        | ALL     | `<文本> [图片]`     | 向表白墙投递匿名稿件，等待管理员审核        |
| 看稿   | 查看稿件     | MEMBER+ | `[稿件ID]`        | 查看投稿库/指定稿件；缺省展示最新一条       |
| 过稿   | 通过稿件     | ADMIN   | `<稿件ID>`        | 审核通过并自动发布该稿件到空间           |
| 拒稿   | 拒绝稿件     | ADMIN   | `<稿件ID> [原因]`   | 拒绝该稿件，可附理由                |
| 删稿   | 删除稿件     | ADMIN   | `<稿件ID>`        | 直接从数据库删除该稿件               |

- 特殊用法1：@群友 也可直接 @QQ号 ，这样就可以查看任何人的说说了
- 特殊用法2：序号不仅可以填数字，也能填范围，如“查看说说 2~5”，表示查看第 2~5 条说说”
- “查看说说 2”是指查看bot的好友们最近发的两条说说； 而“查看说说@某群友 2”则表示查看这位群友的第 2 条说说

### 效果图

## 💡 TODO

- [x] 发说说
- [x] 校园表白墙功能：投稿、审核投稿
- [x] 点赞说说（接口显示成功，但实测点赞无效）
- [x] 评论说说
- [x] 定时自动发说说、日记
- [x] 定时自动评论、点赞好友的说说
- [x] LLM发说说
- [ ] LLM配图
- [ ] 自动上网冲浪写说说

## 👥 贡献指南

- 🌟 Star 这个项目！（点右上角的星星，感谢支持！）
- 🐛 提交 Issue 报告问题
- 💡 提出新功能建议
- 🔧 提交 Pull Request 改进代码

## 📌 注意事项

- 想第一时间得到反馈的可以来作者的插件反馈群（QQ群）：460973561（不点star不给进）

## 🤝 鸣谢

- 部分代码参考了[CampuxBot项目](https://github.com/idoknow/CampuxBot)，由作者之一的Soulter推荐

- [QQ 空间爬虫之爬取说说](https://kylingit.com/blog/qq-空间爬虫之爬取说说/)
  感谢这篇博客提供的思路。

- [一个QQ空间爬虫项目](https://github.com/wwwpf/QzoneExporter)

- [QQ空间](https://qzone.qq.com/) 网页显示本地数据时使用的样式与布局均来自于QQ空间。
