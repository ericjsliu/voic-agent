import { useState, useRef, useEffect } from 'react'
import { Message } from '../types'
import './ChatPanel.css'

interface Props {
  messages: Message[];
  onSend: (text: string) => void;
  onL2Confirm: (taskId: string, stepId: string, branchId: string, accepted: boolean) => void;
  capabilityProfile?: {
    model_id: string;
    model_name: string;
    supported_actions: string[];
  };
}

// 领域分组命令芯片 - 覆盖PRD v1.10完整目录
const DOMAIN_COMMANDS = {
  vehicle: {
    label: '🚗 车辆控制',
    commands: [
      { text: '打开车窗', actions: ['window_open'] },
      { text: '关闭车窗', actions: ['window_close'] },
      { text: '打开天窗', actions: ['sunroof_open'] }, // Model B only
      { text: '关闭天窗', actions: ['sunroof_close'] }, // Model B only
      { text: '锁车', actions: ['door_lock'], level: 'L2' }, // L2 - 经典demo
      { text: '解锁车门', actions: ['door_unlock'] },
      { text: '打开后备箱', actions: ['trunk_open'] },
      { text: '打开前备箱', actions: ['frunk_open'] }, // Model B only
      { text: '打开空调', actions: ['ac_power'] },
      { text: '设置空调温度25度', actions: ['set_ac_temp'] },
      { text: '前挡风除霜', actions: ['defrost_front'] },
      { text: '座椅加热2档', actions: ['seat_heat'] }, // Model B only
      { text: '座椅通风', actions: ['seat_vent'] }, // Model B only
      { text: '方向盘加热', actions: ['steering_wheel_heat'] }, // Model B only
      { text: '打开氛围灯', actions: ['ambient_light'] },
      { text: '打开雾灯', actions: ['fog_light'] },
      { text: '打开近光灯', actions: ['low_beam'] },
      { text: '后视镜折叠', actions: ['mirror_fold'] },
      { text: '雨刮速度3档', actions: ['wiper_speed'] },
    ]
  },
  navigation: {
    label: '🧭 导航',
    commands: [
      { text: '导航到机场', actions: ['set_nav_goal'] }, // 经典demo
      { text: '导航到公司', actions: ['set_nav_goal'] },
      { text: '导航回家', actions: ['set_nav_goal'] },
      { text: '取消导航', actions: ['cancel_nav'] },
      { text: '添加途经点星巴克', actions: ['add_via'] }, // Model B only
      { text: '查询还有多久到达', actions: ['query_eta'] },
      { text: '查询剩余距离', actions: ['query_remaining_distance'] },
      { text: '下一步怎么走', actions: ['query_next_maneuver'] },
    ]
  },
  media: {
    label: '🎵 媒体',
    commands: [
      { text: '播放音乐', actions: ['media_play'] },
      { text: '暂停播放', actions: ['media_pause'] },
      { text: '下一首', actions: ['media_next'] },
      { text: '上一首', actions: ['media_prev'] },
      { text: '音量增大', actions: ['volume_up'] },
      { text: '音量减小', actions: ['volume_down'] },
      { text: '静音', actions: ['mute'] },
      { text: '播放周杰伦的歌', actions: ['play_by_artist'] },
      { text: '播放稻香', actions: ['play_by_title'] },
      { text: '播放我的收藏', actions: ['play_favorites'] },
      { text: '播放电台', actions: ['play_radio'] },
      { text: '随机播放', actions: ['play_random'] }, // Model B only
      { text: '切换蓝牙音源', actions: ['switch_source'] },
    ]
  },
  calendar: {
    label: '📅 日历',
    commands: [
      { text: '查询今天的日程', actions: ['query_events'] },
      { text: '下一个会议是什么', actions: ['query_events'] },
      { text: '创建明天下午3点的会议', actions: ['create_event'] },
      { text: '取消今天的会议', actions: ['cancel_event'] },
    ]
  },
  knowledge: {
    label: '📖 知识',
    commands: [
      { text: '如何使用空调', actions: ['query_manual'] }, // 经典demo - 手册查询
      { text: '怎么调整座椅', actions: ['query_manual'] },
      { text: '胎压监测在哪里', actions: ['query_manual'] },
      { text: '如何设置蓝牙', actions: ['query_manual'] },
    ]
  },
  chitchat: {
    label: '💬 闲聊',
    commands: [
      { text: '今天天气真好', actions: ['chitchat'] }, // 经典demo
      { text: '你好', actions: ['chitchat'] },
      { text: '谢谢', actions: ['chitchat'] },
      { text: '我想听个笑话', actions: ['chitchat'] },
    ]
  },
  combo: {
    label: '🎯 组合指令',
    commands: [
      { text: '打开车窗，同时播放音乐', actions: ['window_open', 'media_play'] }, // 经典demo - 多意图
      { text: '导航到机场，播放轻音乐', actions: ['set_nav_goal', 'media_play'] },
      { text: '锁车并打开氛围灯', actions: ['door_lock', 'ambient_light'] },
      { text: '打开空调25度，播放收藏的歌', actions: ['set_ac_temp', 'play_favorites'] },
    ]
  }
}

