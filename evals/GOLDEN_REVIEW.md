# 黄金测试集待审清单
共 67 条（含 smoke + full）。
| id | 文件 | 话术 | 期望域 | action | mqtt | 问题 | 建议 |
|---|---|---|---|---|---|---|---|
| UI-CAL-01 | calendar.yaml | 查询今天的日程 | calendar | query_events | 0 | — | yes |
| UI-CAL-02 | calendar.yaml | 下一个会议是什么 | calendar | query_events | 0 | — | yes |
| UI-CAL-03 | calendar.yaml | 创建明天下午3点的会议 | calendar | create_event | 0 | — | yes |
| UI-CAL-04 | calendar.yaml | 取消今天的会议 | calendar | cancel_event | 0 | — | yes |
| UI-CAL-X2 | calendar.yaml | 查看今日行程 | calendar | query_events | 0 | — | yes |
| UI-CHI-01 | chitchat.yaml | 今天天气真好 | chitchat | None | 0 | — | yes |
| UI-CHI-02 | chitchat.yaml | 你好 | chitchat | None | 0 | — | yes |
| UI-CHI-03 | chitchat.yaml | 谢谢 | chitchat | None | 0 | — | yes |
| UI-CHI-04 | chitchat.yaml | 我想听个笑话 | chitchat | None | 0 | — | yes |
| UI-CHI-07 | chitchat.yaml | 锁车并打开氛围灯 | chitchat | None | 0 | — | yes |
| UI-KNO-01 | knowledge.yaml | 如何使用空调 | knowledge | query_manual | 0 | — | yes |
| UI-KNO-02 | knowledge.yaml | 怎么调整座椅 | knowledge | query_manual | 0 | — | yes |
| UI-KNO-03 | knowledge.yaml | 胎压监测在哪里 | knowledge | query_manual | 0 | — | yes |
| UI-KNO-04 | knowledge.yaml | 如何设置蓝牙 | knowledge | query_manual | 0 | — | yes |
| UI-KNO-X1 | knowledge.yaml | 关闭发动机后大约5小时从车底发出噪音并持续几分钟 | knowledge | query_manual | 0 | — | yes |
| UI-MED-01 | media.yaml | 播放音乐 | media | media_play | 1 | — | yes |
| UI-MED-02 | media.yaml | 暂停播放 | media | media_pause | 1 | — | yes |
| UI-MED-03 | media.yaml | 下一首 | media | media_next | 1 | — | yes |
| UI-MED-04 | media.yaml | 上一首 | media | media_prev | 1 | — | yes |
| UI-MED-05 | media.yaml | 音量增大 | media | volume_up | 1 | — | yes |
| UI-MED-06 | media.yaml | 音量减小 | media | volume_down | 1 | — | yes |
| UI-MED-07 | media.yaml | 静音 | media | mute | 1 | — | yes |
| UI-MED-08 | media.yaml | 播放周杰伦的歌 | media | play_by_artist | 1 | — | yes |
| UI-MED-09 | media.yaml | 播放稻香 | media | play_by_title | 1 | — | yes |
| UI-MED-10 | media.yaml | 播放我的收藏 | media | play_favorites | 1 | — | yes |
| UI-MED-11 | media.yaml | 播放电台 | media | play_radio | 1 | — | yes |
| UI-MED-12 | media.yaml | 随机播放 | media | play_random | 0 | — | yes |
| UI-MED-13 | media.yaml | 切换蓝牙音源 | media | switch_source | 1 | — | yes |
| UI-CHI-05 | mixed.yaml | 打开车窗，同时播放音乐 | ['vehicle', 'media'] | None | 1 | — | yes |
| UI-CHI-06 | mixed.yaml | 导航到机场，播放轻音乐 | ['vehicle', 'media'] | None | 1 | — | yes |
| UI-CHI-08 | mixed.yaml | 打开空调25度，播放收藏的歌 | ['vehicle', 'media'] | None | 1 | — | yes |
| UI-NAV-01 | navigation.yaml | 导航到机场 | navigation | set_nav_goal | 1 | — | yes |
| UI-NAV-02 | navigation.yaml | 导航到公司 | navigation | set_nav_goal | 1 | — | yes |
| UI-NAV-03 | navigation.yaml | 导航回家 | navigation | set_nav_goal | 1 | — | yes |
| UI-NAV-04 | navigation.yaml | 取消导航 | navigation | cancel_nav | 1 | — | yes |
| UI-NAV-05 | navigation.yaml | 添加途经点星巴克 | navigation | add_via | 0 | — | yes |
| UI-NAV-06 | navigation.yaml | 查询还有多久到达 | navigation | query_eta | 1 | — | yes |
| UI-NAV-07 | navigation.yaml | 查询剩余距离 | navigation | query_remaining_distance | 1 | — | yes |
| UI-NAV-08 | navigation.yaml | 下一步怎么走 | navigation | query_next_maneuver | 1 | — | yes |
| UI-VEH-01 | vehicle.yaml | 打开车窗 | vehicle | window_open | 1 | — | yes |
| UI-VEH-02 | vehicle.yaml | 关闭车窗 | vehicle | window_close | 1 | — | yes |
| UI-VEH-03 | vehicle.yaml | 打开天窗 | vehicle | sunroof_open | 0 | — | yes |
| UI-VEH-04 | vehicle.yaml | 关闭天窗 | vehicle | sunroof_close | 0 | — | yes |
| UI-VEH-05 | vehicle.yaml | 锁车 | vehicle | door_lock | 0 | — | yes |
| UI-VEH-06 | vehicle.yaml | 解锁车门 | vehicle | door_unlock | 0 | — | yes |
| UI-VEH-07 | vehicle.yaml | 打开后备箱 | vehicle | trunk_open | 1 | — | yes |
| UI-VEH-08 | vehicle.yaml | 打开前备箱 | vehicle | frunk_open | 0 | — | yes |
| UI-VEH-09 | vehicle.yaml | 打开空调 | vehicle | ac_power | 0 | — | yes |
| UI-VEH-10 | vehicle.yaml | 设置空调温度25度 | vehicle | set_ac_temp | 0 | — | yes |
| UI-VEH-11 | vehicle.yaml | 前挡风除霜 | vehicle | defrost_front | 0 | — | yes |
| UI-VEH-12 | vehicle.yaml | 座椅加热2档 | vehicle | seat_heat | 0 | — | yes |
| UI-VEH-13 | vehicle.yaml | 座椅通风 | vehicle | seat_vent | 0 | — | yes |
| UI-VEH-14 | vehicle.yaml | 方向盘加热 | vehicle | steering_wheel_heat | 0 | — | yes |
| UI-VEH-15 | vehicle.yaml | 打开氛围灯 | vehicle | ambient_light | 0 | — | yes |
| UI-VEH-16 | vehicle.yaml | 打开雾灯 | vehicle | fog_light | 1 | — | yes |
| UI-VEH-17 | vehicle.yaml | 打开近光灯 | vehicle | low_beam | 1 | — | yes |
| UI-VEH-18 | vehicle.yaml | 后视镜折叠 | vehicle | mirror_fold | 1 | — | yes |
| UI-VEH-19 | vehicle.yaml | 雨刮速度3档 | vehicle | wiper_speed | 1 | — | yes |
| UI-VEH-L1-REJECT | vehicle.yaml | 打开车窗 | vehicle | window_open | 1 | — | yes |
| R-K1 | smoke.yaml | 如何使用空调 | knowledge | None | 0 | — | yes |
| R-K2 | smoke.yaml | 关闭发动机后大约5小时从车底发出噪音并持续几分钟 | knowledge | None | 0 | — | yes |
| R-C1 | smoke.yaml | 我想听个笑话 | chitchat | None | 0 | — | yes |
| R-CAL1 | smoke.yaml | 查询今天的日程 | calendar | None | 0 | — | yes |
| S-MQTT1 | smoke.yaml | 如何使用空调 | knowledge | None | 0 | — | yes |
| S-L2-1 | smoke.yaml | 锁车 | None | None | None | — | review |
| S-L1-1 | smoke.yaml | 打开车窗 | vehicle | window_open | None | — | yes |
| S-PROF1 | smoke.yaml | 打开车窗 | vehicle | window_open | None | — | yes |

