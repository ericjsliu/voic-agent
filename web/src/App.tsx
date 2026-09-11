import { useState, useEffect, useRef } from 'react'
import ChatPanel from './components/ChatPanel'
import InfoPanel from './components/InfoPanel'
import ConnectionSettings from './components/ConnectionSettings'
import { Message, Writeback, VehicleTelemetry, ConnectionConfig } from './types'
import './App.css'

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [writebacks, setWritebacks] = useState<Writeback[]>([])
  const [telemetry, setTelemetry] = useState<VehicleTelemetry | null>(null)
  const [currentTaskGraph, setCurrentTaskGraph] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  const [config, setConfig] = useState<ConnectionConfig>({
    baseUrl: 'http://localhost:8000',
    driverId: 'driver_001',
    modelFilter: 'ModelA',
    versionFilter: '2024'
  })
  
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (config.sessionId) {
      connectWebSocket(config.sessionId)
    }
    return () => {
      wsRef.current?.close()
    }
  }, [config.sessionId])

  const connectWebSocket = (sessionId: string) => {
    const wsUrl = config.baseUrl.replace('http', 'ws') + `/ws/${sessionId}`
    const ws = new WebSocket(wsUrl)
    
    ws.onopen = () => {
      setConnected(true)
      console.log('WebSocket connected')
    }
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      
      if (data.type === 'taskgraph') {
        setCurrentTaskGraph(data.data)
        
        // 检查是否有L2待确认
        const l2Step = data.data.tasks?.[0]?.steps?.find((s: any) => 
          s.action?.level === 'L2'
        )
        
        if (l2Step) {
          setMessages(prev => [...prev, {
            id: Date.now().toString(),
            type: 'system',
            content: `需要确认：${l2Step.description || l2Step.action?.action}`,
            timestamp: new Date().toISOString(),
            l2Pending: {
              taskId: data.data.tasks[0].task_id,
              stepId: l2Step.step_id,
              branchId: data.data.tasks[0].branch_id,
              action: l2Step.action?.action
            }
          }])
        }
      } else if (data.type === 'writeback') {
        setWritebacks(prev => [data.data, ...prev].slice(0, 20))
      }
    }
    
    ws.onclose = () => {
      setConnected(false)
      console.log('WebSocket disconnected')
    }
    
    wsRef.current = ws
  }

  const handleSendMessage = async (text: string) => {
    // 添加用户消息
    const userMsg: Message = {
      id: Date.now().toString(),
      type: 'user',
      content: text,
      timestamp: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMsg])

    try {
      const response = await fetch(`${config.baseUrl}/dialogue`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: config.sessionId,
          driver_id: config.driverId,
          utterance: text,
          telemetry: telemetry || { gear: 'P', speed_kmh: 0 }
        })
      })

      const data = await response.json()
      
      if (!config.sessionId) {
        setConfig(prev => ({ ...prev, sessionId: data.session_id }))
      }

      // 添加助手回复
      const assistantMsg: Message = {
        id: (Date.now() + 1).toString(),
        type: 'assistant',
        content: generateResponseText(data.taskgraph),
        timestamp: new Date().toISOString(),
        taskGraph: data.taskgraph
      }
      setMessages(prev => [...prev, assistantMsg])
      setCurrentTaskGraph(data.taskgraph)

    } catch (error) {
      console.error('Failed to send message:', error)
      setMessages(prev => [...prev, {
        id: (Date.now() + 1).toString(),
        type: 'system',
        content: '发送失败，请检查连接',
        timestamp: new Date().toISOString()
      }])
    }
  }

  const generateResponseText = (taskgraph: any): string => {
    const task = taskgraph.tasks?.[0]
    if (!task) return '好的'
    
    const steps = task.steps || []
    if (steps.length === 0) return '好的'
    
    const descriptions = steps.map((s: any) => 
      s.description || s.action?.action || '执行任务'
    ).join('，')
    
    return `好的，正在${descriptions}`
  }

  const handleL2Confirm = (taskId: string, stepId: string, branchId: string, accepted: boolean) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: 'l2_confirm',
        task_id: taskId,
        step_id: stepId,
        branch_id: branchId,
        accepted
      }))
      
      setMessages(prev => [...prev, {
        id: Date.now().toString(),
        type: 'system',
        content: accepted ? '已确认执行' : '已取消操作',
        timestamp: new Date().toISOString()
      }])
    }
  }

  const handleCreateSession = async () => {
    try {
      const response = await fetch(`${config.baseUrl}/session/create`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          driver_id: config.driverId
        })
      })
      const data = await response.json()
      setConfig(prev => ({ ...prev, sessionId: data.session_id }))
      setMessages([{
        id: '0',
        type: 'system',
        content: `会话已创建: ${data.session_id}`,
        timestamp: new Date().toISOString()
      }])
    } catch (error) {
      console.error('Failed to create session:', error)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🚗 Smart Cockpit Voice Agent</h1>
        <div className="connection-status">
          <span className={`status-dot ${connected ? 'connected' : 'disconnected'}`} />
          {connected ? 'Connected' : 'Disconnected'}
        </div>
      </header>
      
      <div className="app-body">
        <div className="left-panel">
          <ConnectionSettings 
            config={config}
            onChange={setConfig}
            onCreateSession={handleCreateSession}
          />
          <ChatPanel 
            messages={messages}
            onSend={handleSendMessage}
            onL2Confirm={handleL2Confirm}
          />
        </div>
        
        <div className="right-panel">
          <InfoPanel 
            taskGraph={currentTaskGraph}
            writebacks={writebacks}
            telemetry={telemetry}
          />
        </div>
      </div>
    </div>
  )
}

export default App