// Blacklist actions (永不显示在UI)
const BLACKLIST_ACTIONS = ['gear_shift', 'throttle_control', 'steering_control', 'autopilot_enable', 'noa_enable']

export default function ChatPanel({ messages, onSend, onL2Confirm, capabilityProfile }: Props) {
  const [input, setInput] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (input.trim()) {
      onSend(input.trim())
      setInput('')
    }
  }

  const handleChipClick = (phrase: string) => {
    onSend(phrase)
  }

  // 检查命令是否支持（基于当前Capability Profile）
  const isCommandSupported = (command: { text: string; actions: string[] }) => {
    if (!capabilityProfile) return true // 无profile时默认支持（避免阻塞）
    
    // 检查是否包含blacklist actions
    const hasBlacklistedAction = command.actions.some(action => 
      BLACKLIST_ACTIONS.includes(action)
    )
    if (hasBlacklistedAction) return false // 永不显示blacklist
    
    // 检查是否所有actions都在supported_actions中
    return command.actions.every(action => 
      capabilityProfile.supported_actions.includes(action) || 
      action === 'chitchat' || 
      action === 'query_manual'
    )
  }

  return (
    <div className="chat-panel">
      <div className="messages-container">
        {messages.map(msg => (
          <div key={msg.id} className={`message ${msg.type}`}>
            <div className="message-content">
              <div className="message-text">{msg.content}</div>
              {msg.citations && msg.citations.length > 0 && (
                <div className="citations">
                  <div className="citations-header">📚 References:</div>
                  {msg.citations.map((citation, idx) => (
                    <div key={idx} className="citation-card">
                      <div className="citation-meta">
                        {citation.section && <span className="section">{citation.section}</span>}
                        {citation.page && <span className="page">p.{citation.page}</span>}
                      </div>
                      {citation.doc_id && (
                        <div className="citation-doc">{citation.doc_id}</div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              {msg.l2Pending && (
                <div className="l2-confirm-buttons">
                  <button 
                    className="btn-confirm"
                    onClick={() => onL2Confirm(
                      msg.l2Pending!.taskId,
                      msg.l2Pending!.stepId,
                      msg.l2Pending!.branchId,
                      true
                    )}
                  >
                    ✓ 确认
                  </button>
                  <button 
                    className="btn-cancel"
                    onClick={() => onL2Confirm(
                      msg.l2Pending!.taskId,
                      msg.l2Pending!.stepId,
                      msg.l2Pending!.branchId,
                      false
                    )}
                  >
                    ✕ 取消
                  </button>
                </div>
              )}
            </div>
            <div className="message-time">
              {new Date(msg.timestamp).toLocaleTimeString()}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      <div className="quick-chips-grouped">
        {Object.entries(DOMAIN_COMMANDS).map(([domain, { label, commands }]) => (
          <div key={domain} className="command-domain">
            <div className="domain-label">{label}</div>
            <div className="domain-chips">
              {commands
                .filter(cmd => !cmd.actions.some(a => BLACKLIST_ACTIONS.includes(a))) // 过滤blacklist
                .map((command, idx) => {
                  const supported = isCommandSupported(command)
                  return (
                    <button
                      key={`${domain}-${idx}`}
                      className={`chip ${!supported ? 'chip-disabled' : ''} ${command.level === 'L2' ? 'chip-l2' : ''}`}
                      onClick={() => supported && handleChipClick(command.text)}
                      disabled={!supported}
                      title={!supported ? `当前车型 (${capabilityProfile?.model_name || 'unknown'}) 不支持此功能` : command.text}
                    >
                      {command.text}
                      {command.level === 'L2' && <span className="l2-badge">L2</span>}
                    </button>
                  )
                })}
            </div>
          </div>
        ))}
      </div>

      <form className="input-form" onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入语音指令（模拟ASR文本）..."
          className="input-field"
        />
        <button type="submit" className="send-button" disabled={!input.trim()}>
          发送
        </button>
      </form>
    </div>
  )
}
