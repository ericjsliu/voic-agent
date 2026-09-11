import { useState, useRef, useEffect } from 'react'
import { Message } from '../types'
import './ChatPanel.css'

interface Props {
  messages: Message[];
  onSend: (text: string) => void;
  onL2Confirm: (taskId: string, stepId: string, branchId: string, accepted: boolean) => void;
}

const DEMO_PHRASES = [
  '打开车窗，同时播放音乐',
  '锁车',
  '导航到机场',
  '如何使用空调',
  '今天天气真好'
]

export default function ChatPanel({ messages, onSend, onL2Confirm }: Props) {
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

      <div className="quick-chips">
        {DEMO_PHRASES.map(phrase => (
          <button
            key={phrase}
            className="chip"
            onClick={() => handleChipClick(phrase)}
          >
            {phrase}
          </button>
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