## 需人工优先审（1）
- `S-L2-1` 锁车 — —

## 建议进 golden 门槛的首批（无问题且单意图清晰）
- `UI-CAL-01` 查询今天的日程
- `UI-CAL-02` 下一个会议是什么
- `UI-CAL-03` 创建明天下午3点的会议
- `UI-CAL-04` 取消今天的会议
- `UI-CAL-X2` 查看今日行程
- `UI-CHI-01` 今天天气真好
- `UI-CHI-02` 你好
- `UI-CHI-03` 谢谢
- `UI-CHI-04` 我想听个笑话
- `UI-CHI-07` 锁车并打开氛围灯
- `UI-KNO-01` 如何使用空调
- `UI-KNO-02` 怎么调整座椅
- `UI-KNO-03` 胎压监测在哪里
- `UI-KNO-04` 如何设置蓝牙
- `UI-KNO-X1` 关闭发动机后大约5小时从车底发出噪音并持续几分钟，是否表示故障？
- `UI-MED-01` 播放音乐
- `UI-MED-02` 暂停播放
- `UI-MED-03` 下一首
- `UI-MED-04` 上一首
- `UI-MED-05` 音量增大
- `UI-MED-06` 音量减小
- `UI-MED-07` 静音
- `UI-MED-08` 播放周杰伦的歌
- `UI-MED-09` 播放稻香
- `UI-MED-10` 播放我的收藏
